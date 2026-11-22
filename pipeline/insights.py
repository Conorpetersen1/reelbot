"""Daily job: pull stats for reels posted in the last 30 days, plus your follower count."""
from . import ig
from .common import days_since, load_ledger, log, now, save_ledger


def main():
    led = load_ledger()
    try:
        led["followers"][now().date().isoformat()] = ig.me("followers_count")["followers_count"]
    except Exception as e:
        log(led, f"Follower count failed: {e}")
    n = 0
    for mid, p in led["posts"].items():
        if days_since(p["posted_at"]) > 30:
            continue
        try:
            p["insights"] = ig.insights(mid); p["insights_at"] = now().isoformat(); n += 1
            if not p.get("permalink"):
                p["permalink"] = ig.permalink(mid)
        except Exception as e:
            log(led, f"Insights failed for {mid}: {e}")
    log(led, f"Updated insights for {n} reels")
    save_ledger(led)


if __name__ == "__main__":
    main()
