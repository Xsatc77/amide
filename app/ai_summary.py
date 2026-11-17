"""The AI agent's one-page summary of a Progress Review.

Only the numbers on the Progress Review page for the chosen date range are sent: the same results, adherence, intake, journal
counts, labs, flags and gaps the page shows. No name, e-mail, notes, photos or anything from other pages. The call is made only
when the person presses the button, to the agent they picked in Settings (Claude, OpenAI, or a server of their own such as
Ollama or LM Studio) with their own key."""

import json
import urllib.error
import urllib.request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgressSummary, User, naive_utcnow

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "base": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "key": True},
    "openai": {"label": "OpenAI", "base": "https://api.openai.com/v1", "model": "", "key": True},
    "local": {"label": "My own server (Ollama, LM Studio, or any OpenAI-compatible)", "base": "", "model": "", "key": False},
}
TIMEOUT_SECONDS = 180

SYSTEM = (
    "You write a one-page progress summary for one person from the numbers they give you. Be plain and no-nonsense: no greeting, "
    "no cheering, no filler, no hedging boilerplate. Use only the numbers provided; never invent or estimate a value. Where the data "
    "does not establish something, say 'Not established'. Do not diagnose, and do not give medical advice or tell them to change a dose; "
    "you may point at what the numbers show and what looks worth checking. Keep it under 350 words, as plain text (no markdown symbols), "
    "with these short headed parts: Headline (two sentences at most), What Moved, Adherence And Intake, Worth Checking, Gaps In The Data."
)


class AiError(Exception):
    """Something the person can act on: a missing setting, a refused key, an unreachable server. Never contains the key."""


def configured(user: User) -> bool:
    return bool(user.ai_provider in PROVIDERS and _model(user) and (user.ai_api_key or not PROVIDERS[user.ai_provider]["key"])
                and (_base(user)))


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
            out.append(f"- {lab['marker']}: {_num(lab['value'])} {lab['unit']}{rng}{' OUTSIDE RANGE' if lab['out_of_range'] else ''}, drawn {lab['drawn']:%Y-%m-%d}")
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
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise AiError("The agent refused the key. Check it in Settings.") from None
        if err.code == 404:
            raise AiError("The agent does not know that model or address. Check the model name and server address in Settings.") from None
        if err.code == 429:
            raise AiError("The agent says you are out of quota or sending too fast. Try again later.") from None
        raise AiError(f"The agent answered with an error ({err.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AiError("The agent could not be reached. Check the server address in Settings and that it is running.") from None
    except ValueError:
        raise AiError("The agent's answer could not be read.") from None


def ask(user: User, prompt: str) -> str:
    """Send the facts to the person's chosen agent and return its text."""
    if not configured(user):
        raise AiError("Choose an AI agent in Settings first.")
    base, model = _base(user), _model(user)
    if user.ai_provider == "anthropic":
        data = post_json(f"{base}/v1/messages", {"x-api-key": user.ai_api_key, "anthropic-version": "2023-06-01"},
                         {"model": model, "max_tokens": 1500, "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]})
        text = "".join(block.get("text", "") for block in data.get("content", []) if isinstance(block, dict) and block.get("type") == "text")
    else:
        headers = {"Authorization": f"Bearer {user.ai_api_key}"} if user.ai_api_key else {}
        data = post_json(f"{base}/chat/completions", headers,
                         {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]})
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = ""
    text = text.strip()
    if not text:
        raise AiError("The agent sent back nothing. Try again, or check the model name in Settings.")
    return text


def summarize(session: Session, user: User, r: dict, u) -> ProgressSummary:
    """Ask the agent about this page's data and keep the answer for this date range, replacing an earlier one."""
    text = ask(user, "Here are the numbers from my Progress Review. Write the one-page summary.\n\n" + facts(r, u))
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
