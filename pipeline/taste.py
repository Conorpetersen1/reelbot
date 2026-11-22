"""Learning Conor's taste from what he bins.

- Delete a mascot clip from Drive/ai_library, or a mascot reel from Drive/queue: the clip is "binned" and
  never used again.
- Once a day, if there are newly binned clips, Gemini watches them next to clips he kept and writes short
  "avoid" rules plus a negative prompt. New clips are generated with those (see gen_ai_clips.make_clip).
- Topics with 2+ binned clips get fewer new clips.
- Delete a real-clip reel from Drive/queue: that moment is never paired with that topic again.
"""
import random, subprocess
from . import drive, gemini
from .common import CFG, WORK, load_topics, log, now

MAX_REVIEW = 6   # binned clips shown to Gemini at once
KEPT_SHOWN = 3   # kept clips shown alongside, for contrast

PROMPT = """These are short animated clips of a blocky cartoon snowboarder mascot (made with an AI video model) for
Conor's snowboarding Instagram. Each clip is labelled in the top-left corner: BINNED clips are ones Conor deleted
because he didn't like them; KEPT clips are ones he kept. The scene each clip was asked for is listed below.

{scenes}

What he dislikes so far (update this, keep what still holds): {previous}

Work out what the BINNED clips have in common that the KEPT ones don't: e.g. motion (stiff, glitchy, sliding,
floating), the character (wrong outfit or colours, odd face, extra limbs, off-model), the scene, the camera,
the style. Be specific and only say things you can actually see.
Return only JSON: {{"avoid": ["max 8 short rules for future clips, e.g. 'no close-ups of the face'"],
"negative_prompt": "comma-separated terms for the video model to avoid",
"summary": "one sentence for Conor on what you learned"}}"""


def _record(led, clip_id, clip, where):
    topics = {t["id"]: t for t in load_topics(led)}
    led.setdefault("ai_binned", []).append({
        "id": clip_id, "name": clip.get("name"), "topic": clip.get("topic"),
        "scene": clip.get("scene") or topics.get(clip.get("topic"), {}).get("ai_scene", ""),
        "created": clip.get("created"), "binned": now().isoformat(), "where": where, "reviewed": False})


def note_deleted(led):
    """Clips missing from Drive were deleted by Conor: forget them, and remember mascot clips as binned."""
    for key, store in (("ai_library", "ai_clips"), ("real", "clips")):
        present = {f["id"] for f in drive.list_files(key)}
        for cid in [c for c in led[store] if c not in present]:
            clip = led[store].pop(cid)
            if store == "ai_clips":
                _record(led, cid, clip, "ai_library")
                log(led, f"You binned mascot clip {clip.get('name')}: the bot will learn from it")


def note_veto(led, entry):
    """A queued reel deleted from Drive/queue."""
    if entry["kind"] == "ai":
        clip = led["ai_clips"].get(entry["clip"])
        if clip:
            clip["rejected"] = "deleted from queue by Conor"
            _record(led, entry["clip"], clip, "queue")
    else:
        from .plan_day import segments
        cid, i = entry["clip"].rsplit("#", 1)
        clip = led["clips"].get(cid)
        segs = segments(clip) if clip else []
        if int(i) < len(segs):
            segs[int(i)].setdefault("misfit", []).append(entry["topic"])


def binned_topics(led):
    count = {}
    for b in led.get("ai_binned", []):
        count[b.get("topic")] = count.get(b.get("topic"), 0) + 1
    return {t for t, n in count.items() if n >= 2}


def _labelled(src, label, out):
    from .render import FONT_BOLD
    font = FONT_BOLD.replace(":", r"\:")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", "10", "-an",
                    "-vf", "scale=360:640:force_original_aspect_ratio=increase,crop=360:640,fps=6,"
                           f"drawtext=fontfile='{font}':text='{label}':x=10:y=10:fontsize=30:fontcolor=white:"
                           "box=1:boxcolor=black@0.8:boxborderw=6",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def review(led):
    """If there are newly binned mascot clips, have Gemini work out what Conor dislikes. Cheap: one request."""
    new = [b for b in led.get("ai_binned", []) if not b.get("reviewed")]
    if not new or not gemini.available():
        return
    parts, scenes = [], []
    for n, b in enumerate(new[-MAX_REVIEW:], 1):
        try:  # deleted files sit in Drive's bin for 30 days and can still be downloaded
            src = drive.download(b["id"], WORK / f"binned_{n}.mp4")
            parts.append(_labelled(src, f"BINNED {n}", WORK / f"lab_b{n}.mp4"))
            scenes.append(f"BINNED {n}: {b.get('scene', '')}")
        except Exception as e:
            print("Couldn't fetch binned clip", b.get("name"), e)
    kept = [(cid, c) for cid, c in led["ai_clips"].items() if not c.get("rejected")]
    for n, (cid, c) in enumerate(random.sample(kept, min(KEPT_SHOWN, len(kept))), 1):
        try:
            src = drive.download(cid, WORK / f"kept_{n}.mp4")
            parts.append(_labelled(src, f"KEPT {n}", WORK / f"lab_k{n}.mp4"))
            topic = next((t for t in load_topics(led) if t["id"] == c.get("topic")), {})
            scenes.append(f"KEPT {n}: {c.get('scene') or topic.get('ai_scene', '')}")
        except Exception as e:
            print("Couldn't fetch kept clip", c.get("name"), e)
    if not any("BINNED" in s for s in scenes):
        return
    lst = WORK / "taste_list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    reel = WORK / "taste_review.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(reel)], check=True)
    prev = led.get("ai_taste") or {}
    try:
        r = gemini.ask(PROMPT.format(scenes="\n".join(scenes), previous="; ".join(prev.get("avoid", [])) or "nothing yet"),
                       video=reel)
    except Exception as e:
        log(led, f"Couldn't review binned clips yet: {e}"); return
    led["ai_taste"] = {"avoid": [str(a)[:120] for a in (r.get("avoid") or [])][:8],
                       "negative_prompt": str(r.get("negative_prompt", ""))[:400],
                       "summary": str(r.get("summary", ""))[:300], "updated": now().isoformat(),
                       "binned_total": len(led.get("ai_binned", []))}
    for b in new:
        b["reviewed"] = True
    log(led, f"Learned from {len(new)} binned clips: {led['ai_taste']['summary']}")


def scene_extra(led):
    avoid = (led.get("ai_taste") or {}).get("avoid") or []
    return (" Avoid: " + "; ".join(avoid) + ".") if avoid else ""


def negative(led):
    base = "text, logos, watermark, blurry, distorted, extra limbs, melting, glitch"
    extra = (led.get("ai_taste") or {}).get("negative_prompt", "")
    return f"{base}, {extra}" if extra else base
