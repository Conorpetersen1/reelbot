"""Watch any new clips in Drive/real and find their best moments.

Long clips are fine: each clip is split into its best 4-12 second segments, each tagged separately
(heelside, powder, falling...). Gemini watches the whole clip if GEMINI_API_KEY is set; otherwise Claude
looks at frames spread through it.
"""
import subprocess
from . import drive, gemini, llm
from .common import WORK, load_ledger, save_ledger, log, now
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
    led = load_ledger()
    new = [f for f in drive.list_files("real") if f["id"] not in led["clips"]
           and f["name"].lower().endswith((".mp4", ".mov", ".m4v"))]
    for f in new:
        try:
            p = drive.download(f["id"], WORK / f["name"])
            dur = probe_duration(p)
            res, who = None, "claude"
            if gemini.available():
                try:
                    res = gemini.ask(llm.segment_prompt(dur, "This is"), video=gemini.analysis_video(p))
                    who = "gemini"
                except Exception as e:
                    log(led, f"Gemini couldn't watch {f['name']}, using Claude: {e}")
            if res is None:
                res = llm.segment_clip_frames(frames(p, dur), dur)
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
        except Exception as e:
            log(led, f"Couldn't tag {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    main()
