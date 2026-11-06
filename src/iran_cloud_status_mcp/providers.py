"""One adapter per provider: reads the provider's own status API and returns the same shapes for every provider.

Component: {name, group, status}. Incident: {id, title, status, severity, active, started, ended, components,
updates[{status, at, text}]} (updates newest first). Maintenance: {title, status, start, end, text}.
Status words: operational, degraded, partial_outage, major_outage, maintenance.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from datetime import datetime, timedelta, timezone

from .http import ApiError, fetch

ARVAN = "https://www.arvancloudstatus.ir"
HAMRAVESH = "https://api.hamraveshstatus.com/api/v1/status"
CHABOKAN = "https://status.chabokan.net/api"
PARSPACK = "https://status.parspack.com/"
MIHAN = "https://mihanstatus.com/uptime.php"

# `has`: which status tools return data for the provider (components, incidents, maintenance).
PROVIDERS = {
    "arvancloud": {
        "name": "ArvanCloud",
        "status_page": "https://status.arvancloud.ir",
        "has": ["components", "regions", "incidents", "maintenance"],
    },
    "hamravesh": {
        "name": "Hamravesh",
        "status_page": "https://hamraveshstatus.com",
        "has": ["components", "regions", "incidents", "maintenance"],
    },
    "chabokan": {
        "name": "Chabokan",
        "status_page": "https://status.chabokan.net",
        "has": ["components", "regions", "incidents", "maintenance"],
    },
    "parspack": {
        "name": "ParsPack",
        "status_page": "https://status.parspack.com",
        "has": ["incidents"],
        "note": "Announcement board only: incidents and notices with affected product types, no per-service status.",
    },
    "mihanwebhost": {
        "name": "Mihan WebHost",
        "status_page": "https://mihanstatus.com",
        "has": ["components", "regions"],
        "note": "Up/down of each hosting server (live monitor); no incident history.",
    },
}

ARVAN_COMPONENT = {1: "operational", 2: "degraded", 3: "partial_outage", 4: "major_outage"}
ARVAN_UPDATE = {1: "investigating", 2: "identified", 3: "monitoring", 4: "resolved"}
HAMRAVESH_STATUS = {"degraded_performance": "degraded"}
CHABOKAN_STATUS = {"down": "major_outage", "outage": "major_outage", "partial": "partial_outage"}


def when(s: str | None) -> datetime | None:
    """Parse ISO or 'YYYY-MM-DD HH:MM:SS'; a time without zone is read as UTC."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)  # noqa: UP017


def tehran(s: str) -> str:
    """ArvanCloud prints 'YYYY-MM-DD HH:MM:SS' in Iran time (checked against its own 'N days ago' texts)."""
    return s.replace(" ", "T") + "+03:30"


def cutoff(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)  # noqa: UP017


def _arvan_get(path: str, **params):
    return fetch(f"{ARVAN}/api/{path}", params, ttl=60)


async def arvan_components() -> dict:
    j = await _arvan_get("components", per_page=100, include="group")
    groups = {g["id"]: g["attributes"]["name"].strip() for g in j.get("included", []) if g["type"] == "componentGroups"}
    comps = []
    for c in j["data"]:
        a = c["attributes"]
        if not a.get("enabled", True):
            continue
        gid = ((c.get("relationships") or {}).get("group") or {}).get("data") or {}
        comps.append(
            {
                "name": a["name"].strip(),
                "group": groups.get(gid.get("id")),
                "status": ARVAN_COMPONENT.get(a["status"]["value"], a["status"]["human"].lower()),
            }
        )
    top = (await _arvan_get("status"))["data"]
    return {"overall": top["status"], "message": top["message"], "components": comps}


def _arvan_incident(d: dict, inc: dict) -> dict:
    a = d["attributes"]
    rel = d.get("relationships") or {}
    comps, impact = [], {}
    for x in (rel.get("components") or {}).get("data", []):
        c = inc[("components", x["id"])]["attributes"]
        comps.append(c["name"].strip())
        # The status page shows the status the incident sets on the component (pivot), not the component's own.
        if (v := ((c.get("pivot") or {}).get("component_status") or {}).get("value")) is not None:
            impact[comps[-1]] = ARVAN_COMPONENT.get(v, "degraded")
    ups = []
    for x in (rel.get("updates") or {}).get("data", []):
        u = inc[("updates", x["id"])]["attributes"]
        ups.append(
            {
                "status": ARVAN_UPDATE.get(u["status"]["value"], u["status"]["human"].lower()),
                "at": tehran(u["created"]["string"]),
                "text": (u.get("message") or "").strip(),
            }
        )
    ups.sort(key=lambda u: u["at"], reverse=True)
    status = ARVAN_UPDATE.get(a["status"]["value"], a["status"]["human"].lower())
    resolved = [u["at"] for u in ups if u["status"] == "resolved"]
    return {
        "id": int(d["id"]),
        "title": a["name"].strip(),
        "status": status,
        "active": status != "resolved",
        "started": tehran(a["occurred"]["string"]),
        "ended": resolved[0] if resolved and status == "resolved" else None,
        "components": comps,
        "impact": impact,
        "text": (a.get("message") or "").strip(),
        "updates": ups,
    }


async def arvan_incidents(limit: int = 100) -> list[dict]:
    j = await _arvan_get("incidents", per_page=min(limit, 100), include="updates,components")
    inc = {(x["type"], x["id"]): x for x in j.get("included", [])}
    out = [_arvan_incident(d, inc) for d in j["data"]]
    out.sort(key=lambda i: i["started"], reverse=True)
    return out


async def arvan_incident(incident_id: str) -> dict:
    j = await _arvan_get(f"incidents/{incident_id}", include="updates,components")
    return _arvan_incident(j["data"], {(x["type"], x["id"]): x for x in j.get("included", [])})


async def arvan_maintenance(past: bool = False) -> list[dict]:
    j = await _arvan_get("schedules", per_page=100)
    names = {0: "upcoming", 1: "in_progress", 2: "complete"}
    out = []
    for d in j["data"]:
        a = d["attributes"]
        s = names.get(a["status"]["value"], a["status"]["human"].lower())
        if s != "complete" or past:
            done = (a.get("completed") or {}).get("string")
            out.append(
                {
                    "title": a["name"].strip(),
                    "status": s,
                    "start": tehran(a["scheduled"]["string"]),
                    "end": tehran(done) if done else None,
                    "text": (a.get("message") or "").strip(),
                }
            )
    return out


async def hamravesh_components() -> dict:
    j = await fetch(f"{HAMRAVESH}/components/", ttl=60)
    comps = [
        {
            "name": c["persian_name"] or c["name"],
            "en": c["name"],
            "group": c.get("group"),
            "status": "maintenance"
            if c.get("is_under_maintenance")
            else HAMRAVESH_STATUS.get(c["status"], c["status"]),
        }
        for c in j["results"]
    ]
    return {"components": comps}


def _hamravesh_incident(d: dict) -> dict:
    ups = [
        {"status": r["tag"].lower(), "at": r["creation_time"], "text": r["text"].strip()} for r in d.get("reports", [])
    ]
    ups.sort(key=lambda u: u["at"], reverse=True)
    ended = re.sub(r"\.\d+", "", d["end_time"]) if d.get("end_time") else None
    return {
        "id": d["id"],
        "title": d["text"].strip(),
        "status": ups[0]["status"] if ups else ("resolved" if ended else "investigating"),
        "severity": HAMRAVESH_STATUS.get(d["effect"], d["effect"]),
        "active": not ended,
        "started": d["start_time"],
        "ended": ended,
        "components": [c["persian_name"] or c["name"] for c in d.get("components", [])],
        "updates": ups,
    }


async def _hamravesh_rows(since: datetime | None, max_pages: int = 5) -> list[dict]:
    """History rows (incidents and maintenance), newest first, 100 per page; stops once a page reaches past `since`."""
    out: list[dict] = []
    for page in range(max_pages):
        j = await fetch(f"{HAMRAVESH}/history/", {"limit": 100, "offset": 100 * page}, ttl=60)
        out += j["results"]
        oldest = when(j["results"][-1]["start_time"]) if j["results"] else None
        if not j.get("next") or since is None or (oldest and oldest < since):
            break
        await asyncio.sleep(2)  # polite pause between pages
    return out


async def hamravesh_incidents(since: datetime | None = None, max_pages: int = 5) -> list[dict]:
    rows = await _hamravesh_rows(since, max_pages)
    return [_hamravesh_incident(d) for d in rows if d.get("type", "incident") == "incident"]


def _hamravesh_window(m: dict) -> dict:
    start = m.get("expected_start_time") or m.get("start_time")  # same fields in notices and history rows
    end = m.get("expected_end_time") or m.get("end_time")
    s, e, now = when(start), when(end), datetime.now(timezone.utc)  # noqa: UP017
    done = any(r.get("tag") == "completed" for r in m.get("reports", [])) or bool(e and e < now)
    return {
        "title": (m.get("text") or "").strip(),
        "status": "complete" if done else "upcoming" if s and s > now else "in_progress",
        "start": start,
        "end": end,
        "components": [c.get("persian_name") or c.get("name") for c in m.get("components", [])],
    }


async def hamravesh_maintenance(past: bool = False, since: datetime | None = None) -> list[dict]:
    """Announced windows: the notices list plus maintenance rows of the history (which also holds finished ones)."""
    notices = (await fetch(f"{HAMRAVESH}/notices/", ttl=60)).get("maintenances", [])
    rows = [d for d in await _hamravesh_rows(since if past else None) if d.get("type") == "maintenance"]
    out, seen = [], set()
    for w in map(_hamravesh_window, [*notices, *rows]):
        if (w["title"], w["start"]) in seen or (w["status"] == "complete" and not past):
            continue
        seen.add((w["title"], w["start"]))
        out.append(w)
    return out


async def chabokan_components() -> dict:
    ov, cats = await asyncio.gather(
        fetch(f"{CHABOKAN}/overview/", ttl=60), fetch(f"{CHABOKAN}/services-by-category/", ttl=60)
    )
    comps = [
        {"name": s["name"], "group": cat["name"], "status": CHABOKAN_STATUS.get(s["status"], s["status"])}
        for cat in cats
        for s in cat["services"]
    ]
    return {"overall": ov["overall_status"], "components": comps}


def _chabokan_incident(d: dict) -> dict:
    ups = [
        {"status": u["status"], "at": u["created_at"], "text": (u.get("message") or "").strip()}
        for u in d.get("updates", [])
    ]
    ups.sort(key=lambda u: u["at"], reverse=True)
    return {
        "id": d["id"],
        "title": d["title"].strip(),
        "status": d["status"],
        "severity": d.get("severity"),
        "active": d["status"] != "resolved",  # the API's own is_active stays true after resolution
        "started": d["started_at"],
        "ended": d.get("resolved_at"),
        "components": [s["name"] for s in d.get("services", [])],
        "text": (d.get("description") or "").strip(),
        "updates": ups,
    }


async def chabokan_incidents() -> list[dict]:
    j = await fetch(f"{CHABOKAN}/incidents/", ttl=60)
    return [_chabokan_incident(d) for d in j["results"]]


async def chabokan_maintenance(past: bool = False, since: datetime | None = None) -> list[dict]:
    j = await fetch(f"{CHABOKAN}/maintenances/", {} if past else {"upcoming": "true"}, ttl=60)
    return [
        {
            "title": m.get("title"),
            "status": m.get("status"),
            "start": m.get("scheduled_start") or m.get("start_time"),
            "end": m.get("scheduled_end") or m.get("end_time"),
            "text": (m.get("description") or "").strip(),
        }
        for m in j["results"]
    ]


async def chabokan_incident(incident_id: str) -> dict:
    return _chabokan_incident(await fetch(f"{CHABOKAN}/incidents/{incident_id}/", ttl=60))


# --- ParsPack: HTML announcement board (Persian calendar dates, Iran time) ---

JALALI_MONTHS = {
    m: i for i, m in enumerate("فروردین اردیبهشت خرداد تیر مرداد شهریور مهر آبان آذر دی بهمن اسفند".split(), 1)
}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ROW = r'<div class="notice-item__update-row"'


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    feb = 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28
    gm, lengths = 0, [0, 31, feb, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    while gm < 12 and gd > lengths[gm + 1]:
        gd -= lengths[gm + 1]
        gm += 1
    return gy, gm + 1, gd


def jalali_iso(date: str, clock: str) -> str | None:
    """'۲۹ مهر ۱۴۰۵' + '۰۹:۰۷' -> '2026-10-21T09:07:00+03:30' (None when the text is not a date)."""
    m = re.match(r"(\d+) (\S+) (\d+)", date.translate(DIGITS).strip())
    t = re.match(r"(\d+):(\d+)", clock.translate(DIGITS).strip())
    if not m or not t or m[2] not in JALALI_MONTHS:
        return None
    y, mo, d = jalali_to_gregorian(int(m[3]), JALALI_MONTHS[m[2]], int(m[1]))
    return f"{y:04}-{mo:02}-{d:02}T{int(t[1]):02}:{t[2]}:00+03:30"


def _plain(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", fragment)
    return re.sub(r"[ \t]*\n\s*", "\n", html_lib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _find(fragment: str, pattern: str) -> str:
    m = re.search(pattern, fragment, re.S)
    return _plain(m[1]) if m else ""


def _stamp(fragment: str) -> str | None:
    return jalali_iso(
        _find(fragment, r'notice-item__date">(.*?)</span>'), _find(fragment, r'notice-item__time">(.*?)</span>')
    )


def _parspack_item(chunk: str) -> dict | None:
    resolved = chunk.startswith('<div class="notice-item notice--resolved"')
    body_at, foot_at = chunk.find("notice-item__body"), chunk.find("notice-item__footer")
    started = _stamp(chunk[:body_at])
    if not started:
        return None
    ups = []
    for row in chunk.split(ROW)[1:]:
        row = re.split(r'<div class="notice-item__(?:affected|footer)', row)[0]
        spans = [_plain(x) for x in re.findall(r"<span>(.*?)</span>", row, re.S)]
        at = jalali_iso(spans[0], spans[-1]) if len(spans) >= 3 else None
        ups.append(
            {"status": "update", "at": at or started, "text": _find(row, r"notice-item__update-text[^>]*>(.*?)</div>")}
        )
    ended = _stamp(chunk[foot_at:]) if resolved and foot_at > 0 else None
    if ended:
        ups.append(
            {"status": "resolved", "at": ended, "text": _find(chunk[foot_at:], r"notice-item__reply[^>]*>(.*?)</p>")}
        )
    ups.sort(key=lambda u: u["at"], reverse=True)
    return {
        "id": _find(chunk, r'id="status-(\d+)"'),
        "title": _find(chunk, r"<h3[^>]*>(.*?)</h3>"),
        "status": "resolved" if resolved else "notice",
        "active": not resolved,
        "started": started,
        "ended": ended,
        "components": [_plain(x) for x in re.findall(r'notice-item__affected-tag">(.*?)</span>', chunk, re.S)],
        "text": _find(chunk, r'<div class="notice-item__body[^"]*">(.*?)</div>'),
        "updates": ups,
    }


async def parspack_incidents() -> list[dict]:
    h = await fetch(PARSPACK, html=True, ttl=60)
    chunks = re.split(r'(?=<div class="notice-item notice--)', re.sub(r"<svg.*?</svg>", "", h, flags=re.S))[1:]
    out = [i for c in chunks if (i := _parspack_item(c))]
    if chunks and not out:
        raise ApiError("status.parspack.com has a layout this server does not know; announcements could not be read.")
    out.sort(key=lambda i: i["started"], reverse=True)
    return out


async def parspack_components() -> dict:
    return {"components": [], "note": "ParsPack publishes announcements only; see cs_incidents(provider='parspack')."}


async def no_maintenance(past: bool = False, since: datetime | None = None) -> list[dict]:
    return []


# --- Mihan WebHost: live up/down monitor of its hosting servers ---


async def mihan_components() -> dict:
    h = await fetch(MIHAN, html=True, ttl=60)
    m = re.search(r":servers='(\[.*?\])'", h, re.S)
    if not m:
        raise ApiError("mihanstatus.com has a layout this server does not know; server list could not be read.")
    comps = [
        {
            "name": s["name"],
            "group": (re.match(r"[a-z]+", s["name"]) or [None])[0],
            "status": "operational" if s.get("server_status") == 1 else "major_outage",
        }
        for s in json.loads(html_lib.unescape(m[1]))
    ]
    return {"components": comps}


COMPONENTS = {
    "arvancloud": arvan_components,
    "hamravesh": hamravesh_components,
    "chabokan": chabokan_components,
    "parspack": parspack_components,
    "mihanwebhost": mihan_components,
}


async def incidents(provider: str, since: datetime | None = None) -> list[dict]:
    if provider == "arvancloud":
        return await arvan_incidents()
    if provider == "hamravesh":
        return await hamravesh_incidents(since)
    if provider == "parspack":
        return await parspack_incidents()
    if provider == "mihanwebhost":
        return []
    return await chabokan_incidents()


async def maintenance(provider: str, past: bool = False, since: datetime | None = None) -> list[dict]:
    """Announced windows; past=True adds finished ones (since = oldest start wanted)."""
    if provider == "arvancloud":
        return await arvan_maintenance(past)
    if provider == "hamravesh":
        return await hamravesh_maintenance(past, since)
    if provider == "chabokan":
        return await chabokan_maintenance(past, since)
    return []


async def incident(provider: str, incident_id: str) -> dict:
    """One incident with its whole timeline. ArvanCloud and Chabokan have a detail endpoint, the others are searched."""
    key = str(incident_id).strip()
    if provider == "arvancloud":
        return await arvan_incident(key)
    if provider == "chabokan":
        return await chabokan_incident(key)
    rows = (
        await hamravesh_incidents(cutoff(3650), max_pages=8) if provider == "hamravesh" else await incidents(provider)
    )
    for i in rows:
        if str(i["id"]) == key:
            return i
    raise ApiError(f"{PROVIDERS[provider]['name']} has no incident {key!r} in its published history.")


def pick(provider: str | None) -> list[str]:
    """Provider ids for an optional provider argument (case-insensitive, name or id)."""
    if not provider:
        return list(PROVIDERS)
    p = provider.strip().lower().replace(" ", "")
    p = {"mihan": "mihanwebhost", "arvan": "arvancloud"}.get(p, p)
    if p in PROVIDERS:
        return [p]
    raise ApiError(f"Unknown provider {provider!r}. Use one of: {', '.join(PROVIDERS)}.")
