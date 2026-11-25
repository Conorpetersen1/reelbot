"""Spoken voiceovers (the Gemini voice test).

For a reel picked to be voiced: Claude turns its on-screen text into a short spoken script that says the hook in
the first half-second, Gemini's text-to-speech reads it, and the words are timed so the render can show them as
captions while the music dips under the voice. Roughly US$0.003 a reel (Gemini TTS) plus a small Claude call.

Settings: voiceover in config.yaml. The reels are NOT given Instagram's "AI info" label (Conor's choice).
"""
import base64, difflib, json, os, re, subprocess, time
import requests
from .common import CFG, WORK

BASE = "https://generativelanguage.googleapis.com/v1beta"
_working = None


def cfg():
    return CFG.get("voiceover") or {}


# ---------------------------------------------------------------- 1. the script
def script_prompt(topic, reel, mode, voice_guide):
    lines = " / ".join(reel.get("description") or [])
    kind = {"teaching": "a TEACHING reel (one problem, one fix)",
            "moment": "a MOMENT reel (about what's happening in his real footage)"}.get(mode, "a LIFE reel (instructor/snow life, humour)")
    return f"""Write the spoken voiceover for {kind} on Conor's snowboard Instagram (@conor_sends, APSI Level 3 instructor).
On-screen hook: "{reel['hook']}"
On-screen points: "{lines}"
{voice_guide}

Rules:
- 16-26 words total. It is read fast and casually, like a laid-back Aussie instructor talking to camera.
- The FIRST 3-6 words are the hook, said out loud (same idea as the on-screen hook, said the way a person talks).
  No greeting, no "hey guys", no "so", no "in this video".
- Then say the points, in order, and nothing the points don't say: no extra technique, facts or claims.
- Plain spoken English: no hashtags, emojis, brackets or stage directions. Write numbers as words.
- End on the payoff (the fix, or the punchline), not on "follow for more".
Return only JSON: {{"script": "..."}}"""


def write_script(topic, reel, mode, led):
    if os.environ.get("LOCAL_LLM"):  # offline test mode
        return " ".join([reel["hook"].rstrip("?.!") + "."] + list(reel.get("description") or []))
    from . import llm, voice
    msg = llm._client().messages.create(model=CFG["claude_model"], max_tokens=2000, system=llm.RULES,
                                        messages=[{"role": "user", "content": script_prompt(
                                            topic, reel, mode, voice.guide(led))}])
    s = str(llm._json(llm._text(msg)).get("script", "")).strip()
    s = re.sub(r"[#*_\[\]()]|[\U0001F000-\U0001FFFF☀-➿]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not (6 <= len(s.split()) <= 34):
        raise ValueError(f"voiceover script is {len(s.split())} words")
    return s


# ---------------------------------------------------------------- 2. the voice
def _models():
    m = cfg().get("models") or ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview",
                                 "gemini-2.5-flash-preview-tts"]
    return ([_working] if _working else []) + [x for x in m if x != _working]


def _to_wav(data, mime, out):
    """Gemini 3.8 TTS returns a WAV file; older models return raw 24 kHz 16-bit PCM."""
    raw = out.with_suffix(".raw")
    raw.write_bytes(data)
    if data[:4] == b"RIFF":
        src = ["-i", str(raw)]
    else:
        rate = re.search(r"rate=(\d+)", mime or "")
        src = ["-f", "s16le", "-ar", rate.group(1) if rate else "24000", "-ac", "1", "-i", str(raw)]
    # Trim the silence the voice starts and ends with, so the hook is heard straight away
    subprocess.run(["ffmpeg", "-y", "-v", "error", *src, "-af",
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                    "-ar", "44100", "-ac", "1", str(out)], check=True)
    raw.unlink(missing_ok=True)
    return out


_au = {}


def aussie_voice():
    """An Australian male voice from Gemini's Extended Voice Library, if there is one (None otherwise)."""
    if "v" in _au:
        return _au["v"]
    _au["v"] = None
    try:
        r = requests.get(f"{BASE}/voices", params={"pageSize": 1000}, timeout=60,
                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        if r.status_code >= 400:
            print("Voice library unavailable:", r.status_code, r.text[:200]); return None
        data = r.json()
        items = data.get("voices") or next((v for v in data.values() if isinstance(v, list)), [])
        txt = lambda v: json.dumps(v).lower()
        au = [v for v in items if isinstance(v, dict) and ("australia" in txt(v) or "en-au" in txt(v))]
        male = [v for v in au if re.search(r"\bmale\b|\bman\b|masculine", txt(v))] or au
        easy = [v for v in male if re.search(r"casual|easy|friendly|upbeat|laid", txt(v))] or male
        print(f"Voice library: {len(items)} voices, {len(au)} Australian")
        if easy:
            v = easy[0]
            _au["v"] = str(v.get("voiceId") or v.get("id") or v.get("name") or "").split("/")[-1] or None
            print("Using Australian voice:", _au["v"], json.dumps(v)[:300])
    except Exception as e:
        print("Voice library lookup failed:", e)
    return _au["v"]


def speak(text, out, voice_name=None, style=None):
    """Read the script aloud with Gemini TTS (an Australian voice if the library has one, else the configured
    prebuilt voice). Returns the wav path. Records the cost."""
    c = cfg()
    if not (os.environ.get("LOCAL_LLM") or not os.environ.get("GEMINI_API_KEY")):
        if not voice_name and c.get("voice", "auto") == "auto":
            au = aussie_voice()
            if au:
                try:
                    return _speak(text, out, au, style)
                except Exception as e:
                    print("Australian voice failed, using the fallback voice:", e)
            voice_name = c.get("fallback_voice", "Puck")
    return _speak(text, out, voice_name, style)


def _speak(text, out, voice_name=None, style=None):
    global _working
    if os.environ.get("LOCAL_LLM") or not os.environ.get("GEMINI_API_KEY"):
        # offline test mode: a tone as long as the words would take
        secs = max(2.0, 0.32 * len(text.split()))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=220:d={secs:.2f}",
                        "-ar", "44100", "-ac", "1", str(out)], check=True)
        return out
    c = cfg()
    voice_name = voice_name or (c.get("voice") if c.get("voice", "auto") != "auto" else None) or c.get("fallback_voice", "Puck")
    style = style if style is not None else c.get("style", "")
    errors = []
    for model in _models():
        for with_style in ((True, False) if style else (False,)):
            part = {"text": text}
            if with_style:
                part["speech_metadata"] = {"style": style}
            body = {"contents": [{"role": "user", "parts": [part]}],
                    "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}}
            r = None
            for attempt in range(3):
                r = requests.post(f"{BASE}/models/{model}:generateContent", json=body, timeout=180,
                                  headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
                if r.status_code in (500, 502, 503, 504) and attempt < 2:
                    time.sleep(10 * (attempt + 1)); continue
                break
            if r.status_code == 400 and with_style:
                errors.append(f"{model} (style): {r.text[:160]}"); continue  # try again without the style note
            if r.status_code >= 400:
                errors.append(f"{model}: {r.status_code} {r.text[:160]}"); break
            data = r.json()
            parts = [p for cnd in data.get("candidates", [])[:1] for p in cnd.get("content", {}).get("parts", [])]
            audio = next((p.get("inlineData") or p.get("inline_data") for p in parts
                          if p.get("inlineData") or p.get("inline_data")), None)
            if not audio:
                errors.append(f"{model}: no audio in reply"); break
            _working = model
            _au["used"] = voice_name
            _record(model, data.get("usageMetadata"), text)
            return _to_wav(base64.b64decode(audio["data"]), audio.get("mimeType") or audio.get("mime_type"), out)
    raise RuntimeError("Gemini voice failed: " + " | ".join(errors)[:900])


def _record(model, meta, text):
    from . import usage
    pin, pout = cfg().get("price_usd_per_m", [0.5, 9.0])
    meta = meta or {}
    tin = meta.get("promptTokenCount") or len(text) / 3
    tout = meta.get("candidatesTokenCount") or 25 * 0.4 * len(text.split())  # ~25 audio tokens a second
    usage.add("gemini", (tin * pin + tout * pout) / 1e6)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


# ---------------------------------------------------------------- 3. word timings (for captions)
def _norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def _even(words, total):
    """Fallback: share the time between words by length (good enough for 3-word captions)."""
    weights = [len(_norm(w)) + 2 for w in words]
    t, out, scale = 0.0, [], total / max(1, sum(weights))
    for w, k in zip(words, weights):
        out.append((w, round(t, 3), round(t + k * scale, 3))); t += k * scale
    return out


def timings(wav, text):
    """[(word, start, end)] for the script's own words (so captions are spelled right), timed by Whisper
    listening to the audio. Falls back to an even split if Whisper isn't installed."""
    words = text.split()
    total = duration(wav)
    try:
        from faster_whisper import WhisperModel
    except Exception:
        if not os.environ.get("GITHUB_ACTIONS"):
            return _even(words, total)
        try:  # only installed on the runs that actually voice a reel
            subprocess.run(["pip", "install", "-q", "faster-whisper"], check=True, timeout=300)
            from faster_whisper import WhisperModel
        except Exception as e:
            print("Couldn't install Whisper, using an even split:", e)
            return _even(words, total)
    try:
        model = WhisperModel(cfg().get("whisper_model", "base.en"), device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(wav), word_timestamps=True, language="en", initial_prompt=text)
        heard = [(w.word.strip(), w.start, w.end) for s in segs for w in (s.words or [])]
    except Exception as e:
        print("Whisper timing failed, using an even split:", e)
        return _even(words, total)
    if not heard:
        return _even(words, total)
    sm = difflib.SequenceMatcher(a=[_norm(w) for w in words], b=[_norm(h[0]) for h in heard], autojunk=False)
    times = [None] * len(words)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = (heard[b + k][1], heard[b + k][2])
    # Words Whisper misheard: spread them between their matched neighbours
    out, i = [], 0
    while i < len(words):
        if times[i]:
            out.append((words[i], round(times[i][0], 3), round(times[i][1], 3))); i += 1; continue
        j = i
        while j < len(words) and not times[j]:
            j += 1
        t0 = out[-1][2] if out else 0.0
        t1 = times[j][0] if j < len(words) else total
        for w, s, e in _even(words[i:j], max(0.05, t1 - t0)):
            out.append((w, round(t0 + s, 3), round(t0 + e, 3)))
        i = j
    return out


# ---------------------------------------------------------------- all together
def make(topic, reel, mode, led, stem):
    """Write, speak and time a voiceover. Returns {"script", "wav", "words", "seconds"}."""
    text = write_script(topic, reel, mode, led)
    wav = speak(text, WORK / f"{stem}_vo.wav")
    words = timings(wav, text)
    return {"script": text, "wav": wav, "words": words, "seconds": round(duration(wav), 2),
            "voice": _au.get("used") or "offline"}


def wanted(entries_so_far, target, already, topic, draw=None):
    """During the test (until test_until) about per_day of each day's reels are voiced, chosen at random, so
    voiced and silent reels can be compared fairly. After that the learner decides per reel from follows."""
    import random
    from .common import now
    c = cfg()
    if not c.get("enabled") or topic.get("needs_sound"):  # edits built around a sound keep it clean
        return False
    if draw is not None and now().date().isoformat() > str(c.get("test_until", "9999")):
        return draw.best("voice", ["voice", "none"]) == "voice"
    left = max(1, target - entries_so_far)
    need = max(0, int(c.get("per_day", 2)) - already)
    return random.random() < need / left


def check():
    """Diagnostic (check workflow, item 'voice'): make a sample voiceover and put it in Drive/queue."""
    from . import drive
    from .common import load_ledger
    led = load_ledger()
    reel = {"hook": "Why your heelside turns skid",
            "description": ["You're folding at the waist", "Squeeze your glutes and stand tall"]}
    r = make({"category": "tip"}, reel, "teaching", led, "voice_sample")
    print("Script:", r["script"]); print("Seconds:", r["seconds"], "model:", _working)
    print("Timings:", r["words"])
    mp3 = r["wav"].with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(r["wav"]), str(mp3)], check=True)
    print("Uploaded:", drive.upload(mp3, "queue", "voice_sample.mp3", mime="audio/mpeg"))
