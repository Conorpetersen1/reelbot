"""Hourly job: post the next due reel from today's queue (at most one per run, so posts stay spread out).
A reel you deleted from Drive/queue is skipped."""
from . import drive, ig
from .common import WORK, load_ledger, log, now, save_ledger


def main():
    led = load_ledger()
    t = now()
    today = led["queue"].get(t.date().isoformat(), [])
    for e in sorted(today, key=lambda e: e["slot"]):
        if e["status"] not in ("queued", "retry"):
            continue
        hh, mm = map(int, e["time"].split(":"))
        if t < t.replace(hour=hh, minute=mm, second=0, microsecond=0):
            break  # not due yet
        if not drive.exists(e["file_id"]):
            e["status"] = "vetoed"; log(led, f"Skipped {e['file']} (deleted from queue)"); continue
        try:
            path = drive.download(e["file_id"], WORK / e["file"])
            mid = ig.publish_trial_reel(path, e["caption"])
            e.update(status="posted", media_id=mid, posted_at=t.isoformat())
            led["posts"][mid] = {"topic": e["topic"], "kind": e["kind"], "hook": e["hook"], "date": e["date"],
                                 "posted_at": t.isoformat(), "file": e["file"], "insights": {}}
            drive.move(e["file_id"], "posted")
            log(led, f"Posted trial reel {e['file']} -> {mid}")
        except Exception as err:
            e["attempts"] = e.get("attempts", 0) + 1
            e["status"] = "retry" if e["attempts"] < 3 else "failed"
            e["error"] = str(err)[:300]
            log(led, f"Post failed for {e['file']} (attempt {e['attempts']}): {err}")
        break  # one post per run
    save_ledger(led)


if __name__ == "__main__":
    main()
