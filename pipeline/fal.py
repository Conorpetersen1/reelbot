"""fal.ai queue API: one key gets image + video models. Secret: FAL_KEY."""
import base64, time, requests
from .common import CFG, env, now


class BudgetReached(Exception):
    pass


def _month():
    return now().strftime("%Y-%m")


def spent(led):
    return led.setdefault("fal_spend", {}).get(_month(), 0.0)


def charge(led, *kinds):
    """Record the estimated cost of a job (all its steps at once). Raise BudgetReached *before* spending anything
    if it would go over the monthly cap, so a clip is never half-made."""
    prices = CFG.get("fal_cost_estimate_usd", {})
    cost = sum(prices.get(k, 0.4) for k in kinds)
    if spent(led) + cost > CFG.get("fal_monthly_budget_usd", 30):
        raise BudgetReached(f"fal budget of US${CFG.get('fal_monthly_budget_usd', 30)} reached for {_month()} "
                            f"(spent about US${spent(led):.2f})")
    led["fal_spend"][_month()] = round(spent(led) + cost, 2)

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
