import asyncio
from fastapi import FastAPI
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware

from app.scheduler import start_scheduler
from app.database import init_db, get_all_opportunities

app = FastAPI()

# ✅ CORS config (keep as is or restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    # ✅ Ensures DB tables are created when server starts (via SQLAlchemy)
    init_db()

    # ✅ Start the scheduler in a separate thread (worker mode will skip this)
    Thread(target=start_scheduler, daemon=True).start()

@app.get("/")
async def root():
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.get("/opportunities")
async def get_opportunities():
    # ✅ Run blocking DB call asynchronously
    loop = asyncio.get_running_loop()
    opportunities = await loop.run_in_executor(None, get_all_opportunities)
    return opportunities
