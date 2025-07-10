from fastapi import FastAPI
from app.scheduler import start_scheduler
from threading import Thread
from app.database import init_db

app = FastAPI()

@app.get("/ping")
async def ping():
    return {"message": "Bot is running"}

# Start scheduler on launch
Thread(target=start_scheduler, daemon=True).start()

# Initialize database
init_db()
