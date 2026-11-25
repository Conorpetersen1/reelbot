"""Ask Instagram how a posted reel was stored (to check trial status). Usage: python -m pipeline.ig_check MEDIA_ID"""
import json, sys, requests
from . import ig


def main(mid):
    g, t = ig.graph(), ig._tok()
    if mid.startswith("metrics:"):  # which insight metrics Instagram returns for one reel
        m_id = mid.split(":", 1)[1]
        for m in ("reels_skip_rate", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "views", "reach",
                  "follows", "profile_visits", "facebook_views", "crossposted_views"):
            r = requests.get(f"{g}/{m_id}/insights", params={"metric": m, "access_token": t}, timeout=60)
            print("metric ->", m, r.status_code, json.dumps(r.json())[:260])
        return
    if mid == "audio":  # what the Instagram Audio API offers this account (read only)
        uid = ig.user_id()
        for typ, q in (("original_sound", None), ("music", None), ("music", "Young Folks"), ("original_sound", "snowboard"),
                       ("music", "Pitbull Don't Stop the Party"), ("music", "James Bond Theme")):
            params = {"audio_type": typ, "user_id": uid, "access_token": t}
            if q:
                params["search_query"] = q
            r = requests.get(f"{g}/ig_audio", params=params, timeout=60)
            j = r.json()
            items = j.get("audio") or j.get("data") or []
            print("audio ->", typ, repr(q), r.status_code, len(items), json.dumps(j.get("error", {}))[:300])
            for a in items[:12]:
                print("audio ->    ", a.get("audio_id"), "|", a.get("title"), "|", a.get("display_artist") or a.get("username") or "",
                      "|", round((a.get("duration_in_ms") or 0) / 1000), "s", "| ads:", a.get("is_ads_eligible"))
        return
    if mid == "gemini-upload":  # test Gemini's large-file route with a ~20MB video
        import subprocess
        from . import gemini
        from .common import WORK
        v = WORK / "upload_test.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30", "-t", "90",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", str(v)], check=True)
        print("upload ->", round(v.stat().st_size / 1e6, 1), "MB")
        print("upload ->", gemini.ask('What is shown? Return only JSON: {"what": "short"}', video=v, video_fps=1))
        return
    if mid == "top":  # his own reels ranked by views, and whether the voice study could watch each
        from .voice import his_reels
        from .common import load_ledger
        rows = []
        for m in his_reels(load_ledger(), limit=200):
            try:
                v = ig.insights(m["id"]).get("views", 0)
            except Exception:
                v = -1
            rows.append((v, m.get("timestamp", "")[:10], bool(m.get("media_url")), (m.get("caption") or "")[:60].replace("\n", " ")))
        rows.sort(reverse=True)
        print("reels ->", len(rows), "readable:", sum(r[2] for r in rows))
        for r in rows[:25]:
            print("top ->", r)
        return
    if mid == "list":
        r = requests.get(f"{g}/{ig.user_id()}/media", params={"fields": "id,timestamp,media_product_type,is_shared_to_feed,permalink,caption",
                                                             "limit": 6, "access_token": t}, timeout=60)
        for m in r.json().get("data", []):
            m["caption"] = (m.get("caption") or "")[:50]
            print("media ->", json.dumps(m))
        print("list ->", r.status_code, json.dumps(r.json().get("error", {}))[:300])
        return
    for fields in ("media_product_type,media_type,is_shared_to_feed,permalink,timestamp,caption",
                   "trial_params", "is_trial_reel", "boost_eligibility_info"):
        r = requests.get(f"{g}/{mid}", params={"fields": fields, "access_token": t}, timeout=60)
        print(fields, "->", r.status_code, json.dumps(r.json())[:600])


if __name__ == "__main__":
    main(sys.argv[1])
