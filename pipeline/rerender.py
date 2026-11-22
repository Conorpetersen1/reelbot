"""Re-render queued reels whose on-screen text was corrected.

Mark a queued entry with "rerender": true (after fixing its hook/description in the ledger) and run the
"Fix a queued reel" workflow. The same clip, style, sound and variation are used; only the text changes.
Add "resound": true (or let the old sound go missing from Drive/audio) to give it a fresh sound, avoiding
sounds already used by other reels that day.
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
        match = [s for s in audio if s["name"] == e.get("sound")]
        if e.get("sound") and match and not e.get("resound"):
            music = pick_sound(led, match)["path"]
        elif e.get("sound") or e.get("resound"):
            if not audio:
                log(led, f"No sounds in Drive/audio, left {e['date']} {e['time']} {e['topic']} as it was")
                e.pop("rerender", None); e.pop("resound", None); save_ledger(led)
                continue
            taken = {x.get("sound") for x in led["queue"].get(e["date"], []) if x is not e}
            pool = [s for s in audio if s["name"] not in taken] or audio
            s = pick_sound(led, pool)
            music = s["path"]
            e["sound"] = s["name"]; e["sound_kind"] = "sound"
            u = led.setdefault("sound_uses", {}); u[s["name"]] = u.get(s["name"], 0) + 1
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
        e.pop("rerender", None); e.pop("resound", None)
        log(led, f"Re-rendered {e['date']} {e['time']} {e['topic']}" + (f" with sound {e['sound']}" if e.get("sound") else ""))
        save_ledger(led)


if __name__ == "__main__":
    main()
