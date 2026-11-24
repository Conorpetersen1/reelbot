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


def public_url(path):
    """Instagram only honours trial_params when it fetches the video from a link (video_url), not with a direct
    (resumable) upload. So the finished reel is put on fal's file host, which gives a public link with an
    unguessable address; Instagram downloads it from there."""
    import fal_client
    os.environ.setdefault("FAL_KEY", env("FAL_KEY"))
    return fal_client.upload_file(path)


def publish_trial_reel(path, caption, audio=None, drive_id=None):
    """audio: optional {"audio_id", "audio_volume", "video_volume"} to attach an Instagram sound (Audio API).
    drive_id: the reel's Drive file, used as the public link if fal's file host is unavailable."""
    shared = False
    try:
        try:
            url = public_url(path)
        except Exception as e:
            if not drive_id:
                raise
            from . import drive
            print("fal file host unavailable, sharing from Drive instead:", e)
            url = drive.share_link(drive_id); shared = True
        return _publish(url, caption, audio)
    finally:
        if shared:
            try:
                from . import drive
                drive.unshare(drive_id)
            except Exception as e:
                print("Couldn't make the Drive file private again:", e)


def _publish(video_url, caption, audio=None):
    uid = user_id()
    GRAPH = graph()
    # 1) container from a public link, trial reel with MANUAL graduation (you decide what reaches followers).
    #    Never fall back to a direct upload: that posts a normal reel to followers.
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption,
            "trial_params": json.dumps({"graduation_strategy": CFG["graduation"]}), "access_token": _tok()}
    if audio:
        data["audio_configuration"] = json.dumps({k: audio[k] for k in ("audio_id", "audio_volume", "video_volume")
                                                  if k in audio})
    c = _check(requests.post(f"{GRAPH}/{uid}/media", data=data, timeout=120))
    cid = c["id"]
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
