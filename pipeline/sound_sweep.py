"""Sound sweeps: screen-record yourself scrolling Instagram's trending audio list, drop the recording in
Drive/sound_sweeps, and the bot cuts it into separate sounds in Drive/audio.

Gemini listens to the recording and reads the song titles off the screen to find where each preview starts and
stops. Without Gemini, the recording is split at the quiet gaps between previews and the sounds are numbered.
Songs you already have (same title, added in the last max_age_days) are skipped.
"""
import re, subprocess
from . import drive, gemini
from .common import CFG, WORK, days_since, load_ledger, log, now, save_ledger
from .render import probe_duration

VIDEO = (".mp4", ".mov", ".m4v")
PROMPT = """This is a screen recording of someone browsing Instagram's trending audio list, previewing songs one
after another. List every distinct song or sound that plays for at least 4 seconds. Use the song title and artist
shown on screen while it plays. Times are in seconds from the start of the recording; trim off any tapping,
loading or silence at the edges. Mark is_music false for speech, voiceovers or app noises.
Return only JSON: {"sounds": [{"start": s, "end": s, "title": "...", "artist": "...", "is_music": true}]}"""


def safe(text):
    return re.sub(r"[^A-Za-z0-9 '&()-]+", "", text or "").strip()[:60] or "sound"


def quiet_gaps(path, dur):
    """Fallback without Gemini: split at silences between previews."""
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-40dB:d=0.4", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    edges = sorted([0.0] + starts + ends + [dur])
    out, n = [], 0
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= 5:
            n += 1
            out.append({"start": a, "end": b, "title": f"untitled {now():%H%M} {n}", "artist": "", "is_music": True,
                        "dedupe": False})
    return out


def main():
    led = load_ledger()
    sweeps = [f for f in drive.list_files("sound_sweeps") if f["name"].lower().endswith(VIDEO)]
    if not sweeps:
        print("No sound sweeps"); return
    max_age = CFG.get("sounds", {}).get("max_age_days", 45)
    have = {re.sub(r"^sweep \S+ - ", "", f["name"].rsplit(".", 1)[0]).lower()
            for f in drive.list_files("audio")
            if not f.get("createdTime") or days_since(f["createdTime"].replace("Z", "+00:00")) < max_age}
    stamp = now().strftime("%Y-%m-%d")
    for f in sweeps:
        try:
            p = drive.download(f["id"], WORK / f["name"])
            dur = probe_duration(p)
            found, who = None, "quiet gaps"
            if gemini.available():
                try:
                    found = gemini.ask(PROMPT, video=gemini.small_video(p, max_seconds=900, fps=2)).get("sounds")
                    who = "gemini"
                except Exception as e:
                    log(led, f"Gemini couldn't read sweep {f['name']}, splitting at gaps: {e}")
            if found is None:
                found = quiet_gaps(p, dur)
            added = 0
            for s in found:
                a, b = max(0.0, float(s.get("start", 0))), min(dur, float(s.get("end", 0)))
                if not s.get("is_music", True) or b - a < 4:
                    continue
                label = safe(f"{s.get('title', '')}" + (f" - {s['artist']}" if s.get("artist") else ""))
                if s.get("dedupe", True) and label.lower() in have:
                    continue
                out = WORK / f"sweep {stamp} - {label}.m4a"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-i", str(p), "-t", f"{min(b - a, 30):.2f}",
                                "-vn", "-c:a", "aac", "-b:a", "160k",
                                "-af", "afade=t=in:d=0.15", str(out)], check=True)
                drive.upload(out, "audio", out.name, mime="audio/mp4")
                have.add(label.lower()); added += 1
            drive.move(f["id"], "sound_sweeps_done")
            log(led, f"Sound sweep {f['name']} ({who}): {added} new sounds added to audio")
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't process sound sweep {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    main()
