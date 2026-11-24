"""Sound sweeps: screen-record yourself scrolling Instagram's trending audio list, drop the recording in
Drive/sound_sweeps, and the bot cuts it into separate sounds in Drive/audio.

Gemini watches for the taps: a sound starts when Conor taps play on a track and ends when he switches to another.
Each cut is then moved onto the exact moment the audio switches (a short silence or a sudden change of sound).
Sounds Gemini isn't sure about, or shorter than MIN_SOUND, are skipped. If Gemini can't read the recording at all,
it's left in sound_sweeps to try again the next day.
Songs you already have (same title, added in the last max_age_days) are skipped.
"""
import json, re, subprocess
from . import drive, gemini
from .common import CFG, ROOT, WORK, days_since, load_ledger, log, now, save_ledger, paused
from .render import probe_duration

VIDEO = (".mp4", ".mov", ".m4v")
MIN_SOUND = 7.0  # seconds; a reel is 8-10 s, so shorter previews would leave it silent at the end
PROMPT = """This is a screen recording of Conor browsing Instagram's trending audio list. He taps a track to play
its preview, listens, then taps a different track (or pauses, or leaves the list). Use the VISUAL cues to find each
preview: it STARTS the moment he taps a track and its play/pause icon or waveform/progress starts moving, and it
ENDS the moment he taps another track, taps pause, scrolls that track away or the screen changes. Never let one
sound run across a tap on another track. Read the title and artist of the track that is visibly playing.
A yellow clock in the top-left corner shows the recording time in seconds (e.g. "83.4s"). Read every start
and end time off that clock at the moment of the tap; don't estimate times any other way.
For each preview give "confidence": "high" if you clearly saw the tap that started it, the tap or switch that
ended it, and can read its title; "medium" if you can read the title and are fairly sure of both times;
otherwise "low". Mark is_music false for speech, voiceovers
or app noises. Return only JSON:
{"sounds": [{"start": s, "end": s, "title": "...", "artist": "...", "is_music": true, "confidence": "high|medium|low"}]}"""


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


def clocked_video(path, height=640, fps=4, crf=32, max_seconds=900):
    """A small copy of the recording with a big running clock in the corner. Gemini's own sense of time drifts
    over a long video (it once put a 5:49 recording's last song at 9:08), but it reads an on-screen clock reliably."""
    from .render import FONT_BOLD
    out = WORK / (path.stem + "_clock.mp4")
    font = FONT_BOLD.replace(":", r"\:")
    clock = (f"drawtext=fontfile='{font}':text='%{{eif\\:t\\:d}}.%{{eif\\:mod(t*10\\,10)\\:d}}s':"
             "x=12:y=12:fontsize=44:fontcolor=yellow:box=1:boxcolor=black@0.8:boxborderw=8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(max_seconds),
                    "-vf", f"scale=-2:{height},fps={fps},{clock}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out)], check=True)
    return out


def audio_edges(path):
    """Moments where the audio switches: short silences and sudden changes in the sound (a new track starting).
    Gemini's times from the video can be a second or two out; these let each cut land exactly on the switch."""
    import numpy as np
    sr, hop = 8000, 400  # 50 ms frames
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = len(x) // hop
    if n < 10:
        return []
    fr = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((fr ** 2).mean(axis=1)) + 1e-6
    db = 20 * np.log10(rms)
    spec = np.abs(np.fft.rfft(fr * np.hanning(hop), axis=1))
    spec = spec / (spec.sum(axis=1, keepdims=True) + 1e-9)
    # Compare the sound just before and just after each moment (0.5 s windows), so a change of track stands out
    # from the ordinary changes within a song.
    k = 10
    flux = np.zeros(n)
    for i in range(k, n - k):
        flux[i] = np.abs(spec[i - k:i].mean(axis=0) - spec[i:i + k].mean(axis=0)).sum()
    edges = []
    quiet = db < max(-45.0, np.percentile(db, 50) - 25)
    i = 0
    while i < n:  # silences of 0.1 s or more
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= 2:
                edges.append({"t": i * hop / sr, "t_end": j * hop / sr, "kind": "silence"})
            i = j
        else:
            i += 1
    thr = np.percentile(flux[k:n - k], 97) if n > 2 * k + 10 else 1e9
    for i in range(k, n - k):
        if flux[i] >= thr and flux[i] == flux[max(0, i - 10):i + 11].max():
            edges.append({"t": i * hop / sr, "t_end": i * hop / sr, "kind": "change"})
    total = len(x) / sr
    edges += [{"t": 0.0, "t_end": 0.0, "kind": "edge"}, {"t": total, "t_end": total, "kind": "edge"}]
    return sorted(edges, key=lambda e: e["t"])


def snap(t, edges, side, window=2.5):
    """Nearest audio switch to Gemini's time t. A silence (the gap while the next track loads) is trusted over a
    sudden change of sound, which could also be a beat drop inside one song, so changes must be closer.
    side='start' uses the end of a silence, 'end' its start."""
    def nearest(kinds, win):
        best = None
        for e in edges:
            if e["kind"] not in kinds:
                continue
            cand = e["t_end"] if side == "start" else e["t"]
            if abs(cand - t) <= win and (best is None or abs(cand - t) < abs(best - t)):
                best = cand
        return best
    found = nearest(("silence", "edge"), window)
    return found if found is not None else nearest(("change",), 1.5)


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
                    v = clocked_video(p)
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
            last = max([float(x.get("end", 0)) for x in found] or [0])
            if last > dur + 3:  # times ran past the end of the recording: squeeze them back to fit
                k = dur / last
                for x in found:
                    x["start"], x["end"] = float(x.get("start", 0)) * k, float(x.get("end", 0)) * k
                    x["confidence"] = "medium" if x.get("confidence") == "high" else x.get("confidence")
                log(led, f"Sweep {f['name']}: Gemini's times ran past the end, scaled by {k:.2f}")
            added = skipped = 0
            try:
                edges = audio_edges(p)
            except Exception as e:
                edges = []; print("Couldn't read the sweep's audio:", e)
            report = []
            for s in found:
                ga, gb = float(s.get("start", 0)), float(s.get("end", 0))
                sa, sb = snap(ga, edges, "start"), snap(gb, edges, "end")
                # Cut on the audio switch when there is one; otherwise stay well inside Gemini's times
                a = (sa + 0.15) if sa is not None else ga + 0.8
                b = (sb - 0.25) if sb is not None else gb - 1.0
                a, b = max(0.0, a), min(dur, b)
                conf = s.get("confidence", "low")
                sure = conf == "high" or (conf == "medium" and sa is not None and sb is not None)
                why = ("not music" if not s.get("is_music", True) else "unsure" if not sure
                       else f"too short ({b - a:.1f}s)" if b - a < MIN_SOUND else "kept")
                report.append({**s, "cut_start": round(a, 2), "cut_end": round(b, 2),
                               "snapped": [sa is not None, sb is not None], "result": why})
                if why != "kept":
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
                led["sound_newest"] = now().isoformat()
            # Keep a record of every cut so a bad split can be checked
            rec = ROOT / "data" / "sweeps"; rec.mkdir(parents=True, exist_ok=True)
            (rec / f"{stamp} {safe(f['name'])}.json").write_text(json.dumps(
                {"sweep": f["name"], "duration": dur, "sounds": report, "audio_edges": edges}, indent=1))
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
