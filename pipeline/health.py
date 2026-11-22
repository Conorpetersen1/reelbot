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
    month = now().strftime("%Y-%m")
    if led.get("fal_spend", {}).get(month, 0) >= CFG.get("fal_monthly_budget_usd", 30) - 1:
        notify_once(led, f"budget-{month}", "💸 AI video budget reached for this month",
                    "The bot has stopped making new mascot clips until next month and is reusing existing ones. "
                    "To keep generating, top up fal and raise `fal_monthly_budget_usd` in config.yaml.")
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
