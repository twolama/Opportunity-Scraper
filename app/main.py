import os
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware

from app.scheduler import start_scheduler, run_daily_tasks
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
import requests
import os
from app.database import (
    init_db,
    get_all_opportunities,
    get_unposted_opportunities,
    SessionLocal,
    Opportunity,
)

app = FastAPI()

# --- Telegram Webhook Handler ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

def get_stats():
    from app.database import get_all_opportunities, get_unposted_opportunities
    all_ops = get_all_opportunities()
    unposted = get_unposted_opportunities()
    posted = [op for op in all_ops if op.get("posted_to_telegram")]
    if posted:
        last_posted = max(posted, key=lambda x: x.get("created_at"))
        last_posted_time = last_posted.get("created_at")
    else:
        last_posted_time = "N/A"
    return {
        "total": len(all_ops),
        "unposted": len(unposted),
        "posted": len(posted),
        "last_posted": last_posted_time
    }

from datetime import datetime, timedelta
def build_main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Scrape Today", "callback_data": "scrape_today"},
                {"text": "📊 Analytics", "callback_data": "stats"}
            ],
            [
                {"text": "🟡 Unposted", "callback_data": "list_unposted"},
                {"text": "🟢 Posted", "callback_data": "list_posted"}
            ],
            [
                {"text": "📅 Go to Date", "callback_data": "goto_date_menu"}
            ],
            [
                {"text": "ℹ️ About", "callback_data": "about"}
            ]
        ]
    }

def build_date_nav_keyboard(date_str, mode):
    # mode: 'posted' or 'unposted'
    date = datetime.strptime(date_str, "%Y-%m-%d")
    prev_date = (date - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (date + timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "inline_keyboard": [
            [
                {"text": "⬅️ Previous", "callback_data": f"{mode}_date_{prev_date}"},
                {"text": f"{date_str}", "callback_data": "noop"},
                {"text": "Next ➡️", "callback_data": f"{mode}_date_{next_date}"}
            ],
            [
                {"text": "📅 Pick Date", "callback_data": f"{mode}_pick_year"}
            ],
            [
                {"text": "🔙 Main Menu", "callback_data": "main_menu"}
            ]
        ]
    }

def build_year_picker(mode):
    this_year = datetime.utcnow().year
    years = [this_year - i for i in range(5)]
    keyboard = [[{"text": str(y), "callback_data": f"{mode}_pick_month_{y}"}] for y in years]
    keyboard.append([{"text": "🔙 Back", "callback_data": f"{mode}_date_{datetime.utcnow().strftime('%Y-%m-%d')}"}])
    return {"inline_keyboard": keyboard}

def build_month_picker(mode, year):
    months = [
        ("Jan", 1), ("Feb", 2), ("Mar", 3), ("Apr", 4), ("May", 5), ("Jun", 6),
        ("Jul", 7), ("Aug", 8), ("Sep", 9), ("Oct", 10), ("Nov", 11), ("Dec", 12)
    ]
    keyboard = [[{"text": m[0], "callback_data": f"{mode}_pick_day_{year}-{m[1]:02d}"} for m in months[i:i+4]] for i in range(0, 12, 4)]
    keyboard.append([{"text": "🔙 Back", "callback_data": f"{mode}_pick_year"}])
    return {"inline_keyboard": keyboard}

def build_day_picker(mode, year_month):
    year, month = map(int, year_month.split("-"))
    from calendar import monthrange
    days = monthrange(year, month)[1]
    keyboard = []
    for i in range(1, days+1, 7):
        row = []
        for d in range(i, min(i+7, days+1)):
            date_str = f"{year}-{month:02d}-{d:02d}"
            row.append({"text": str(d), "callback_data": f"{mode}_date_{date_str}"})
        keyboard.append(row)
    keyboard.append([{"text": "🔙 Back", "callback_data": f"{mode}_pick_month_{year}"}])
    return {"inline_keyboard": keyboard}

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    import logging
    def safe_edit_message_text(payload):
        resp = requests.post(f"{TELEGRAM_API_URL}/editMessageText", json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {}
        if not resp.ok or not data.get("ok", True):
            # If edit fails (e.g., message not found or not sent by bot), send a new message
            logging.warning(f"editMessageText failed: {data}")
            payload2 = payload.copy()
            payload2.pop("message_id", None)
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload2)
    data = await request.json()
    message = data.get("message")
    callback_query = data.get("callback_query")
    chat_id = None
    text = None
    if message:
        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        if user_id != BOT_OWNER_ID:
                requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "Sorry, you are not authorized to control this bot."
                })
                return {"ok": True}
        text = message.get("text", "")
    elif callback_query:
        # Always answer callback query to avoid Telegram UI spinner
        callback_id = callback_query.get("id")
        user_id = callback_query["from"]["id"]
        chat_id = callback_query["message"]["chat"]["id"]
        if user_id != BOT_OWNER_ID:
                callback_id = callback_query.get("id")
                if callback_id:
                    requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "Not authorized.", "show_alert": True})
                return {"ok": True}
        if callback_id:
            requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})
        chat_id = callback_query["message"]["chat"]["id"]
        text = callback_query["data"]

    if not chat_id:
        return {"ok": True}

    if message and text.startswith("/start"):
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": (
                "<b>Welcome to Opportunity Scraper Bot!</b>\n\n"
                "Use the menu below to control the bot, get analytics, and view opportunities.\n\n"
                "<i>Created by @ScholarshipSpot</i>"
            ),
            "reply_markup": build_main_menu(),
            "parse_mode": "HTML"
        })
    elif callback_query:
        # Date navigation for posted/unposted
        if text == "noop":
            # Just answer the callback, do nothing
            callback_id = callback_query.get("id")
            if callback_id:
                requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})
        elif text.startswith("posted_pick_year"):
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": "Pick a year:",
                "parse_mode": "HTML",
                "reply_markup": build_year_picker("posted")
            })
        elif text.startswith("posted_pick_month_"):
            try:
                year = int(text.split("posted_pick_month_")[-1])
            except Exception:
                year = datetime.utcnow().year
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": f"Pick a month for {year}:",
                "parse_mode": "HTML",
                "reply_markup": build_month_picker("posted", year)
            })
        elif text.startswith("posted_pick_day_"):
            try:
                year_month = text.split("posted_pick_day_")[-1]
            except Exception:
                year_month = datetime.utcnow().strftime("%Y-%m")
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": f"Pick a day for {year_month}:",
                "parse_mode": "HTML",
                "reply_markup": build_day_picker("posted", year_month)
            })
        elif text.startswith("unposted_pick_year"):
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": "Pick a year:",
                "parse_mode": "HTML",
                "reply_markup": build_year_picker("unposted")
            })
        elif text.startswith("unposted_pick_month_"):
            try:
                year = int(text.split("unposted_pick_month_")[-1])
            except Exception:
                year = datetime.utcnow().year
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": f"Pick a month for {year}:",
                "parse_mode": "HTML",
                "reply_markup": build_month_picker("unposted", year)
            })
        elif text.startswith("unposted_pick_day_"):
            try:
                year_month = text.split("unposted_pick_day_")[-1]
            except Exception:
                year_month = datetime.utcnow().strftime("%Y-%m")
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": f"Pick a day for {year_month}:",
                "parse_mode": "HTML",
                "reply_markup": build_day_picker("unposted", year_month)
            })
        elif text.startswith("posted_date_"):
            date_str = text.replace("posted_date_", "")
            from app.database import get_all_opportunities
            posted = [op for op in get_all_opportunities() if op.get("posted_to_telegram")]
            from collections import defaultdict
            grouped = defaultdict(list)
            for op in posted:
                op_date = str(op.get("created_at", "N/A"))[:10]
                grouped[op_date].append(op)
            ops = grouped.get(date_str, [])
            if not ops:
                msg = f"<b>No posted opportunities for {date_str}.</b>"
            else:
                msg = f"<b>🟢 Posted Opportunities for {date_str}:</b>\n\n" + "\n\n".join([
                    f"<b>{op['title']}</b>\n<a href='{op['link']}'>Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in ops[:10]
                ])
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_date_nav_keyboard(date_str, "posted")
            })
        else:
            # Log and answer any unhandled callback data
            logging.warning(f"Unhandled callback data: {text}")
            callback_id = callback_query.get("id")
            if callback_id:
                requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "Not implemented or invalid action.", "show_alert": False})
    elif text.startswith("unposted_date_"):
            date_str = text.replace("unposted_date_", "")
            from app.database import get_unposted_opportunities
            unposted = get_unposted_opportunities()
            from collections import defaultdict
            grouped = defaultdict(list)
            for op in unposted:
                op_date = str(op.get("created_at", "N/A"))[:10]
                grouped[op_date].append(op)
            ops = grouped.get(date_str, [])
            if not ops:
                msg = f"<b>No unposted opportunities for {date_str}.</b>"
            else:
                msg = f"<b>🟡 Unposted Opportunities for {date_str}:</b>\n\n" + "\n\n".join([
                    f"<b>{op['title']}</b>\n<a href='{op['link']}'>Apply / Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in ops[:10]
                ])
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_date_nav_keyboard(date_str, "unposted")
            })
    elif text == "main_menu":
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": "Back to main menu.",
            "reply_markup": build_main_menu(),
            "parse_mode": "HTML"
        })
    elif text == "goto_date_menu":
        today = datetime.utcnow().strftime("%Y-%m-%d")
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Posted by Date", "callback_data": f"posted_pick_year"},
                    {"text": "Unposted by Date", "callback_data": f"unposted_pick_year"}
                ],
                [
                    {"text": "🔙 Main Menu", "callback_data": "main_menu"}
                ]
            ]
        }
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": "Choose which opportunities to view by date:",
            "reply_markup": keyboard,
            "parse_mode": "HTML"
        })
    elif text == "stats":
        stats = get_stats()
        msg = (
            f"<b>📊 Analytics</b>\n"
            f"Total: <b>{stats['total']}</b>\n"
            f"Unposted: <b>{stats['unposted']}</b>\n"
            f"Posted: <b>{stats['posted']}</b>\n"
            f"Last Posted: <b>{stats['last_posted']}</b>\n"
        )
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML"
        })
    elif text == "list_unposted":
        from app.database import get_unposted_opportunities
        unposted = get_unposted_opportunities()
        if not unposted:
            msg = "<b>No unposted opportunities.</b>"
        else:
            msg = "<b>🟡 Unposted Opportunities (latest 10):</b>\n\n" + "\n\n".join([
                f"<b>{op['title']}</b>\n<a href='{op['link']}'>Apply / Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in unposted[:10]
            ])
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
    elif text == "list_posted":
        from app.database import get_all_opportunities
        from collections import defaultdict
        posted = [op for op in get_all_opportunities() if op.get("posted_to_telegram")]
        if not posted:
            msg = "<b>No posted opportunities.</b>"
        else:
            # Group by date
            grouped = defaultdict(list)
            for op in posted:
                date_str = str(op.get("created_at", "N/A"))[:10]
                grouped[date_str].append(op)
            msg = "<b>🟢 Posted Opportunities (by date, latest 3 days):</b>\n"
            for date in sorted(grouped.keys(), reverse=True)[:3]:
                msg += f"\n<b>{date}</b>\n"
                for op in grouped[date][:5]:
                    msg += f"- <b>{op['title']}</b> (<a href='{op['link']}'>Details</a>)\n"
            msg += "\n<i>Showing up to 5 per day, latest 3 days.</i>"
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
    elif text == "about":
        msg = (
            "<b>About Opportunity Scraper Bot</b>\n\n"
            "This bot scrapes, stores, and shares the latest opportunities (scholarships, grants, fellowships, etc.) from the web.\n"
            "You can control scraping, view analytics, and browse opportunities right here!\n\n"
            "<i>Made with ❤️ by @twolamaa</i>"
        )
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML"
        })
    return {"ok": True}

# CORS config - allow all origins for now, restrict in production if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()
    # Start scheduler in a background thread
    if os.getenv("RUN_SCHEDULER", "true").lower() == "true":
        Thread(target=start_scheduler, daemon=True).start()
        print("🟢 Scheduler started")

@app.get("/")
async def root():
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.head("/ping")
async def ping_head():
    return

@app.get("/opportunities")
async def get_opportunities():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_all_opportunities)

@app.get("/opportunities/unposted")
async def get_unposted():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_unposted_opportunities)

@app.get("/opportunities/posted")
async def get_posted():
    def fetch_posted():
        db = SessionLocal()
        try:
            results = db.query(Opportunity).filter_by(posted_to_telegram=True).all()
            return [
                {
                    "id": opp.id,
                    "title": opp.title,
                    "link": opp.link,
                    "description": opp.description,
                    "deadline": opp.deadline,
                    "thumbnail": opp.thumbnail,
                    "tags": opp.tags.split(", ") if opp.tags else [],
                    "created_at": opp.created_at,
                    "posted_to_telegram": opp.posted_to_telegram,
                }
                for opp in results
            ]
        finally:
            db.close()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_posted)

# ✅ Optional: Trigger the task manually (for testing via browser)
@app.get("/run-once")
async def run_once():
    def run():
        run_daily_tasks()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run)
    return {"status": "Scheduler manually triggered."}
