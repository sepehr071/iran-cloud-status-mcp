from datetime import datetime, timezone

import httpx
import pytest
from conftest import fixture, page

from iran_cloud_status_mcp import providers

pytestmark = pytest.mark.anyio


@pytest.fixture
def clouds(api, monkeypatch):
    # Fixtures are from 2026-10-31; pin the window start so day counts keep matching.
    monkeypatch.setattr(providers, "cutoff", lambda days: datetime(2026, 10, 31 - min(days, 30), tzinfo=timezone.utc))
    api["/api/components"] = fixture("arvan_components.json")
    api["/api/status"] = fixture("arvan_status.json")
    api["/api/incidents"] = fixture("arvan_incidents.json")
    api["/api/schedules"] = fixture("arvan_schedules.json")
    api["/api/v1/status/components/"] = fixture("ham_components.json")
    api["/api/v1/status/history/"] = fixture("ham_history.json")
    api["/api/v1/status/notices/"] = fixture("ham_notices.json")
    api["/api/overview/"] = fixture("cb_overview.json")
    api["/api/services-by-category/"] = fixture("cb_services.json")
    api["/api/incidents/"] = fixture("cb_incidents.json")
    api["/api/maintenances/"] = fixture("cb_maintenances.json")
    api["/pricing/"] = page("liara.html")
    api["/pricing"] = page("hamravesh.html")
    return api


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    assert not r.is_error, r
    return r.structured_content


async def test_lists_all_tools(client):
    names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "cs_status",
        "cs_components",
        "cs_incidents",
        "cs_maintenance",
        "cs_reliability",
        "cs_pricing",
        "cs_compare_prices",
        "cs_providers",
        "cs_regions",
        "cs_incident",
    }


async def test_status_all_providers(client, clouds):
    r = await call(client, "cs_status")
    a, h, c = (r["providers"][k] for k in ("arvancloud", "hamravesh", "chabokan"))
    assert a["overall"] == "partial_outage" and a["not_operational"][0]["status"] == "degraded"
    assert any(x.get("via_incident") == 88 and x["name"] == "Qeysar - Network" for x in a["not_operational"])
    assert h["overall"] == "major_outage" and h["not_operational"][0]["name"] == "کانتینر رجیستری"
    assert c["overall"] == "operational" and c["components"] > 0
    assert a["upcoming_maintenance"] == 1  # only the schedule that is not complete


async def test_status_one_provider_failing_does_not_hide_others(client, clouds):
    clouds["/api/overview/"] = lambda request: httpx.Response(503, text="down")
    r = await call(client, "cs_status")
    assert "error" in r["providers"]["chabokan"] and r["providers"]["arvancloud"]["overall"]


async def test_components_filter(client, clouds):
    r = await call(client, "cs_components", {"provider": "arvancloud", "only_issues": True})
    assert r["count"] == 1 and r["components"][0]["group"]
    r = await call(client, "cs_components", {"provider": "hamravesh", "query": "registry"})
    assert [c["en"] for c in r["components"]] == ["container-registry"]


async def test_incidents_arvan_timeline(client, clouds):
    r = await call(client, "cs_incidents", {"provider": "arvancloud", "days": 365, "updates": True, "limit": 5})
    inc = r["incidents"]
    assert [i["started"] for i in inc] == sorted((i["started"] for i in inc), reverse=True)
    done = next(i for i in inc if i["id"] == 101)
    assert done["status"] == "resolved" and done["ended"] and done["updates"][0]["status"] == "resolved"
    open_ = next(i for i in inc if i["id"] == 88)
    assert open_["active"] and open_["components"] == ["Qeysar - Network"]


async def test_incidents_active_only_and_query(client, clouds):
    r = await call(client, "cs_incidents", {"days": 365, "active_only": True})
    assert r["found"] >= 2 and all(i["active"] for i in r["incidents"])
    assert {i["provider"] for i in r["incidents"]} >= {"arvancloud", "hamravesh"}
    r = await call(client, "cs_incidents", {"provider": "arvancloud", "days": 365, "query": "storage"})
    assert r["found"] == 1 and "last_update" in r["incidents"][0]


async def test_maintenance(client, clouds):
    r = await call(client, "cs_maintenance", {"provider": "arvancloud"})
    m = r["providers"]["arvancloud"]["windows"]
    assert len(m) == 1 and m[0]["status"] == "upcoming" and "MySQL" in m[0]["title"]


async def test_reliability(client, clouds):
    r = await call(client, "cs_reliability", {"provider": "hamravesh", "days": 365})
    h = r["providers"]["hamravesh"]
    assert h["incidents"] == 3 and h["open"] >= 1 and h["outage_minutes"] > 0


async def test_unknown_provider_rejected(client, clouds):
    r = await client.call_tool("cs_components", {"provider": "azure"})
    assert r.is_error


async def test_pricing_liara_and_filter(client, clouds):
    r = await call(client, "cs_pricing", {"provider": "liara"})
    assert set(r["products"]) >= {"PaaS", "DBaaS", "IaaS"} and r["iaas_ipv4_per_month"] == 200000
    earth = r["products"]["PaaS"][0]
    assert earth == {"plan": "زمین", "month": 750000, "hour": 1041, "ram_gb": 0.512, "vcpu": 0.5, "disk_gb": 5}
    r = await call(client, "cs_pricing", {"provider": "liara", "product": "iaas"})
    assert list(r["products"]) == ["IaaS"] and len(r["products"]["IaaS"]) == 6


async def test_pricing_hamravesh(client, clouds):
    r = await call(client, "cs_pricing", {"provider": "hamravesh"})
    assert r["datacenters"]["iran"]["per_unit"]["vcpu"] == {"hour": 826, "month": 595000}
    assert r["datacenters"]["germany"]["per_unit"]["ram_gb"]["month"] == 480000
    assert r["traffic_per_gb"]["domestic_download"] == 3200 and r["dedicated_ip_per_year"]["foreign"] == 4800000


async def test_compare_prices(client, clouds):
    r = await call(client, "cs_compare_prices", {"vcpu": 2, "ram_gb": 4, "disk_gb": 20})
    month = {(o["provider"], o["product"]): o["month"] for o in r["offers"]}
    assert month[("Hamravesh", "Darkube (iran datacenter)")] == 2 * 595000 + 4 * 290000 + 20 * 16500
    assert month[("Liara", "PaaS")] == 4125000 and month[("Liara", "IaaS")] == 2650000 + 200000
    assert r["offers"] == sorted(r["offers"], key=lambda o: o["month"])


async def test_maintenance_hamravesh_uses_expected_times(client, clouds):
    # Shape copied from a maintenance row in /history/ (the notices list was empty when captured).
    clouds["/api/v1/status/notices/"] = {
        "incidents": [],
        "maintenances": [
            {
                "text": "نگهداری هم\u200cگیت",
                "components": [{"name": "hamgit", "persian_name": "هم\u200cگیت"}],
                "expected_start_time": "2099-01-01T00:45:00+03:30",
                "expected_end_time": "2099-01-01T01:00:00+03:30",
            }
        ],
    }
    r = await call(client, "cs_maintenance", {"provider": "hamravesh"})
    w = r["providers"]["hamravesh"]["windows"][0]
    assert w["status"] == "upcoming" and w["start"].startswith("2099") and w["components"] == ["هم\u200cگیت"]


@pytest.fixture
def extra(clouds):
    clouds["/api/incidents/101"] = fixture("arvan_incident_101.json")
    clouds["/api/incidents/4/"] = fixture("cb_incident_4.json")
    clouds["/"] = page("parspack.html")
    clouds["/uptime.php"] = page("mihan_uptime.html")
    clouds["/pricing/"] = lambda request: (
        page("chabokan_pricing.html")(request) if request.url.host == "chabokan.net" else page("liara.html")(request)
    )
    return clouds


def test_jalali_dates():
    assert providers.jalali_iso("۲۹ مهر ۱۴۰۵", "۰۹:۰۷") == "2026-10-21T09:07:00+03:30"
    assert providers.jalali_iso("۹ اسفند ۱۴۰۴", "۱۵:۳۲") == "2026-02-28T15:32:00+03:30"
    assert providers.jalali_iso("hello", "1:2") is None


async def test_arvan_times_are_iran_time(client, extra):
    r = await call(client, "cs_incident", {"provider": "arvancloud", "incident_id": "101"})
    assert r["started"] == "2026-10-30T02:10:00+03:30" and r["ended"] == "2026-10-30T09:35:11+03:30"
    assert [u["status"] for u in r["updates"]] == ["resolved", "monitoring", "investigating"]


async def test_incident_chabokan_and_missing(client, extra):
    r = await call(client, "cs_incident", {"provider": "chabokan", "incident_id": "4"})
    assert r["severity"] == "major" and r["active"] is False and r["updates"][0]["status"] == "resolved"
    bad = await client.call_tool("cs_incident", {"provider": "parspack", "incident_id": "1"})
    assert bad.is_error


async def test_parspack_incidents(client, extra, monkeypatch):
    monkeypatch.setattr(providers, "cutoff", lambda days: datetime(2026, 1, 1, tzinfo=timezone.utc))
    r = await call(client, "cs_incidents", {"provider": "parspack", "days": 365, "updates": True})
    notice = next(i for i in r["incidents"] if i["id"] == "7269")
    assert notice["status"] == "notice" and notice["active"] and notice["started"] == "2026-10-21T09:07:00+03:30"
    assert "PaaS" in notice["components"] and notice["updates"][0]["at"] > notice["updates"][-1]["at"]
    done = [i for i in r["incidents"] if i["status"] == "resolved"]
    assert done and all(i["ended"] and i["ended"] >= i["started"] for i in done)
    one = await call(client, "cs_incident", {"provider": "parspack", "incident_id": "7269"})
    assert one["title"] == notice["title"]


async def test_mihan_and_regions(client, extra):
    r = await call(client, "cs_regions", {"provider": "mihanwebhost"})
    rows = {x["region"]: x for x in r["providers"]["mihanwebhost"]["regions"]}
    assert sum(x["components"] for x in rows.values()) == 3
    assert any(x["status"] == "major_outage" for x in rows.values())
    r = await call(client, "cs_regions", {"provider": "arvancloud", "query": "qeysar"})
    q = r["providers"]["arvancloud"]["regions"]
    assert len(q) == 1 and q[0]["status"] == "degraded" and q[0]["not_operational"][0]["via_incident"] == 88


async def test_status_covers_five_providers(client, extra):
    r = (await call(client, "cs_status"))["providers"]
    assert set(r) == {"arvancloud", "hamravesh", "chabokan", "parspack", "mihanwebhost"}
    assert r["mihanwebhost"]["overall"] == "major_outage" and "overall" not in r["parspack"]
    assert r["parspack"]["active_incidents"][0]["status"] == "notice"


async def test_components_limit(client, extra):
    r = await call(client, "cs_components", {"provider": "mihanwebhost", "limit": 2})
    assert r["count"] == 3 and len(r["components"]) == 2 and r["truncated"]


async def test_maintenance_past_hamravesh(client, extra):
    r = await call(client, "cs_maintenance", {"provider": "hamravesh", "include_past": True, "days": 365})
    ws = r["providers"]["hamravesh"]["windows"]
    assert ws and all(w["status"] == "complete" or w["status"] == "upcoming" for w in ws)
    r = await call(client, "cs_maintenance", {"provider": "hamravesh"})
    assert all(w["status"] != "complete" for w in r["providers"]["hamravesh"].get("windows", []))


async def test_reliability_counts_overlaps_once(client, extra):
    r = await call(client, "cs_reliability", {"provider": "arvancloud", "days": 30, "query": "qeysar"})
    a = r["providers"]["arvancloud"]
    assert a["incidents"] == 1 and a["open"] == 1 and 0 < a["incident_time_percent"] <= 100


async def test_pricing_more_liara_and_chabokan(client, extra):
    r = await call(client, "cs_pricing", {"provider": "liara"})
    assert set(r["products"]) >= {"PaaS", "IaaS", "Email", "AI", "WordPress", "ObjectStorage"}
    assert r["products"]["WordPress"][0]["plan"] == "سهند" and r["products"]["WordPress"][0]["month"] == 3900000
    assert r["products"]["Email"][0]["sends_per_day"] == 500 and r["products"]["AI"][0]["month"] == 825000
    assert r["products"]["ObjectStorage"][0]["month"] == 475000
    r = await call(client, "cs_pricing", {"provider": "chabokan"})
    assert r["starting_prices"][0] == {"product": "هاست ابری", "from": 177498, "per": "ماه"}
    assert r["starting_prices"][1]["from"] == 0
    r = await call(client, "cs_compare_prices", {"vcpu": 2, "ram_gb": 4})
    assert {o["product"] for o in r["offers"] if o["provider"] == "Liara"} <= {"PaaS", "IaaS"}


async def test_providers_tool(client):
    r = await call(client, "cs_providers")
    assert "parspack" in r["status"] and "not_covered" in r


async def test_request_headers_match_each_providers_frontend(api):
    from iran_cloud_status_mcp import http

    api["/api/overview/"] = {}
    api["/components/"] = {}
    api["/price"] = {}
    await http.fetch("https://status.chabokan.net/api/overview/")
    h = api.calls[-1].headers
    assert h["accept"] == "*/*" and h["referer"] == "https://status.chabokan.net/" and "origin" not in h
    assert (h["sec-fetch-site"], h["sec-fetch-mode"], h["priority"]) == ("same-origin", "cors", "u=1, i")
    await http.fetch("https://api.hamraveshstatus.com/components/")
    h = api.calls[-1].headers
    assert h["accept"] == "application/json, text/plain, */*" and h["origin"] == "https://hamraveshstatus.com"
    assert h["sec-fetch-site"] == "same-site" and h["referer"] == "https://hamraveshstatus.com/"
    await http.fetch("https://liara.ir/price", html=True)
    h = api.calls[-1].headers
    assert h["accept"].startswith("text/html") and "origin" not in h and "referer" not in h
    assert (h["sec-fetch-site"], h["sec-fetch-mode"], h["sec-fetch-dest"]) == ("none", "navigate", "document")
