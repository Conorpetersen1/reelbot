"""Daily health check: tells you (GitHub notification) when something needs attention.
Also called by the schedules if a run crashes: python -m pipeline.health --crashed "Daily plan"
"""
import datetime as dt, sys
from . import drive
from .common import CFG, load_ledger, notify_once, now, save_ledger


def main():
    led = load_ledger()
    today = now().date()
    if "--crashed" in sys.argv:
        name = sys.argv[sys.argv.index("--crashed") + 1]
        notify_once(led, f"crash-{name}-{today}", f"⚠️ reelbot: '{name}' crashed",
                    f"The **{name}** run failed today. Open the repo › **Actions**, click the red run and copy the "
                    "error into Claude. Other runs carry on as normal.")
        save_ledger(led); return
    for key, d in (CFG.get("key_expiry") or {}).items():
        days = (dt.date.fromisoformat(str(d)) - today).days
        if days <= 3:
            notify_once(led, f"expiry-{key}-{d}", f"🔑 {key} expires {'today' if days <= 0 else f'in {days} days'}",
                        f"Your **{key}** expires on {d}. Make a new key, update the GitHub secret "
                        "(Settings › Secrets and variables › Actions), then change or delete the date in config.yaml.")
    if today.weekday() == 0:  # Monday
        week = today.isocalendar()
        notify_once(led, f"status-{week[0]}-{week[1]}", "🗓️ Weekly 1-minute check: Instagram Account Status",
                    "Open Instagram › Settings › **Account Status** and check there are no warnings about "
                    "*recommendations* or *unoriginal content*. If there are, set `paused: true` in config.yaml and "
                    "tell Claude what it says. Also worth a look on the dashboard: graduate this week's best reels.")
    month = now().strftime("%Y-%m")
    if led.get("fal_spend", {}).get(month, 0) >= CFG.get("fal_monthly_budget_usd", 30) - 1:
        notify_once(led, f"budget-{month}", "💸 AI video budget reached for this month",
                    "The bot has stopped making new mascot clips until next month and is reusing existing ones. "
                    "To keep generating, top up fal and raise `fal_monthly_budget_usd` in config.yaml.")
    from . import reminders
    for r in reminders.active(led):
        if r["key"].startswith("expiry-soon-"):
            continue  # key expiry has its own notification above (3 days out)
        if r.get("due") is None or r["due"] <= today:
            notify_once(led, f"reminder-{r['key']}", "📌 " + r["text"][:70], r["text"])
    try:
        used, limit = drive.quota()
        if limit and used / limit > 0.9:
            notify_once(led, f"drive-full-{month}", "💾 Google Drive is over 90% full",
                        f"Drive is at {used / limit:.0%}. When it's full, uploads fail and reels stop. "
                        "Delete big raw clips you've finished with from reelbot › real, or empty Drive's bin.")
    except Exception as e:
        print("Drive quota check skipped:", e)
    save_ledger(led)


if __name__ == "__main__":
    main()
