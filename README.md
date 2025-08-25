# Opportunity Scraper Bot

A robust, automated solution for scraping, storing, and sharing the latest opportunities (scholarships, grants, fellowships, etc.) from the web. The bot collects opportunities, stores them in a database, and posts them to a Telegram channel. It also exposes a REST API for programmatic access.

---

## Features

- **Automated Web Scraping:** Gathers opportunities from [opportunitydesk.org](https://opportunitydesk.org) and other sources.
- **Database Storage:** Saves opportunities in a PostgreSQL database, avoiding duplicates.
- **Telegram Integration:** Automatically posts new opportunities to a Telegram channel.
- **REST API:** FastAPI-powered endpoints to access and manage opportunities.
- **Scheduled Tasks:** Runs scraping and posting jobs multiple times daily.
- **Environment Config:** Uses `.env` for secure configuration.

---

## Getting Started

### 1. Clone the Repository
```bash
# Clone the repository
git clone https://github.com/mechatemesgen/Opportunity-Scraper
cd "Opportunity-Scraper"
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables
Create a `.env` file in the root directory with the following variables:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/opportunities_db
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHANNEL_ID=@your_channel_id
```

### 4. Run the Application
Start the FastAPI server:
```bash
uvicorn app.main:app --reload
```

The scheduler will run automatically on startup (unless disabled via `RUN_SCHEDULER=false`).

---

## API Endpoints

- **Base URL:** http://127.0.0.1:8000
- **Health Check:** `/ping`  
  Returns `{ "status": "ok" }`
- **List Opportunities:** `/opportunities`  
  Returns a list of all stored opportunities.
- **List Unposted Opportunities:** `/opportunities/unposted`  
  Returns a list of opportunities not yet posted to Telegram.
- **List Posted Opportunities:** `/opportunities/posted`  
  Returns a list of opportunities already posted to Telegram.
- **Manually Trigger Scheduler:** `/run-once`  
  Triggers the scraping/posting tasks once (for testing).

---

## Project Structure

```
app/
  ├── __init__.py
  ├── main.py           # FastAPI app & endpoints
  ├── database.py       # SQLAlchemy models & DB logic
  ├── scraper.py        # Web scraping logic
  ├── scheduler.py      # Task scheduling
  ├── telegram_bot.py   # Telegram posting logic
  ├── utils.py          # Helper functions
  └── worker.py         # (Optional) CLI entry point
requirements.txt        # Python dependencies
render.yaml             # (Optional) Render.com deployment config
data/                   # (Optional) Data storage
```


### Using Docker

Build the Docker image:
```bash
docker build -t opportunity-scraper .
```

Run the container with environment variables from `.env` and expose port 8000:
```bash
docker run --env-file .env -p 8000:8000 opportunity-scraper
```

---

## Deployment

- **Local:** Use the steps above.
- **Production:** Configure environment variables and use a production server (e.g., Gunicorn, Docker, or Render.com).

---

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## License

MIT License. See `LICENSE` file for details.

---

## Acknowledgments

- [Opportunity Desk](https://opportunitydesk.org) for data
- [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/), [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), [python-telegram-bot](https://python-telegram-bot.org/)