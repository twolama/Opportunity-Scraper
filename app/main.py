import os
import secrets
import asyncio
import time
import logging
from fastapi import FastAPI, Request, BackgroundTasks
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional

from app.scheduler import start_scheduler, run_daily_tasks
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
import requests
from app.database import (
    init_db,
    get_all_opportunities,
    get_unposted_opportunities,
    SessionLocal,
    Opportunity,
    is_admin,
    add_admin,
    remove_admin,
    get_admins,
)

# --- Reusable HTTP session (connection pool => way faster) ---
_http = requests.Session()

# --- Pydantic Schemas ---

class OpportunityOut(BaseModel):
    id: int
    title: str
    link: str
    description: Optional[str] = None
    deadline: Optional[str] = None
    thumbnail: Optional[str] = None
    tags: list[str] = []
    created_at: Optional[datetime] = None
    posted_to_telegram: Optional[bool] = None

class StatsOut(BaseModel):
    total: int
    unposted: int
    posted: int
    last_posted: str

class PingOut(BaseModel):
    status: str

class RootOut(BaseModel):
    message: str

class RunOnceOut(BaseModel):
    status: str

class WebhookOut(BaseModel):
    ok: bool

app = FastAPI(
    title="Opportunity Scraper API",
    description="Scrapes opportunities (scholarships, grants, fellowships) from opportunitydesk.org, stores them in PostgreSQL, and posts new ones to a Telegram channel.",
    version="1.0.0",
    contact={"name": "Mecha Temesgen", "url": "https://t.me/twolamaa"},
)

# --- Telegram Webhook Handler ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

BOT_OWNER_ID_str = os.getenv("BOT_OWNER_ID", "")
BOT_OWNER_ID = int(BOT_OWNER_ID_str) if BOT_OWNER_ID_str.strip() else 0

# Lazy-loaded bot username (from getMe)
BOT_USERNAME: str | None = None

# --- In-memory admin cache (no DB query on every update) ---
_admin_ids: set[int] = set()
_admin_names: dict[int, str] = {}
_last_admin_refresh: float = 0
_ADMIN_CACHE_TTL = 60
_pending_admins: dict[int, str] = {}  # user_id -> first_name
_invite_tokens: dict[str, int] = {}    # token -> owner_id (who generated it)

def _refresh_admin_cache():
    global _admin_ids, _admin_names, _last_admin_refresh
    now = time.time()
    if now - _last_admin_refresh > _ADMIN_CACHE_TTL:
        admins = get_admins()
        _admin_ids = {a["user_id"] for a in admins}
        _admin_names = {a["user_id"]: a["name"] for a in admins}
        _last_admin_refresh = now

def _is_authorized(user_id: int) -> bool:
    if BOT_OWNER_ID and user_id == BOT_OWNER_ID:
        return True
    _refresh_admin_cache()
    return user_id in _admin_ids

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

def build_main_menu(user_id=None):
    is_owner = user_id and BOT_OWNER_ID and user_id == BOT_OWNER_ID
    keyboard = [
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
    if is_owner:
        keyboard.append([
            {"text": "👥 Admins", "callback_data": "admin_menu"}
        ])
    return {"inline_keyboard": keyboard}

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


def safe_edit_message_text(payload):
    resp = _http.post(f"{TELEGRAM_API_URL}/editMessageText", json=payload)
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not resp.ok or not data.get("ok", True):
        err = data.get("description", "")
        if "message is not modified" in err:
            return
        logging.warning(f"editMessageText failed: {data}")
        payload2 = payload.copy()
        payload2.pop("message_id", None)
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload2)

def _scrape_and_post(today, chat_id, message_id):
    try:
        new_ops = fetch_opportunities_by_date(today)
        posted_count = post_new_opportunities(today)
        msg = f"✅ Scraping and posting complete!\nNew opportunities scraped: <b>{len(new_ops)}</b>\nOpportunities posted to Telegram: <b>{posted_count}</b>"
    except Exception as e:
        msg = f"❌ Error during scraping: {e}"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML"
    }
    if message_id:
        payload["message_id"] = message_id
        _http.post(f"{TELEGRAM_API_URL}/editMessageText", json=payload)
    else:
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)


def process_telegram_update(data, run_in_background=None):
    message = data.get("message")
    callback_query = data.get("callback_query")
    chat_id = None
    text = None
    user_id = None
    if message:
        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        text = message.get("text", "")
        # /myid works for anyone (even non-admins)
        if text and text.startswith("/myid"):
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"Your Telegram user ID: <code>{user_id}</code>",
                "parse_mode": "HTML"
            })
            return {"ok": True}
        # /start works for anyone — check for invite token
        if text and text.startswith("/start"):
            parts = text.split(maxsplit=1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if token and token.startswith("invite_"):
                code = token.replace("invite_", "")
                owner_id = _invite_tokens.pop(code, None)
                if owner_id and add_admin(user_id, owner_id, message["from"].get("first_name", "")):
                    _admin_ids.add(user_id)
                    _admin_names[user_id] = message["from"].get("first_name", "")
                    _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "🎉 You've been added as an admin! Use the menu below to control the bot.",
                        "reply_markup": build_main_menu(user_id),
                        "parse_mode": "HTML"
                    })
                    return {"ok": True}
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": (
                    "<b>Welcome to Opportunity Scraper Bot!</b>\n\n"
                    "Use the menu below to control the bot, get analytics, and view opportunities.\n\n"
                    "<i>Created by 👉 @twolamaa </i>"
                ),
                "reply_markup": build_main_menu(user_id),
                "parse_mode": "HTML"
            })
            return {"ok": True}
        if text and text.startswith("/help"):
            msg = (
                "<b>Available commands:</b>\n\n"
                "/start - Show the main menu\n"
                "/myid - Show your Telegram user ID\n"
                "/help - Show this message\n"
                "/request_admin - Request admin access from the owner\n\n"
                "<i>Owner-only:</i>\n"
                "/add_admin &lt;id&gt; - Add admin\n"
                "/remove_admin &lt;id&gt; - Remove an admin\n"
                "/list_admins - List all admins"
            )
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
            })
            return {"ok": True}
        if text and text.startswith("/request_admin"):
            name = message["from"].get("first_name", "")
            _pending_admins[user_id] = name
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": (
                    "Your admin request has been sent to the owner for approval.\n\n"
                    "Alternatively, ask the owner to send you an invite link."
                )
            })
            if BOT_OWNER_ID:
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": BOT_OWNER_ID,
                    "text": (
                        f"👤 <b>Admin request</b>\n"
                        f"User: {name} (<code>{user_id}</code>)"
                    ),
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "✅ Approve", "callback_data": f"approve_pending_{user_id}"},
                                {"text": "❌ Reject", "callback_data": f"reject_pending_{user_id}"}
                            ]
                        ]
                    }
                })
            return {"ok": True}
        if not _is_authorized(user_id):
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": (
                    "Sorry, you are not authorized to control this bot.\n\n"
                    "If you want admin access, use /request_admin or ask the owner for an invite link."
                )
            })
            return {"ok": True}
    elif callback_query:
        callback_id = callback_query.get("id")
        user_id = callback_query["from"]["id"]
        chat_id = callback_query["message"]["chat"]["id"]
        if not _is_authorized(user_id):
            if callback_id:
                _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "Not authorized.", "show_alert": True})
            return {"ok": True}
        if callback_id:
            _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})
        chat_id = callback_query["message"]["chat"]["id"]
        text = callback_query["data"]

    if not chat_id:
        return {"ok": True}

    # Handle shared contacts — if an admin shares a contact, add that person as admin
    if message and message.get("contact"):
        contact = message["contact"]
        target_id = contact.get("user_id")
        if target_id:
            name = contact.get("first_name", "")
            if add_admin(target_id, user_id, name):
                _admin_ids.add(target_id)
                _admin_names[target_id] = name
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"User <code>{target_id}</code> ({name}) added as admin.",
                    "parse_mode": "HTML"
                })
            else:
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"User <code>{target_id}</code> is already an admin.",
                    "parse_mode": "HTML"
                })
        else:
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "Could not read user ID from that contact. Ask them to message me first, then try again.",
                "parse_mode": "HTML"
            })
        return {"ok": True}

    if message and text.startswith("/add_admin") and user_id == BOT_OWNER_ID:
        parts = text.split()
        if len(parts) != 2:
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": (
                    "<b>Add an Admin</b>\n\n"
                    "Two ways:\n\n"
                    "1️⃣ <b>Forward a message</b>\n"
                    "  Forward any message from the person here.\n\n"
                    "2️⃣ <b>Manual</b> — <code>/add_admin &lt;user_id&gt;</code>\n\n"
                    "3️⃣ <b>Share contact</b> — tap 📎 &gt; Contact"
                ),
                "parse_mode": "HTML"
            })
        else:
            try:
                target_id = int(parts[1])
                if add_admin(target_id, user_id):
                    _admin_ids.add(target_id)
                    _admin_names[target_id] = ""
                    msg = f"User <code>{target_id}</code> added as admin."
                else:
                    msg = f"User <code>{target_id}</code> is already an admin."
            except ValueError:
                msg = "Invalid user ID."
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
            })
    elif message and text.startswith("/remove_admin") and user_id == BOT_OWNER_ID:
        parts = text.split()
        if len(parts) != 2:
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": "Usage: /remove_admin &lt;telegram_user_id&gt;", "parse_mode": "HTML"
            })
        else:
            try:
                target_id = int(parts[1])
                if target_id == BOT_OWNER_ID:
                    msg = "Cannot remove the owner."
                elif remove_admin(target_id):
                    _admin_ids.discard(target_id)
                    _admin_names.pop(target_id, None)
                    msg = f"User <code>{target_id}</code> removed from admins."
                else:
                    msg = f"User <code>{target_id}</code> is not an admin."
            except ValueError:
                msg = "Invalid user ID."
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
            })
    elif message and text.startswith("/list_admins") and user_id == BOT_OWNER_ID:
        _refresh_admin_cache()
        if not _admin_ids:
            msg = "<b>No admins found.</b>"
        else:
            lines = ["<b>Bot Admins:</b>"]
            for aid in sorted(_admin_ids):
                name = _admin_names.get(aid, "")
                if name:
                    lines.append(f"  - {name} <code>{aid}</code>")
                else:
                    lines.append(f"  - <code>{aid}</code>")
            msg = "\n".join(lines)
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif callback_query:
        if text == "noop":
            callback_id = callback_query.get("id")
            if callback_id:
                _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})
        elif text == "main_menu":
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": (
                    "<b>Opportunity Scraper Bot</b>\n\n"
                    "Use the menu below to control the bot, get analytics, and view opportunities.\n\n"
                    "<i>Created by 👉 @twolamaa </i>"
                ),
                "parse_mode": "HTML",
                "reply_markup": build_main_menu(user_id)
            })
        elif text == "admin_menu" and user_id == BOT_OWNER_ID:
            _refresh_admin_cache()
            admins = sorted(_admin_ids)
            lines = ["<b>👥 Admin Management</b>\n"]
            if admins:
                lines.append("<b>Current admins:</b>")
                for aid in admins:
                    name = _admin_names.get(aid, "")
                    if aid == BOT_OWNER_ID:
                        lines.append(f"  - {name}  (<code>{aid}</code>) (you)")
                    elif name:
                        lines.append(f"  - {name}  (<code>{aid}</code>)")
                    else:
                        lines.append(f"  - <code>{aid}</code>")
            else:
                lines.append("No admins yet.")
            if _pending_admins:
                lines.append(f"\n<b>⏳ Pending requests:</b>")
                for uid, name in _pending_admins.items():
                    lines.append(f"  - {name} (<code>{uid}</code>)")
            lines.append("\n<i>Share an invite link to let someone add themselves.</i>")
            msg = "\n".join(lines)
            remove_buttons = []
            for aid in admins:
                if aid != BOT_OWNER_ID:
                    label = f"❌ Remove {_admin_names.get(aid, aid)}"
                    remove_buttons.append([
                        {"text": label, "callback_data": f"remove_admin_click_{aid}"}
                    ])
            pending_buttons = []
            for uid in _pending_admins:
                pending_buttons.append([
                    {"text": f"✅ Approve {uid}", "callback_data": f"approve_pending_{uid}"},
                    {"text": f"❌ Reject {uid}", "callback_data": f"reject_pending_{uid}"}
                ])
            keyboard = pending_buttons + remove_buttons + [
                [{"text": "🔗 Generate Invite Link", "callback_data": "generate_invite"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
            })
        elif text == "generate_invite" and user_id == BOT_OWNER_ID:
            global BOT_USERNAME
            if BOT_USERNAME is None:
                try:
                    me = _http.post(f"{TELEGRAM_API_URL}/getMe").json()
                    BOT_USERNAME = me.get("result", {}).get("username", "")
                except Exception:
                    BOT_USERNAME = ""
            token = secrets.token_hex(8)
            _invite_tokens[token] = user_id
            link = f"https://t.me/{BOT_USERNAME}?start=invite_{token}" if BOT_USERNAME else f"Invite code: <code>invite_{token}</code>"
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": (
                    "<b>🔗 Invite Link Generated</b>\n\n"
                    f"Share this with the person you want to add:\n\n"
                    f"<code>{link}</code>\n\n"
                    "Once they click it and start the bot, they'll be auto-added as an admin.\n\n"
                    "<i>One-time use only.</i>"
                ),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "👥 Admin Menu", "callback_data": "admin_menu"}]
                    ]
                }
            })
        elif text.startswith("remove_admin_click_") and user_id == BOT_OWNER_ID:
            target_id = int(text.replace("remove_admin_click_", ""))
            if target_id == BOT_OWNER_ID:
                msg = "Cannot remove the owner."
            elif remove_admin(target_id):
                _admin_ids.discard(target_id)
                _admin_names.pop(target_id, None)
                msg = f"Admin <code>{target_id}</code> removed."
            else:
                msg = f"User <code>{target_id}</code> is not an admin."
            _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={
                "callback_query_id": callback_query.get("id"),
                "text": f"Admin {target_id} removed." if "removed" in msg else "Failed.",
                "show_alert": False
            })
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "👥 Admin Menu", "callback_data": "admin_menu"}],
                        [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
                    ]
                }
            })
        elif text.startswith("approve_pending_") and user_id == BOT_OWNER_ID:
            target_id = int(text.replace("approve_pending_", ""))
            name = _pending_admins.pop(target_id, "Unknown")
            if add_admin(target_id, user_id, name):
                _admin_ids.add(target_id)
                _admin_names[target_id] = name
                txt = f"User <code>{target_id}</code> ({name}) approved as admin."
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": target_id,
                    "text": "🎉 You've been approved as an admin! Use /start to control the bot."
                })
            else:
                txt = f"User <code>{target_id}</code> is already an admin."
            _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={
                "callback_query_id": callback_query.get("id"),
                "text": txt,
                "show_alert": False
            })
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[{"text": "👥 Admin Menu", "callback_data": "admin_menu"}]]
                }
            })
        elif text.startswith("reject_pending_") and user_id == BOT_OWNER_ID:
            target_id = int(text.replace("reject_pending_", ""))
            name = _pending_admins.pop(target_id, "Unknown")
            txt = f"User <code>{target_id}</code> ({name}) rejected."
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": target_id,
                "text": "Your admin request was rejected by the owner."
            })
            _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={
                "callback_query_id": callback_query.get("id"),
                "text": txt,
                "show_alert": False
            })
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[{"text": "👥 Admin Menu", "callback_data": "admin_menu"}]]
                }
            })
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
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_date_nav_keyboard(date_str, "posted")
            })
        elif text.startswith("unposted_date_"):
            date_str = text.replace("unposted_date_", "")
            from app.database import get_all_opportunities
            all_ops = get_all_opportunities()
            posted = [op for op in all_ops if op.get("posted_to_telegram")]
            unposted = [op for op in all_ops if not op.get("posted_to_telegram")]
            from collections import defaultdict
            posted_grouped = defaultdict(list)
            unposted_grouped = defaultdict(list)
            for op in posted:
                posted_grouped[str(op.get("created_at", "N/A"))[:10]].append(op)
            for op in unposted:
                unposted_grouped[str(op.get("created_at", "N/A"))[:10]].append(op)

            today_unposted = unposted_grouped.get(date_str, [])
            today_posted = posted_grouped.get(date_str, [])

            if today_unposted:
                msg = f"<b>🟡 Unposted for {date_str}:</b>\n\n" + "\n\n".join([
                    f"<b>{op['title']}</b>\n<a href='{op['link']}'>Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in today_unposted[:10]
                ])
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "📤 Post All", "callback_data": f"post_date_{date_str}"}
                        ],
                        [
                            {"text": "🔙 Main Menu", "callback_data": "main_menu"}
                        ]
                    ]
                }
            elif today_posted:
                msg = f"<b>All opportunities for {date_str} are already posted.</b>"
                keyboard = {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "main_menu"}]]}
            else:
                msg = f"<b>No data for {date_str}.</b>\n\nWould you like to scrape it?"
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "🔄 Scrape", "callback_data": f"scrape_date_{date_str}"}
                        ],
                        [
                            {"text": "🔙 Main Menu", "callback_data": "main_menu"}
                        ]
                    ]
                }
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard
            })
        elif text.startswith("scrape_date_"):
            date_str = text.replace("scrape_date_", "")
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            resp = _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"⏳ Scraping {date_str}... Please wait.",
                "parse_mode": "HTML"
            })
            try:
                msg_id = resp.json().get("result", {}).get("message_id")
            except Exception:
                msg_id = None
            def _scrape_date_only():
                new_ops = []
                try:
                    new_ops = fetch_opportunities_by_date(date_str.replace("-", "/"))
                    if new_ops:
                        msg = f"<b>✅ Scraped {len(new_ops)} opportunities for {date_str}:</b>\n\n" + "\n\n".join([
                            f"<b>{op['title']}</b>\n<a href='{op['link']}'>Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in new_ops[:10]
                        ])
                        if len(new_ops) > 10:
                            msg += f"\n\n<i>...and {len(new_ops) - 10} more.</i>"
                        keyboard = {
                            "inline_keyboard": [
                                [{"text": f"📤 Post All ({len(new_ops)})", "callback_data": f"post_date_{date_str}"}],
                                [{"text": "🟡 View Unposted", "callback_data": f"unposted_date_{date_str}"}],
                                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
                            ]
                        }
                        _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": msg,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                            "reply_markup": keyboard
                        })
                    else:
                        txt = f"No new opportunities found for {date_str}."
                        _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": txt,
                            "parse_mode": "HTML"
                        })
                except Exception as e:
                    _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"❌ Error: {e}",
                        "parse_mode": "HTML"
                    })
            if run_in_background:
                run_in_background(_scrape_date_only)
            else:
                Thread(target=_scrape_date_only, daemon=True).start()
        elif text.startswith("post_date_"):
            date_str = text.replace("post_date_", "")
            from app.database import get_all_opportunities
            from app.telegram_bot import post_to_telegram
            date_ops = [op for op in get_all_opportunities() if not op.get("posted_to_telegram") and str(op.get("created_at", "N/A"))[:10] == date_str]
            if not date_ops:
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id, "text": f"No unposted opportunities for {date_str}.", "parse_mode": "HTML"
                })
            else:
                sent = 0
                for op in date_ops:
                    if post_to_telegram(op):
                        sent += 1
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"📤 Posted {sent}/{len(date_ops)} opportunities for {date_str}.",
                    "parse_mode": "HTML"
                })
        elif text == "post_all_unposted":
            from app.database import get_unposted_opportunities
            from app.telegram_bot import post_to_telegram
            all_unposted = get_unposted_opportunities()
            if not all_unposted:
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id, "text": "No unposted opportunities.", "parse_mode": "HTML"
                })
            else:
                sent = 0
                for op in all_unposted:
                    if post_to_telegram(op):
                        sent += 1
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"📤 Posted {sent}/{len(all_unposted)} unposted opportunities.",
                    "parse_mode": "HTML"
                })
        elif text == "stats":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            stats = get_stats()
            msg = (
                f"<b>📊 Analytics</b>\n"
                f"Total: <b>{stats['total']}</b>\n"
                f"Unposted: <b>{stats['unposted']}</b>\n"
                f"Posted: <b>{stats['posted']}</b>\n"
                f"Last Posted: <b>{stats['last_posted']}</b>\n"
            )
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": build_main_menu(user_id)
            })
        elif text == "list_unposted":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            from app.database import get_unposted_opportunities
            unposted = get_unposted_opportunities()
            if not unposted:
                msg = "<b>No unposted opportunities.</b>"
                keyboard = build_main_menu(user_id)
            else:
                msg = "<b>🟡 Unposted Opportunities (latest 10):</b>\n\n" + "\n\n".join([
                    f"<b>{op['title']}</b>\n<a href='{op['link']}'>Apply / Details</a>\nDeadline: {op.get('deadline', 'N/A')}" for op in unposted[:10]
                ])
                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"📤 Post All ({len(unposted)})", "callback_data": "post_all_unposted"}],
                        [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
                    ]
                }
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard
            })
        elif text == "list_posted":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            from app.database import get_all_opportunities
            from collections import defaultdict
            posted = [op for op in get_all_opportunities() if op.get("posted_to_telegram")]
            if not posted:
                msg = "<b>No posted opportunities.</b>"
            else:
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
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_main_menu(user_id)
            })
        elif text == "scrape_today":
            today = datetime.utcnow().strftime("%Y-%m-%d")
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            resp = _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ Scraping today's opportunities... Please wait.",
                "parse_mode": "HTML"
            })
            try:
                message_id = resp.json().get("result", {}).get("message_id")
            except Exception:
                message_id = None
            if run_in_background:
                run_in_background(_scrape_and_post, today, chat_id, message_id)
            else:
                Thread(target=_scrape_and_post, args=(today, chat_id, message_id), daemon=True).start()
        elif text == "goto_date_menu":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
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
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": "Choose which opportunities to view by date:",
                "reply_markup": keyboard,
                "parse_mode": "HTML"
            })
        elif text == "about":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            msg = (
                "<b>About Opportunity Scraper Bot</b>\n\n"
                "This bot scrapes, stores, and shares the latest opportunities (scholarships, grants, fellowships, etc.) from the web.\n"
                "You can control scraping, view analytics, and browse opportunities right here!\n\n"
                "<i>Made with ❤️ by @twolamaa</i>"
            )
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": build_main_menu(user_id)
            })
        else:
            logging.warning(f"Unhandled callback data: {text}")
            callback_id = callback_query.get("id")
            if callback_id:
                _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "Not implemented or invalid action.", "show_alert": False})
    return {"ok": True}

@app.post("/webhook", tags=["Telegram"], summary="Receive Telegram updates", response_model=WebhookOut)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, process_telegram_update, data, background_tasks.add_task)
    return {"ok": True}

# CORS config - allow all origins for now, restrict in production if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def set_webhook():
    public_url = os.getenv("PUBLIC_URL")
    if public_url:
        webhook_url = f"{public_url.rstrip('/')}/webhook"
        resp = _http.post(f"{TELEGRAM_API_URL}/setWebhook", json={"url": webhook_url})
        if resp.ok:
            print(f"[OK] Webhook set to {webhook_url}")
        else:
            print(f"[ERR] Failed to set webhook: {resp.text}")

def start_polling():
    offset = 0
    backoff = 1
    max_backoff = 30
    print("[Polling] Started (local mode - no webhook required)")
    while True:
        try:
            resp = _http.get(
                f"{TELEGRAM_API_URL}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            if resp.ok:
                backoff = 1
                for update in resp.json().get("result", []):
                    process_telegram_update(update)
                    offset = update["update_id"] + 1
        except requests.exceptions.Timeout:
            backoff = 1
            pass
        except Exception as e:
            logging.warning(f"Polling error: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

@app.on_event("startup")
def on_startup():
    """Initialize database tables and start the background scheduler."""
    init_db()
    # Register webhook if PUBLIC_URL is set (production)
    set_webhook()
    # Start polling fallback (used when there's no public URL / local dev)
    if os.getenv("USE_POLLING", "true").lower() == "true":
        Thread(target=start_polling, daemon=True).start()
    # Start scheduler in a background thread
    if os.getenv("RUN_SCHEDULER", "true").lower() == "true":
        Thread(target=start_scheduler, daemon=True).start()
        print("[OK] Scheduler started")

@app.get("/", tags=["Health"], summary="Root welcome message", response_model=RootOut)
async def root():
    """Returns a simple welcome message."""
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping", tags=["Health"], summary="Health check", response_model=PingOut)
async def ping():
    """Returns a simple health-check status."""
    return {"status": "ok"}

@app.head("/ping", tags=["Health"], summary="Health check (HEAD)", include_in_schema=False)
async def ping_head():
    return

@app.get("/opportunities", tags=["Opportunities"], summary="List all opportunities", response_model=list[OpportunityOut])
async def get_opportunities():
    """Returns every opportunity in the database, newest first."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_all_opportunities)

@app.get("/opportunities/unposted", tags=["Opportunities"], summary="List unposted opportunities", response_model=list[OpportunityOut])
async def get_unposted():
    """Returns opportunities that have not yet been sent to Telegram."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_unposted_opportunities)

@app.get("/opportunities/posted", tags=["Opportunities"], summary="List posted opportunities", response_model=list[OpportunityOut])
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
@app.get("/run-once", tags=["Management"], summary="Trigger daily tasks manually", response_model=RunOnceOut)
async def run_once():
    """Runs the full daily routine: scrape, post to Telegram, and clean old entries."""
    def run():
        run_daily_tasks()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run)
    return {"status": "Scheduler manually triggered."}
