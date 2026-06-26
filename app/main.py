import os
import csv
import io
import asyncio
import time
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
_logger = logging.getLogger(__name__)
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, Query
from threading import Thread, Event
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional

from app.scheduler import start_scheduler, run_scrape, run_post
from app.scraper import fetch_opportunities_by_date
import requests
from app.database import (
    init_db,
    engine,
    get_unposted_opportunities,
    get_stats_from_db,
    get_opportunity_by_id,
    update_opportunity,
    delete_opportunity,
    search_opportunities,
    opportunity_to_dict,
    Opportunity,
    Admin,
    add_admin,
    remove_admin,
    get_admins,
)
from app.schemas import (
    OpportunityOut, OpportunityCreate, OpportunityUpdate,
    SearchResultOut, StatsOut, AdminOut, AdminCreate,
    RootOut, RunOnceOut, WebhookOut,
)
from app.config import TELEGRAM_API_URL, BOT_OWNER_ID, PUBLIC_URL, USE_POLLING, RUN_SCHEDULER, API_KEY, SENTRY_DSN, TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_ID

if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.1,
            send_default_pii=False,
            environment="production" if PUBLIC_URL else "development",
        )
    except Exception as e:
        logging.warning("Sentry initialization failed (non-fatal): %s", e)

from app.http_client import http as _http, sanitize as _sanitize
from app.rate_limiter import api_limiter
from app.telegram_handlers import process_telegram_update, set_bot_info

_shutdown_event = Event()

@asynccontextmanager
async def lifespan(app):
    try:
        init_db()
    except Exception as e:
        logging.warning("DB init failed (will retry on next restart): %s", e)
    if not TELEGRAM_CHANNEL_ID:
        logging.info("TELEGRAM_CHANNEL_ID not set — will use channels configured via Telegram admin")
    try:
        me = _http.post(f"{TELEGRAM_API_URL}/getMe").json().get("result", {})
        set_bot_info(me.get("username"), me.get("first_name", "Opportunity Search Bot"))
    except Exception:
        logging.warning("Failed to get bot info from Telegram (non-fatal)")
    primary = os.getenv("PRIMARY_WORKER", "true").lower() == "true"
    threads = []
    if primary:
        try:
            set_webhook()
        except Exception as e:
            logging.warning(_sanitize(f"Webhook setup failed (non-fatal): {e}"))
        if USE_POLLING:
            t = Thread(target=start_polling, args=(_shutdown_event,), daemon=True)
            t.start()
            threads.append(t)
        if RUN_SCHEDULER:
            t = Thread(target=start_scheduler, args=(_shutdown_event,), daemon=True)
            t.start()
            threads.append(t)
            _logger.info("Scheduler started (primary worker)")
    else:
        _logger.info("Secondary worker (no scheduler/webhook)")
    if os.getenv("RENDER"):
        def _keepalive(shutdown: Event):
            while not shutdown.is_set():
                shutdown.wait(300)
                if shutdown.is_set():
                    break
                try:
                    resp = _http.get(f"http://localhost:{os.getenv('PORT', '8000')}/ping", timeout=10)
                    resp.content
                except Exception:
                    pass
        t = Thread(target=_keepalive, args=(_shutdown_event,), daemon=True)
        t.start()
        threads.append(t)
    yield
    _logger.info("Shutdown: signalling threads to stop...")
    _shutdown_event.set()
    for t in threads:
        t.join(timeout=10)
    _logger.info("Shutdown: closing database connections...")
    engine.dispose()
    _logger.info("Shutdown: done.")


app = FastAPI(
    title="Opportunity Search API",
    description="Searches for opportunities (scholarships, grants, fellowships), stores them, and posts new ones to a Telegram channel.",
    version="1.0.0",
    contact={"name": "Mecha T.", "url": "https://twolama.me"},
    lifespan=lifespan,
)

# API Key auth dependency for write endpoints
from fastapi import Header, HTTPException, Depends

async def verify_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return x_api_key


@app.post("/webhook", tags=["Telegram"], summary="Receive Telegram updates", response_model=WebhookOut)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    loop = asyncio.get_running_loop()

    def _thread_safe_add_task(coro):
        loop.call_soon_threadsafe(background_tasks.add_task, coro)

    try:
        await loop.run_in_executor(None, process_telegram_update, data, _thread_safe_add_task)
    except Exception as e:
        logging.warning(_sanitize(f"Webhook processing error: {e}"))
    return {"ok": True}

# CORS config - allow all origins for now, restrict in production if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if os.getenv("TESTING", "").lower() == "true":
        return await call_next(request)
    if request.url.path not in ("/ping", "/", "/docs", "/openapi.json"):
        ip = request.client.host if request.client else "unknown"
        wait = api_limiter.consume(ip)
        if wait > 0:
            from fastapi.responses import JSONResponse
            logging.warning("Rate limit hit for %s on %s", ip, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(int(wait))},
            )
    return await call_next(request)

def set_webhook():
    use_polling = os.getenv("USE_POLLING", "true").lower() == "true"
    public_url = os.getenv("PUBLIC_URL")
    if public_url and not use_polling:
        webhook_url = f"{public_url.rstrip('/')}/webhook"
        resp = _http.post(f"{TELEGRAM_API_URL}/setWebhook", json={"url": webhook_url})
        if resp.ok:
            _logger.info("Webhook set to %s", webhook_url)
        else:
            _logger.warning("Failed to set webhook: %s", _sanitize(resp.text))
    else:
        _http.get(f"{TELEGRAM_API_URL}/deleteWebhook")
        _logger.info("Webhook cleared (polling mode)")

def start_polling(shutdown: Event):
    offset = 0
    backoff = 1
    max_backoff = 30
    _logger.info("Polling started (local mode - no webhook required)")
    while not shutdown.is_set():
        try:
            resp = _http.get(
                f"{TELEGRAM_API_URL}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            if resp.ok:
                backoff = 1
                for update in resp.json().get("result", []):
                    try:
                        process_telegram_update(update)
                    except Exception as e:
                        logging.warning(_sanitize(f"Error processing update {update.get('update_id')}: {e}"))
                    finally:
                        offset = update["update_id"] + 1
        except requests.exceptions.Timeout:
            backoff = 1
            pass
        except Exception as e:
            logging.warning(_sanitize(f"Polling error: {e}"))
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

@app.get("/", tags=["Health"], summary="Root welcome message", response_model=RootOut)
async def root():
    """Returns a simple welcome message."""
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping", tags=["Health"], summary="Health check")
async def ping():
    """Checks app, DB, and Telegram API connectivity."""
    checks = {"app": "ok"}
    try:
        from app.db import get_session
        with get_session() as db:
            db.execute(Opportunity.__table__.select().limit(1))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        logging.warning("Healthcheck DB failure: %s", e)
    try:
        tg = _http.get(f"{TELEGRAM_API_URL}/getMe", timeout=5)
        if tg.ok:
            checks["telegram"] = "ok"
        else:
            checks["telegram"] = f"error: {tg.status_code}"
            logging.warning("Healthcheck Telegram failure: %s", tg.status_code)
    except Exception as e:
        checks["telegram"] = f"error: {e}"
        logging.warning("Healthcheck Telegram error: %s", e)
    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=checks)
    return checks

@app.head("/ping", tags=["Health"], summary="Health check (HEAD)", include_in_schema=False)
async def ping_head():
    return

@app.get("/opportunities", tags=["Opportunities"], summary="List opportunities", response_model=SearchResultOut)
async def get_opportunities(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    search: Optional[str] = Query(None, description="Search keyword in title/description/tags"),
    posted: Optional[str] = Query(None, description="Filter: 'true' for posted, 'false' for unposted, omit for all"),
):
    """Search and paginate opportunities."""
    posted_bool = {"true": True, "false": False}.get(posted.lower()) if posted else None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, search_opportunities, search or "", skip, limit, posted_bool)

@app.get("/opportunities/export", tags=["Opportunities"], summary="Export as CSV")
async def export_opportunities(
    posted: Optional[str] = Query(None, description="Filter: 'true' for posted, 'false' for unposted, omit for all"),
    search: Optional[str] = Query(None, description="Search keyword"),
):
    """Export opportunities as a CSV file."""
    from fastapi.responses import StreamingResponse
    posted_bool = {"true": True, "false": False}.get(posted.lower()) if posted else None
    def _fetch_all():
        return search_opportunities(search or "", 0, 1000000, posted_bool)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _fetch_all)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "title", "link", "description", "deadline", "tags", "created_at", "posted_to_telegram"])
    for op in data["results"]:
        writer.writerow([
            op["id"], op["title"], op["link"], op.get("description", ""),
            op.get("deadline", ""), ", ".join(op.get("tags", [])),
            str(op.get("created_at", ""))[:10] if op.get("created_at") else "",
            op.get("posted_to_telegram", False),
        ])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=opportunities.csv"})

@app.get("/opportunities/unposted", tags=["Opportunities"], summary="List unposted opportunities", response_model=list[OpportunityOut])
async def get_unposted():
    """Returns opportunities that have not yet been sent to Telegram."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_unposted_opportunities)

@app.get("/opportunities/posted", tags=["Opportunities"], summary="List posted opportunities", response_model=list[OpportunityOut])
async def get_posted():
    def fetch_posted():
        from app.db import get_session
        with get_session() as db:
            results = db.query(Opportunity).filter_by(posted_to_telegram=True).all()
            return [opportunity_to_dict(opp) for opp in results]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_posted)

@app.get("/opportunities/{opportunity_id}", tags=["Opportunities"], summary="Get an opportunity by ID", response_model=OpportunityOut)
async def get_opportunity(opportunity_id: int):
    """Returns a single opportunity by its ID."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, get_opportunity_by_id, opportunity_id)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return result

@app.post("/opportunities", tags=["Opportunities"], summary="Create an opportunity", response_model=OpportunityOut, status_code=201, dependencies=[Depends(verify_api_key)])
async def create_opportunity(body: OpportunityCreate):
    """Create a new opportunity manually."""
    from app.database import save_opportunity, get_opportunity_by_id
    data = {
        "title": body.title,
        "link": body.link,
        "description": body.description or "",
        "deadline": body.deadline or "",
        "thumbnail": body.thumbnail or "",
        "tags": body.tags or [],
    }
    def _create():
        opp_id = save_opportunity(data, scraped_date=body.created_at)
        if opp_id is None:
            return None
        return get_opportunity_by_id(opp_id)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _create)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Failed to create opportunity (may be duplicate)")
    return result

# ✅ Optional: Trigger the task manually (for testing via browser)
@app.get("/run-once", tags=["Management"], summary="Trigger daily tasks manually", response_model=RunOnceOut)
async def run_once():
    """Runs the full daily routine: scrape, post to Telegram, and clean old entries."""
    def run():
        run_scrape()
        run_post()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run)
    return {"status": "Scheduler manually triggered."}

# ----- Opportunity CRUD -----

@app.put("/opportunities/{opportunity_id}", tags=["Opportunities"], summary="Update an opportunity", response_model=OpportunityOut, dependencies=[Depends(verify_api_key)])
async def update_opportunity_endpoint(opportunity_id: int, body: OpportunityUpdate):
    """Update fields on an existing opportunity."""
    loop = asyncio.get_running_loop()
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = await loop.run_in_executor(None, update_opportunity, opportunity_id, data)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Opportunity not found or update failed")
    result = await loop.run_in_executor(None, get_opportunity_by_id, opportunity_id)
    return result

@app.delete("/opportunities/{opportunity_id}", tags=["Opportunities"], summary="Delete an opportunity", dependencies=[Depends(verify_api_key)])
async def delete_opportunity_endpoint(opportunity_id: int):
    """Delete a single opportunity by ID."""
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, delete_opportunity, opportunity_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"ok": True}

@app.delete("/opportunities", tags=["Opportunities"], summary="Bulk delete opportunities", dependencies=[Depends(verify_api_key)])
async def bulk_delete_opportunities(
    ids: Optional[str] = Query(None, description="Comma-separated IDs to delete"),
    older_than_days: Optional[int] = Query(None, ge=1, description="Delete opportunities older than N days"),
    posted: Optional[str] = Query(None, description="Delete only posted ('true') or unposted ('false')"),
):
    """Delete opportunities matching filters. At least one filter is required."""
    from app.database import delete_old_entries
    from app.db import get_session
    def _bulk_delete():
        with get_session() as db:
            q = db.query(Opportunity)
            if ids:
                id_list = []
                for part in ids.split(","):
                    item = part.strip()
                    if item.isdigit():
                        id_list.append(int(item))
                    else:
                        logging.warning("Skipping invalid ID in bulk_delete: %s", _sanitize(item))
                q = q.filter(Opportunity.id.in_(id_list))
            if older_than_days:
                cutoff = datetime.utcnow() - timedelta(days=older_than_days)
                q = q.filter(Opportunity.created_at < cutoff)
            if posted is not None:
                posted_bool = {"true": True, "false": False}.get(posted.lower())
                if posted_bool is not None:
                    q = q.filter(Opportunity.posted_to_telegram == posted_bool)
            deleted = q.delete(synchronize_session=False)
            return deleted

    if not ids and not older_than_days and posted is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Provide at least one filter: ids, older_than_days, or posted")
    loop = asyncio.get_running_loop()
    count = await loop.run_in_executor(None, _bulk_delete)
    return {"deleted": count}

@app.post("/opportunities/{opportunity_id}/post", tags=["Opportunities"], summary="Mark as posted and post to Telegram", dependencies=[Depends(verify_api_key)])
async def post_opportunity(opportunity_id: int):
    """Mark an opportunity as posted and send it to Telegram."""
    from app.telegram_bot import post_to_telegram
    def _post():
        opp = get_opportunity_by_id(opportunity_id)
        if not opp:
            return None
        ok = post_to_telegram(opp)
        if ok:
            from app.database import update_posted_status
            update_posted_status(opportunity_id)
        return ok
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, _post)
    if ok is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="Failed to post to Telegram")
    return {"ok": True, "message": "Posted to Telegram"}

@app.post("/opportunities/{opportunity_id}/unpost", tags=["Opportunities"], summary="Mark as unposted", dependencies=[Depends(verify_api_key)])
async def unpost_opportunity(opportunity_id: int):
    """Reset posted_to_telegram to False for an opportunity."""
    from app.db import get_session
    def _unpost():
        with get_session() as db:
            opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
            if not opp:
                return False
            opp.posted_to_telegram = False
            return True
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, _unpost)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"ok": True}

# ----- Admin CRUD -----

@app.get("/admins", tags=["Admins"], summary="List all admins", response_model=list[AdminOut])
async def list_admins():
    """Returns all registered admins."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_admins)

@app.post("/admins", tags=["Admins"], summary="Add an admin", response_model=AdminOut, status_code=201, dependencies=[Depends(verify_api_key)])
async def create_admin(body: AdminCreate):
    """Add a user as an admin."""
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, add_admin, body.user_id, BOT_OWNER_ID or 0, body.name)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Admin already exists or failed to create")
    def _fetch_admin():
        from app.db import get_session
        with get_session() as db:
            a = db.query(Admin).filter_by(user_id=body.user_id).first()
            if a:
                return {"user_id": a.user_id, "name": a.name, "added_by": a.added_by, "created_at": a.created_at}
            return None
    result = await loop.run_in_executor(None, _fetch_admin)
    return result

@app.delete("/admins/{user_id}", tags=["Admins"], summary="Remove an admin", dependencies=[Depends(verify_api_key)])
async def delete_admin(user_id: int):
    """Remove an admin by Telegram user ID."""
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, remove_admin, user_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Admin not found")
    return {"ok": True}

# ----- Utility Endpoints -----

@app.get("/stats", tags=["Management"], summary="Get analytics", response_model=StatsOut)
async def stats_endpoint():
    """Returns detailed analytics as JSON."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_stats_from_db)

@app.post("/scrape", tags=["Management"], summary="Trigger searching", dependencies=[Depends(verify_api_key)])
async def trigger_scrape(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (default: yesterday)")):
    """Trigger a scrape for a specific date. Posting is handled by the scheduler."""
    def _scrape():
        if date:
            target = date.replace("-", "/")
        else:
            target = None
        ops = fetch_opportunities_by_date(target)
        return {"found": len(ops)}
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _scrape)
    return result
