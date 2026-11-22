"""Instagram API (Instagram Login): publish trial reels, read insights. Secret: IG_ACCESS_TOKEN."""
import json, time, requests
from .common import CFG, env

VER = CFG["ig_api_version"]
GRAPH = f"https://graph.instagram.com/{VER}"


def _tok():
    return env("IG_ACCESS_TOKEN")


def _check(r):
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram API {r.status_code}: {r.text[:500]}")
    return r.json()


def me(fields="user_id,username,followers_count"):
    return _check(requests.get(f"{GRAPH}/me", params={"fields": fields, "access_token": _tok()}, timeout=60))


def publish_trial_reel(path, caption):
    uid = me("user_id")["user_id"]
    # 1) container, resumable upload, trial reel with MANUAL graduation (you decide what reaches followers)
    c = _check(requests.post(f"{GRAPH}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "trial_params": json.dumps({"graduation_strategy": CFG["graduation"]}),
        "access_token": _tok()}, timeout=60))
    cid = c["id"]
    uri = c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{VER}/{cid}"
    # 2) upload the file bytes
    data = path.read_bytes()
    _check(requests.post(uri, headers={"Authorization": f"OAuth {_tok()}", "offset": "0",
                                       "file_size": str(len(data))}, data=data, timeout=600))
    # 3) wait for Instagram to process it
    for _ in range(60):
        s = _check(requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status",
                                                          "access_token": _tok()}, timeout=60))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing failed: {s}")
        time.sleep(10)
    else:
        raise TimeoutError("Instagram took too long to process the video")
    # 4) publish
    return _check(requests.post(f"{GRAPH}/{uid}/media_publish",
                                data={"creation_id": cid, "access_token": _tok()}, timeout=60))["id"]


METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
           "ig_reels_avg_watch_time"]


def insights(media_id):
    out = {}
    r = requests.get(f"{GRAPH}/{media_id}/insights",
                     params={"metric": ",".join(METRICS), "access_token": _tok()}, timeout=60)
    rows = r.json().get("data") if r.ok else None
    if rows is None:  # one metric unsupported -> ask one by one
        rows = []
        for m in METRICS:
            rr = requests.get(f"{GRAPH}/{media_id}/insights", params={"metric": m, "access_token": _tok()},
                              timeout=60)
            if rr.ok:
                rows += rr.json().get("data", [])
    for row in rows:
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value", row.get("total_value", {}).get("value", 0))
    return out


def permalink(media_id):
    r = requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": _tok()}, timeout=60)
    return r.json().get("permalink") if r.ok else None


def refresh_token():
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _tok()}, timeout=60)
    return _check(r)["access_token"]
