"""Public price lists, read from the server-rendered HTML of the providers' own pricing pages (Toman)."""

from __future__ import annotations

import re

from .http import PRICE_TTL, ApiError, fetch

LIARA_URL = "https://liara.ir/pricing/"
HAMRAVESH_URL = "https://hamravesh.com/pricing"
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٬،", "0123456789,,")

# One plan card: name, monthly price, hourly price, then RAM (GB), vCPU, disk (GB SSD).
LIARA_ROW = re.compile(
    r'font-weight:700;width:100%">([^<]+)</p></div>'
    r'<div><p class="info">([^<]+)<!-- --> <span>تومان</span></p></div>'
    r'<div><p class="info">([^<]+)<!-- --> <span>تومان</span></p></div>'
    r'<div><p class="info">([^<]+)</p></div><div><p class="info">([^<]+)</p></div><div><p class="info">([^<]+)</p></div>'
)
LIARA_SECTIONS = {"paas": "PaaS", "dbaas": "DBaaS", "iaas": "IaaS"}
LIARA_IDS = "paas|dbaas|iaas|email|ai|wordpress|object-storage"
# Sections whose cards read as flat text (Persian digits converted first), each with its own column order.
WORDPRESS = re.compile(r"(\S+) ([\d,]+) تومان ([\d,]+) تومان (\d+) (\d+) (\d+)(?= )")
EMAIL = re.compile(r"(\S+) ([\d,]+) تومان (\d+) ([\d,]+) بی.نهایت (\d+) (\d+) ([\d,]+) (\d+) (\d+) BCC (\d+) CC")
AI = re.compile(r"(\S+) ([\d,]+) تومان ([\d,]+) تومان (\d+) \D+? (\d+) روز (\d+) روز (\d+)")
OBJECT = re.compile(r"قیمت ماهانه: ([\d,]+) تومان قیمت ساعتی: ([\d,]+) تومان اندازه\(SSD\): (\d+) GB")
CHABOKAN_URL = "https://chabokan.net/pricing/"
CHABOKAN_CARD = re.compile(
    r"<h3[^>]*>([^<]+)</h3>(?:(?!<h3).)*?شروع از(?:<!-- -->)? <strong[^>]*>([^<]+)</strong>"
    r"(?: <span[^>]*>تومان</span>)? <span[^>]*>/ (?:<!-- -->)?([^<]+)</span>",
    re.S,
)
LIARA_NOTES = ("ترافیک دانلود", "IPv4", "موقعیت تمامی پلن")


def num(s: str) -> float:
    v = float(s.translate(DIGITS).replace(",", "").replace("٫", "."))
    return int(v) if v == int(v) else v


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


LIARA_FLAT = {"wordpress": "WordPress", "email": "Email", "ai": "AI", "object-storage": "ObjectStorage"}


def _liara_flat(sid: str, t: str) -> list[dict]:
    """Plans of the sections that are not the shared RAM/vCPU/disk card (flat text, ASCII digits)."""
    if sid == "wordpress":
        return [
            {
                "plan": m[1],
                "month": num(m[2]),
                "hour": num(m[3]),
                "ram_gb": num(m[4]),
                "vcpu": num(m[5]),
                "disk_gb": num(m[6]),
            }
            for m in WORDPRESS.finditer(t)
        ]
    if sid == "email":
        return [
            {
                "plan": m[1],
                "month": num(m[2]),
                "sends_per_day": num(m[3]),
                "sends_per_month": num(m[4]),
                "addresses": num(m[5]),
                "forwards_per_address": num(m[6]),
                "emails_kept": num(m[7]),
                "smtp_users": num(m[8]),
                "max_bcc": num(m[9]),
                "max_cc": num(m[10]),
            }
            for m in EMAIL.finditer(t)
        ]
    if sid == "ai":
        return [
            {
                "plan": m[1],
                "month": num(m[2]),
                "hour": num(m[3]),
                "models": num(m[4]),
                "log_days": num(m[5]),
                "requests_per_second": num(m[7]),
            }
            for m in AI.finditer(t)
        ]
    m = OBJECT.search(t)
    return [{"plan": f"{m[3]} GB SSD", "month": num(m[1]), "hour": num(m[2]), "disk_gb": num(m[3])}] if m else []


async def liara() -> dict:
    h = await fetch(LIARA_URL, html=True, ttl=PRICE_TTL)
    marks = sorted((m.start(), m.group(1)) for m in re.finditer(rf'id="({LIARA_IDS})"', h))
    products: dict[str, list] = {}
    for i, (pos, sid) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(h)
        if sid not in LIARA_SECTIONS:
            if rows := _liara_flat(sid, text(h[pos:end]).translate(DIGITS)):
                products[LIARA_FLAT[sid]] = rows
            continue
        plans = [
            {
                "plan": m[1],
                "month": num(m[2]),
                "hour": num(m[3]),
                "ram_gb": num(m[4]),
                "vcpu": num(m[5]),
                "disk_gb": num(m[6]),
            }
            for m in LIARA_ROW.finditer(h, pos, end)
        ]
        if plans:
            products[LIARA_SECTIONS[sid]] = plans
    if not products:
        raise ApiError("liara.ir pricing page has a layout this server does not know; prices could not be read.")
    notes = [n for n in re.split(r"(?<=[.])\s", text(h)) if len(n) < 160 and any(k in n for k in LIARA_NOTES)]
    out = {
        "provider": "Liara",
        "currency": "Toman",
        "source": LIARA_URL,
        "products": products,
        "notes": [n[:200] for n in notes[:4]],
    }
    if ip := re.search(r"ماهانه ([۰-۹]+) هزار تومان[^.]*?IPv4", text(h)):
        out["iaas_ipv4_per_month"] = num(ip[1]) * 1000
    return out


async def chabokan() -> dict:
    h = await fetch(CHABOKAN_URL, html=True, ttl=PRICE_TTL)
    cards = [
        {"product": m[1].strip(), "from": 0 if m[2].strip() == "رایگان" else num(m[2]), "per": m[3].strip()}
        for m in CHABOKAN_CARD.finditer(h)
    ]
    if not cards:
        raise ApiError("chabokan.net pricing page has a layout this server does not know; prices could not be read.")
    return {
        "provider": "Chabokan",
        "currency": "Toman",
        "source": CHABOKAN_URL,
        "starting_prices": cards,
        "note": "Only the 'from' price of each product; ماه = month, ساعت = hour, from 0 = free tier. Plan tables are on the product pages.",
    }


async def hamravesh() -> dict:
    h = await fetch(HAMRAVESH_URL, html=True, ttl=PRICE_TTL)
    t = text(h)
    out: dict = {"provider": "Hamravesh", "currency": "Toman", "source": HAMRAVESH_URL, "datacenters": {}}
    start = {key: t.find(dc) for dc, key in (("دیتاسنتر ایران", "iran"), ("دیتاسنتر آلمان", "germany"))}
    end = {"iran": start["germany"], "germany": t.find("هزینه ترافیک")}
    for key, i in start.items():
        seg = t[i : end[key]] if i >= 0 and end[key] > i else ""
        rows = re.findall(r"هر (vCore|GB رم|GB فضای ذخیره\u200cسازی SSD)[^۰-۹]*?([۰-۹٬]+) تومان ([۰-۹٬]+) تومان", seg)
        prices = {}
        for name, hour, month in rows:
            k = "vcpu" if name == "vCore" else "ram_gb" if "رم" in name else "disk_gb"
            prices[k] = {"hour": num(hour), "month": num(month)}
        if len(prices) == 3:
            out["datacenters"][key] = {"per_unit": prices}
    traffic = re.search(r"داخلی ([۰-۹٬]+) تومان رایگان خارجی ([۰-۹٬]+) تومان ([۰-۹٬]+) تومان", t)
    if traffic:
        out["traffic_per_gb"] = {
            "domestic_download": num(traffic[1]),
            "domestic_upload": 0,
            "foreign_download": num(traffic[2]),
            "foreign_upload": num(traffic[3]),
        }
    ip = re.search(r"داخلی ([۰-۹٬]+) تومان خارجی ([۰-۹٬]+) تومان هزینه سرویس", t)
    if ip:
        out["dedicated_ip_per_year"] = {"domestic": num(ip[1]), "foreign": num(ip[2])}
    mon = re.search(r"مانیتورینگ عمومی ([۰-۹٬]+) تومان", t)
    if mon:
        out["monitoring_per_month"] = num(mon[1])
    if not out["datacenters"]:
        raise ApiError("hamravesh.com pricing page has a layout this server does not know; prices could not be read.")
    out["note"] = (
        "Monthly price is for a 30-day month (720 hours). Product: Darkube cloud platform; other products have separate prices on the site."
    )
    return out
