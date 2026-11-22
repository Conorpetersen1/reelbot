"""Instagram API: publish trial reels, read insights.

Works with either token type:
- Unified login / Facebook Login for Business (token starts "EAA", never expires): graph.facebook.com,
  needs IG_USER_ID (your Instagram account's ID, shown next to the account when you generate the token).
- Instagram login (token starts "IG", lasts 60 days, refreshed weekly): graph.instagram.com.
"""
import json, os, time, requests
from .common import CFG, env

VER = CFG["ig_api_version"]


def _tok():
    return env("IG_ACCESS_TOKEN").strip()


def facebook_token():
    return _tok().startswith("EAA")


def graph():
    host = "graph.facebook.com" if facebook_token() else "graph.instagram.com"
    return f"https://{host}/{VER}"


def user_id():
    if os.environ.get("IG_USER_ID"):
        return os.environ["IG_USER_ID"].strip()
    if facebook_token():
        raise SystemExit("Missing IG_USER_ID. Add your Instagram account ID as a GitHub Actions secret.")
    r = requests.get(f"{graph()}/me", params={"fields": "user_id", "access_token": _tok()}, timeout=60)
    return _check(r)["user_id"]


def _check(r):
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram API {r.status_code}: {r.text[:500]}")
    return r.json()


def me(fields="username,followers_count"):
    return _check(requests.get(f"{graph()}/{user_id()}", params={"fields": fields, "access_token": _tok()},
                               timeout=60))


def publish_trial_reel(path, caption):
    uid = user_id()
    GRAPH = graph()
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


def is_trial(media_id):
    """True if Instagram is holding the reel back from followers (a trial), False if it went out as a normal
    reel, None if Instagram won't say. Trial reels report is_shared_to_feed = false."""
    time.sleep(5)
    r = requests.get(f"{graph()}/{media_id}", params={"fields": "is_shared_to_feed,media_product_type",
                                                       "access_token": _tok()}, timeout=60)
    if r.status_code >= 300:
        return None
    v = r.json().get("is_shared_to_feed")
    return None if v is None else (not v)


METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
           "ig_reels_avg_watch_time"]


def insights(media_id):
    GRAPH = graph()
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
    r = requests.get(f"{graph()}/{media_id}", params={"fields": "permalink", "access_token": _tok()}, timeout=60)
    return r.json().get("permalink") if r.ok else None


def refresh_token():
    if facebook_token():
        return None  # system-user tokens never expire
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _tok()}, timeout=60)
    return _check(r)["access_token"]
