"""Talking to Amide's token API. Never logs or raises with the token, message text or file contents."""

from dataclasses import dataclass

import httpx

from watcher.ports import Payload, Topic


class AmideAuthError(RuntimeError):
    """Amide refused the token (wrong, revoked, or its owner is no longer an administrator)."""


class AmideUnavailable(RuntimeError):
    """Amide could not be reached or is busy; try again later."""


@dataclass
class Source:
    chat_id: str
    topics: list[str] | None = None        # None: follow the whole group; otherwise only these topic ids


@dataclass
class Delivery:
    kind: str                       # delivered | retry | drop | auth
    detail: str = ""


def make_http(config) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.amide_url, timeout=60.0)


class AmideClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            response = await self.http.request(method, url, headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise AmideUnavailable(type(exc).__name__) from None
        if response.status_code == 401:
            raise AmideAuthError("Amide did not accept the token")
        if response.status_code == 429 or response.status_code >= 500:
            raise AmideUnavailable(f"HTTP {response.status_code}")
        return response

    async def register(self, chat_id: str, title: str, topics: list[Topic] | None = None) -> None:
        body = {"title": title[:200]}
        if topics:
            body["topics"] = [{"id": t.topic_id, "title": t.title} for t in topics][:200]
        await self._call("PUT", f"/api/ingest/sources/{chat_id}", json=body)

    async def list_sources(self) -> list[Source]:
        response = await self._call("GET", "/api/ingest/sources")
        response.raise_for_status()
        out = []
        for row in response.json():
            topics = row.get("topics") if isinstance(row, dict) else None       # an older Amide sends no topics: the whole group
            out.append(Source(str(row["chat_id"] if isinstance(row, dict) else row), None if topics is None else [str(t) for t in topics]))
        return out

    async def report_state(self, chat_id: str, state: str, reason: str | None = None) -> bool:
        try:
            response = await self._call("POST", f"/api/ingest/sources/{chat_id}/state", json={"state": state, "reason": reason})
        except AmideUnavailable:
            return False
        return response.status_code == 200

    async def send(self, payload: Payload) -> Delivery:
        data = {"chat_id": payload.chat_id, "message_id": payload.message_id, "date": payload.date, "text": payload.text}
        if payload.album_id:
            data["album_id"] = payload.album_id
        if payload.topic_id:
            data["topic_id"] = payload.topic_id
            if payload.topic_title:
                data["topic_title"] = payload.topic_title
        files = [("files", (name, content, "application/octet-stream")) for name, content in payload.files] or None
        try:
            response = await self._call("POST", "/api/ingest/messages", data=data, files=files)
        except AmideAuthError:
            return Delivery("auth", "token refused")
        except AmideUnavailable as exc:
            return Delivery("retry", str(exc))
        if response.status_code == 200:
            return Delivery("delivered")
        return Delivery("drop", f"HTTP {response.status_code}")
