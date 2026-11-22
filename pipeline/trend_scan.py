"""Trend inbox: analyse reels you saved to Drive/trends and turn their format into new topic ideas.

From your phone: on a trending reel, screen-record it (or use Share > Download if the creator allows it),
then share the video to the Google Drive app > reelbot > trends. Optional: rename it with a note,
e.g. "this hook is gold - do it about catching edges.mp4". Screenshots work too.
Their footage and audio are never used. Only the format gets copied.
"""
import re, subprocess
from . import drive, gemini, llm
from .common import CFG, WORK, load_ledger, load_topics, log, now, save_ledger, paused
from .render import probe_duration

VIDEO = (".mp4", ".mov", ".m4v", ".webm")
IMAGE = (".png", ".jpg", ".jpeg", ".heic", ".webp")


def scene_cuts(path):
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo",
                        "-f", "null", "-"], capture_output=True, text=True)
    return r.stderr.count("pts_time:")


def frames(path, n=6):
    dur = probe_duration(path)
    out = []
    for i in range(n):
        f = WORK / f"trend_{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur*(i+0.5)/n:.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=540:-2", str(f)], check=True)
        out.append(f)
    return out, dur


def to_jpg(path):
    out = WORK / (path.stem + "_s.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "scale=540:-2", str(out)], check=True)
    return out


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
    led = load_ledger()
    led.setdefault("trends", []); led.setdefault("trend_topics", {})
    files = [f for f in drive.list_files("trends") if f["name"].lower().endswith(VIDEO + IMAGE)]
    if not files:
        print("Trend inbox empty"); return
    ref = None
    for f in files:
        try:
            p = drive.download(f["id"], WORK / f["name"])
            note = re.sub(r"\.[a-z0-9]+$", "", f["name"], flags=re.I)
            if f["name"].lower().endswith(VIDEO):
                fr, dur = frames(p); cuts = scene_cuts(p)
            else:
                fr, dur, cuts = [to_jpg(p)], 0, 0
            hooks = [t["hook"] for t in load_topics(led)]
            a, who = None, "claude"
            if gemini.available():  # Gemini watches the whole video with sound; Claude only sees frames
                try:
                    vid = gemini.small_video(p) if f["name"].lower().endswith(VIDEO) else None
                    a = gemini.ask(llm.trend_prompt("This is" if vid else "This screenshot is from", dur, cuts,
                                                    note, hooks), system=llm.RULES, video=vid,
                                   images=() if vid else fr)
                    who = "gemini"
                except Exception as e:
                    log(led, f"Gemini trend analysis failed, using Claude: {e}")
            if a is None:
                a = llm.analyze_trend(fr, dur, cuts, note, hooks)
            kept = []
            for idea in a.get("ideas", [])[:3]:
                idea["description"] = idea["description"] if isinstance(idea["description"], list) \
                    else str(idea["description"]).split("|")
                ok, why = llm.check_reel(idea, {"hook": idea["hook"], "description": idea["description"]})
                if not ok:
                    log(led, f"Trend idea rejected ({why}): {idea['hook']}"); continue
                tid = f"x{len(led['trend_topics']) + 1:03d}"
                topic = {"id": tid, "category": idea.get("category", "snow_life"), "level": idea.get("level", "any"),
                         "hook": idea["hook"], "description": "|".join(idea["description"]),
                         "manual_ref": idea.get("manual_ref", ""), "cta_keyword": idea.get("cta_keyword", "LESSON"),
                         "clip_tags": idea.get("clip_tags", "lifestyle"), "ai_scene": idea.get("ai_scene", ""),
                         "style": a.get("style", "question"), "added": now().isoformat(), "source": f["name"]}
                led["trend_topics"][tid] = topic; kept.append(tid)
                # Trends go stale fast, so make a couple of AI clips now rather than waiting for Monday
                if topic["ai_scene"] and CFG.get("trend_ai_clips", 0):
                    try:
                        from . import gen_ai_clips
                        ref = ref or gen_ai_clips.mascot_ref()
                        for n in range(CFG["trend_ai_clips"]):
                            gen_ai_clips.make_clip(led, topic, ref, n)
                    except SystemExit as e:
                        log(led, str(e))
                    except Exception as e:  # includes the monthly fal budget being reached
                        log(led, f"No trend AI clip for {tid}: {e}")
            led["trends"].append({"file": f["name"], "date": now().date().isoformat(),
                                  "format": a.get("format_name"), "template": a.get("hook_template"),
                                  "why": a.get("why_it_works"), "style": a.get("style"), "ideas": kept,
                                  "analysed_by": who})
            drive.move(f["id"], "trends_done")
            log(led, f"Trend '{f['name']}' ({who}) -> {a.get('format_name')}: {len(kept)} new ideas {kept}")
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't analyse trend {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    main()
