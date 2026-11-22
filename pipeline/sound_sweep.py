"""Sound sweeps: screen-record yourself scrolling Instagram's trending audio list, drop the recording in
Drive/sound_sweeps, and the bot cuts it into separate sounds in Drive/audio.

Gemini watches for the taps: a sound starts when Conor taps play on a track and ends when he switches to another.
Sounds Gemini isn't sure about, or shorter than MIN_SOUND, are skipped. If Gemini can't read the recording at all,
it's left in sound_sweeps to try again the next day.
Songs you already have (same title, added in the last max_age_days) are skipped.
"""
import re, subprocess
from . import drive, gemini
from .common import CFG, WORK, days_since, load_ledger, log, now, save_ledger, paused
from .render import probe_duration

VIDEO = (".mp4", ".mov", ".m4v")
MIN_SOUND = 8.0  # seconds; shorter previews would have to loop under a reel
PROMPT = """This is a screen recording of Conor browsing Instagram's trending audio list. He taps a track to play
its preview, listens, then taps a different track (or pauses, or leaves the list). Use the VISUAL cues to find each
preview: it STARTS the moment he taps a track and its play/pause icon or waveform/progress starts moving, and it
ENDS the moment he taps another track, taps pause, scrolls that track away or the screen changes. Never let one
sound run across a tap on another track. Read the title and artist of the track that is visibly playing.
Times are seconds from the start of the recording, accurate to about 0.3s.
For each preview give "confidence": "high" only if you clearly saw both the tap that started it and the tap or
switch that ended it, and you can read its title; otherwise "low". Mark is_music false for speech, voiceovers
or app noises. Return only JSON:
{"sounds": [{"start": s, "end": s, "title": "...", "artist": "...", "is_music": true, "confidence": "high"}]}"""


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


def redo(date):
    """Undo a day's sweep: bin the sounds it made and put its recordings back in sound_sweeps to be cut again."""
    led = load_ledger()
    gone = [f for f in drive.list_files("audio") if f["name"].startswith(f"sweep {date} - ")]
    for f in gone:
        drive.trash(f["id"])
    back = [f for f in drive.list_files("sound_sweeps_done") if f["name"].lower().endswith(VIDEO)
            and (f.get("createdTime") or "")[:10] >= date]
    for f in back:
        drive.move(f["id"], "sound_sweeps")
    log(led, f"Redo sweep {date}: removed {len(gone)} sounds, {len(back)} recordings back in sound_sweeps")
    save_ledger(led)


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
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
                    v = gemini.small_video(p, max_seconds=900, fps=4)
                    try:  # 4 frames a second so it sees each tap
                        found = gemini.ask(PROMPT, video=v, video_fps=4).get("sounds")
                    except Exception:
                        found = gemini.ask(PROMPT, video=v).get("sounds")
                    who = "gemini"
                except Exception as e:
                    log(led, f"Gemini couldn't read sweep {f['name']}, splitting at gaps: {e}")
            if found is None:
                # Not sure where the sounds are: leave the recording in sound_sweeps to try again tomorrow,
                # rather than guessing and cutting sounds in the wrong places.
                log(led, f"Left sweep {f['name']} for tomorrow: Gemini couldn't read it")
                continue
            added = skipped = 0
            for s in found:
                # Trim a little inside the taps so no tap sound or neighbouring track gets in
                a, b = max(0.0, float(s.get("start", 0)) + 0.3), min(dur, float(s.get("end", 0)) - 0.5)
                if not s.get("is_music", True) or s.get("confidence", "low") != "high" or b - a < MIN_SOUND:
                    skipped += 1
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
            log(led, f"Sound sweep {f['name']} ({who}): {added} new sounds added to audio, {skipped} skipped (unsure or too short)")
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't process sound sweep {f['name']}: {e}")
    save_ledger(led)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--redo":
        redo(sys.argv[2])
    main()
