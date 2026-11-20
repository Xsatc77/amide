"""Checks for everything that comes from outside the watcher: Telegram file names and ids, the Amide address."""

import ipaddress
import re
import unicodedata

MAX_NAME_BYTES = 120
_EXTENSIONS = {"pdf": (".pdf",), "image": (".jpg", ".jpeg", ".png"), "xlsx": (".xlsx",)}
_DEFAULT_EXTENSION = {"pdf": ".pdf", "image": ".jpg", "xlsx": ".xlsx"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
_BAD_CHARS = re.compile(r'[<>:"|?*]')
_CHAT_ID = re.compile(r"-?[0-9]{1,20}")
_TOPIC_ID = re.compile(r"[0-9]{1,12}")


def valid_chat_id(value) -> bool:
    return isinstance(value, str) and _CHAT_ID.fullmatch(value) is not None


def valid_topic_id(value) -> bool:
    return isinstance(value, str) and _TOPIC_ID.fullmatch(value) is not None


def _kind_of(name: str) -> str | None:
    lower = name.lower()
    return next((kind for kind, exts in _EXTENSIONS.items() if lower.endswith(exts)), None)


def safe_filename(name, kind: str | None = None) -> str:
    """A plain file name that is safe to show, store or send on: no folders, no control or direction-changing characters, no reserved
    Windows names, at most MAX_NAME_BYTES bytes, and an extension that matches the kind of file Telegram reported (pdf, image, xlsx).
    The watcher never uses it as a local path (queue files are numbered); this protects whatever receives the name."""
    text = unicodedata.normalize("NFC", name if isinstance(name, str) else "")
    text = text.replace("\\", "/").split("/")[-1]
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in "CZ" or ch == " ")
    text = _BAD_CHARS.sub("_", text)
    while ".." in text:
        text = text.replace("..", ".")
    text = text.strip(" .").lstrip("-~. ")
    kind = kind if kind in _EXTENSIONS else _kind_of(text)
    extension = ""
    if kind is not None:
        allowed = _EXTENSIONS[kind]
        matched = next((e for e in allowed if text.lower().endswith(e)), None)
        if matched:
            text, extension = text[:-len(matched)], text[-len(matched):]
        else:
            extension = _DEFAULT_EXTENSION[kind]
    stem = text.strip(" .") or "file"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    room = MAX_NAME_BYTES - len(extension.encode("utf-8"))
    stem = stem.encode("utf-8")[:room].decode("utf-8", errors="ignore").strip(" .") or "file"
    return stem + extension


def private_host(host: str) -> bool:
    """True for a loopback, private or link-local address and for names that cannot be on the public internet (a bare container
    name such as `amide`, localhost, .local, .internal, .lan)."""
    host = host.strip("[]").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
    return address.is_loopback or address.is_private or address.is_link_local


def write_private(path, data: bytes | str) -> None:
    """Write a file atomically (a temporary sibling, flushed, then renamed over) and readable by the owner only (0600 where the OS has it)."""
    import os
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
