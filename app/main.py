from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.auth import gate
from app.migrate import upgrade_db
from app.routers import auth, calculator, calendar, inventory, library, protocols


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    upgrade_db()
    yield


app = FastAPI(title="Amide", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
gate.install(app)
app.include_router(auth.router)
app.include_router(inventory.router)
app.include_router(protocols.router)
app.include_router(calendar.router)
app.include_router(calculator.router)
app.include_router(library.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(auth.HOME)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
