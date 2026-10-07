from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.auth import gate
from app.db import SessionLocal
from app.food.foods import load_starter
from app import reminders
from app.backup import scheduled as scheduled_backup
from app.ingest import worker
from app.migrate import upgrade_db
from app.routers import (
    auth, backup, body_photos, calculator, calendar, dashboard, dosing, fitness_test, food, ingest_admin, ingest_api, inventory, journal, labels, labs, legal, library, library_extras,
    measurements, order_tracking, price_alerts, protocol_shop, protocols, settings, spending, vendors, workout_insights, workouts,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    upgrade_db()
    with SessionLocal() as session:
        load_starter(session)      # the built-in starter foods: add what is missing, refresh what changed
    task, reminder_task, backup_task = worker.start(), reminders.start(), scheduled_backup.start()
    yield
    await worker.stop(task)
    await reminders.stop(reminder_task)
    await scheduled_backup.stop(backup_task)


app = FastAPI(title="Amide", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
gate.install(app)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(labels.router)              # before inventory too: "/inventory/orders/<id>/labels"
app.include_router(spending.router)            # before inventory: "/inventory/spending" must not be read as an item id
app.include_router(order_tracking.router)      # before inventory: "/inventory/orders" must not be read as an item id
app.include_router(inventory.router)
app.include_router(library_extras.router)     # before library: "/library/stacks" must not be read as a peptide id
app.include_router(vendors.router)
app.include_router(protocol_shop.router)
app.include_router(protocols.router)
app.include_router(measurements.router)
app.include_router(body_photos.router)
app.include_router(food.router)
app.include_router(ingest_api.router)
app.include_router(ingest_admin.router)
app.include_router(journal.router)
app.include_router(labs.router)
app.include_router(calendar.router)
app.include_router(dosing.router)
app.include_router(calculator.router)
app.include_router(backup.router)
app.include_router(legal.router)
app.include_router(price_alerts.router)
app.include_router(library.router)
app.include_router(workouts.router)
app.include_router(workout_insights.router)
app.include_router(settings.router)
app.include_router(fitness_test.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(auth.HOME)


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """The service worker must be served from the root to control every page."""
    return FileResponse(Path(__file__).parent / "static" / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
