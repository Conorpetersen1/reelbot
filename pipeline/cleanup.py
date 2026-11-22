"""Daily: keep Google Drive from filling up.
Deletes (to Drive's bin) reels in 'posted' after 14 days, and analysed trend / sound-sweep recordings after 30 days.
Your own clips, AI clips and sounds are never touched here.
"""
from . import drive
from .common import CFG, days_since, load_ledger, log, save_ledger

RULES = (("posted", "keep_posted_days", 14), ("trends_done", "keep_recordings_days", 30),
         ("sound_sweeps_done", "keep_recordings_days", 30))


def main():
    led = load_ledger()
    c = CFG.get("cleanup", {})
    total = 0
    for folder, key, default in RULES:
        keep = c.get(key, default)
        for f in drive.list_files(folder):
            created = (f.get("createdTime") or "").replace("Z", "+00:00")
            if created and days_since(created) > keep:
                try:
                    drive.trash(f["id"]); total += 1
                except Exception as e:
                    log(led, f"Cleanup couldn't delete {f['name']}: {e}")
    if total:
        log(led, f"Cleanup: moved {total} old files to the Drive bin")
    save_ledger(led)


if __name__ == "__main__":
    main()
