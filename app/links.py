"""The Links page: a person's saved sites, grouped by type, and a short description read from the site itself when they did not write one.

Reading a description is the one place Amide fetches a page you did not type into a form, so it is careful: only http and https, never an
address on your own network or this computer, a short timeout, a size cap, and nothing is sent but a plain GET with no cookies. It can be
turned off with AMIDE_LINK_DESCRIPTIONS=0."""

import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TYPES = ("Vendor", "Supplies", "Community", "Research", "Blog/Vlog", "Calculator", "Workouts", "Nutrition", "Other")
MAX_NAME, MAX_URL, MAX_DESCRIPTION = 120, 500, 300
SHORT = 200                       # a fetched description is cut to about this many characters
MAX_BYTES = 262_144               # the first 256 KB of a page is plenty for its description
TIMEOUT = 8
MAX_REDIRECTS = 3
_USER_AGENT = "Mozilla/5.0 (compatible; AmideLinkPreview/1.0)"


def clean_url(raw: str) -> str | None:
    """The address with a scheme (https if none was typed), or None when it is not a usable web address."""
    raw = (raw or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return None
    if "://" not in raw:
        if re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)", raw):      # javascript:..., mailto:..., data:... are not web addresses
            return None
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname or len(raw) > MAX_URL:
        return None
    return raw


def host_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _public(host: str) -> bool:
    """False for a name that points at this computer or a private network (so a saved link cannot be used to probe them)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return bool(infos)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch_page(url: str) -> str | None:
    """The first part of the page's HTML, following up to a few redirects that each point somewhere public. None on any problem."""
    for _ in range(MAX_REDIRECTS + 1):
        host = urllib.parse.urlsplit(url).hostname
        if not host or not _public(host):
            return None
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        try:
            with _opener.open(request, timeout=TIMEOUT) as response:
                if "html" not in (response.headers.get("Content-Type") or "").lower():
                    return None
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(MAX_BYTES).decode(charset, errors="replace")
        except urllib.error.HTTPError as err:
            location = err.headers.get("Location") if err.code in (301, 302, 303, 307, 308) else None
            if not location:
                return None
            url = urllib.parse.urljoin(url, location)
            if clean_url(url) is None:
                return None
        except (OSError, ValueError):
            return None
    return None


class _Reader(HTMLParser):
    """Collects the description meta tags, the title and the first real paragraph."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.paragraph = ""
        self._in = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description", "twitter:description") and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag in ("script", "style", "noscript", "nav", "footer", "header"):
            self._skip += 1
        elif tag in ("title", "p") and not self._skip:
            self._in = tag

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "p" and not self.paragraph:
            text = " ".join(data.split())
            if len(text) >= 40:
                self.paragraph = text


def _shorten(text: str) -> str:
    text = " ".join(html.unescape(text).split())
    if len(text) <= SHORT:
        return text
    cut = text[:SHORT].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def describe(page_html: str) -> str | None:
    """A short description of a page: its own description tag, else its first real paragraph, else its title."""
    reader = _Reader()
    try:
        reader.feed(page_html)
    except Exception:                                  # noqa: BLE001  broken markup: use what was read so far
        pass
    for candidate in (reader.meta.get("description"), reader.meta.get("og:description"), reader.meta.get("twitter:description"),
                      reader.paragraph, reader.title):
        text = _shorten(candidate or "")
        if len(text) >= 8:
            return text
    return None


def fetch_description(url: str) -> str | None:
    page = fetch_page(url)
    return describe(page) if page else None


_NON_WORD = re.compile(r"\s+")


def sort_key(name: str) -> str:
    return _NON_WORD.sub(" ", name).strip().casefold()


def grouped(links) -> list[tuple[str, list]]:
    """The links under their type, in the order TYPES lists them (types with no links left out), each section A to Z by site name."""
    out = []
    for kind in TYPES:
        rows = sorted((l for l in links if l.link_type == kind), key=lambda l: sort_key(l.name))
        if rows:
            out.append((kind, rows))
    return out
