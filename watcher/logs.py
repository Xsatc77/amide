"""Logging to a rotating file and the console. Callers log only titles, ids, counts and error class names."""

import logging
import logging.handlers
from pathlib import Path


def get_logger(path: Path | None) -> logging.Logger:
    log = logging.getLogger("watcher")
    if not log.handlers:
        log.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        log.addHandler(console)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            log.addHandler(handler)
    return log
