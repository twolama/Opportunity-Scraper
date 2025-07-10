import sqlite3
from datetime import datetime, timedelta
from os import getenv

DB_PATH = getenv("DB_PATH", "data/opportunities.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT,
            description TEXT,
            deadline TEXT,
            thumbnail TEXT,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posted_to_telegram BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def opportunity_exists(title, link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM opportunities WHERE title = ? AND link = ?", (title, link))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def save_opportunity(opportunity):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO opportunities (title, link, description, deadline, thumbnail, tags)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        opportunity['title'],
        opportunity['link'],
        opportunity['description'],
        opportunity['deadline'],
        opportunity['thumbnail'],
        ', '.join(opportunity['tags'])
    ))
    conn.commit()
    conn.close()
    return True

def update_posted_status(opportunity_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE opportunities SET posted_to_telegram = 1 WHERE id = ?", (opportunity_id,))
    conn.commit()
    conn.close()

def get_unposted_opportunities():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, link, description, deadline, thumbnail, tags FROM opportunities WHERE posted_to_telegram = 0")
    rows = c.fetchall()
    conn.close()
    opportunities = []
    for row in rows:
        opportunities.append({
            "id": row[0],
            "title": row[1],
            "link": row[2],
            "description": row[3],
            "deadline": row[4],
            "thumbnail": row[5],
            "tags": row[6].split(", ") if row[6] else []
        })
    return opportunities


def get_all_opportunities():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, link, description, deadline, thumbnail, tags, created_at, posted_to_telegram FROM opportunities ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()

    print(f"Fetched {len(rows)} opportunities from DB")  # DEBUG

    opportunities = []
    for row in rows:
        opportunities.append({
            "id": row[0],
            "title": row[1],
            "link": row[2],
            "description": row[3],
            "deadline": row[4],
            "thumbnail": row[5],
            "tags": row[6].split(", ") if row[6] else [],
            "created_at": row[7],
            "posted_to_telegram": bool(row[8]),
        })

    return opportunities



def delete_old_entries():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    month_ago = datetime.now() - timedelta(days=30)
    cursor.execute("DELETE FROM opportunities WHERE created_at < ?", (month_ago.isoformat(),))

    deleted = cursor.rowcount  # number of deleted rows
    conn.commit()
    conn.close()

    print(f"🧹 Deleted {deleted} old opportunities (older than 30 days).")
