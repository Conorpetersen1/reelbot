"""Things Conor needs to do: shown at the top of the dashboard, and sent as a GitHub notification by the daily
health check. Each reminder: {"key": unique id, "text": what to do, "due": today-or-later date or None}."""
import datetime as dt
from . import usage
from .common import CFG, now

NAMES = {"claude": "Claude (Anthropic)", "gemini": "Gemini (Google AI Studio)", "fal": "fal.ai"}
WHERE = {"claude": "console.anthropic.com › Billing", "gemini": "aistudio.google.com › Billing",
         "fal": "fal.ai › Billing"}


def active(led):
    today = now().date()
    out = []
    # Your own dated reminders: show from 3 days before until a week after (then remove them from config.yaml)
    for r in CFG.get("reminders") or []:
        try:
            d = dt.date.fromisoformat(str(r["date"]))
        except Exception:
            continue
        if -7 <= (d - today).days <= 3:
            when = "today" if d == today else (f"by {d:%a %d %b}" if d > today else f"was due {d:%a %d %b}")
            out.append({"key": f"own-{d}-{r['text'][:30]}", "text": f"{r['text']} ({when})", "due": d})
    # Credit running low
    warn = (CFG.get("api_credits") or {}).get("warn_when_left") or {}
    s = usage.summary()
    for api in usage.APIS:
        left = s[api]["left"]
        if left is not None and left < float(warn.get(api, 2)):
            bal = ((CFG.get("api_credits") or {}).get(api) or {}).get("balance") or {}
            out.append({"key": f"credit-{api}-{bal.get('date')}-{bal.get('amount')}", "due": today,
                        "text": f"Top up {NAMES[api]}: about {s[api]['sym']}{max(left, 0):.2f} left ({WHERE[api]}). "
                                f"Then update its balance under api_credits in config.yaml, or tell Claude."})
    # Sounds going stale
    every = CFG.get("sound_sweep_every_days", 12)
    newest = led.get("sound_newest")
    if newest:
        age = (today - dt.date.fromisoformat(newest[:10])).days
        if age >= every:
            out.append({"key": f"sweep-{newest[:10]}", "due": today,
                        "text": f"Do a sound sweep: your newest sound is {age} days old. Screen-record yourself "
                                "playing 15-25 trending sounds (10+ s each) and drop it in Drive › sound_sweeps."})
    else:
        out.append({"key": "sweep-none", "due": today,
                    "text": "Do a sound sweep: no sounds in Drive › audio yet."})
    # Keys expiring within 2 weeks
    for key, d in (CFG.get("key_expiry") or {}).items():
        days = (dt.date.fromisoformat(str(d)) - today).days
        if days <= 14:
            out.append({"key": f"expiry-soon-{key}-{d}", "due": dt.date.fromisoformat(str(d)),
                        "text": f"{key} expires {'today' if days == 0 else f'in {days} days' if days > 0 else 'already'}"
                                f" ({d}). Make a new key, update the GitHub secret, then change the date in config.yaml."})
    return out
