"""Ask Instagram how a posted reel was stored (to check trial status). Usage: python -m pipeline.ig_check MEDIA_ID"""
import json, sys, requests
from . import ig


def main(mid):
    g, t = ig.graph(), ig._tok()
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
