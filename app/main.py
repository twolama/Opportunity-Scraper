import os
import asyncio
from fastapi import FastAPI
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware

from app.scheduler import start_scheduler
from app.database import init_db, get_all_opportunities

app = FastAPI()

# CORS config - allow all origins for now, restrict in production if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    # Create DB tables if missing
    init_db()

    # Only start scheduler if RUN_SCHEDULER env var is true or unset (default true)
    if os.getenv("RUN_SCHEDULER", "true").lower() == "true":
        Thread(target=start_scheduler, daemon=True).start()

@app.get("/")
async def root():
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.head("/ping")
async def ping_head():
    return

@app.get("/opportunities")
async def get_opportunities():
    # Run blocking DB query asynchronously to avoid blocking event loop
    loop = asyncio.get_running_loop()
    opportunities = await loop.run_in_executor(None, get_all_opportunities)
    return opportunities
