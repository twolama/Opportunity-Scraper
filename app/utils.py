import os
import html


def _close_html_tags(text: str) -> str:
    """Close any unclosed HTML tags after truncation."""
    tags = []
    i = 0
    while i < len(text):
        if text[i] == '<':
            close = text.find('>', i)
            if close == -1:
                text = text[:i]
                break
            tag = text[i+1:close]
            if tag.startswith('/'):
                if tags and tags[-1] == tag[1:]:
                    tags.pop()
            elif not tag.endswith('/') and tag[0] != '/' and ' ' not in tag and tag not in ('br', 'hr'):
                tags.append(tag.split()[0])
            i = close + 1
        else:
            i += 1
    for t in reversed(tags):
        text += f'</{t}>'
    return text


def split_html_message(message: str, max_length: int = 4096) -> list[str]:
    """Split an HTML message into chunks not exceeding max_length.

    Splits at paragraph boundaries (double newlines) when possible.
    Each chunk has properly closed HTML tags.
    """
    if len(message) <= max_length:
        return [message]

    paragraphs = message.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        candidate = current + ("\n\n" + para if current else para)
        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                chunks.append(_close_html_tags(current))

            if len(para) > max_length:
                remaining = para
                while remaining:
                    if len(remaining) <= max_length:
                        chunks.append(_close_html_tags(remaining))
                        remaining = ""
                    else:
                        split_at = remaining[:max_length]
                        last_space = split_at.rfind(" ")
                        if last_space > 0:
                            split_point = last_space
                        else:
                            split_point = max_length
                        chunks.append(_close_html_tags(remaining[:split_point]))
                        remaining = remaining[split_point:].lstrip()
                current = ""
            else:
                current = para

    if current:
        chunks.append(_close_html_tags(current))

    return chunks


def format_teaser(description: str, max_chars: int = 250) -> str:
    """Truncate description to max_chars, ending at a word boundary."""
    escaped = html.escape(description)
    if len(escaped) <= max_chars:
        return escaped
    truncated = escaped[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


def format_condensed_post(opportunity: dict, telegraph_url: str) -> str:
    """Format a condensed Telegram post with teaser and Telegraph link."""
    title = f"📌 <b>{html.escape(opportunity['title'])}</b>" if opportunity.get("title") else ""

    teaser = format_teaser(opportunity.get("description", ""), max_chars=250)
    read_more = f'\n\n📖 <a href="{html.escape(telegraph_url)}">Read more</a>'

    deadline = f"\n\n📅 <b>Deadline:</b> {html.escape(opportunity['deadline'])}" if opportunity.get("deadline") else ""

    join_us_url = os.getenv("JOIN_US_URL", "https://t.me/opportunityspots")
    join_us = f'\n\n✅ <a href="{html.escape(join_us_url)}">Join Us</a>'

    tags_text = os.getenv("TAGS_TEXT", "#Opportunities #Scholarships #Grants #Education #Career @opportunityspots")
    tags = f"\n\n{html.escape(tags_text)}"

    return f"{title}\n\n{teaser}{read_more}{deadline}{join_us}{tags}"


def format_telegram_message(opportunity: dict) -> str:
    title = f"📌 <b>{html.escape(opportunity['title'])}</b>" if opportunity.get("title") else ""
    description = html.escape(opportunity.get("description", ""))
    deadline = f"\n\n📅 <b>Deadline:</b> {html.escape(opportunity['deadline'])}" if opportunity.get("deadline") else ""
    join_us_url = os.getenv("JOIN_US_URL", "https://t.me/opportunityspots")
    join_us = f'\n\n✅ <a href="{html.escape(join_us_url)}"><b>Join Us</b></a>'
    tags_text = os.getenv("TAGS_TEXT", "#Opportunities #Scholarships #Grants #Education #Career @opportunityspots")
    tags = f"\n\n{html.escape(tags_text)}"

    return f"{title}\n\n{description}{deadline}{join_us}{tags}"
