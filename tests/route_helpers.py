"""Walk every route of a FastAPI app, including routers nested by include_router, in one flat list.

FastAPI 0.14x keeps included routers as wrapper objects (`original_router` plus an `include_context` with the prefix) instead of
copying their routes; older versions copy them. Both shapes are handled, so a new FastAPI cannot silently hide routes from the
security tests (the route inventory test also asserts the count is not suspiciously small)."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteInfo:
    path: str
    methods: tuple          # sorted, HEAD/OPTIONS left out
    mounted: bool = False   # a Mount (static files, sub-app): its children are not enumerated
    endpoint: object = None

    @property
    def int_params(self) -> list[str]:
        """Names of path parameters declared as integers ({id} or {id:int} or an `: int` annotated endpoint argument)."""
        names = re.findall(r"{(\w+)(?::\w+)?}", self.path)
        if self.endpoint is None:
            return []
        hints = getattr(self.endpoint, "__annotations__", {})
        return [n for n in names if hints.get(n) is int]


def iter_routes(routes, prefix: str = ""):
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            ctx = getattr(r, "include_context", None)
            yield from iter_routes(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
            continue
        path = prefix + (getattr(r, "path", "") or "")
        sub = getattr(r, "routes", None)
        if type(r).__name__ == "Mount":
            yield RouteInfo(path, (), True)
            continue
        methods = tuple(sorted(m for m in (getattr(r, "methods", None) or ()) if m not in ("HEAD", "OPTIONS")))
        if sub and not methods:
            yield from iter_routes(sub, path)
            continue
        yield RouteInfo(path, methods, False, getattr(r, "endpoint", None))


def all_routes(app) -> list[RouteInfo]:
    return list(iter_routes(app.routes))
