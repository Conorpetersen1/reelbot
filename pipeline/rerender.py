"""Re-render queued reels whose on-screen text was corrected.

Mark a queued entry with "rerender": true (after fixing its hook/description in the ledger) and run the
"Fix a queued reel" workflow. The same clip, style, sound and variation are used; only the text changes.
Add "resound": true (or let the old sound go missing from Drive/audio) to give it a fresh sound, avoiding
sounds already used by other reels that day. Add "voice": true to give it a spoken voiceover (a reel that already
has one is re-voiced from its saved script).
Also runs at the end of every hourly Publish job, so flagged reels get fixed without pressing anything.
"""
from . import drive, llm, voiceover
from .common import CFG, WORK, load_ledger, load_topics, log, save_ledger
from .plan_day import cut, segments, sound_library, pick_sound, trim_start
from .render import render


def main():
    led = load_ledger()
    todo = [e for d in led["queue"].values() for e in d if e.get("rerender") and e.get("status") == "queued"]
    todo.sort(key=lambda e: (e["date"], e["time"]))  # soonest first
    if not todo:
        print("Nothing to re-render"); return
    audio = sound_library(led)
    snd = CFG.get("sounds", {})
    for e in todo:
        try:
            gone = not drive.exists(e["file_id"])
        except Exception:
            gone = False
        if gone:  # Conor deleted it from Drive/queue (a veto): don't bring it back
            e.pop("rerender", None); e.pop("resound", None); e.pop("voice", None)
            log(led, f"Didn't re-render {e['date']} {e['time']} {e['topic']}: it was deleted from the queue")
            save_ledger(led); continue
        if e["kind"] == "ai":
            src = drive.download(e["clip"], WORK / f"re_src_{e['slot']}.mp4")
        else:
            cid, i = e["clip"].rsplit("#", 1)
            clip = led["clips"][cid]; meta = segments(clip)[int(i)]
            full = drive.download(cid, WORK / f"re_full_{e['slot']}_{clip['name']}")
            src = cut(full, meta["start"], meta["end"], WORK / f"re_src_{e['slot']}.mp4")
        music = None
        if not e.get("no_music"):  # no_music: Instagram attaches the sound when posting
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
        vo = None
        if e.get("voice") or e.get("voiceover"):
            try:
                topic = next((t for t in load_topics(led) if t["id"] == e["topic"]), {"category": e.get("category")})
                reel = {"hook": e["hook"], "description": e["description"]}
                text = e.get("voiceover") or voiceover.write_script(topic, reel, llm.reel_mode(topic), led)
                wav = voiceover.speak(text, WORK / f"{out.stem}_vo.wav")
                vo = {"script": text, "wav": wav, "words": voiceover.timings(wav, text)}
            except Exception as ex:
                log(led, f"Voiceover failed for {e['date']} {e['time']} {e['topic']}, kept it without: {ex}")
        render(src, out, e["hook"], e["description"], music=music, style=e.get("style", "question"),
               music_volume=snd.get("sound_volume", 0.9), clip_volume=snd.get("clip_sound_volume", 0.25),
               start=trim_start(e["kind"]), voice=vo["wav"] if vo else None, words=vo["words"] if vo else None,
               **(e.get("variation") or {}))
        e["voiceover"] = vo["script"] if vo else None
        if vo:
            e["voice_name"] = voiceover._au.get("used") or "offline"
        old = e["file_id"]
        e["file_id"] = drive.upload(out, "queue", out.name)
        if e.get("no_music") and not e.get("fallback_file_id"):
            e["fallback_file_id"] = old  # keep the baked version in case Instagram won't attach the sound
        else:
            try:
                drive.trash(old)
            except Exception as ex:
                print("Couldn't remove the old file:", ex)
        e.pop("rerender", None); e.pop("resound", None); e.pop("voice", None)
        log(led, f"Re-rendered {e['date']} {e['time']} {e['topic']}" + (f" with sound {e['sound']}" if e.get("sound") else "")
            + (" and a voiceover" if vo else ""))
        save_ledger(led)


if __name__ == "__main__":
    main()
