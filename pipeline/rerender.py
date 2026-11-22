"""Re-render queued reels whose on-screen text was corrected.

Mark a queued entry with "rerender": true (after fixing its hook/description in the ledger) and run the
"Fix a queued reel" workflow. The same clip, style, sound and variation are used; only the text changes.
"""
from . import drive
from .common import CFG, WORK, load_ledger, log, save_ledger
from .plan_day import cut, segments, sound_library, pick_sound
from .render import render


def main():
    led = load_ledger()
    todo = [e for d in led["queue"].values() for e in d if e.get("rerender") and e.get("status") == "queued"]
    if not todo:
        print("Nothing to re-render"); return
    audio = sound_library(led)
    snd = CFG.get("sounds", {})
    for e in todo:
        if e["kind"] == "ai":
            src = drive.download(e["clip"], WORK / f"re_src_{e['slot']}.mp4")
        else:
            cid, i = e["clip"].rsplit("#", 1)
            clip = led["clips"][cid]; meta = segments(clip)[int(i)]
            full = drive.download(cid, WORK / f"re_full_{e['slot']}_{clip['name']}")
            src = cut(full, meta["start"], meta["end"], WORK / f"re_src_{e['slot']}.mp4")
        music = None
        if e.get("sound"):
            match = [s for s in audio if s["name"] == e["sound"]]
            if match:
                music = pick_sound(led, match)["path"]
        out = WORK / e["file"]
        render(src, out, e["hook"], e["description"], music=music, style=e.get("style", "question"),
               music_volume=snd.get("sound_volume", 0.9), clip_volume=snd.get("clip_sound_volume", 0.25),
               **(e.get("variation") or {}))
        old = e["file_id"]
        e["file_id"] = drive.upload(out, "queue", out.name)
        try:
            drive.trash(old)
        except Exception as ex:
            print("Couldn't remove the old file:", ex)
        e.pop("rerender", None)
        log(led, f"Re-rendered {e['date']} {e['time']} {e['topic']} with corrected text")
        save_ledger(led)


if __name__ == "__main__":
    main()
