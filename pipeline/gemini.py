"""Gemini: watches whole trend videos (with sound) and acts as the second-opinion reviewer.
Secret: GEMINI_API_KEY (from aistudio.google.com). Optional: if it's missing, Claude does these jobs instead.
"""
import base64, json, os, re, subprocess, time
import requests
from .common import CFG, WORK

BASE = "https://generativelanguage.googleapis.com/v1beta"
_working_model = None


def available():
    return bool(os.environ.get("GEMINI_API_KEY")) and not os.environ.get("LOCAL_LLM")


def _models():
    m = CFG.get("gemini_models") or ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash",
                                     "gemini-3-flash-preview", "gemini-2.5-flash"]
    return ([_working_model] if _working_model else []) + [x for x in m if x != _working_model]


def _json(text):
    """Parse the first JSON object in a model reply (tolerates code fences and text before/after it)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    i = t.find("{")
    if i < 0:
        raise ValueError("no JSON in reply: " + t[:200])
    obj, _ = json.JSONDecoder().raw_decode(t[i:])
    return obj


def small_video(path, max_seconds=90, fps=15):
    """Shrink a phone screen recording so it fits in one request (a few MB, keeps the sound)."""
    out = WORK / (path.stem + "_g.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(max_seconds),
                    "-vf", f"scale=-2:640,fps={fps}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out)], check=True)
    return out


def analysis_video(path, max_seconds=600):
    """Small, silent, low-res copy of a long clip so Gemini can watch all of it (up to 10 min)."""
    out = WORK / (path.stem + "_a.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(max_seconds),
                    "-vf", "scale=-2:360,fps=4", "-c:v", "libx264", "-preset", "veryfast", "-crf", "35",
                    "-an", str(out)], check=True)
    return out


INLINE_MAX = 15 * 1024 * 1024  # bigger videos go through the Files API (inline requests are capped at ~20MB)


def upload(path, mime="video/mp4"):
    """Upload a large file to Gemini's Files API (kept 48h) and wait until it's ready. Returns its URI."""
    key = os.environ["GEMINI_API_KEY"]
    size = path.stat().st_size
    r = requests.post("https://generativelanguage.googleapis.com/upload/v1beta/files", timeout=60,
                      headers={"x-goog-api-key": key, "X-Goog-Upload-Protocol": "resumable",
                               "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(size),
                               "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"},
                      json={"file": {"display_name": path.name}})
    r.raise_for_status()
    up = r.headers["X-Goog-Upload-URL"]
    r = requests.post(up, data=path.read_bytes(), timeout=600,
                      headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                               "X-Goog-Upload-Command": "upload, finalize"})
    r.raise_for_status()
    f = r.json()["file"]
    for _ in range(60):
        if f.get("state") != "PROCESSING":
            break
        time.sleep(5)
        f = requests.get(f"{BASE}/{f['name']}", headers={"x-goog-api-key": key}, timeout=60).json()
    if f.get("state") != "ACTIVE":
        raise RuntimeError(f"Gemini couldn't process the upload: {f.get('state')}")
    return f["uri"]


def ask(prompt, system=None, video=None, images=(), video_fps=None):
    """Send a prompt (plus optional video or images) and return the JSON object in the reply."""
    global _working_model
    parts = []
    if video:
        if video.stat().st_size > INLINE_MAX:
            part = {"file_data": {"mime_type": "video/mp4", "file_uri": upload(video)}}
        else:
            part = {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video.read_bytes()).decode()}}
        if video_fps:  # Gemini looks at 1 frame a second unless told otherwise
            part["video_metadata"] = {"fps": video_fps}
        parts.append(part)
    for img in images:
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(img.read_bytes()).decode()}})
    parts.append({"text": prompt})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    errors = []
    for model in _models():
        r = None
        for attempt in range(3):
            r = requests.post(f"{BASE}/models/{model}:generateContent", json=body, timeout=300,
                              headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            if r.status_code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(15 * (attempt + 1)); continue  # Google busy: wait and retry the same model
            break
        if r.status_code in (404, 429, 500, 502, 503, 504):
            # retired, over its free-tier limit, or overloaded: try the next model (each has its own limits)
            errors.append(f"{model}: {r.status_code}"); continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini {model} {r.status_code}: {r.text[:400]}")
        _working_model = model
        data = r.json()
        from . import usage
        usage.gemini(model, data.get("usageMetadata"))
        text = "".join(p.get("text", "") for c in data.get("candidates", [])[:1]
                       for p in c.get("content", {}).get("parts", []))
        return _json(text)
    raise RuntimeError("No Gemini model available right now (" + ", ".join(errors) + ")")
