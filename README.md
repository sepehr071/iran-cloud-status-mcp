<!-- mcp-name: io.github.sepehr071/iran-cloud-status-mcp -->

<div align="center">

# iran-cloud-status-mcp: Iranian cloud status, incidents and prices MCP server for AI agents

**iran-cloud-status-mcp is an unofficial, read-only MCP server that tells AI agents whether Iranian cloud providers are up.**<br>
Its 10 tools read the public status pages of ArvanCloud (آروان), Hamravesh (هم‌روش), Chabokan (چابکان), ParsPack and Mihan WebHost, list incidents and<br>
maintenance, count outages over a period, and read the public price lists of Liara (لیارا), Hamravesh and Chabokan.<br>
No API key or account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[![PyPI](https://img.shields.io/pypi/v/iran-cloud-status-mcp?color=2563eb)](https://pypi.org/project/iran-cloud-status-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/iran-cloud-status-mcp/blob/main/LICENSE)

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## For AI agents

- **What:** unofficial MCP server for the public status pages and price pages of Iranian clouds: live status per component, incidents with update timelines, planned maintenance, incident counts and outage minutes, plan and per-unit prices, and a price comparison for a given vCPU/RAM/disk size.
- **Install:** `uvx iran-cloud-status-mcp` or `claude mcp add iran-cloud-status -- uvx iran-cloud-status-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `IRAN_CLOUD_STATUS_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only. It cannot log in or change anything, so no confirmation is needed before calling any tool.
- **Tools:** 10, all prefixed `cs_`. Typical sequence: `cs_status` -> `cs_incidents` (active_only, updates) for a live problem; `cs_reliability` to compare providers; `cs_compare_prices` for the cheapest size.
- **Use when:** the user asks if ArvanCloud, Hamravesh or Chabokan is down or slow, what an outage was, whether maintenance is planned, which Iranian cloud was most stable, or what Liara or Hamravesh charge.
- **Do not use when:** the question is about another provider (Liara publishes no status page; ArvanCloud prices sit behind a JavaScript check and are not read) or about a customer's own account.
- **Output:** compact JSON. Status words: operational, degraded, partial_outage, major_outage, maintenance. Prices in Toman. Times are ISO in Iran time (+03:30). Titles and messages are the providers' own text (Persian or English).

## Why

During an outage the first question is "is it us or the cloud?". The three status pages have three different layouts
and APIs, and none of them says how the others are doing. With `iran-cloud-status-mcp` an agent answers in one call:

> **You:** Is anything broken at the Iranian clouds right now?
>
> **Agent:** *calls* `cs_status()`
>
> ArvanCloud reports a partial outage: "Qeysar Network Congestion" (open since 2026-10-10, Qeysar - Network at
> performance issues) and a planned MySQL 8.4 upgrade on 2026-11-03 02:00. Hamravesh and Chabokan: all services operational.

## Quick start

```bash
claude mcp add iran-cloud-status -- uvx iran-cloud-status-mcp
```

For other clients add `{"command": "uvx", "args": ["iran-cloud-status-mcp"]}` to the MCP server list.

## Tools

| Tool | What it does |
|---|---|
| `cs_status` | Live status of all providers: overall, components not operational, open incidents, upcoming maintenance. |
| `cs_providers` | What is covered per provider (no network call). |
| `cs_regions` | Status per region or group: component count, worst status, what is down. |
| `cs_components` | One provider's components with status; filter by text or only problems. |
| `cs_incidents` | Recent incidents, newest first, latest update or the whole timeline; filter by provider, days, open only, text. |
| `cs_incident` | One incident in full: whole timeline, affected components and the status set on each. |
| `cs_maintenance` | Announced maintenance windows that are upcoming or running; `include_past` adds recent finished ones. |
| `cs_reliability` | Incident count, open count, incident minutes (overlaps once) and share of the window over N days, most affected components; optional text filter. |
| `cs_pricing` | Liara plans (PaaS, DBaaS, IaaS, WordPress, Email, AI, ObjectStorage), Hamravesh per-unit prices, traffic, IP and monitoring fees, or Chabokan starting prices. |
| `cs_compare_prices` | Monthly price of one vCPU/RAM/disk size at Hamravesh (Iran, Germany) and Liara (cheapest fitting plan). |

## FAQ

**Which providers?** Status: ArvanCloud, Hamravesh, Chabokan, ParsPack (announcements only), Mihan WebHost (server up/down only). Prices: Liara, Hamravesh, Chabokan. Liara has no status page; ArvanCloud prices are not read.

**Is there an uptime percentage?** No provider publishes one. `cs_reliability` counts incidents and the time at least one was open (overlaps counted once); a partial incident counts fully, so it is not an uptime figure.

**Is it affiliated with the providers?** No. It reads the same public pages anyone can open.

## فارسی

`iran-cloud-status-mcp` یک سرور MCP غیررسمی و فقط‌خواندنی است که وضعیت زنده، رخدادها، تعمیرات برنامه‌ریزی‌شده و قیمت‌های عمومی
ابرهای ایرانی (آروان، هم‌روش، چابکان و لیارا) را از صفحات عمومی خودشان می‌خواند. نیازی به کلید یا حساب کاربری نیست.

نصب: `claude mcp add iran-cloud-status -- uvx iran-cloud-status-mcp`

Author: Sepehr Radmard (سپهر رادمرد) · github.com/sepehr071 · linkedin.com/in/sepehr-radmard
