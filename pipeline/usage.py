"""What the bot spends on Claude, Gemini and fal, so the dashboard can show costs and warn before credit runs out.

Every Claude and Gemini reply carries its token counts; these are priced with api_prices in config.yaml.
fal jobs are recorded at fal_cost_estimate_usd when they're charged. Everything here is an estimate: the
provider's own billing page is the final word.
"""
import datetime as dt, json
from .common import CFG, ROOT, now

PATH = ROOT / "data" / "usage.json"
APIS = ("claude", "gemini", "fal")


def load():
    if PATH.exists():
        try:
            return json.loads(PATH.read_text())
        except Exception:
            pass
    return {"days": {}, "calls": {}}


def add(api, usd, calls=1):
    u = load()
    day = now().date().isoformat()
    d = u["days"].setdefault(day, {})
    d[api] = round(d.get(api, 0) + usd, 6)
    c = u["calls"].setdefault(day, {})
    c[api] = c.get(api, 0) + calls
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(u, indent=1, sort_keys=True))


def _price(api, model):
    """(input, output) US$ per million tokens for this model on this date."""
    table = (CFG.get("api_prices") or {}).get(api) or {}
    today = now().date().isoformat()
    best, best_len = table.get("default", [2.0, 10.0]), -1
    for name, p in table.items():
        if name == "default" or not str(model).startswith(name) or len(name) <= best_len:
            continue
        if isinstance(p, dict):  # {"until": date, "price": [..], "after": [..]}
            p = p["price"] if today <= str(p.get("until", "9999")) else p.get("after", p["price"])
        best, best_len = p, len(name)
    return best


def claude(msg):
    try:
        u = msg.usage
        tin = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        tin += 0.1 * (getattr(u, "cache_read_input_tokens", 0) or 0)
        tout = getattr(u, "output_tokens", 0) or 0
        pin, pout = _price("claude", getattr(msg, "model", CFG.get("claude_model", "")))
        add("claude", (tin * pin + tout * pout) / 1e6)
    except Exception as e:
        print("Couldn't record Claude usage:", e)


def gemini(model, meta):
    try:
        meta = meta or {}
        tin = meta.get("promptTokenCount", 0) or 0
        tout = (meta.get("candidatesTokenCount", 0) or 0) + (meta.get("thoughtsTokenCount", 0) or 0)
        pin, pout = _price("gemini", model)
        add("gemini", (tin * pin + tout * pout) / 1e6)
    except Exception as e:
        print("Couldn't record Gemini usage:", e)


def summary():
    """Spend per API: today, last 7 days, this month, all time, and the estimated credit left."""
    u = load()
    today = now().date()
    month = today.strftime("%Y-%m")
    credits = CFG.get("api_credits") or {}
    out = {}
    for api in APIS:
        days = {d: v.get(api, 0) for d, v in u["days"].items()}
        c = credits.get(api) or {}
        topups = c.get("topups") or []
        put_in = sum(float(t.get("usd", 0)) for t in topups)
        before = float(c.get("spent_before_tracking", 0))
        total = sum(days.values()) + before
        out[api] = {
            "today": days.get(today.isoformat(), 0),
            "week": sum(v for d, v in days.items() if (today - dt.date.fromisoformat(d)).days < 7),
            "month": sum(v for d, v in days.items() if d.startswith(month)),
            "total": total,
            "put_in": put_in,
            "left": (put_in - total) if topups else None,
            "last_topup": max(topups, key=lambda t: str(t.get("date", "")), default=None),
            "calls_week": sum(v.get(api, 0) for d, v in u["calls"].items()
                              if (today - dt.date.fromisoformat(d)).days < 7),
        }
    # Daily totals for the last 30 days, for the chart
    out["daily"] = [((today - dt.timedelta(days=i)).isoformat(),
                     round(sum(u["days"].get((today - dt.timedelta(days=i)).isoformat(), {}).values()), 2))
                    for i in range(29, -1, -1)]
    return out
