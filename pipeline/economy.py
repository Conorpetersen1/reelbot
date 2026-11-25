"""Spending less per follower.

1. Clip budget: each week, make only the mascot clips the coming week needs (mascot reels planned minus uses left
   in existing clips), scaled by how mascot reels convert compared with real ones. 4 to 14 clips.
2. Winners and flops: a clip behind a strong reel gets 2 extra uses (with fresh text and a new crop/mirror each
   time); a clip behind a clear flop is retired early.
3. Mix: once there's enough data, shift the daily mascot/real split by up to 2 reels towards whichever converts
   better, and adapt how many real reels are moment reels.
"""
import math
from .common import CFG, days_since, log

MIN_REELS = 10       # matured reels before the learner's verdicts are trusted for spending decisions
WIN, FLOP = 1.5, 0.4  # x the median score of matured mascot reels


def _score(p):
    """Engagement at ~72h, adjusted for skip rate (same shape as the learner's target)."""
    i = p.get("insights") or {}
    e = (i.get("views", 0) or 0) + 20 * (i.get("shares", 0) or 0) + 10 * (i.get("saved", 0) or 0)
    skip = i.get("reels_skip_rate")
    if isinstance(skip, (int, float)):
        e *= max(0.6, min(1.6, (1 - skip / 100) / 0.5))
    return e


def rate_clips(led):
    """Mark mascot clips behind strong reels as winners and behind clear flops as retired."""
    for m, p in led["posts"].items():  # older posts didn't record their clip: look it up from the queue
        if not p.get("clip"):
            e = next((e for d in led["queue"].values() for e in d if e.get("media_id") == m), None)
            if e:
                p["clip"] = e.get("clip")
    matured = [(m, p) for m, p in led["posts"].items()
               if p.get("kind") == "ai" and p.get("clip") and days_since(p.get("posted_at")) >= 3]
    if len(matured) < 6:
        return
    scores = sorted(_score(p) for _, p in matured)
    med = scores[len(scores) // 2] or 1
    by_clip = {}
    for _, p in matured:
        by_clip.setdefault(p["clip"], []).append(_score(p))
    for cid, ss in by_clip.items():
        c = led["ai_clips"].get(cid)
        if not c:
            continue
        best = max(ss)
        was = (c.get("winner"), c.get("retired_clip"))
        c["winner"] = best >= WIN * med
        c["retired_clip"] = best <= FLOP * med and not c["winner"]
        if (c["winner"], c["retired_clip"]) != was and (c["winner"] or c["retired_clip"]):
            log(led, f"Mascot clip {c.get('name')}: {'winner, 2 extra uses' if c['winner'] else 'flop, retired'}")


def max_uses(c):
    if c.get("rejected") or c.get("retired_clip"):
        return 0
    return CFG["ai_clip_max_uses"] + (1 if c.get("rated_high") else 0) + (2 if c.get("winner") else 0)


def kind_gap(model):
    """(posterior mean of real minus mascot effect, its sd), or None before there's enough data."""
    if model.n < MIN_REELS:
        return None
    mr, vr = model.marginal("kind", "real")
    ma, va = model.marginal("kind", "ai")
    return mr - ma, math.sqrt(max(vr + va, 1e-9))


def daily_mix(led, model, draw, n_ai, n_real):
    """Shift mascot/real by up to 2 reels a day towards whichever converts better (at least 1 of each)."""
    total = n_ai + n_real
    g = kind_gap(model)
    if g:
        gap, sd = g
        shift = 0 if abs(gap) < sd else (1 if abs(gap) < 2 * sd else 2)
        shift = shift if gap > 0 else -shift      # positive: real converts better
    else:  # little data: explore with the Thompson draw, one reel either way
        d = draw.effect("kind", "real") - draw.effect("kind", "ai")
        shift = 1 if d > 0.15 else -1 if d < -0.15 else 0
    if shift > 0 and not led["clips"]:
        shift = 0
    if shift < 0 and not led["ai_clips"]:
        shift = 0
    n_ai = max(1, min(total - 1, n_ai - shift))
    return n_ai, total - n_ai


def moment_share(model):
    """Share of real-clip reels written as moment reels: adapts to how moment reels convert vs teaching ones."""
    base = CFG.get("real_moment_share", 0.5)
    if model.n < MIN_REELS:
        return base
    mm, vm = model.marginal("category", "moment")
    others = [model.marginal("category", c)[0] for c in ("tip", "freestyle", "first_timer")]
    gap = mm - sum(others) / len(others)
    if abs(gap) < math.sqrt(vm):
        return base
    return max(0.25, min(0.75, base + (0.2 if gap > 0 else -0.2)))


def weekly_clip_count(led, model):
    """How many new mascot clips to make this week."""
    per_day = CFG["reels_per_day"]["ai"]
    g = kind_gap(model)
    perf = 1.0
    if g:
        gap, sd = g          # gap > 0: real converts better, so lean on cheap real reels
        perf = 0.6 if gap > sd else 1.25 if gap < -sd else 1.0
    need_uses = per_day * 7 * perf + 3                 # next week's mascot reels, plus spares
    left = sum(max(0, max_uses(c) - c.get("uses", 0)) for c in led["ai_clips"].values())
    per_clip = max(1.0, CFG["ai_clip_max_uses"] * 0.8)  # not every use lands (topic cooldowns, fit checks)
    n = math.ceil(max(0.0, need_uses - left) / per_clip)
    return max(4, min(CFG.get("ai_clips_per_week", 14), n))
