"""Look at any new clips in Drive/real and tag them (what's in it, good riding or a mistake, usable?)."""
import subprocess
from . import drive, llm
from .common import WORK, load_ledger, save_ledger, log, now
from .render import probe_duration


def frames(path, n=3):
    dur = probe_duration(path)
    out = []
    for i in range(n):
        f = WORK / f"{path.stem}_f{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur*(i+1)/(n+1):.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=512:-2", str(f)], check=True)
        out.append(f)
    return out, dur


def main():
    led = load_ledger()
    new = [f for f in drive.list_files("real") if f["id"] not in led["clips"]
           and f["name"].lower().endswith((".mp4", ".mov", ".m4v"))]
    for f in new:
        try:
            p = drive.download(f["id"], WORK / f["name"])
            fr, dur = frames(p)
            tags = llm.tag_clip(fr, dur)
            led["clips"][f["id"]] = {"name": f["name"], "duration": dur, "uses": 0, "last_used": None,
                                     "added": now().isoformat(), **tags}
            log(led, f"Tagged {f['name']}: {tags.get('tags')} usable={tags.get('usable')}")
            p.unlink(missing_ok=True)
        except Exception as e:
            log(led, f"Couldn't tag {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    main()
