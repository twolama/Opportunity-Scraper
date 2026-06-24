from datetime import datetime, timedelta
from calendar import monthrange
from app.config import BOT_OWNER_ID

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

def build_search_keyboard(offset, total, keyword):
    keyboard = []
    if offset > 0:
        keyboard.append([{"text": "⬅️ Previous", "callback_data": f"search_{keyword}_{offset - 10}"}])
    if offset + 10 < total:
        keyboard.append([{"text": "Next ➡️", "callback_data": f"search_{keyword}_{offset + 10}"}])
    keyboard.append([{"text": "🔙 Main Menu", "callback_data": "main_menu"}])
    return {"inline_keyboard": keyboard}

def build_stats_keyboard(total, unposted, posted):
    keyboard = []
    if unposted:
        keyboard.append([{"text": f"🟡 View Unposted ({unposted})", "callback_data": "browse_unposted_0"}])
    if posted:
        keyboard.append([{"text": f"🟢 View Posted ({posted})", "callback_data": "browse_posted_0"}])
    if total:
        keyboard.append([{"text": f"📄 View All ({total})", "callback_data": "browse_all_0"}])
    keyboard.append([{"text": "🔙 Main Menu", "callback_data": "main_menu"}])
    return {"inline_keyboard": keyboard}

def build_browse_keyboard(page, total, total_count, mode):
    per_page = 10
    max_page = (total_count - 1) // per_page if total_count else 0
    keyboard = []
    row = []
    if page > 0:
        row.append({"text": "⬅️ Prev", "callback_data": f"browse_{mode}_{page - 1}"})
    if page < max_page:
        row.append({"text": "Next ➡️", "callback_data": f"browse_{mode}_{page + 1}"})
    if row:
        keyboard.append(row)
    keyboard.append([{"text": "📊 Back to Stats", "callback_data": "stats"}])
    keyboard.append([{"text": "🔙 Main Menu", "callback_data": "main_menu"}])
    return {"inline_keyboard": keyboard}
