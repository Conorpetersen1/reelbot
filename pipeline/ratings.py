"""Conor's 1-5 ratings of reels: a. video, b. words on screen, c. sound, d. caption (+ an optional "why").

He rates on the dashboard; "Save" opens a pre-filled GitHub issue labelled `rating`, which he submits. This
module (run hourly) reads those issues, stores the ratings and closes the issues. Only issues opened by the
repo owner count.

What ratings change (followers stay the main signal; ratings steer quality and taste):
- Video 1-2: a mascot clip is binned (and taught to the clip maker); a real moment isn't used again.
  Video 5: a mascot clip may be used one extra time.
- Words / caption: best- and worst-rated examples, with his reasons, go to Claude when it writes.
- Sound 1-2: that sound is dropped. Sound 4-5: picked more often.
- Topics and categories he rates well get a small nudge up in the planner (and low ones down).
"""
import os, re, requests
from .common import CFG, log, now

PARTS = ("video", "words", "sound", "caption")
LABELS = {"video": "a. Video", "words": "b. Words on screen", "sound": "c. Sound", "caption": "d. Caption"}


def repo():
    return os.environ.get("GITHUB_REPOSITORY") or CFG.get("github_repo", "Conorpetersen1/reelbot")


def parse(body):
    out = {}
    m = re.search(r"^reel:\s*(\S+)", body or "", re.M)
    if not m:
        return None
    out["key"] = m.group(1)
    for p in PARTS:
        m = re.search(rf"^{p}:\s*([1-5])\b", body, re.M | re.I)
        if m:
            out[p] = int(m.group(1))
    m = re.search(r"^why:\s*(.*)\Z", body, re.M | re.S | re.I)
    if m and m.group(1).strip():
        out["why"] = m.group(1).strip()[:500]
    return out if any(p in out for p in PARTS) or out.get("why") else None


def entry_for(led, key):
    date, _, slot = key.partition("#")
    for e in led["queue"].get(date, []):
        if str(e.get("slot")) == slot:
            return e
    return None


def apply(led, key, r):
    """Act on one rating straight away."""
    e = entry_for(led, key)
    if not e:
        return
    v = r.get("video")
    if v is not None:
        if e["kind"] == "ai":
            clip = led["ai_clips"].get(e["clip"])
            if clip and v <= 2 and not clip.get("rejected"):
                from . import taste
                clip["rejected"] = f"rated {v}/5 by Conor"
                taste._record(led, e["clip"], clip, "rating")
                if r.get("why"):
                    led["ai_binned"][-1]["why"] = r["why"]
            elif clip and v >= 5:
                clip["rated_high"] = True
        else:
            from .plan_day import segments
            cid, i = e["clip"].rsplit("#", 1)
            clip = led["clips"].get(cid)
            segs = segments(clip) if clip else []
            if int(i) < len(segs) and v <= 2:
                segs[int(i)]["usable"] = False
                segs[int(i)]["rated_low"] = v
    s = r.get("sound")
    if s is not None and e.get("sound"):
        led.setdefault("sound_ratings", {})[e["sound"]] = s


def collect(led):
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        print("No GitHub token: ratings not collected"); return 0
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    owner = repo().split("/")[0].lower()
    r = requests.get(f"https://api.github.com/repos/{repo()}/issues", headers=h, timeout=60,
                     params={"labels": "rating", "state": "open", "per_page": 100})
    r.raise_for_status()
    n = 0
    for issue in r.json():
        if issue.get("user", {}).get("login", "").lower() != owner:
            continue  # only Conor's own ratings
        got = parse(issue.get("body") or "")
        if got:
            key = got.pop("key")
            old = led.setdefault("ratings", {}).get(key, {})
            led["ratings"][key] = {**old, **got, "at": now().isoformat(), "issue": issue["number"]}
            apply(led, key, got)
            n += 1
        requests.patch(issue["url"], headers=h, timeout=60, json={"state": "closed", "state_reason": "completed"})
    if n:
        log(led, f"Saved {n} rating{'s' if n > 1 else ''} from you")
    return n


# ---- used by the planner and the writer ----

def _rated_entries(led):
    for key, r in (led.get("ratings") or {}).items():
        e = entry_for(led, key)
        if e:
            yield e, r


def overall(r):
    vals = [r[p] for p in PARTS if p in r]
    return sum(vals) / len(vals) if vals else None


def bonus(led, topic):
    """Small planner nudge from Conor's average rating of a topic (and its category): about ±0.15 per point."""
    by_topic, by_cat = {}, {}
    for e, r in _rated_entries(led):
        o = overall(r)
        if o is None:
            continue
        by_topic.setdefault(e["topic"], []).append(o)
        by_cat.setdefault(e.get("category"), []).append(o)
    t = by_topic.get(topic["id"]); c = by_cat.get(topic.get("category"))
    val = 0.0
    if t:
        val += 0.15 * (sum(t) / len(t) - 3)
    if c and len(c) >= 2:
        val += 0.08 * (sum(c) / len(c) - 3)
    return max(-0.35, min(0.35, val))


def writer_notes(led, limit=4):
    """Examples of on-screen words and captions Conor rated high and low, for Claude."""
    good, bad = [], []
    for e, r in sorted(_rated_entries(led), key=lambda x: x[1].get("at", ""), reverse=True):
        w, c, why = r.get("words"), r.get("caption"), r.get("why")
        item = {"hook": e["hook"], "lines": e.get("description", []), "caption": e.get("caption", "")}
        if why:
            item["his_comment"] = why
        score = min([x for x in (w, c) if x is not None], default=None)
        top = max([x for x in (w, c) if x is not None], default=None)
        if top is not None and top >= 4 and (score is None or score >= 3):
            good.append({**item, "words": w, "caption_score": c})
        elif score is not None and score <= 2:
            bad.append({**item, "words": w, "caption_score": c})
    if not good and not bad:
        return ""
    import json
    return ("\nConor rates reels himself (1-5). Write more like the ones he rated highly and avoid what he "
            "rated low (his comments say why):\n"
            f"Rated highly: {json.dumps(good[:limit], ensure_ascii=False)}\n"
            f"Rated low: {json.dumps(bad[:limit], ensure_ascii=False)}\n")


def sound_weight(led, name):
    s = (led.get("sound_ratings") or {}).get(name)
    if s is None:
        return 1.0
    return 0.0 if s <= 2 else 2.0 if s >= 4 else 1.0


def main():
    from .common import load_ledger, save_ledger
    led = load_ledger()
    collect(led)
    save_ledger(led)


if __name__ == "__main__":
    main()
