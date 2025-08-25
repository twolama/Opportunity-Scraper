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
                {"text": "ℹ️ About", "callback_data": "about"}
            ]
        ]
    }

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    message = data.get("message")
    callback_query = data.get("callback_query")
    chat_id = None
    text = None
    if message:
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
    elif callback_query:
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
        if text == "scrape_today":
            background_tasks.add_task(fetch_opportunities_by_date, target_date=None)
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "🔁 Scraping today's opportunities..."
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
