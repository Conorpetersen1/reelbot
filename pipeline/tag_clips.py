"""Watch any new clips in Drive/real and find their best moments.

Long clips are fine: each clip is split into its best 4-12 second segments, each tagged separately
(heelside, powder, falling...). Gemini watches the whole clip if GEMINI_API_KEY is set; otherwise Claude
looks at frames spread through it.
"""
import subprocess, sys, time
from . import drive, gemini, llm
from .common import CFG, WORK, load_ledger, save_ledger, log, now
from .render import probe_duration


def frames(path, dur, n=16):
    step = max(1.5, dur / n)
    out, t = [], step / 2
    while t < dur and len(out) < n:
        f = WORK / f"{path.stem}_f{len(out)}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=480:-2", str(f)], check=True)
        out.append((t, f)); t += step
    return out


def clean(segs, dur):
    good = []
    for s in sorted(segs or [], key=lambda s: float(s.get("start", 0))):
        a, b = max(0.0, float(s.get("start", 0))), min(dur, float(s.get("end", 0)))
        if b - a > 15:
            b = a + 15
        if b - a < 3 or (good and a < good[-1]["end"]):
            continue
        good.append({"start": round(a, 2), "end": round(b, 2), "tags": s.get("tags", []),
                     "summary": s.get("summary", ""), "technique": s.get("technique", "n/a"),
                     "usable": bool(s.get("usable", True)), "uses": 0, "last_used": None})
    return good


def main():
    """Tags up to tag_per_run new clips (oldest first) so a big upload never blocks the evening plan.
    `python -m pipeline.tag_clips --all` (the 'Tag my clips' workflow) works through the whole backlog."""
    led = load_ledger()
    new = sorted([f for f in drive.list_files("real") if f["id"] not in led["clips"]
                  and f["name"].lower().endswith((".mp4", ".mov", ".m4v"))], key=lambda f: f.get("createdTime", ""))
    cap = len(new) if "--all" in sys.argv else CFG.get("tag_per_run", 15)
    if len(new) > cap:
        log(led, f"{len(new)} new clips: tagging {cap} now, the rest in later runs")
    gemini_down = False
    for f in new[:cap]:
        try:
            p = drive.download(f["id"], WORK / f["name"])
            dur = probe_duration(p)
            res, who = None, "claude"
            if gemini.available() and not gemini_down:
                for attempt in range(3):
                    try:
                        res = gemini.ask(llm.segment_prompt(dur, "This is"), video=gemini.analysis_video(p))
                        who = "gemini"; break
                    except Exception as e:
                        err = str(e)
                        if any(c in err for c in ("401", "403", "API key", "PERMISSION_DENIED")):
                            gemini_down = True  # key problem: stop and keep the rest for the next run
                            log(led, f"Gemini key problem ({err[:120]}); leaving remaining clips for the next run")
                            break
                        busy = any(c in err for c in ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED",
                                                      "No Gemini model available"))
                        if attempt < 2:
                            time.sleep(90 if busy else 5); continue  # busy: wait; odd reply: just ask again
                        if busy:
                            gemini_down = True
                            log(led, f"Gemini busy for too long ({err[:120]}); leaving remaining clips for the next run")
                        else:
                            log(led, f"Skipped {f['name']} this run (Gemini reply unreadable: {err[:80]})")
                        break
                time.sleep(4)  # stay under the free tier's per-minute limit
            if res is None:
                if gemini.available():
                    p.unlink(missing_ok=True)
                    if gemini_down:
                        break  # don't fall back to paid Claude for a whole backlog
                    continue  # just this clip failed: it'll be retried next run
                res = llm.segment_clip_frames(frames(p, dur, n=8), dur)
            segs = clean(res.get("segments"), dur)
            if not segs:  # nothing picked: fall back to the whole clip (or its first 12s)
                segs = clean([{"start": 0, "end": min(dur, 12), "tags": ["lifestyle"], "summary": f["name"]}], dur)
            led["clips"][f["id"]] = {"name": f["name"], "duration": dur, "uses": 0, "last_used": None,
                                     "added": now().isoformat(), "segments": segs, "analysed_by": who,
                                     "tags": sorted({t for s in segs for t in s["tags"]}),
                                     "usable": any(s["usable"] for s in segs)}
            log(led, f"{f['name']} ({dur:.0f}s, {who}): {len(segs)} usable moments "
                     f"{[s['tags'][:2] for s in segs if s['usable']][:6]}")
            p.unlink(missing_ok=True)
            save_ledger(led)  # save after every clip so a long run never loses work
        except Exception as e:
            log(led, f"Couldn't tag {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    main()
