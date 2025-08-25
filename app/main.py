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
    return {
        "total": len(all_ops),
        "unposted": len(unposted),
        "posted": len(posted)
    }

def build_main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "Scrape Today", "callback_data": "scrape_today"},
                {"text": "Stats", "callback_data": "stats"}
            ],
            [
                {"text": "List Unposted", "callback_data": "list_unposted"},
                {"text": "List Posted", "callback_data": "list_posted"}
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
            "text": "Welcome to Opportunity Scraper Bot! Use the menu below to control the bot.",
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
            msg = f"<b>Analytics</b>\nTotal: {stats['total']}\nUnposted: {stats['unposted']}\nPosted: {stats['posted']}"
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            })
        elif text == "list_unposted":
            from app.database import get_unposted_opportunities
            unposted = get_unposted_opportunities()
            if not unposted:
                msg = "No unposted opportunities."
            else:
                msg = "<b>Unposted Opportunities:</b>\n" + "\n".join([f"- {op['title']}" for op in unposted[:10]])
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            })
        elif text == "list_posted":
            from app.database import get_all_opportunities
            posted = [op for op in get_all_opportunities() if op.get("posted_to_telegram")]
            if not posted:
                msg = "No posted opportunities."
            else:
                msg = "<b>Posted Opportunities:</b>\n" + "\n".join([f"- {op['title']}" for op in posted[:10]])
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
