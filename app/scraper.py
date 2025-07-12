
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta
import sys
from urllib.parse import urlparse
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

from app.database import save_opportunity, opportunity_exists

BASE_URL = "https://opportunitydesk.org"


USER_AGENTS = [
    # Chrome (Windows, Mac, Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Mobile
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]


# More realistic browser headers, including Accept-Encoding, Origin, Host, etc.
BROWSER_HEADERS = [
    {
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", ";Not A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": BASE_URL,
        "Host": urlparse(BASE_URL).netloc,
    },
    {
        "sec-ch-ua": '"Chromium";v="124", "Not.A/Brand";v="99", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": BASE_URL,
        "Host": urlparse(BASE_URL).netloc,
    },
    {
        "sec-ch-ua": '"Chromium";v="124", "Not.A/Brand";v="99", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": BASE_URL,
        "Host": urlparse(BASE_URL).netloc,
    },
]

REFERERS = [
    BASE_URL + "/",
    BASE_URL + "/about/",
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
]

def random_headers():
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": random.choice([
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ]),
        "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8", "en;q=0.7"]),
        "Connection": "keep-alive",
        "Referer": random.choice(REFERERS),
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }
    # Add random browser headers
    headers.update(random.choice(BROWSER_HEADERS))
    # Add random cookie header (simulate browser session)
    cookies = [
        "cookieconsent_status=allow",
        f"_ga={random.randint(10000000,99999999)}.{random.randint(1000000000,9999999999)}",
        f"_gid={random.randint(10000000,99999999)}.{random.randint(1000000000,9999999999)}",
        f"wordpress_logged_in_{random.randint(10000,99999)}=user%7C{random.randint(1000000000,9999999999)}%7C{random.randint(1000000000,9999999999)}%7C{random.randint(1000000000,9999999999)}%7C{random.randint(1000000000,9999999999)}%7C{random.randint(1000000000,9999999999)}",
    ]
    if random.random() > 0.5:
        headers["Cookie"] = "; ".join(cookies)
    # Randomize header order
    items = list(headers.items())
    random.shuffle(items)
    return dict(items)

# Optional: Proxy support (set to None or a list of proxies)
PROXIES = None  # Example: ["http://proxy1:port", "http://proxy2:port"]

def safe_get(session, url, max_retries=5):
    last_exception = None
    for i in range(max_retries):
        try:
            proxies = None
            if PROXIES:
                proxy = random.choice(PROXIES)
                proxies = {"http": proxy, "https": proxy}
            headers = random_headers()
            response = session.get(
                url,
                headers=headers,
                timeout=30,
                proxies=proxies,
                allow_redirects=True
            )
            # Emulate browser cookies (set on first request)
            if not session.cookies:
                session.cookies.set("cookieconsent_status", "allow")
            response.raise_for_status()
            # Add a random delay after each request
            time.sleep(random.uniform(1.5, 3.5))
            # If we get a 403 but content looks like a real page, still return
            if response.status_code == 403 and 'captcha' in response.text.lower():
                raise requests.exceptions.RequestException("Blocked by CAPTCHA")
            return response
        except requests.exceptions.RequestException as e:
            last_exception = e
            print(f"⚠️ Attempt {i + 1} failed for {url}: {e}")
            # Stronger random backoff
            time.sleep((2 ** i) + random.uniform(2, 6))

    # Fallback: Use Selenium if available and all requests failed
    if SELENIUM_AVAILABLE:
        print(f"🔄 All requests failed for {url}, trying with Selenium...")
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument(f'user-agent={random.choice(USER_AGENTS)}')
            driver = webdriver.Chrome(options=chrome_options)
            driver.get(url)
            time.sleep(random.uniform(3, 6))
            page_source = driver.page_source
            driver.quit()
            # Fake a requests.Response-like object
            class DummyResponse:
                def __init__(self, text):
                    self.text = text
                    self.status_code = 200
                def raise_for_status(self):
                    pass
            return DummyResponse(page_source)
        except Exception as e:
            print(f"❌ Selenium also failed for {url}: {e}")
            return None
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
