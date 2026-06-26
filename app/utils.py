import os
import html


def format_telegram_message(opportunity: dict) -> str:
    title = f"<b>{html.escape(opportunity['title'])}</b>" if opportunity.get("title") else ""
    description = html.escape(opportunity.get("description", ""))
    deadline = f"\n\n<b>Deadline:</b> {html.escape(opportunity['deadline'])}" if opportunity.get("deadline") else ""
    join_us_url = os.getenv("JOIN_US_URL", "https://t.me/opportunityspots")
    join_us = f'\n\n✅ <a href="{html.escape(join_us_url)}"><b>Join Us</b></a>'
    tags_text = os.getenv("TAGS_TEXT", "#Opportunities #Scholarships #Grants #Education #Career @opportunityspots")
    tags = f"\n\n{html.escape(tags_text)}"

    return f"{title}\n\n{description}{deadline}{join_us}{tags}"
