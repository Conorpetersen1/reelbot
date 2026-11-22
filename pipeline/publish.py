"""Hourly job: post the next due reel from today's queue (at most one per run, so posts stay spread out).
A reel you deleted from Drive/queue is skipped."""
from . import drive, ig
from .common import WORK, load_ledger, log, now, save_ledger, paused, notify_once


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
    led = load_ledger()
    t = now()
    led["publish_heartbeat"] = t.isoformat()  # the daily check warns if hourly posting stops running
    save_ledger(led)
    if led.get("auto_paused"):
        print("Posting auto-paused:", led["auto_paused"]); return
    today = led["queue"].get(t.date().isoformat(), [])
    for e in sorted(today, key=lambda e: e["slot"]):
        if e["status"] not in ("queued", "retry"):
            continue
        hh, mm = map(int, e["time"].split(":"))
        if t < t.replace(hour=hh, minute=mm, second=0, microsecond=0):
            break  # not due yet
        if not drive.exists(e["file_id"]):
            e["status"] = "vetoed"; log(led, f"Skipped {e['file']} (deleted from queue)")
            from . import taste
            taste.note_veto(led, e)
            continue
        try:
            path = drive.download(e["file_id"], WORK / e["file"])
            mid = ig.publish_trial_reel(path, e["caption"])
            e.update(status="posted", media_id=mid, posted_at=t.isoformat())
            led["posts"][mid] = {"topic": e["topic"], "kind": e["kind"], "hook": e["hook"], "date": e["date"],
                                 "posted_at": t.isoformat(), "file": e["file"], "insights": {},
                                 **{k: e.get(k) for k in ("category", "style", "hook_type", "sound_kind", "time", "variation",
                                                          "source", "sound", "fit")}}
            drive.move(e["file_id"], "posted")
            trial = ig.is_trial(mid)
            e["trial"] = led["posts"][mid]["trial"] = trial
            log(led, f"Posted trial reel {e['file']} -> {mid} (Instagram says trial: {trial})")
            if trial is False:
                # It went out as a normal reel to followers: stop everything and tell Conor
                led["auto_paused"] = f"{e['file']} posted as a normal reel, not a trial ({t:%d %b %H:%M})"
                notify_once(led, f"not-trial-{mid}", "⚠️ reelbot: a reel posted as a NORMAL reel, posting paused",
                            f"Instagram says **{e['hook']}** went out to followers, not as a trial. Posting is "
                            "paused. Delete it in the app if you don't want it, then tell Claude.")
        except Exception as err:
            e["attempts"] = e.get("attempts", 0) + 1
            e["status"] = "retry" if e["attempts"] < 3 else "failed"
            e["error"] = str(err)[:300]
            log(led, f"Post failed for {e['file']} (attempt {e['attempts']}): {err}")
            if e["status"] == "failed":
                notify_once(led, f"postfail-{e['file']}", f"❌ A reel failed to post: {e['hook']}",
                            f"Instagram rejected **{e['file']}** 3 times. Last error:\n\n```\n{e['error']}\n```\n"
                            "Common causes: the trial reel daily limit (lower reels_per_day in config.yaml), an expired "
                            "token, or a video Instagram didn't like. Paste the error into Claude if unsure.")
        break  # one post per run
    save_ledger(led)


if __name__ == "__main__":
    main()
