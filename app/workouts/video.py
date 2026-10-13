"""Exercise demo links: the video page a workout PDF links an exercise to, or else a video search for its name."""

from urllib.parse import quote_plus, urlparse

MAX_URL = 500


def safe_url(raw) -> str | None:
    """A web address worth linking to: http or https with a host, no longer than the column. Anything else (a script, a file, plain
    text that a PDF stored in its link field) is dropped."""
    url = str(raw or "").strip()
    if not url or len(url) > MAX_URL:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def demo_url(name: str, stored: str | None) -> str:
    return safe_url(stored) or "https://www.youtube.com/results?search_query=" + quote_plus(f"{name} exercise form")
