"""Trend inbox: analyse reels you saved to Drive/trends and turn their format into new topic ideas.

From your phone: on a trending reel, screen-record it (or use Share > Download if the creator allows it),
then share the video to the Google Drive app > reelbot > trends. Optional: rename it with a note,
e.g. "this hook is gold - do it about catching edges.mp4". Screenshots work too.
Their footage and audio are never used. Only the format gets copied.

Scroll mode: a recording longer than SCROLL_SECONDS is treated as you scrolling your Reels feed. Gemini watches every
reel in it, finds the formats that are trending, and adapts the best into up to 6 ideas. The latest trending
formats are also given to the writer (led["feed_formats"]).
"""
import json, re, subprocess
from . import drive, gemini, llm
from .common import CFG, WORK, days_since, load_ledger, load_topics, log, now, save_ledger, paused
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


SCROLL_SECONDS = 75  # recordings longer than this are treated as a scroll through many reels

SCROLL_PROMPT = """This is a screen recording of Conor scrolling his Instagram Reels / Explore feed (his feed is tuned
to snow content). A yellow clock in the corner shows seconds. Watch every reel that appears, even briefly.

{voice}

1. List each distinct reel: when it appears (seconds), its niche, the exact on-screen hook text, the format
   (e.g. "POV + reaction", "expectation vs reality", "satirical list", "tutorial with numbered steps"), the likes /
   views / comments you can see (Instagram shows like and comment counts beside or under each reel; copy them
   exactly, e.g. "412K likes"), the SOUND (song/audio name shown on screen), whether the edit only works with that
   exact sound (beat-synced cuts, an audio meme), and why it holds attention.
   Set "viral": true for reels with clearly huge numbers (roughly 50K+ likes or 1M+ views) or that are obviously
   blowing up.
2. Find the formats that are TRENDING. Rank them by how VIRAL they are (big numbers first), then by how often they
   repeat. Include entertainment formats (relatable humour, memes, POVs, skits, satire), not only tutorials:
   Conor's own biggest reels (up to 1.2M views) are relatable humour, as his voice guide above shows.
3. Adapt the best formats into ideas for his account (max 8 ideas, max 2 per format). At least HALF must be
   entertainment / relatable / snow-life ideas in his voice (category "snow_life"); the rest can be teaching tips.
   Each will be made with an animated blocky mascot clip or his riding footage, plus on-screen text. Copy the
   FORMAT only, never another creator's words or footage. Teaching ideas may only use technique the rules allow.
   Don't repeat these existing hooks: {hooks}

Return only JSON:
{{"reels": [{{"t": seconds, "niche": "...", "hook": "...", "format": "...", "numbers": "...", "viral": true/false,
   "sound": "Song - Artist or ''", "sound_dependent": true/false, "why": "..."}}],
 "trending_sounds": [{{"sound": "Song - Artist", "seen": n}}],
 "trending_formats": [{{"format_name": "...", "hook_template": "...", "why_it_works": "one sentence",
   "style": "question|list|pov", "seen": n, "viral_examples": n, "kind": "entertainment|teaching",
   "needs_sound": "Song - Artist if the format only works with it, else ''"}}],
 "ideas": [{{"format_name": "...", "category": "tip|snow_life", "level": "beginner|new_rider|any",
   "hook": "max 45 chars", "description": ["0-3 lines, max 38 chars each"], "manual_ref": "APSI section or empty",
   "cta_keyword": "ONEWORD", "clip_tags": "main_tag,other_tags from {tags}",
   "ai_scene": "what the animated mascot clip should show",
   "needs_sound": "Song - Artist if this idea only works with that sound, else ''"}}]}}"""


def scroll_analysis(p, hooks):
    from .sound_sweep import clocked_video
    vid = clocked_video(p, height=480, fps=2, crf=34, max_seconds=1200)  # up to 20 minutes; enough to read hooks
    from .voice import guide
    led = load_ledger()
    return gemini.ask(SCROLL_PROMPT.format(hooks=json.dumps(hooks[-60:]), tags=llm.TAGS, voice=guide(led)),
                      system=llm.RULES, video=vid, video_fps=2)


def catch_up_clips(led, max_n=8):
    """Fresh trend ideas (last 14 days) with no mascot clip yet, e.g. because fal was out of credit: make one each."""
    from . import gen_ai_clips
    have = {c["topic"] for c in led["ai_clips"].values()}
    todo = [t for tid, t in led.get("trend_topics", {}).items()
            if tid not in have and t.get("ai_scene") and days_since(t.get("added")) <= 14][:max_n]
    if not todo:
        return
    ref = gen_ai_clips.mascot_ref()
    made = 0
    for t in todo:
        try:
            gen_ai_clips.make_clip(led, t, ref, 0); made += 1
            save_ledger(led)
        except SystemExit as e:
            log(led, str(e)); break
        except Exception as e:
            log(led, f"Still no trend AI clip for {t['id']}: {e}")
            if "403" in str(e) or "budget" in str(e).lower():
                break
    if made:
        log(led, f"Made {made} mascot clips for trend ideas that were waiting")


def redo(date):
    """Re-analyse scroll recordings from a date: move them from trends_done back into the inbox."""
    led = load_ledger()
    back = [f for f in drive.list_files("trends_done") if f["name"].lower().endswith(VIDEO)
            and (f.get("createdTime") or "")[:10] >= date]
    for f in back:
        drive.move(f["id"], "trends")
    log(led, f"Redo trends {date}: {len(back)} recordings back in the inbox")
    save_ledger(led)


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
    led = load_ledger()
    try:
        catch_up_clips(led)
    except Exception as e:
        print("Trend clip catch-up skipped:", e)
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
            scroll = f["name"].lower().endswith(VIDEO) and dur > SCROLL_SECONDS
            if scroll:
                if not gemini.available():
                    log(led, f"Left scroll recording {f['name']}: needs Gemini"); continue
                a, who = scroll_analysis(p, hooks), "gemini (scroll)"
                fmts = a.get("trending_formats") or []
                a["format_name"] = ", ".join(x.get("format_name", "") for x in fmts[:4])
                a["hook_template"] = " | ".join(x.get("hook_template", "") for x in fmts[:4])
                a["why_it_works"] = f"{len(a.get('reels') or [])} reels watched, {len(fmts)} trending formats"
                led["feed_formats"] = {"date": now().isoformat(), "formats": fmts[:8]}
                want = {w["sound"].lower(): w for w in led.get("sounds_wanted", [])}
                for x in (a.get("trending_sounds") or [])[:15]:
                    if x.get("sound"):
                        w = want.setdefault(x["sound"].lower(), {"sound": x["sound"], "seen": 0, "for": []})
                        w["seen"] += int(x.get("seen", 1) or 1); w["date"] = now().isoformat()
                for i in a.get("ideas") or []:
                    if i.get("needs_sound"):
                        want.setdefault(i["needs_sound"].lower(), {"sound": i["needs_sound"], "seen": 1, "for": []})
                led["sounds_wanted"] = sorted(want.values(), key=lambda w: -w["seen"])[:20]
            elif gemini.available():  # Gemini watches the whole video with sound; Claude only sees frames
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
            for idea in a.get("ideas", [])[:(8 if scroll else 3)]:
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
                         "style": a.get("style", "question") if not scroll else next((x.get("style", "question") for x in (a.get("trending_formats") or []) if x.get("format_name") == idea.get("format_name")), "question"),
                         "added": now().isoformat(), "source": f["name"],
                         "needs_sound": (idea.get("needs_sound") or "").strip()}
                if topic["needs_sound"]:
                    for w in led.get("sounds_wanted", []):
                        if w["sound"].lower() == topic["needs_sound"].lower():
                            w.setdefault("for", []).append(tid)
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
                                  "analysed_by": who,
                                  "reels": (a.get("reels") or [])[:80] if scroll else None})
            drive.move(f["id"], "trends_done")
            log(led, f"Trend '{f['name']}' ({who}) -> {a.get('format_name')}: {len(kept)} new ideas {kept}")
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't analyse trend {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--redo":
        redo(sys.argv[2])
    main()
