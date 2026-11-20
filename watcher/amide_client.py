"""Talking to Amide's token API. Never logs or raises with the token, message text or file contents."""

from dataclasses import dataclass

import httpx

from watcher.ports import Payload, Topic
from watcher.safety import safe_filename, valid_chat_id, valid_topic_id

MAX_RETRY_AFTER = 3600
MAX_TITLE = 200
MAX_TEXT = 8000


class AmideAuthError(RuntimeError):
    """Amide refused the token (wrong, revoked, or its owner is no longer an administrator)."""


class AmideUnavailable(RuntimeError):
    """Amide could not be reached or is busy; try again later. `retry_after` is the wait Amide asked for, in seconds (0: none)."""

    def __init__(self, message: str = "", retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Source:
    chat_id: str
    topics: list[str] | None = None        # None: follow the whole group; otherwise only these topic ids


@dataclass
class Delivery:
    kind: str                       # delivered | retry | drop | auth
    detail: str = ""
    retry_after: int = 0


def make_http(config) -> httpx.AsyncClient:
    # Never follow a redirect: the token must only ever go to the configured address. TLS certificates are verified (httpx default, stated here).
    return httpx.AsyncClient(base_url=config.amide_url, timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False, verify=True)


def _retry_after(response: httpx.Response) -> int:
    try:
        return max(0, min(int(response.headers.get("retry-after", "0")), MAX_RETRY_AFTER))
    except ValueError:
        return 0


class AmideClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            response = await self.http.request(method, url, headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise AmideUnavailable(type(exc).__name__) from None
        if response.status_code in (401, 403):
            raise AmideAuthError("Amide did not accept the token")
        if 300 <= response.status_code < 400:
            raise AmideUnavailable(f"HTTP {response.status_code} redirect refused")        # the token is never sent anywhere else
        if response.status_code == 429 or response.status_code >= 500:
            raise AmideUnavailable(f"HTTP {response.status_code}", _retry_after(response))
        return response

    async def register(self, chat_id: str, title: str, topics: list[Topic] | None = None) -> None:
        body = {"title": title[:MAX_TITLE]}
        if topics:
            body["topics"] = [{"id": t.topic_id, "title": t.title[:MAX_TITLE]} for t in topics if valid_topic_id(t.topic_id)][:200]
        await self._call("PUT", f"/api/ingest/sources/{chat_id}", json=body)

    async def list_sources(self) -> list[Source]:
        response = await self._call("GET", "/api/ingest/sources")
        if response.status_code != 200:
            raise AmideUnavailable(f"HTTP {response.status_code}")
        out = []
        rows = response.json()
        for row in rows if isinstance(rows, list) else []:
            topics = row.get("topics") if isinstance(row, dict) else None       # an older Amide sends no topics: the whole group
            chat_id = row.get("chat_id") if isinstance(row, dict) else row
            if not valid_chat_id(chat_id if isinstance(chat_id, str) else None):        # these ids go into URL paths: only digits are allowed
                continue
            out.append(Source(chat_id, None if not isinstance(topics, list) else [t for t in map(str, topics) if valid_topic_id(t)]))
        return out

    async def report_state(self, chat_id: str, state: str, reason: str | None = None) -> bool:
        try:
            response = await self._call("POST", f"/api/ingest/sources/{chat_id}/state", json={"state": state, "reason": reason})
        except AmideUnavailable:
            return False
        return response.status_code == 200

    async def send(self, payload: Payload) -> Delivery:
        data = {"chat_id": payload.chat_id, "message_id": payload.message_id, "date": payload.date, "text": payload.text[:MAX_TEXT]}
        if payload.album_id:
            data["album_id"] = payload.album_id
        if payload.topic_id:
            data["topic_id"] = payload.topic_id
            if payload.topic_title:
                data["topic_title"] = payload.topic_title[:MAX_TITLE]
        files = [("files", (safe_filename(name), content, "application/octet-stream")) for name, content in payload.files] or None
        try:
            response = await self._call("POST", "/api/ingest/messages", data=data, files=files)
        except AmideAuthError:
            return Delivery("auth", "token refused")
        except AmideUnavailable as exc:
            return Delivery("retry", str(exc), exc.retry_after)
        if response.status_code == 200:
            return Delivery("delivered")
        return Delivery("drop", f"HTTP {response.status_code}")
