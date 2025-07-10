def format_telegram_message(opportunity: dict) -> str:
    title = f"<b>{opportunity['title']}</b>" if opportunity.get("title") else ""
    description = opportunity.get("description", "")
    deadline = f"\n\n<b>Deadline:</b> {opportunity['deadline']}" if opportunity.get("deadline") else ""
    # apply_link = f'\n\n📨 <a href="{opportunity["link"]}"><b>Apply Now</b></a>' if opportunity.get("link") else ""
    join_us = '\n\n✅ <a href="https://t.me/ScholarshipSpot"><b>Join Us</b></a>'
    tags = "\n\n#Opportunities #Scholarships #Grants #Education #Career @ScholarshipSpot"

    return f"{title}\n\n{description}{deadline}{join_us}{tags}"
