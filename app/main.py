import asyncio
from fastapi import FastAPI
from threading import Thread
from app.scheduler import start_scheduler
from app.database import init_db, get_all_opportunities
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.get("/opportunities")
async def get_opportunities():
    # Run blocking DB call in thread pool executor
    loop = asyncio.get_running_loop()
    opportunities = await loop.run_in_executor(None, get_all_opportunities)
    return opportunities

Thread(target=start_scheduler, daemon=True).start()
init_db()
