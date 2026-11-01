import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_status(client):
    r = (await client.call_tool("cs_status", {})).structured_content["providers"]
    assert set(r) == {"arvancloud", "hamravesh", "chabokan", "parspack", "mihanwebhost"}
    assert all("error" not in v for v in r.values()), r
    assert all(r[k]["components"] > 10 for k in ("arvancloud", "hamravesh", "chabokan", "mihanwebhost")), r


async def test_live_components(client):
    r = (await client.call_tool("cs_components", {"provider": "arvancloud", "query": "object"})).structured_content
    assert r["count"] > 0 and all("object" in (c["name"] + c["group"]).lower() for c in r["components"])


async def test_live_incidents(client):
    r = (await client.call_tool("cs_incidents", {"days": 365, "limit": 20})).structured_content
    assert r["found"] > 0 and not r.get("errors"), r


async def test_live_maintenance(client):
    r = (await client.call_tool("cs_maintenance", {})).structured_content["providers"]
    assert all("count" in v for v in r.values()), r  # empty lists are dropped from output


async def test_live_reliability(client):
    r = (await client.call_tool("cs_reliability", {"days": 90})).structured_content["providers"]
    assert all("incidents" in v for v in r.values()), r


async def test_live_pricing(client):
    liara = (await client.call_tool("cs_pricing", {"provider": "liara"})).structured_content
    assert len(liara["products"]["PaaS"]) >= 5 and all(p["month"] > 100000 for p in liara["products"]["PaaS"])
    ham = (await client.call_tool("cs_pricing", {"provider": "hamravesh"})).structured_content
    assert ham["datacenters"]["iran"]["per_unit"]["vcpu"]["month"] > 100000


async def test_live_compare(client):
    r = (await client.call_tool("cs_compare_prices", {"vcpu": 2, "ram_gb": 4})).structured_content
    assert len(r["offers"]) >= 3


async def test_live_regions_and_incident(client):
    r = (await client.call_tool("cs_regions", {"provider": "arvancloud"})).structured_content["providers"]
    assert len(r["arvancloud"]["regions"]) >= 6, r
    one = (await client.call_tool("cs_incident", {"provider": "arvancloud", "incident_id": "101"})).structured_content
    assert one["title"] == "Simin object storage latency" and one["started"].startswith("2026-10-30T02:10")


async def test_live_parspack_and_mihan(client):
    r = (await client.call_tool("cs_incidents", {"provider": "parspack", "days": 365})).structured_content
    assert r["found"] > 3 and r["incidents"][0]["started"].startswith("20"), r
    m = (await client.call_tool("cs_components", {"provider": "mihanwebhost", "limit": 5})).structured_content
    assert m["count"] > 100 and len(m["components"]) == 5


async def test_live_more_prices(client):
    liara = (await client.call_tool("cs_pricing", {"provider": "liara"})).structured_content["products"]
    assert {"WordPress", "Email", "AI", "ObjectStorage"} <= set(liara), list(liara)
    cb = (await client.call_tool("cs_pricing", {"provider": "chabokan"})).structured_content
    assert len(cb["starting_prices"]) >= 5
