"""fal.ai queue API: one key gets image + video models. Secret: FAL_KEY."""
import base64, time, requests
from .common import env

BASE = "https://queue.fal.run"


def run(model, args, timeout=900):
    h = {"Authorization": f"Key {env('FAL_KEY')}"}
    r = requests.post(f"{BASE}/{model}", json=args, headers=h, timeout=60)
    r.raise_for_status()
    job = r.json()
    status_url, result_url = job["status_url"], job["response_url"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = requests.get(status_url, headers=h, timeout=60).json()
        if s.get("status") == "COMPLETED":
            res = requests.get(result_url, headers=h, timeout=60)
            res.raise_for_status()
            return res.json()
        if s.get("status") in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal job failed: {s}")
        time.sleep(5)
    raise TimeoutError(f"fal job timed out: {model}")


def data_uri(path, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def fetch(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def first_url(result, kind):
    """Pull the output URL out of a fal result (images: [...] or video: {...})."""
    if kind == "image":
        return result["images"][0]["url"]
    v = result.get("video")
    return v["url"] if isinstance(v, dict) else v
