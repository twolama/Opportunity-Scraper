import os
import csv
import io
import asyncio
import time
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Query
from threading import Thread
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
    SessionLocal,
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
import sentry_sdk
from app.config import TELEGRAM_API_URL, BOT_OWNER_ID, PUBLIC_URL, USE_POLLING, RUN_SCHEDULER, API_KEY, SENTRY_DSN

if SENTRY_DSN:
    try:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.1,
            send_default_pii=True,
            environment="production" if PUBLIC_URL else "development",
        )
    except Exception:
        pass  # Sentry is optional — never block startup

# --- Reusable HTTP session (connection pool => way faster) ---
from requests.adapters import HTTPAdapter

class _TimeoutAdapter(HTTPAdapter):
    def __init__(self, timeout=15, *args, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)
    def send(self, request, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().send(request, **kwargs)

_http = requests.Session()
_http.mount("https://", _TimeoutAdapter(timeout=15))
_http.mount("http://", _TimeoutAdapter(timeout=15))

from app.telegram_handlers import process_telegram_update, _sanitize, set_bot_info

app = FastAPI(
    title="Opportunity Search API",
    description="Searches for opportunities (scholarships, grants, fellowships), stores them, and posts new ones to a Telegram channel.",
    version="1.0.0",
    contact={"name": "Mecha T.", "url": "https://twolama.me"},
)

# API Key auth dependency for write endpoints
from fastapi import Header, HTTPException, Depends

async def verify_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return x_api_key


@app.post("/webhook", tags=["Telegram"], summary="Receive Telegram updates", response_model=WebhookOut)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, process_telegram_update, data, background_tasks.add_task)
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

def set_webhook():
    use_polling = os.getenv("USE_POLLING", "true").lower() == "true"
    public_url = os.getenv("PUBLIC_URL")
    if public_url and not use_polling:
        webhook_url = f"{public_url.rstrip('/')}/webhook"
        resp = _http.post(f"{TELEGRAM_API_URL}/setWebhook", json={"url": webhook_url})
        if resp.ok:
            print(f"[OK] Webhook set to {webhook_url}")
        else:
            print(f"[ERR] Failed to set webhook: {_sanitize(resp.text)}")
    else:
        _http.get(f"{TELEGRAM_API_URL}/deleteWebhook")
        print("[OK] Webhook cleared (polling mode)")

def start_polling():
    offset = 0
    backoff = 1
    max_backoff = 30
    print("[Polling] Started (local mode - no webhook required)")
    while True:
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

@app.on_event("startup")
def on_startup():
    """Initialize database tables and start the background scheduler."""
    try:
        init_db()
    except Exception as e:
        logging.warning(f"DB init failed (will retry on next restart): {e}")
    try:
        me = _http.post(f"{TELEGRAM_API_URL}/getMe").json().get("result", {})
        set_bot_info(me.get("username"), me.get("first_name", "Opportunity Search Bot"))
    except Exception:
        pass
    primary = os.getenv("PRIMARY_WORKER", "true").lower() == "true"
    if primary:
        try:
            set_webhook()
        except Exception as e:
            logging.warning(_sanitize(f"Webhook setup failed (non-fatal): {e}"))
        if os.getenv("USE_POLLING", "true").lower() == "true":
            Thread(target=start_polling, daemon=True).start()
        if os.getenv("RUN_SCHEDULER", "true").lower() == "true":
            Thread(target=start_scheduler, daemon=True).start()
            print("[OK] Scheduler started (primary worker)")
    else:
        print("[OK] Secondary worker (no scheduler/webhook)")
    # Self-keepalive: ping every 5min so Render doesn't sleep the service
    if os.getenv("RENDER"):
        def _keepalive():
            while True:
                time.sleep(300)
                try:
                    _http.get(f"http://localhost:{os.getenv('PORT', '8000')}/ping", timeout=10)
                except Exception:
                    pass
        Thread(target=_keepalive, daemon=True).start()

@app.on_event("shutdown")
def on_shutdown():
    """Gracefully close DB connections on shutdown."""
    print("[Shutdown] Closing database connections...")
    engine.dispose()
    print("[Shutdown] Done.")

@app.get("/", tags=["Health"], summary="Root welcome message", response_model=RootOut)
async def root():
    """Returns a simple welcome message."""
    return {"message": "Am here to help you with opportunities!"}

@app.get("/ping", tags=["Health"], summary="Health check")
async def ping():
    """Checks app, DB, and Telegram API connectivity."""
    checks = {"app": "ok"}
    try:
        db = SessionLocal()
        db.execute(Opportunity.__table__.select().limit(1))
        db.close()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
    try:
        tg = _http.get(f"{TELEGRAM_API_URL}/getMe", timeout=5)
        if tg.ok:
            checks["telegram"] = "ok"
        else:
            checks["telegram"] = f"error: {tg.status_code}"
    except Exception as e:
        checks["telegram"] = f"error: {e}"
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
        return search_opportunities(search or "", 0, 100000, posted_bool)
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
    from app.database import save_opportunity, SessionLocal, Opportunity
    data = {
        "title": body.title,
        "link": body.link,
        "description": body.description or "",
        "deadline": body.deadline or "",
        "thumbnail": body.thumbnail or "",
        "tags": body.tags or [],
    }
    def _create():
        ok = save_opportunity(data, scraped_date=body.created_at)
        if not ok:
            return None
        db = SessionLocal()
        try:
            opp = db.query(Opportunity).filter_by(title=data["title"], link=data["link"]).order_by(Opportunity.id.desc()).first()
            if opp:
                return opportunity_to_dict(opp)
            return None
        finally:
            db.close()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _create)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Failed to create opportunity (may be duplicate)")
    return result

@app.get("/opportunities/unposted", tags=["Opportunities"], summary="List unposted opportunities", response_model=list[OpportunityOut])
async def get_unposted():
    """Returns opportunities that have not yet been sent to Telegram."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_unposted_opportunities)

@app.get("/opportunities/posted", tags=["Opportunities"], summary="List posted opportunities", response_model=list[OpportunityOut])
async def get_posted():
    def fetch_posted():
        db = SessionLocal()
        try:
            results = db.query(Opportunity).filter_by(posted_to_telegram=True).all()
            return [opportunity_to_dict(opp) for opp in results]
        finally:
            db.close()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_posted)

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
    def _bulk_delete():
        db = SessionLocal()
        try:
            q = db.query(Opportunity)
            if ids:
                id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
                q = q.filter(Opportunity.id.in_(id_list))
            if older_than_days:
                cutoff = datetime.utcnow() - timedelta(days=older_than_days)
                q = q.filter(Opportunity.created_at < cutoff)
            if posted is not None:
                posted_bool = {"true": True, "false": False}.get(posted.lower())
                if posted_bool is not None:
                    q = q.filter(Opportunity.posted_to_telegram == posted_bool)
            deleted = q.delete(synchronize_session=False)
            db.commit()
            return deleted
        except Exception:
            db.rollback()
            return 0
        finally:
            db.close()
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
    def _unpost():
        db = SessionLocal()
        try:
            opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
            if not opp:
                return False
            opp.posted_to_telegram = False
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
        finally:
            db.close()
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
        db = SessionLocal()
        try:
            a = db.query(Admin).filter_by(user_id=body.user_id).first()
            if a:
                return {"user_id": a.user_id, "name": a.name, "added_by": a.added_by, "created_at": a.created_at}
            return None
        finally:
            db.close()
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
