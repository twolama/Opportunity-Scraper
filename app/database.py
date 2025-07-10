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
