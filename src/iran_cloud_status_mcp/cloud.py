"""Tools: live status, components, incidents, maintenance and reliability of Iranian clouds; public prices."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import Field

from . import pricing, providers
from .http import ApiError
from .registry import tool

Provider = Literal["arvancloud", "hamravesh", "chabokan", "parspack", "mihanwebhost"]
Priced = Literal["liara", "hamravesh", "chabokan"]
Prov = Annotated[Provider | None, Field(description="One provider; leave out for all that support this tool.")]
RANK = {"operational": 0, "maintenance": 1, "degraded": 2, "partial_outage": 3, "major_outage": 4}


async def _each(ids: list[str], fn) -> dict[str, Any]:
    """Run fn(provider) for every id at once; one provider failing gives {"error": ...} for it only."""
    res = await asyncio.gather(*(fn(i) for i in ids), return_exceptions=True)
    return {i: ({"error": str(r)} if isinstance(r, Exception) else r) for i, r in zip(ids, res, strict=True)}


def _worst(comps: list[dict]) -> str:
    return max((c["status"] for c in comps), key=lambda s: RANK.get(s, 0), default="operational")


def _with(feature: str, provider: str | None) -> list[str]:
    """Provider ids for the argument; with none given, only providers that publish `feature`."""
    ids = providers.pick(provider)
    return ids if provider else [i for i in ids if feature in providers.PROVIDERS[i]["has"]]


def _issues(comps: list[dict], inc: list[dict]) -> list[dict]:
    """Components that are not operational, plus the ones an open incident sets a status on (ArvanCloud pivot)."""
    issues = [{"name": c["name"], "status": c["status"]} for c in comps if c["status"] != "operational"]
    have = {c["name"] for c in issues}
    for i in inc:
        if i["active"]:
            issues += [
                {"name": n, "status": s, "via_incident": i["id"]}
                for n, s in i.get("impact", {}).items()
                if s != "operational" and n not in have
            ]
    return issues


def _short(i: dict, updates: bool, limit: int = 400) -> dict:
    out = {
        k: i[k]
        for k in ("id", "title", "status", "severity", "active", "started", "ended", "components")
        if i.get(k) not in (None, [])
    }
    ups = i["updates"]
    if updates:
        out["updates"] = [{**u, "text": u["text"][:limit]} for u in ups]
    elif ups:
        out["last_update"] = {**ups[0], "text": ups[0]["text"][:limit]}
    if i.get("text") and updates:
        out["text"] = i["text"][:limit]
    return out


@tool("Cloud status now")
async def cs_status() -> dict[str, Any]:
    """Show the live status of every covered Iranian cloud provider in one call.

    Use it first for "is X down?". Per provider: overall status, non-operational components, open incidents,
    upcoming maintenance count. Next: cs_regions, cs_components, cs_incidents, cs_maintenance.
    """

    async def one(pid: str) -> dict:
        data, inc, mnt = await asyncio.gather(
            providers.COMPONENTS[pid](), providers.incidents(pid, providers.cutoff(2)), providers.maintenance(pid)
        )
        comps = data["components"]
        issues = _issues(comps, inc)
        worst = _worst(issues) if issues else "operational"
        overall = data.get("overall") or (worst if comps else None)
        if overall == "operational" and worst != "operational":
            overall = worst
        return {
            "overall": overall,
            "message": data.get("message") or data.get("note"),
            "components": len(comps),
            "not_operational": issues,
            "active_incidents": [
                {
                    "id": i["id"],
                    "title": i["title"][:200],
                    "status": i["status"],
                    "components": i["components"],
                    "since": i["started"],
                }
                for i in inc
                if i["active"]
            ],
            "upcoming_maintenance": len(mnt),
            "status_page": providers.PROVIDERS[pid]["status_page"],
        }

    return {
        "providers": await _each(list(providers.PROVIDERS), one),
        "note": "Times are ISO with the zone the provider publishes in (Iran time, +03:30).",
    }


@tool("Providers and coverage")
async def cs_providers() -> dict[str, Any]:
    """List the providers covered, what each publishes, and which are not covered.

    No network calls.
    """
    return {
        "status": {
            pid: {
                "name": p["name"],
                "status_page": p["status_page"],
                "tools": ["cs_" + t for t in ("status", *p["has"])],
                "note": p.get("note"),
            }
            for pid, p in providers.PROVIDERS.items()
        },
        "prices": {
            "liara": "PaaS, DBaaS, IaaS, WordPress, Email, AI, ObjectStorage plans",
            "hamravesh": "Darkube per-unit prices (Iran and Germany), traffic, IP, monitoring",
            "chabokan": "starting price of each product",
        },
        "not_covered": {
            "liara_status": "no public status page found",
            "iranserver": "its status page is a ping tool, not a status feed",
            "arvancloud_prices": "pricing pages sit behind a JavaScript redirect check, so they are not read",
            "no_resolving_status_host": "Hostiran, Asiatech, Sindad, Pars Online, Abrha, Pishgaman, Runflare, Mizbanfa",
        },
    }


@tool("Components of a provider")
async def cs_components(
    provider: Annotated[Provider, Field(description="Provider id.")],
    query: Annotated[
        str | None,
        Field(
            description="Name or group contains this text (case-insensitive).",
            max_length=60,
        ),
    ] = None,
    only_issues: Annotated[bool, Field(description="Only non-operational components.")] = False,
    limit: Annotated[int, Field(description="Most components to list.", ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """List one provider's status components (services, regions, clusters, servers) with current status.

    Names are as published (ArvanCloud English, Hamravesh and Chabokan Persian). Use cs_regions for one line per region.
    """
    data = await providers.COMPONENTS[provider]()
    comps = data["components"]
    if query:
        q = query.lower()
        comps = [
            c
            for c in comps
            if q in c["name"].lower() or q in (c.get("group") or "").lower() or q in c.get("en", "").lower()
        ]
    if only_issues:
        comps = [c for c in comps if c["status"] != "operational"]
    return {
        "provider": provider,
        "count": len(comps),
        "components": comps[:limit],
        "truncated": len(comps) > limit or None,
        "note": data.get("note"),
    }


@tool("Status per region")
async def cs_regions(
    provider: Prov = None,
    query: Annotated[
        str | None,
        Field(description="Region name contains this text, e.g. 'simin'.", max_length=40),
    ] = None,
) -> dict[str, Any]:
    """Summarise status per region or group: component count, worst status and what is not operational.

    Groups are the provider's own (ArvanCloud regions, Hamravesh clusters, Chabokan categories, Mihan server types).
    Next: cs_components for members, cs_incidents for what happened.
    """

    async def one(pid: str) -> dict:
        data, inc = await asyncio.gather(providers.COMPONENTS[pid](), providers.incidents(pid, providers.cutoff(2)))
        comps = data["components"]
        by = {c["name"]: c.get("group") or "(ungrouped)" for c in comps}
        groups: dict[str, dict] = {}
        for c in comps:
            groups.setdefault(by[c["name"]], {"components": 0, "not_operational": []})["components"] += 1
        for x in _issues(comps, inc):
            g = groups.setdefault(by.get(x["name"], "(ungrouped)"), {"components": 0, "not_operational": []})
            g["not_operational"].append(x)
        rows = [
            {"region": k, "status": _worst(v["not_operational"]) if v["not_operational"] else "operational", **v}
            for k, v in sorted(groups.items())
        ]
        q = (query or "").lower()
        return {"regions": [r for r in rows if q in r["region"].lower()]}

    return {"providers": await _each(_with("regions", provider), one)}


@tool("Incidents")
async def cs_incidents(
    provider: Prov = None,
    days: Annotated[int, Field(description="Started within this many days.", ge=1, le=365)] = 14,
    active_only: Annotated[bool, Field(description="Only unresolved incidents.")] = False,
    query: Annotated[str | None, Field(description="Title or components contain this text.", max_length=60)] = None,
    updates: Annotated[bool, Field(description="Include the full update timeline, not just the latest.")] = False,
    limit: Annotated[int, Field(description="Most incidents to return, newest first.", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """List recent incidents (outages, degradations, announcements) of the Iranian cloud providers, newest first, with their latest update.

    ParsPack entries are announcements (status 'notice' while open). Large `days` cost extra Hamravesh requests.
    Next: cs_incident for one; cs_reliability for counts.
    """
    since = providers.cutoff(days)

    async def one(pid: str) -> list[dict]:
        rows = await providers.incidents(pid, since)
        return [i for i in rows if (providers.when(i["started"]) or since) >= since or i["active"]]

    res = await _each(_with("incidents", provider), one)
    q = (query or "").lower()
    out, errors = [], {}
    for pid, rows in res.items():
        if isinstance(rows, dict):
            errors[pid] = rows["error"]
            continue
        for i in rows:
            if (active_only and not i["active"]) or (
                q and q not in i["title"].lower() and not any(q in c.lower() for c in i["components"])
            ):
                continue
            out.append({"provider": pid, "_t": providers.when(i["started"]) or since, **_short(i, updates)})
    out.sort(key=lambda i: i["_t"], reverse=True)
    for i in out:
        del i["_t"]
    return {"found": len(out), "incidents": out[:limit], **({"errors": errors} if errors else {})}


@tool("One incident in detail")
async def cs_incident(
    provider: Annotated[Provider, Field(description="Provider id (not mihanwebhost).")],
    incident_id: Annotated[
        str,
        Field(description="`id` from cs_incidents or cs_status.", max_length=60),
    ],
) -> dict[str, Any]:
    """Show one incident with its full update timeline, affected components and the status each was set to.

    Use it after cs_incidents or cs_status. Hamravesh lookup covers only the latest ~800 history entries.
    """
    if provider == "mihanwebhost":
        raise ApiError("Mihan WebHost publishes no incidents.")
    i = await providers.incident(provider, incident_id)
    return {"provider": provider, **_short(i, True, 3000), "impact": i.get("impact")}


@tool("Planned maintenance")
async def cs_maintenance(
    provider: Prov = None,
    include_past: Annotated[
        bool,
        Field(description="Also list finished windows from the last `days` days."),
    ] = False,
    days: Annotated[int, Field(description="With include_past: look-back days.", ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """List announced maintenance windows that are upcoming or running, per provider, optionally with recent finished ones.

    Empty means nothing is announced.
    """
    since = providers.cutoff(days) if include_past else None

    async def one(pid: str) -> dict:
        rows = await providers.maintenance(pid, include_past, since)
        if since:
            rows = [w for w in rows if w["status"] != "complete" or (providers.when(w["start"]) or since) >= since]
        rows.sort(key=lambda w: w["start"] or "", reverse=True)
        return {"windows": rows, "count": len(rows)}

    res = await _each(_with("maintenance", provider), one)
    return {"providers": res}


def _union_minutes(spans: list[tuple[datetime, datetime]]) -> float:
    """Total length of the spans with overlaps counted once."""
    total, edge = 0.0, None
    for s, e in sorted(spans):
        if edge is None or s > edge:
            total += (e - s).total_seconds()
            edge = e
        elif e > edge:
            total += (e - edge).total_seconds()
            edge = e
    return total / 60


@tool("Reliability over a period")
async def cs_reliability(
    provider: Prov = None,
    days: Annotated[int, Field(description="Look-back days.", ge=1, le=365)] = 30,
    query: Annotated[
        str | None,
        Field(description="Only incidents whose title or components contain this text.", max_length=60),
    ] = None,
) -> dict[str, Any]:
    """Count incidents and add up incident time per provider over the last days, with the most affected components.

    Use it to compare reliability. Overlapping incidents count once; incident_time_percent is the window share with
    an incident open, not uptime (no provider publishes uptime).
    """
    since = providers.cutoff(days)
    now = datetime.now(timezone.utc)  # noqa: UP017
    q = (query or "").lower()

    async def one(pid: str) -> dict:
        rows, spans, by = [], [], Counter()
        for i in await providers.incidents(pid, since):
            s = providers.when(i["started"])
            e = providers.when(i["ended"]) or (now if i["active"] else s)
            if not s or i["status"] == "notice" or e < since:
                continue
            if q and q not in i["title"].lower() and not any(q in c.lower() for c in i["components"]):
                continue
            rows.append(i)
            spans.append((max(s, since), e))
            by.update(i["components"])
        mins = _union_minutes(spans)
        return {
            "incidents": len(rows),
            "open": sum(i["active"] for i in rows),
            "outage_minutes": round(mins),
            "incident_time_percent": round(100 * mins / (days * 1440), 1),
            "most_affected": dict(by.most_common(5)),
        }

    return {"days": days, "providers": await _each(_with("incidents", provider), one)}


@tool("Public cloud prices")
async def cs_pricing(
    provider: Annotated[
        Priced,
        Field(description="Provider id; ArvanCloud prices are not readable."),
    ],
    product: Annotated[
        str | None,
        Field(
            description="Liara only: PaaS, DBaaS, IaaS, WordPress, Email, AI or ObjectStorage (default all).",
            max_length=20,
        ),
    ] = None,
) -> dict[str, Any]:
    """Show a provider's public price list in Toman, read from its own pricing page.

    Liara: fixed plans per product; Hamravesh: per-unit prices (Iran and Germany); Chabokan: starting prices.
    Use cs_compare_prices to price one server size.
    """
    if provider == "hamravesh":
        return await pricing.hamravesh()
    if provider == "chabokan":
        return await pricing.chabokan()
    data = await pricing.liara()
    if product:
        key = next((k for k in data["products"] if k.lower() == product.lower()), None)
        if not key:
            raise ApiError(f"Liara has no product {product!r} here. Use one of: {', '.join(data['products'])}.")
        data["products"] = {key: data["products"][key]}
    return data


@tool("Compare prices for a size")
async def cs_compare_prices(
    vcpu: Annotated[float, Field(description="vCPU cores needed, e.g. 2.", gt=0, le=64)],
    ram_gb: Annotated[float, Field(description="RAM in GB needed, e.g. 4.", gt=0, le=256)],
    disk_gb: Annotated[float, Field(description="SSD disk in GB needed.", ge=1, le=2000)] = 20,
) -> dict[str, Any]:
    """Price one server size (vCPU, RAM, disk) per month at Hamravesh and Liara, cheapest first.

    Liara: cheapest PaaS and IaaS plan at least that big. Traffic excluded; ArvanCloud and Chabokan not compared.
    """
    liara, ham = await asyncio.gather(pricing.liara(), pricing.hamravesh())
    offers = []
    for dc, d in ham["datacenters"].items():
        u = d["per_unit"]
        month = vcpu * u["vcpu"]["month"] + ram_gb * u["ram_gb"]["month"] + disk_gb * u["disk_gb"]["month"]
        offers.append(
            {
                "provider": "Hamravesh",
                "product": f"Darkube ({dc} datacenter)",
                "plan": "per-unit, exact size",
                "month": round(month),
            }
        )
    for product in ("PaaS", "IaaS"):
        plans = liara["products"].get(product, [])
        fit = [p for p in plans if p["vcpu"] >= vcpu and p["ram_gb"] >= ram_gb and p["disk_gb"] >= disk_gb]
        if fit:
            p = min(fit, key=lambda p: p["month"])
            extra = liara.get("iaas_ipv4_per_month", 0) if product == "IaaS" else 0
            offers.append(
                {
                    "provider": "Liara",
                    "product": product,
                    "plan": p["plan"],
                    "size": f"{p['vcpu']} vCPU, {p['ram_gb']} GB RAM, {p['disk_gb']} GB",
                    "month": round(p["month"] + extra),
                    **({"includes_ipv4": extra} if extra else {}),
                }
            )
    offers.sort(key=lambda o: o["month"])
    return {
        "currency": "Toman",
        "wanted": {"vcpu": vcpu, "ram_gb": ram_gb, "disk_gb": disk_gb},
        "offers": offers,
        "note": "Traffic not included. Hamravesh month = 30 days.",
    }
