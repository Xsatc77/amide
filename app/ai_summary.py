"""The AI agent's one-page summary of a Progress Review.

Only the numbers on the Progress Review page for the chosen date range are sent: the same results, adherence, intake, journal
counts, labs, flags and gaps the page shows. Plus the notes the person keeps on that page for the summary to take into account. No name, e-mail, photos or anything from other pages. The call is made only
when the person presses the button, to the agent they picked in Settings (Claude, OpenAI, or a server of their own such as
Ollama or LM Studio) with their own key."""

import html
import contextvars
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import netguard
from app.models import ProgressSummary, User, naive_utcnow

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "base": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "key": True},
    "openai": {"label": "OpenAI", "base": "https://api.openai.com/v1", "model": "", "key": True},
    "local": {"label": "My own server (Ollama, LM Studio, or any OpenAI-compatible)", "base": "", "model": "", "key": False},
}
TIMEOUT_SECONDS = 180
MAX_ANSWER_BYTES = 2_097_152
_HOSTED_HOSTS = {urllib.parse.urlsplit(p["base"]).hostname for p in PROVIDERS.values() if p["base"]}     # Claude and OpenAI: fixed public https hosts

SYSTEM = (
    "You are a blunt, practical analyst writing a one-page brief for one person about ONE specific date range. They can already read every "
    "number on the page, so never list or restate the data. Your job is judgment: what the numbers mean together, what trade-offs or risks "
    "they reveal, and what to do or check next.\n\n"
    "Rules:\n"
    "- Judge everything against the period you are given (its start, end and length in days). Use pace per week over that period instead of "
    "raw totals, say whether the pace is fast, slow or fine for that span, and say when something is too short or too old to conclude from "
    "(for example a lab drawn once at the start of the period).\n"
    "- Use a number only when it proves a point; about a dozen in total at most.\n"
    "- Use only the numbers provided. Never invent or estimate a value. If the data does not establish something, say so in one line.\n"
    "- The person's own notes are context to weigh: goals, medicines, schedule, questions. Use them; do not repeat them back.\n"
    "- Do not diagnose or tell them to change a dose. You may say what looks worth raising with a clinician or checking with a repeat measurement.\n"
    "- No greeting, no cheering, no filler.\n\n"
    "Format exactly, as plain text with these lines and nothing else (the app shows '## ' lines as headings and '- ' lines as bullets):\n"
    "## Verdict\n(one or two sentences: is this period going the right way, and the single most important thing)\n"
    "## What It Means\n- three to five bullets that connect the measures, name trade-offs, and set the pace against the period\n"
    "## Do Next\n- two to four specific actions or checks, most important first\n"
    "## Not Established\n- one to three bullets on what this data cannot tell\n"
    "Under 250 words in total."
)


class AiError(Exception):
    """Something the person can act on: a missing setting, a refused key, an unreachable server. Never contains the key."""


def configured(user: User) -> bool:
    return bool(user.ai_provider in PROVIDERS and _model(user) and (user.ai_api_key or not PROVIDERS[user.ai_provider]["key"])
                and (_base(user)))


def base_url_error(base: str) -> str | None:
    """Why a "My own server" address is not acceptable, else None. No query string or fragment (so nothing can swallow the fixed path Amide adds),
    no user:password@, and a real http or https host."""
    if not base or any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in base):
        return "Use a full address starting with http:// or https://."
    if "?" in base or "#" in base:
        return "The address cannot contain a question mark or a hash."
    try:
        parts = urllib.parse.urlsplit(base)
        host, _ = parts.hostname, parts.port
    except ValueError:
        return "Use a full address starting with http:// or https://."
    if parts.scheme not in ("http", "https") or not host:
        return "Use a full address starting with http:// or https://."
    if "@" in parts.netloc:
        return "The address cannot contain a user name or password."
    return None


def _local_hosts_allowed(host: str) -> bool:
    """AMIDE_AI_LOCAL_ALLOWED_HOSTS (comma separated names or networks): when set, My own server must be exactly one of them."""
    entries = [e.strip().lower() for e in os.environ.get("AMIDE_AI_LOCAL_ALLOWED_HOSTS", "").split(",") if e.strip()]
    if not entries:
        return True
    host = host.lower().strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for entry in entries:
        if entry == host:
            return True
        if ip is not None and "/" in entry:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


# Set around a send for an account that may not reach private addresses (not an administrator, and no allowed list is configured).
_deny_private = contextvars.ContextVar("amide_ai_deny_private", default=False)
UNREACHABLE = "The agent could not be reached."
ASK_ADMIN = "Ask the administrator to allow this address with AMIDE_AI_LOCAL_ALLOWED_HOSTS."


def _allowed_list_set() -> bool:
    return any(e.strip() for e in os.environ.get("AMIDE_AI_LOCAL_ALLOWED_HOSTS", "").split(","))


def private_host(host: str) -> bool:
    """True when the host is, or resolves to, anything but a public internet address (a LAN, this computer, a link-local or reserved range).
    A name with no dot, or one that cannot be resolved, counts as private: only a public name is vouched for."""
    host = (host or "").lower().strip("[]")
    try:
        return not ipaddress.ip_address(host.split("%")[0]).is_global
    except ValueError:
        pass
    if "." not in host or host.endswith((".local", ".lan", ".home", ".internal", ".localdomain", ".localhost")):
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return True
    try:
        return any(not ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos) or not infos
    except ValueError:
        return True


def address_problem(base: str, user: User) -> str | None:
    """Why this account may not use this "My own server" address, else None. With AMIDE_AI_LOCAL_ALLOWED_HOSTS set, only those hosts;
    without it, a private-network address only for an administrator (a public host is open to every account)."""
    host = urllib.parse.urlsplit(base).hostname or ""
    if _allowed_list_set():
        return None if _local_hosts_allowed(host) else ASK_ADMIN
    if not getattr(user, "is_admin", False) and private_host(host):
        return ASK_ADMIN
    return None


def _loopback_ok() -> bool:
    return os.environ.get("AMIDE_AI_ALLOW_LOOPBACK", "") == "1"


def _model(user: User) -> str:
    return (user.ai_model or PROVIDERS.get(user.ai_provider or "", {}).get("model") or "").strip()


def _base(user: User) -> str:
    """Claude and OpenAI always go to their own address; only "My own server" uses the address the person typed (a browser can autofill that box with Amide's own address)."""
    own = (user.ai_base_url or "").strip().rstrip("/") if user.ai_provider == "local" else ""
    return own or (PROVIDERS.get(user.ai_provider or "", {}).get("base") or "").strip().rstrip("/")


def _num(value, places: int = 1) -> str:
    return f"{value:,.{places}f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"


def facts(r: dict, u) -> str:
    """The page's data as plain lines, in display units. This is everything the agent is given."""
    out = [f"Period: {r['start']:%Y-%m-%d} to {r['end']:%Y-%m-%d} ({r['days']} days). Units: {u.weight_label}, {u.length_label}."]
    if r["body"]:
        out.append("Body measurements (first, latest, change, number of readings):")
        for b in r["body"]:
            pct = f", {b['pct']:+.1f}%" if b.get("pct") is not None else ""
            out.append(f"- {b['label']}: {_num(b['first'])} -> {_num(b['last'])} {b['unit']}, change {b['change']:+.1f}{pct}, {b['readings']} readings")
    else:
        out.append("Body measurements: none in this period.")
    weight = next((b for b in r["body"] if b["key"] == "weight_lbs"), None)
    if weight and r["days"] >= 7:
        out.append(f"Weight pace over this period: {weight['change'] / (r['days'] / 7):+.1f} {weight['unit']} per week on average across {r['days']} days.")
    if len(r["weeks"]) > 1:
        out.append("Average weight by week:")
        for w in r["weeks"]:
            step = f", change {u.weight(w['change']):+.1f} ({w['pct']:+.1f}%)" if "change" in w else ""
            out.append(f"- week of {w['start']:%Y-%m-%d}: {u.weight(w['weight']):.1f} {u.weight_label}{step}")
    a = r["adherence"]
    out.append(f"Adherence: {a['workouts']} workouts logged; {a['doses_logged']} doses logged and {a['doses_skipped']} skipped; "
               f"food logged on {a['food_days']} of {a['days']} days; water logged on {a['water_days']} of {a['days']} days.")
    i = r["intake"]
    if i:
        parts = []
        if i.get("calories") is not None:
            parts.append(f"{i['calories']} kcal, {i['protein_g']} g protein, {i['carb_g']} g carbs, {i['fat_g']} g fat, {i['fiber_g']} g fiber")
        if i.get("water_oz") is not None:
            parts.append(f"water {u.volume(i['water_oz'])} {u.volume_label}")
        out.append("Daily intake averages over days with something logged: " + "; ".join(parts) + ".")
    j = r["journal"]
    if j["entries"]:
        line = f"Journal: {j['entries']} days"
        if j["rated_days"]:
            line += f"; poor sleep on {j['sleep_low_days']} of {j['rated_days']} rated nights"
        if j["side_effects"]:
            line += "; side effects logged: " + ", ".join(f"{name} on {n} day{'' if n == 1 else 's'}" for name, n in j["side_effects"])
        out.append(line + ".")
    if r["labs"]:
        out.append("Labs in this period (latest per marker):")
        for lab in r["labs"]:
            rng = f" (range {'' if lab['low'] is None else _num(lab['low'])}-{'' if lab['high'] is None else _num(lab['high'])})" if lab["low"] is not None or lab["high"] is not None else ""
            out.append(f"- {lab['marker']}: {_num(lab['value'])} {lab['unit']}{rng}{' OUTSIDE RANGE' if lab['out_of_range'] else ''}, drawn {lab['drawn']:%Y-%m-%d} (day {(lab['drawn'] - r['start']).days + 1} of {r['days']})")
    if r["flags"]:
        out.append("Flags raised by fixed rules:")
        out.extend(f"- {f['text']} (rule: {f['rule']})" for f in r["flags"])
    out.append("Not established:")
    out.extend(f"- {t}" for t in r["unknowns"])
    return "\n".join(out)


def post_json(url: str, headers: dict, body: dict) -> dict:
    """POST JSON and return the decoded answer. The one place this module touches the network (tests replace it)."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    hosted = urllib.parse.urlsplit(url).hostname in _HOSTED_HOSTS
    try:
        if hosted and not url.startswith("https://"):
            raise netguard.UnsafeURL("hosted agents use https")
        # Claude and OpenAI: public addresses only. My own server: a private network is fine (a LAN Ollama), this computer only on request.
        deny = _deny_private.get()
        with netguard.safe_open(request, TIMEOUT_SECONDS, allow_private=not hosted and not deny, allow_loopback=not hosted and not deny and _loopback_ok(),
                                max_bytes=MAX_ANSWER_BYTES, deadline=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise AiError("The agent refused the key. Check it in Settings.") from None
        if err.code == 404:
            raise AiError("The agent does not know that model or address. Check the model name and server address in Settings.") from None
        if err.code == 429:
            raise AiError("The agent says you are out of quota or sending too fast. Try again later.") from None
        raise AiError(f"The agent answered with an error ({err.code}).") from None
    except (netguard.UnsafeURL, urllib.error.URLError, TimeoutError, OSError):
        raise AiError(UNREACHABLE) from None      # one answer for a blocked address, a closed port and a timeout, so the message cannot be used to scan a network
    except ValueError:
        raise AiError("The agent's answer could not be read.") from None


def ask(user: User, prompt: str) -> str:
    """Send the facts to the person's chosen agent and return its text."""
    token = _deny_private.set(user.ai_provider == "local" and not getattr(user, "is_admin", False) and not _allowed_list_set())
    try:
        return _ask(user, prompt)
    finally:
        _deny_private.reset(token)


def _ask(user: User, prompt: str) -> str:
    if not configured(user):
        raise AiError("Choose an AI agent in Settings first.")
    base, model = _base(user), _model(user)
    if user.ai_provider == "local":
        if base_url_error(base):
            raise AiError("The server address in Settings is not valid. Check it.")
        if address_problem(base, user):
            raise AiError(ASK_ADMIN)
    if user.ai_provider == "anthropic":
        data = post_json(f"{base}/v1/messages", {"x-api-key": user.ai_api_key, "anthropic-version": "2023-06-01"},
                         {"model": model, "max_tokens": 2500, "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]})
        text = "".join(block.get("text", "") for block in data.get("content", []) if isinstance(block, dict) and block.get("type") == "text")
        cut = data.get("stop_reason") == "max_tokens"
    else:
        headers = {"Authorization": f"Bearer {user.ai_api_key}"} if user.ai_api_key else {}
        data = post_json(f"{base}/chat/completions", headers,
                         {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]})
        try:
            text = data["choices"][0]["message"]["content"] or ""
            cut = data["choices"][0].get("finish_reason") == "length"
        except (KeyError, IndexError, TypeError):
            text, cut = "", False
    text = text.strip()
    if not text:
        raise AiError("The agent sent back nothing. Try again, or check the model name in Settings.")
    return text + ("\n\n- The agent's answer was cut off." if cut else "")


def summarize(session: Session, user: User, r: dict, u) -> ProgressSummary:
    """Ask the agent about this page's data and keep the answer for this date range, replacing an earlier one."""
    notes = (user.ai_notes or "").strip()
    text = ask(user, "Write the one-page brief for this period.\n\n"
                     + (f"My notes (context to weigh, not to repeat):\n{notes}\n\n" if notes else "My notes: none.\n\n")
                     + "The numbers from my Progress Review page:\n" + facts(r, u))
    row = session.scalar(select(ProgressSummary).where(ProgressSummary.owner_id == user.id, ProgressSummary.start_date == r["start"],
                                                       ProgressSummary.end_date == r["end"]))
    if row is None:
        row = ProgressSummary(owner_id=user.id, start_date=r["start"], end_date=r["end"])
        session.add(row)
    row.text, row.provider, row.model, row.created_at = text, user.ai_provider, _model(user), naive_utcnow()
    session.commit()
    return row


def saved(session: Session, uid: int, start, end) -> ProgressSummary | None:
    return session.scalar(select(ProgressSummary).where(ProgressSummary.owner_id == uid, ProgressSummary.start_date == start,
                                                        ProgressSummary.end_date == end))


def to_html(text: str) -> Markup:
    """The summary as safe HTML: '## ' lines are headings, '- ' lines bullets, **bold** is bold; everything else is escaped text."""
    out, bullets = [], False
    for line in text.splitlines():
        line = line.strip()
        item = line[2:] if line.startswith("- ") else None
        if bullets and item is None:
            out.append("</ul>")
            bullets = False
        if not line:
            continue
        body = item if item is not None else (line.lstrip("# ") if line.startswith("#") else line)
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(body))
        if item is not None:
            if not bullets:
                out.append("<ul>")
                bullets = True
            out.append(f"<li>{safe}</li>")
        elif line.startswith("#"):
            out.append(f"<h3>{safe}</h3>")
        else:
            out.append(f"<p>{safe}</p>")
    if bullets:
        out.append("</ul>")
    return Markup("".join(out))
