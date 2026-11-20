"""The one guarded way Amide makes an outbound web request (link previews, AI agents, ntfy, USDA).

The address is checked and the host is resolved exactly ONCE; the connection then goes to that resolved address while the original name is
still sent as the Host header and the TLS server name. A name that answers differently a moment later (DNS rebinding) therefore cannot move a
request onto an address that was already approved. Only http and https, no user:password@ in the address, redirects are never followed here
(the caller decides, and re-checks), and a response is cut off at a size cap."""

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

MAX_RESPONSE_BYTES = 4_194_304
_METADATA = tuple(ipaddress.ip_network(n) for n in ("169.254.0.0/16", "100.100.100.200/32", "fd00:ec2::254/128", "0.0.0.0/8"))
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class UnsafeURL(OSError):
    """The address is not one Amide will contact. The message names the problem, never the address's query string or key."""


class Target(NamedTuple):
    scheme: str
    host: str
    port: int
    path: str            # path plus query, as sent on the request line
    ip: str              # the one resolved address the connection will use
    is_global: bool


def _embedded(ip):
    """The IPv4 address hidden inside an IPv4-mapped or NAT64 IPv6 address, else None."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _refusal(ip, allow_private: bool, allow_loopback: bool) -> str | None:
    inner = _embedded(ip)
    if inner is not None:
        return _refusal(inner, allow_private, allow_loopback)
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved or any(ip in n for n in _METADATA if n.version == ip.version):
        return "that address is not allowed"
    if ip.is_loopback:
        return None if allow_loopback else "an address on this computer is not allowed"
    if not ip.is_global and not allow_private:
        return "an address on a private network is not allowed"
    return None


def _split(url: str):
    if not isinstance(url, str) or not url or any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in url):
        raise UnsafeURL("that is not a usable web address")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("only http and https addresses are allowed")
    if "@" in parts.netloc:
        raise UnsafeURL("an address with a user name or password in it is not allowed")
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        raise UnsafeURL("that is not a usable web address") from None
    if not host:
        raise UnsafeURL("that is not a usable web address")
    return parts, host, port or (443 if parts.scheme == "https" else 80)


def check_url(url: str, *, allow_private: bool = False, allow_loopback: bool = False) -> Target:
    """Validate the address and resolve it once. Every address the name resolves to must be allowed (so a name that is public first and
    private second is refused); the first one is the address the connection will use. Raises UnsafeURL."""
    parts, host, port = _split(url)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise UnsafeURL("the host name could not be found") from None
    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            raise UnsafeURL("the host name gave an unusable address") from None
        if (why := _refusal(ip, allow_private, allow_loopback)):
            raise UnsafeURL(why)
        ips.append(ip)
    if not ips:
        raise UnsafeURL("the host name could not be found")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    first = ips[0]
    return Target(parts.scheme, host, port, path, str(first), first.is_global)


def _pinned(conn, ip: str, port: int):
    def create(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        return socket.create_connection((ip, port), timeout, source_address)
    conn._create_connection = create
    return conn


class PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to a fixed address; the Host header still carries the name."""


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a fixed address; the TLS server name and certificate check still use the name."""


class Response:
    """What a request returned: .status, .headers, and read() that never returns more than the size cap."""

    def __init__(self, raw, conn, max_bytes: int):
        self._raw, self._conn, self._left = raw, conn, max_bytes
        self.status, self.headers = raw.status, raw.msg

    def read(self, amt: int | None = None) -> bytes:
        want = self._left + 1 if amt is None else min(amt, self._left + 1)
        data = self._raw.read(want)
        if len(data) > self._left:
            raise UnsafeURL("the answer was larger than allowed")
        self._left -= len(data)
        return data

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def safe_open(request, timeout: float, allow_private: bool = False, *, allow_loopback: bool = False,
              max_bytes: int = MAX_RESPONSE_BYTES, https_for_public: bool = False) -> Response:
    """Send a urllib Request through the guard. A 3xx or 4xx/5xx answer raises urllib.error.HTTPError (redirects are never followed here).
    With https_for_public, plain http is accepted only for a host on a private network."""
    if isinstance(request, str):
        request = urllib.request.Request(request)
    target = check_url(request.full_url, allow_private=allow_private, allow_loopback=allow_loopback)
    if target.scheme == "http" and https_for_public and target.is_global:
        raise UnsafeURL("a server on the internet must use https")
    if target.scheme == "https":
        conn = PinnedHTTPSConnection(target.host, target.port, timeout=timeout, context=ssl.create_default_context())
    else:
        conn = PinnedHTTPConnection(target.host, target.port, timeout=timeout)
    _pinned(conn, target.ip, target.port)
    headers = {k: v for k, v in request.header_items()}
    headers.setdefault("Accept-Encoding", "identity")
    headers["Connection"] = "close"
    try:
        conn.request(request.get_method(), target.path, request.data, headers)
        raw = conn.getresponse()
    except Exception:
        conn.close()
        raise
    response = Response(raw, conn, max_bytes)
    if raw.status >= 300:
        shown = urllib.parse.urlsplit(request.full_url)
        err = urllib.error.HTTPError(f"{shown.scheme}://{shown.netloc}{shown.path}", raw.status, raw.reason, raw.msg, None)
        conn.close()
        raise err
    return response
