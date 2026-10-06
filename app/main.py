from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.auth import gate
from app.db import SessionLocal
from app.food.foods import load_starter
from app.migrate import upgrade_db
from app.routers import (
    auth, backup, body_photos, calculator, calendar, dashboard, dosing, fitness_test, inventory, journal, labs, legal, library,
    measurements, price_alerts, protocols, settings, vendors, workout_insights, workouts,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    upgrade_db()
    with SessionLocal() as session:
        load_starter(session)      # the built-in starter foods: add what is missing, refresh what changed
    yield


app = FastAPI(title="Amide", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
gate.install(app)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(inventory.router)
app.include_router(vendors.router)
app.include_router(protocols.router)
app.include_router(measurements.router)
app.include_router(body_photos.router)
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


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
