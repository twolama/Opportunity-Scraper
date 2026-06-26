import os
import re
import secrets
import csv
import io
import time
import logging
from threading import Thread
from typing import Optional
from datetime import datetime, timedelta

from app.scheduler import reload_schedules
from app.scraper import fetch_opportunities_by_date
from app.database import (
    get_admins,
    get_stats_from_db,
    get_unposted_opportunities,
    get_unposted_by_date,
    get_posted_by_date,
    get_opportunity_by_id,
    update_opportunity,
    delete_opportunity,
    search_opportunities,
    opportunity_to_dict,
    is_admin,
    add_admin,
    remove_admin,
    get_schedule_times,
    add_schedule_time,
    remove_schedule_time,
    parse_time_12h,
    format_time_12h,
    add_pending_admin,
    remove_pending_admin,
    get_pending_admins,
    add_invite_token,
    consume_invite_token,
    set_pending_schedule_input,
    pop_pending_schedule_input,
    add_channel,
    remove_channel,
    get_active_channels,
)
from app.telegram_bot import post_to_telegram
from app.config import TELEGRAM_API_URL, BOT_OWNER_ID, TELEGRAM_CHANNEL_ID
from app.keyboards import (
    build_main_menu, build_date_nav_keyboard, build_year_picker,
    build_month_picker, build_day_picker, build_search_keyboard,
    build_stats_keyboard, build_browse_keyboard,
)
import sentry_sdk
from app.http_client import http as _http, sanitize as _sanitize
from app.rate_limiter import telegram_limiter

logger = logging.getLogger(__name__)

# Lazy-loaded bot info (from getMe)
BOT_USERNAME: str | None = None
BOT_FIRST_NAME: str = "Opportunity Search Bot"

def set_bot_info(username: str | None, first_name: str) -> None:
    global BOT_USERNAME, BOT_FIRST_NAME
    BOT_USERNAME = username
    BOT_FIRST_NAME = first_name

# --- Admin cache (short TTL; survives worker restart) ---
_admin_ids: set[int] = set()
_admin_names: dict[int, str] = {}
_last_admin_refresh: float = 0
_ADMIN_CACHE_TTL = 10


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
    return get_stats_from_db()

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
        logging.warning(_sanitize(f"editMessageText failed: {data}"))
        payload2 = payload.copy()
        payload2.pop("message_id", None)
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload2)

def _scrape_only(today, chat_id, message_id):
    try:
        target = today.replace("-", "/")
        new_ops = fetch_opportunities_by_date(target)
        msg = f"✅ Search complete!\nNew opportunities found: <b>{len(new_ops)}</b>"
    except Exception as e:
        msg = f"❌ Error during search: {e}"
    try:
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
    except Exception:
        logger.warning("Failed to send scrape result to Telegram (chat_id=%s)", chat_id, exc_info=True)


def process_telegram_update(data, run_in_background=None):
    # Auto-detect when bot is added to a group or channel
    my_chat_member = data.get("my_chat_member")
    if my_chat_member:
        chat = my_chat_member.get("chat", {})
        chat_id = chat.get("id")
        new_status = my_chat_member.get("new_chat_member", {}).get("status", "")
        if new_status in ("member", "administrator"):
            title = chat.get("title", f"Chat {chat_id}")
            add_channel(chat_id, title=title)
            logger.info("Auto-added channel %s (%s)", title, chat_id)
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Bot added! Use the bot's admin panel to manage this channel.",
                "parse_mode": "HTML"
            })
            return {"ok": True}

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
                owner_id = consume_invite_token(code)
                if owner_id is None:
                    reply = "<b>Invalid or expired invite link.</b>"
                elif add_admin(user_id, owner_id, message["from"].get("first_name", "")):
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
                    f"<b>Welcome to {BOT_FIRST_NAME}!</b>\n\n"
                    "Use the menu below to control the bot, get analytics, and view opportunities.\n\n"
                    "<i>Created by 👉 @twolamaa </i>"
                ),
                "reply_markup": build_main_menu(user_id),
                "parse_mode": "HTML"
            })
            return {"ok": True}
        if text and text.startswith("/search"):
            keyword = text[len("/search "):].strip() if len(text) > len("/search ") else ""
            if not keyword:
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "Usage: /search &lt;keyword&gt;\n\nExample: /search scholarship",
                    "parse_mode": "HTML"
                })
            else:
                result = search_opportunities(keyword, 0, 10)
                if result["total"] == 0:
                    _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"No results found for \"<b>{keyword}</b>\".",
                        "parse_mode": "HTML"
                    })
                else:
                    lines = [f"<b>Results for \"{keyword}\" ({result['total']} found):</b>\n"]
                    for op in result["results"]:
                        status = "🟢" if op["posted_to_telegram"] else "🟡"
                        date_str = str(op.get("created_at", ""))[:10] if op.get("created_at") else "?"
                        lines.append(f"{status} <b>{op['title']}</b>\n📅 {date_str} | <a href='{op['link']}'>Link</a>")
                    msg = "\n\n".join(lines)
                    kb = build_search_keyboard(0, result["total"], keyword) if result["total"] > 10 else {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "main_menu"}]]}
                    _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": msg,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": kb
                    })
            return {"ok": True}
        if text and text.startswith("/help"):
            msg = (
                "<b>Available commands:</b>\n\n"
                "/start - Show the main menu\n"
                "/myid - Show your Telegram user ID\n"
                "/help - Show this message\n"
                "/search &lt;keyword&gt; - Search opportunities by title, description, or tags\n"
                "/request_admin - Request admin access from the owner\n\n"
                "<i>Owner-only:</i>\n"
                "/add_admin &lt;id&gt; - Add admin\n"
                "/remove_admin &lt;id&gt; - Remove an admin\n"
                "/list_admins - List all admins\n"
                "/add_scrape HH:MM - Add auto-scrape time (UTC)\n"
                "/add_post HH:MM - Add auto-post time (UTC)\n"
                "/remove_scrape HH:MM - Remove a scrape time\n"
                "/remove_post HH:MM - Remove a post time\n"
                "/list_schedules - List all schedule times"
            )
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
            })
            return {"ok": True}
        if text and text.startswith("/request_admin"):
            name = message["from"].get("first_name", "")
            add_pending_admin(user_id, name)
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
            lines = ["<b>📋 Bot Admins</b>\n"]
            for i, aid in enumerate(sorted(_admin_ids), 1):
                name = _admin_names.get(aid, "")
                if name:
                    lines.append(f"{i}. {name} — <code>{aid}</code>")
                else:
                    lines.append(f"{i}. <code>{aid}</code>")
            msg = "\n".join(lines)
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and text.startswith("/add_scrape") and user_id == BOT_OWNER_ID:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            msg = "Usage: <code>/add_scrape HH:MM</code> (24h UTC) or <code>/add_scrape 6:30 AM</code>"
        else:
            time_str = parse_time_12h(parts[1])
            if not time_str:
                msg = "❌ Invalid time. Use 24h like <code>06:30</code> or 12h like <code>6:30 AM</code>."
            elif add_schedule_time(time_str, "scrape"):
                msg = f"✅ Search time added: <code>{time_str}</code> ({format_time_12h(time_str)})"
                reload_schedules()
            else:
                msg = f"❌ Search time <code>{time_str}</code> already exists."
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and text.startswith("/add_post") and user_id == BOT_OWNER_ID:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            msg = "Usage: <code>/add_post HH:MM</code> (24h UTC) or <code>/add_post 6:30 AM</code>"
        else:
            time_str = parse_time_12h(parts[1])
            if not time_str:
                msg = "❌ Invalid time."
            elif add_schedule_time(time_str, "post"):
                msg = f"✅ Post time added: <code>{time_str}</code> ({format_time_12h(time_str)})"
                reload_schedules()
            else:
                msg = f"❌ Post time <code>{time_str}</code> already exists."
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and text.startswith("/remove_scrape") and user_id == BOT_OWNER_ID:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            msg = "Usage: <code>/remove_scrape HH:MM</code> (24h UTC) or <code>/remove_scrape 6:30 AM</code>"
        else:
            time_str = parse_time_12h(parts[1])
            if not time_str:
                msg = "❌ Invalid time."
            elif remove_schedule_time(time_str, "scrape"):
                msg = f"🗑️ Search time removed: <code>{time_str}</code> ({format_time_12h(time_str)})"
                reload_schedules()
            else:
                msg = f"❌ Search time not found."
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and text.startswith("/remove_post") and user_id == BOT_OWNER_ID:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            msg = "Usage: <code>/remove_post HH:MM</code> (24h UTC) or <code>/remove_post 6:30 AM</code>"
        else:
            time_str = parse_time_12h(parts[1])
            if not time_str:
                msg = "❌ Invalid time."
            elif remove_schedule_time(time_str, "post"):
                msg = f"🗑️ Post time removed: <code>{time_str}</code> ({format_time_12h(time_str)})"
                reload_schedules()
            else:
                msg = f"❌ Post time not found."
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and text.startswith("/list_schedules") and user_id == BOT_OWNER_ID:
        scrape_times = get_schedule_times("scrape")
        post_times = get_schedule_times("post")
        lines = []
        if scrape_times:
            lines.append("<b>⏰ Search Times (UTC):</b>")
            for i, t in enumerate(scrape_times, 1):
                lines.append(f"{i}. <code>{t}</code> ({format_time_12h(t)})")
        else:
            lines.append("<b>⏰ Search Times:</b> None")
        if post_times:
            lines.append("\n<b>📤 Post Times (UTC):</b>")
            for i, t in enumerate(post_times, 1):
                lines.append(f"{i}. <code>{t}</code> ({format_time_12h(t)})")
        else:
            lines.append("\n<b>📤 Post Times:</b> None")
        msg = "\n".join(lines)
        _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
        })
    elif message and user_id == BOT_OWNER_ID and text and not text.startswith("/"):
        pending_type = pop_pending_schedule_input(user_id)
        if pending_type:
            time_str = parse_time_12h(text)
            if not time_str:
                msg = "❌ Invalid time. Try <code>6:30 AM</code> or <code>06:30</code> (UTC)."
            elif add_schedule_time(time_str, pending_type):
                type_label = "Scrape" if pending_type == "scrape" else "Post"
                msg = f"✅ {type_label} time added: <code>{time_str}</code> ({format_time_12h(time_str)})"
                reload_schedules()
            else:
                type_label = "Scrape" if pending_type == "scrape" else "Post"
                msg = f"❌ {type_label} time <code>{time_str}</code> already exists."
            _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
            })
            return {"ok": True}
        # Check if user sent a chat ID to add a channel
        try:
            potential_chat_id = int(text.strip().lstrip("-"))
            text_stripped = text.strip()
            # Accept numeric chat IDs (positive for users, negative for groups/channels)
            if text_stripped.lstrip("-").isdigit():
                chat_id_val = int(text_stripped)
                add_channel(chat_id_val, title=f"Channel {chat_id_val}", added_by=user_id)
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"✅ Channel <code>{chat_id_val}</code> added!",
                    "parse_mode": "HTML"
                })
                return {"ok": True}
        except (ValueError, TypeError):
            pass
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
                    f"<b>{BOT_FIRST_NAME}</b>\n\n"
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
                for i, aid in enumerate(admins, 1):
                    name = _admin_names.get(aid, "")
                    if aid == BOT_OWNER_ID:
                        lines.append(f"{i}. {name} — <code>{aid}</code> (you)")
                    elif name:
                        lines.append(f"{i}. {name} — <code>{aid}</code>")
                    else:
                        lines.append(f"{i}. <code>{aid}</code>")
            else:
                lines.append("No admins yet.")
            pending_admins = get_pending_admins()
            if pending_admins:
                lines.append(f"\n<b>⏳ Pending requests:</b>")
                for i, pa in enumerate(pending_admins, 1):
                    lines.append(f"{i}. {pa['name']} — <code>{pa['user_id']}</code>")
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
            for pa in pending_admins:
                uid = pa["user_id"]
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
            if not BOT_USERNAME:
                try:
                    me = _http.post(f"{TELEGRAM_API_URL}/getMe").json()
                    BOT_USERNAME = me.get("result", {}).get("username", "") or ""
                except Exception:
                    BOT_USERNAME = ""
            token = secrets.token_hex(8)
            add_invite_token(token, user_id)
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
        elif text == "list_schedules" and user_id == BOT_OWNER_ID:
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": "<b>⏰ Schedule Management</b>\n\nChoose a schedule type to manage:",
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "⏰ Search Schedule", "callback_data": "view_scrape_schedule"}],
                        [{"text": "📤 Post Schedule", "callback_data": "view_post_schedule"}],
                        [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
                    ]
                }
            })
        elif text == "view_scrape_schedule" and user_id == BOT_OWNER_ID:
            times = get_schedule_times("scrape")
            if times:
                lines = ["<b>⏰ Search Times (UTC):</b>"]
                for i, t in enumerate(times, 1):
                    lines.append(f"{i}. <code>{t}</code> ({format_time_12h(t)})")
            else:
                lines = ["<b>⏰ Search Times:</b> None configured."]
            txt = "\n".join(lines)
            rm = [[{"text": f"❌ {format_time_12h(t)}", "callback_data": f"remove_scrape_{t}"}] for t in times]
            keyboard = rm + [
                [{"text": "➕ Add Search Time", "callback_data": "add_scrape_prompt"}],
                [{"text": "🔙 Schedules", "callback_data": "list_schedules"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
            })
        elif text == "view_post_schedule" and user_id == BOT_OWNER_ID:
            times = get_schedule_times("post")
            if times:
                lines = ["<b>📤 Post Times (UTC):</b>"]
                for i, t in enumerate(times, 1):
                    lines.append(f"{i}. <code>{t}</code> ({format_time_12h(t)})")
            else:
                lines = ["<b>📤 Post Times:</b> None configured."]
            txt = "\n".join(lines)
            rm = [[{"text": f"❌ {format_time_12h(t)}", "callback_data": f"remove_post_{t}"}] for t in times]
            keyboard = rm + [
                [{"text": "➕ Add Post Time", "callback_data": "add_post_prompt"}],
                [{"text": "🔙 Schedules", "callback_data": "list_schedules"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
            })
        elif text == "add_scrape_prompt" and user_id == BOT_OWNER_ID:
            set_pending_schedule_input(user_id, "scrape")
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": (
                    "<b>➕ Add Search Time</b>\n\n"
                    "Send me a time in 12-hour or 24-hour format, e.g.:\n"
                    "• <code>6:30 AM</code>\n"
                    "• <code>10:59 PM</code>\n"
                    "• <code>06:30</code>\n\n"
                    "All times are in <b>UTC</b>."
                ),
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🔙 Search Schedule", "callback_data": "view_scrape_schedule"}]
                    ]
                }
            })
        elif text == "add_post_prompt" and user_id == BOT_OWNER_ID:
            set_pending_schedule_input(user_id, "post")
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": (
                    "<b>➕ Add Post Time</b>\n\n"
                    "Send me a time in 12-hour or 24-hour format, e.g.:\n"
                    "• <code>8:00 AM</code>\n"
                    "• <code>2:00 PM</code>\n"
                    "• <code>14:00</code>\n\n"
                    "All times are in <b>UTC</b>."
                ),
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🔙 Post Schedule", "callback_data": "view_post_schedule"}]
                    ]
                }
            })
        elif text.startswith("remove_scrape_") and user_id == BOT_OWNER_ID:
            time_str = text[len("remove_scrape_"):]
            removed = remove_schedule_time(time_str, "scrape")
            if removed:
                reload_schedules()
            txt = f"🗑️ Removed search <code>{time_str}</code>." if removed else "❌ Not found."
            times = get_schedule_times("scrape")
            if times:
                txt += "\n\n<b>⏰ Remaining Search Times:</b>\n" + "\n".join(f"{i}. <code>{t}</code> ({format_time_12h(t)})" for i, t in enumerate(times, 1))
            rm = [[{"text": f"❌ {format_time_12h(t)}", "callback_data": f"remove_scrape_{t}"}] for t in times]
            keyboard = rm + [
                [{"text": "➕ Add Search Time", "callback_data": "add_scrape_prompt"}],
                [{"text": "🔙 Schedules", "callback_data": "list_schedules"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
            })
        elif text.startswith("remove_post_") and user_id == BOT_OWNER_ID:
            time_str = text[len("remove_post_"):]
            removed = remove_schedule_time(time_str, "post")
            if removed:
                reload_schedules()
            txt = f"🗑️ Removed post <code>{time_str}</code>." if removed else "❌ Not found."
            times = get_schedule_times("post")
            if times:
                txt += "\n\n<b>📤 Remaining Post Times:</b>\n" + "\n".join(f"{i}. <code>{t}</code> ({format_time_12h(t)})" for i, t in enumerate(times, 1))
            rm = [[{"text": f"❌ {format_time_12h(t)}", "callback_data": f"remove_post_{t}"}] for t in times]
            keyboard = rm + [
                [{"text": "➕ Add Post Time", "callback_data": "add_post_prompt"}],
                [{"text": "🔙 Schedules", "callback_data": "list_schedules"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
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
            name = remove_pending_admin(target_id) or "Unknown"
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
            name = remove_pending_admin(target_id) or "Unknown"
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
        elif text == "channels" and user_id == BOT_OWNER_ID:
            channels = get_active_channels()
            lines = ["<b>📢 Channels</b>\n"]
            if channels:
                for i, ch in enumerate(channels, 1):
                    lines.append(f"{i}. {ch['title']} — <code>{ch['chat_id']}</code>")
            else:
                lines.append("No channels configured.")
                if TELEGRAM_CHANNEL_ID:
                    lines.append(f"\nUsing <code>{TELEGRAM_CHANNEL_ID}</code> from TELEGRAM_CHANNEL_ID env var.")
            txt = "\n".join(lines)
            rm = [[{"text": f"❌ {ch['title']}", "callback_data": f"remove_channel_{ch['chat_id']}"}] for ch in channels]
            keyboard = rm + [
                [{"text": "➕ Add Channel", "callback_data": "add_channel_prompt"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
            })
        elif text == "add_channel_prompt" and user_id == BOT_OWNER_ID:
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": (
                    "<b>➕ Add a Channel</b>\n\n"
                    "Send me the chat ID of the channel or group.\n\n"
                    "Get the ID by:\n"
                    "1. Forward a message from the channel to <code>@getidsbot</code>\n"
                    "2. Or add me to the group and I'll auto-detect it\n\n"
                    "Group/channel IDs are negative numbers (e.g., <code>-1001234567890</code>)."
                ),
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🔙 Channels", "callback_data": "channels"}]
                    ]
                }
            })
        elif text.startswith("remove_channel_") and user_id == BOT_OWNER_ID:
            try:
                target_id = int(text.replace("remove_channel_", ""))
                removed = remove_channel(target_id)
                msg = f"🗑️ Channel <code>{target_id}</code> removed." if removed else "❌ Channel not found."
            except ValueError:
                msg = "❌ Invalid channel ID."
            channels = get_active_channels()
            lines = ["<b>📢 Channels</b>\n"]
            if channels:
                for i, ch in enumerate(channels, 1):
                    lines.append(f"{i}. {ch['title']} — <code>{ch['chat_id']}</code>")
            else:
                lines.append("No channels configured.")
            txt = msg + "\n\n" + "\n".join(lines)
            rm = [[{"text": f"❌ {ch['title']}", "callback_data": f"remove_channel_{ch['chat_id']}"}] for ch in channels]
            keyboard = rm + [
                [{"text": "➕ Add Channel", "callback_data": "add_channel_prompt"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}
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
            rest = text[len("posted_date_"):]
            parts = rest.rsplit("_", 1)
            date_str = parts[0]
            page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            per_page = 10
            ops = get_posted_by_date(date_str)
            total = len(ops)
            page_ops = ops[page * per_page:(page + 1) * per_page]
            if not page_ops:
                msg = f"<b>No posted opportunities for {date_str}.</b>"
            else:
                lines = [f"<b>🟢 Posted for {date_str} — Page {page + 1}/{max(1, (total + per_page - 1) // per_page)} ({total} total):</b>\n"]
                for op in page_ops:
                    lines.append(f"<b>{op['title']}</b>\n<a href='{op['link']}'>Details</a>\nDeadline: {op.get('deadline', 'N/A')}")
                msg = "\n\n".join(lines)
            nav = build_date_nav_keyboard(date_str, "posted")
            page_row = []
            if page > 0:
                page_row.append({"text": "⬅️ Prev Page", "callback_data": f"posted_date_{date_str}_{page - 1}"})
            if (page + 1) * per_page < total:
                page_row.append({"text": "Next Page ➡️", "callback_data": f"posted_date_{date_str}_{page + 1}"})
            inline_kb = nav["inline_keyboard"]
            if page_row:
                inline_kb.insert(0, page_row)
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": inline_kb}
            })
        elif text.startswith("unposted_date_"):
            rest = text[len("unposted_date_"):]
            parts = rest.rsplit("_", 1)
            date_str = parts[0]
            page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            per_page = 10
            today_unposted = get_unposted_by_date(date_str)
            today_posted = get_posted_by_date(date_str)
            total = len(today_unposted)
            page_ops = today_unposted[page * per_page:(page + 1) * per_page]

            if page_ops:
                lines = [f"<b>🟡 Unposted for {date_str} — Page {page + 1}/{max(1, (total + per_page - 1) // per_page)} ({total} total):</b>\n"]
                for op in page_ops:
                    lines.append(f"<b>{op['title']}</b>\n<a href='{op['link']}'>Details</a>\nDeadline: {op.get('deadline', 'N/A')}")
                msg = "\n\n".join(lines)
                nav = build_date_nav_keyboard(date_str, "unposted")
                page_row = []
                if page > 0:
                    page_row.append({"text": "⬅️ Prev Page", "callback_data": f"unposted_date_{date_str}_{page - 1}"})
                if (page + 1) * per_page < total:
                    page_row.append({"text": "Next Page ➡️", "callback_data": f"unposted_date_{date_str}_{page + 1}"})
                inline_kb = nav["inline_keyboard"]
                if page_row:
                    inline_kb.insert(0, page_row)
                inline_kb.insert(0, [{"text": "📤 Post All", "callback_data": f"post_date_{date_str}"}])
                keyboard = {"inline_keyboard": inline_kb}
            elif today_posted:
                msg = f"<b>All opportunities for {date_str} are already posted.</b>"
                keyboard = build_date_nav_keyboard(date_str, "unposted")
            else:
                msg = f"<b>No data for {date_str}.</b>\n\nWould you like to search for it?"
                nav = build_date_nav_keyboard(date_str, "unposted")
                nav["inline_keyboard"].insert(0, [{"text": "🔄 Search", "callback_data": f"scrape_date_{date_str}"}])
                keyboard = nav
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
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
                "text": f"⏳ Searching {date_str}... Please wait.",
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
                        msg = f"<b>✅ Searched {len(new_ops)} opportunities for {date_str}:</b>\n\n" + "\n\n".join([
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
                        safe_edit_message_text({
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": msg,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                            "reply_markup": keyboard
                        })
                    else:
                        txt = f"No new opportunities found for {date_str}."
                        safe_edit_message_text({
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": txt,
                            "parse_mode": "HTML"
                        })
                except Exception as e:
                    try:
                        safe_edit_message_text({
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": f"❌ Error: {_sanitize(e)}",
                            "parse_mode": "HTML"
                        })
                    except Exception:
                        logger.warning("Failed to send error message to Telegram (chat_id=%s)", chat_id, exc_info=True)
            Thread(target=_scrape_date_only, daemon=True).start()
        elif text.startswith("post_date_"):
            date_str = text.replace("post_date_", "")
            def _post_date_job():
                from app.telegram_bot import post_to_telegram
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import time
                date_ops = get_unposted_by_date(date_str)
                if not date_ops:
                    _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                        "chat_id": chat_id, "text": f"No unposted opportunities for {date_str}.", "parse_mode": "HTML"
                    })
                    return
                sent = 0
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {}
                    for op in date_ops:
                        wait = telegram_limiter.consume()
                        if wait > 0:
                            time.sleep(wait)
                        futures[pool.submit(post_to_telegram, op)] = op
                    for future in as_completed(futures):
                        if future.result():
                            sent += 1
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"📤 Posted {sent}/{len(date_ops)} opportunities for {date_str}.",
                    "parse_mode": "HTML"
                })
            Thread(target=_post_date_job, daemon=True).start()
        elif text == "post_all_unposted":
            def _post_all_job():
                from app.database import get_unposted_opportunities
                from app.telegram_bot import post_to_telegram
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import time
                all_unposted = get_unposted_opportunities()
                if not all_unposted:
                    _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                        "chat_id": chat_id, "text": "No unposted opportunities.", "parse_mode": "HTML"
                    })
                    return
                sent = 0
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {}
                    for op in all_unposted:
                        wait = telegram_limiter.consume()
                        if wait > 0:
                            time.sleep(wait)
                        futures[pool.submit(post_to_telegram, op)] = op
                    for future in as_completed(futures):
                        if future.result():
                            sent += 1
                _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"📤 Posted {sent}/{len(all_unposted)} unposted opportunities.",
                    "parse_mode": "HTML"
                })
            Thread(target=_post_all_job, daemon=True).start()
        elif text == "stats":
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            stats = get_stats()
            tags_section = ""
            if stats.get("top_tags"):
                tags_list = [f"  {t[0]}: {t[1]}" for t in stats["top_tags"][:5]]
                tags_section = "\n<b>Top Tags:</b>\n" + "\n".join(tags_list)
            msg = (
                f"<b>📊 Analytics</b>\n\n"
                f"Total: <b>{stats['total']}</b>\n"
                f"🟢 Posted: <b>{stats['posted']}</b>\n"
                f"🟡 Unposted: <b>{stats['unposted']}</b>\n\n"
                f"<b>Search Results:</b>\n"
                f"  Today: <b>{stats['today']}</b>\n"
                f"  This Week: <b>{stats['week']}</b>\n"
                f"  This Month: <b>{stats['month']}</b>\n\n"
                f"<b>Timeline:</b>\n"
                f"  Oldest: <b>{stats['oldest']}</b>\n"
                f"  Last Posted: <b>{stats['last_posted']}</b>"
                f"{tags_section}"
            )
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": build_stats_keyboard(stats["total"], stats["unposted"], stats["posted"])
            })
        elif text in ("list_unposted", "list_posted"):
            mode = "unposted" if text == "list_unposted" else "posted"
            page = 0
            per_page = 10
            posted_filter = {"unposted": False, "posted": True}.get(mode)
            result = search_opportunities("", page * per_page, per_page, posted_filter)
            ops = result["results"]
            if not ops:
                msg = f"<b>No {'unposted' if mode == 'unposted' else 'posted'} opportunities.</b>"
            else:
                lines = [f"<b>Page 1/{max(1, (result['total'] + per_page - 1) // per_page)} ({result['total']} total):</b>\n"]
                for op in ops:
                    s = "🟢" if op["posted_to_telegram"] else "🟡"
                    date_str = str(op.get("created_at", ""))[:10] if op.get("created_at") else "?"
                    lines.append(f"{s} <b>{op['title']}</b>\n📅 {date_str} | <a href='{op['link']}'>Link</a>")
                msg = "\n\n".join(lines)
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_browse_keyboard(page, result["total"], result["total"], mode)
            })
        elif text == "scrape_today":
            today = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            _http.post(f"{TELEGRAM_API_URL}/sendChatAction", json={
                "chat_id": chat_id,
                "action": "typing"
            })
            resp = _http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ Searching opportunities... Please wait.",
                "parse_mode": "HTML"
            })
            try:
                message_id = resp.json().get("result", {}).get("message_id")
            except Exception:
                message_id = None
            Thread(target=_scrape_only, args=(today, chat_id, message_id), daemon=True).start()
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
                "<b>About this Bot</b>\n\n"
                "This bot searchs, stores, and shares the latest opportunities (scholarships, grants, fellowships, etc.) from the web.\n"
                "You can control scheduling, view analytics, and browse opportunities right here!\n\n"
                "<i>Made with ❤️ by @twolamaa</i>"
            )
            _http.post(f"{TELEGRAM_API_URL}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": build_main_menu(user_id)
            })
        elif text.startswith("search_"):
            try:
                rest = text[len("search_"):]
                keyword, offset_str = rest.rsplit("_", 1)
                offset = int(offset_str)
            except (IndexError, ValueError):
                keyword = ""
                offset = 0
            result = search_opportunities(keyword, offset, 10)
            if not result["results"]:
                msg = f"No more results for \"<b>{keyword}</b>\"."
            else:
                lines = [f"<b>Results for \"{keyword}\" ({result['total']} found):</b>\n"]
                for op in result["results"]:
                    status = "🟢" if op["posted_to_telegram"] else "🟡"
                    date_str = str(op.get("created_at", ""))[:10] if op.get("created_at") else "?"
                    lines.append(f"{status} <b>{op['title']}</b>\n📅 {date_str} | <a href='{op['link']}'>Link</a>")
                msg = "\n\n".join(lines)
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_search_keyboard(offset, result["total"], keyword)
            })
        elif text.startswith("browse_"):
            try:
                parts = text.split("_", 2)
                mode = parts[1]  # 'all', 'unposted', or 'posted'
                page = int(parts[2])
            except (IndexError, ValueError):
                mode = "all"
                page = 0
            per_page = 10
            posted_filter = {"all": None, "unposted": False, "posted": True}.get(mode)
            result = search_opportunities("", page * per_page, per_page, posted_filter)
            ops = result["results"]
            if not ops:
                msg = "<b>No opportunities found.</b>"
            else:
                status_map = {None: "", False: "🟡 ", True: "🟢 "}
                prefix = status_map.get(posted_filter, "")
                lines = [f"<b>{prefix}Page {page + 1}/{max(1, (result['total'] + per_page - 1) // per_page)} ({result['total']} total):</b>\n"]
                for op in ops:
                    s = "🟢" if op["posted_to_telegram"] else "🟡"
                    date_str = str(op.get("created_at", ""))[:10] if op.get("created_at") else "?"
                    lines.append(f"{s} <b>{op['title']}</b>\n📅 {date_str} | <a href='{op['link']}'>Link</a>")
                msg = "\n\n".join(lines)
            safe_edit_message_text({
                "chat_id": chat_id,
                "message_id": callback_query["message"]["message_id"],
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_browse_keyboard(page, result["total"], result["total"], mode)
            })
        else:
            logging.warning(f"Unhandled callback data: {text}")
            callback_id = callback_query.get("id")
            if callback_id:
                _http.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": "Not implemented or invalid action.", "show_alert": False})

