"""Gemini: watches whole trend videos (with sound) and acts as the second-opinion reviewer.
Secret: GEMINI_API_KEY (from aistudio.google.com). Optional: if it's missing, Claude does these jobs instead.
"""
import base64, json, os, re, subprocess
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
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def small_video(path):
    """Shrink a phone screen recording so it fits in one request (a few MB, keeps the sound)."""
    out = WORK / (path.stem + "_g.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", "90",
                    "-vf", "scale=-2:640,fps=15", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out)], check=True)
    return out


def analysis_video(path, max_seconds=600):
    """Small, silent, low-res copy of a long clip so Gemini can watch all of it (up to 10 min)."""
    out = WORK / (path.stem + "_a.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(max_seconds),
                    "-vf", "scale=-2:360,fps=4", "-c:v", "libx264", "-preset", "veryfast", "-crf", "35",
                    "-an", str(out)], check=True)
    return out


def ask(prompt, system=None, video=None, images=()):
    """Send a prompt (plus optional video or images) and return the JSON object in the reply."""
    global _working_model
    parts = []
    if video:
        parts.append({"inline_data": {"mime_type": "video/mp4",
                                      "data": base64.b64encode(video.read_bytes()).decode()}})
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
        r = requests.post(f"{BASE}/models/{model}:generateContent", json=body, timeout=300,
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        if r.status_code == 404:  # model name retired or not available: try the next one
            errors.append(f"{model}: 404"); continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini {model} {r.status_code}: {r.text[:400]}")
        _working_model = model
        data = r.json()
        text = "".join(p.get("text", "") for c in data.get("candidates", [])[:1]
                       for p in c.get("content", {}).get("parts", []))
        return _json(text)
    raise RuntimeError("No Gemini model available: " + ", ".join(errors))
