"""Every 5 minutes (started by the Ticker): pick up Conor's ratings, refresh the stats of reels posted in the
last 2 days, and rebuild the dashboard. Follower history and view alerts stay hourly (pipeline.alerts)."""
from . import dashboard, ig, ratings
from .common import load_ledger, now, save_ledger, days_since


def main():
    led = load_ledger()
    try:
        ratings.collect(led)
    except Exception as e:
        print("Ratings not collected:", e)
    for mid, p in led["posts"].items():
        if days_since(p.get("posted_at", p.get("date", "2000-01-01"))) > 2:
            continue
        try:
            new = ig.insights(mid)
            if new != p.get("insights"):
                p["insights"] = new; p["insights_at"] = now().isoformat()
            if not p.get("permalink"):
                p["permalink"] = ig.permalink(mid)
        except Exception as e:
            print("Stats not refreshed for", mid, e)
    save_ledger(led)
    dashboard.main()


if __name__ == "__main__":
    main()
