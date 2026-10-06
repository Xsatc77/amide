"""The background loop that processes settled items every 20 seconds."""

import asyncio

from app import config
from app.db import SessionLocal
from app.ingest import process
from app.models import naive_utcnow

INTERVAL = 20


async def _loop():
    while True:
        try:
            await asyncio.to_thread(process.process_due, SessionLocal, naive_utcnow())
        except Exception:
            pass                                    # the next round tries again; an item that crashed is already marked failed
        await asyncio.sleep(INTERVAL)


def start():
    return asyncio.create_task(_loop()) if config.INGEST_WORKER_ENABLED else None


async def stop(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
