import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta
import sys

from app.database import save_opportunity, opportunity_exists

BASE_URL = "https://opportunitydesk.org"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }

def safe_get(session, url, max_retries=5):
    for i in range(max_retries):
        try:
            response = session.get(url, headers=random_headers(), timeout=30)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Attempt {i + 1} failed for {url}: {e}")
            time.sleep((2 ** i) + random.uniform(2, 4))
    return None

def extract_detail_info(session, detail_url):
    response = safe_get(session, detail_url)
    if not response:
        return None, None, None, None, []

    soup = BeautifulSoup(response.text, "html.parser")

    more_info_link = None
    deadline = None
    thumbnail_url = None
    description = None
    tags = []

    for p in soup.find_all("p"):
        text = p.get_text(strip=True).lower()
        if "for more information" in text or "apply here" in text or "apply now" in text:
            a_tag = p.find("a", href=True)
            if a_tag:
                more_info_link = a_tag['href']

        strong_tag = p.find("strong")
        if strong_tag and "deadline:" in strong_tag.get_text(strip=True).lower():
            match = re.search(r"deadline:\s*(.*)", strong_tag.get_text(strip=True), re.IGNORECASE)
            if match:
                deadline = match.group(1).strip()

    figure = soup.find("figure", class_="image-link")
    if figure:
        img = figure.find("img")
        if img and img.has_attr("src"):
            thumbnail_url = img['src']

    content_div = soup.find("div", class_="entry-content")
    if content_div:
        paragraphs = content_div.find_all("p", recursive=False)
        if paragraphs:
            raw_description = " ".join(p.get_text(strip=True) for p in paragraphs[:2])
            raw_description = re.sub(r"^deadline:\s*[^.]*\.?\s*", "", raw_description, flags=re.IGNORECASE)
            description = raw_description

    categories = soup.find_all("a", rel="category tag")
    if categories:
        tags = [cat.get_text(strip=True) for cat in categories]

    if not more_info_link:
        more_info_link = detail_url

    time.sleep(random.uniform(3, 6))
    return more_info_link, deadline, thumbnail_url, description, tags

def clean_deadline(deadline_str):
    if not deadline_str:
        return None
    date_patterns = [
        r'(\d{1,2}(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})',
        r'((January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})',
        r'(\d{1,2}/\d{1,2}/\d{4})',
        r'(\d{4}-\d{2}-\d{2})'
    ]
    for pattern in date_patterns:
        match = re.search(pattern, deadline_str, re.IGNORECASE)
        if match:
            return match.group(1)
    return deadline_str

def fetch_opportunities_by_date(target_date=None):
    """Fetch and save opportunities for given date (default: yesterday)"""
    if not target_date:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d")

    all_opportunities = []
    session = requests.Session()
    url = f"{BASE_URL}/{target_date}/"
    print(f"\n🔍 Fetching: {url}")

    page_response = safe_get(session, url)
    if not page_response:
        print(f"❌ Could not fetch {url}")
        return []

    soup = BeautifulSoup(page_response.text, "html.parser")
    articles = soup.select("article")
    print(f"✅ Found {len(articles)} articles.")

    for idx, article in enumerate(articles, start=1):
        try:
            title_link = article.find("a", string=True, href=True)
            if not title_link:
                print(f"⚠️ No title found in article #{idx}, skipping...")
                continue

            title = title_link.get_text(strip=True)
            detail_url = title_link['href']

            link, deadline, thumbnail, description, tags = extract_detail_info(session, detail_url)

            if not link or link.startswith(BASE_URL):
                print(f"⏩ Skipping '{title}' (no valid link)")
                continue

            cleaned_deadline = clean_deadline(deadline)
            opportunity = {
                "title": title,
                "link": link,
                "deadline": cleaned_deadline,
                "thumbnail": thumbnail,
                "description": description,
                "tags": tags
            }

            if opportunity_exists(title, link):
                print(f"🟡 Already exists: {title}")
                continue

            saved = save_opportunity(opportunity)
            if saved:
                print(f"✅ Saved: {title}")
                all_opportunities.append(opportunity)
            else:
                print(f"❌ Failed to save: {title}")

        except Exception as e:
            print(f"❌ Error parsing article #{idx}: {e}")

    return all_opportunities

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            datetime.strptime(sys.argv[1], "%Y/%m/%d")
            fetch_opportunities_by_date(sys.argv[1])
        except ValueError:
            print("Invalid date. Use format: YYYY/MM/DD")
    else:
        fetch_opportunities_by_date()
