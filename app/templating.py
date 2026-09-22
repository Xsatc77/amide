from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def money(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def mg(value: float | None) -> str:
    return "" if value is None else f"{value:g} mg"


def shortdate(value: date | None) -> str:
    return "" if value is None else f"{value:%b} {value.day}, {value.year}"


templates.env.filters["money"] = money
templates.env.filters["mg"] = mg
templates.env.filters["shortdate"] = shortdate
