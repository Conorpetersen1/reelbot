"""The follower-growth learner.

Instagram doesn't say which reel brought which follower, so the learner works it out in three steps:

1. ATTRIBUTION. Every hour the bot records your follower count and each recent reel's views. When followers
   go up, the gain is shared between the reels that were getting views in that hour, in proportion to
   views x that reel's estimated follow rate (plus a "background" share for followers who came from elsewhere).
   Repeating this (an EM / Poisson mixture fit) over hundreds of hours separates strong reels from weak ones.

2. MODEL. A Bayesian Poisson regression explains followers per reel from what you control: topic, mascot vs
   your footage, text style, hook type, sound, posting time, reused footage, and where the idea came from.
   Early on, when there are only a few followers to learn from, it learns from engagement instead
   (views, shares, saves scaled to follower units) and shifts to real follower data as it accumulates.
   Overlapping factors (e.g. mascot reels always posted at 7:30) are separated properly because all
   factors are fitted together, not averaged one at a time.

3. DECISIONS. Thompson sampling: each day the planner draws plausible values for every effect from the
   model's uncertainty and picks what looks best under that draw. Proven winners get used most; untested
   options still get tried in proportion to how likely they are to be better. No hand-tuned exploration rate.

Runs on numbers already collected: no extra API cost.
"""
import datetime as dt, math, re
import numpy as np
from .common import CFG, now

GROUPS = ["category", "topic", "kind", "style", "hook", "sound", "slot", "reuse", "source", "cta"]
PRIOR_SD = {"category": 0.5, "topic": 0.6, "kind": 0.5, "style": 0.5, "hook": 0.5, "sound": 0.5, "slot": 0.4,
            "reuse": 0.4, "source": 0.5, "cta": 0.4}
HOOK_TYPES = ["how_i", "why", "question", "pov", "number", "statement"]
CTA_TYPES = ["follow", "save", "share"]  # the caption's call to action, tested like hooks
MATURE_HOURS = 72


def lc():
    return CFG.get("learning", {})


# ---------------------------------------------------------------- features
def hook_type(hook):
    h = (hook or "").strip().lower()
    if h.startswith("pov"):
        return "pov"
    if re.match(r"^(how i|the (one|drill|trick)|what (fixed|changed))", h):
        return "how_i"
    if re.match(r"^\d+\s", h) or re.search(r"\b\d+ (mistakes|things|signs|tips|rules)\b", h):
        return "number"
    if h.startswith("why"):
        return "why"
    if h.endswith("?"):
        return "question"
    return "statement"


def features(rec):
    """rec: a queue entry or post record. Returns {group: level}."""
    t = rec.get("time") or rec.get("slot_time") or "12:00"
    src = rec.get("source") or ("trend" if str(rec.get("topic", "")).startswith("x") else "bank")
    return {"category": rec.get("category") or "tip", "topic": rec.get("topic", "?"), "kind": rec.get("kind", "ai"), "style": rec.get("style") or "question",
            "hook": rec.get("hook_type") or hook_type(rec.get("hook", "")),
            "sound": rec.get("sound_kind") or ("sound" if rec.get("sound") else "none"),
            "slot": t.split(":")[0].zfill(2), "reuse": "reuse" if rec.get("variation") else "first", "source": src,
            "cta": rec.get("cta") or "mixed"}


# ---------------------------------------------------------------- data collection (hourly)
def record(led, followers, insights_by_media):
    """Store this hour's follower count and each young reel's cumulative stats."""
    t = now()
    fh = led.setdefault("followers_hourly", [])
    if followers is not None:
        fh.append([t.isoformat(timespec="minutes"), int(followers)])
        cutoff = (t - dt.timedelta(days=60)).isoformat()
        led["followers_hourly"] = [x for x in fh if x[0] >= cutoff]
    for mid, ins in insights_by_media.items():
        p = led["posts"].get(mid)
        if not p:
            continue
        age = (t - dt.datetime.fromisoformat(p["posted_at"])).total_seconds() / 3600
        if age > MATURE_HOURS + 6:
            continue
        p.setdefault("series", []).append([round(age, 2), ins.get("views", 0) or 0, ins.get("reach", 0) or 0,
                                           ins.get("shares", 0) or 0, ins.get("saved", 0) or 0])


def _views_at(p, when):
    """Cumulative views of reel p at absolute time `when` (linear between snapshots, flat after the last)."""
    posted = dt.datetime.fromisoformat(p["posted_at"])
    h = (when - posted).total_seconds() / 3600
    if h <= 0:
        return 0.0
    pts = [(0.0, 0.0)] + [(s[0], float(s[1])) for s in p.get("series", [])]
    for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
        if h <= h1:
            return v0 + (v1 - v0) * (h - h0) / max(h1 - h0, 1e-6)
    return pts[-1][1]


# ---------------------------------------------------------------- 1. attribution (EM)
def attribute(led, iters=40):
    """Estimate followers brought in by each reel. Returns {media_id: est_follows}, background_per_hour."""
    fh = [(dt.datetime.fromisoformat(a), b) for a, b in led.get("followers_hourly", [])]
    posts = {m: p for m, p in led["posts"].items() if p.get("series")}
    if len(fh) < 2 or not posts:
        return {}, 0.0
    hours = []  # (gain, {mid: view increment})
    for (t0, f0), (t1, f1) in zip(fh, fh[1:]):
        gain = max(0, f1 - f0)
        inc = {}
        for m, p in posts.items():
            dv = _views_at(p, t1) - _views_at(p, t0)
            if dv > 0:
                inc[m] = dv
        hours.append((gain, inc, max((t1 - t0).total_seconds() / 3600, 0.25)))
    total_gain = sum(h[0] for h in hours)
    total_views = sum(sum(h[1].values()) for h in hours) or 1.0
    g = (total_gain + 1) / (total_views + 1000)  # global follows-per-view, used as the prior
    rate = {m: g for m in posts}
    lam = max(total_gain * 0.2 / max(sum(h[2] for h in hours), 1), 1e-4)  # background follows per hour
    a, b = 2.0, 2.0 / g  # Gamma prior on each reel's rate: mean g, worth ~2 follows of evidence
    for _ in range(iters):
        att = {m: 0.0 for m in posts}
        bg = 0.0
        for gain, inc, dur in hours:
            if not gain:
                continue
            parts = {m: rate[m] * v for m, v in inc.items()}
            denom = lam * dur + sum(parts.values())
            if denom <= 0:
                continue
            for m, x in parts.items():
                att[m] += gain * x / denom
            bg += gain * lam * dur / denom
        views = {m: 0.0 for m in posts}
        for _, inc, _ in hours:
            for m, v in inc.items():
                views[m] += v
        rate = {m: (att[m] + a) / (views[m] + b) for m in posts}
        lam = max(bg / max(sum(h[2] for h in hours), 1), 1e-4)
    return att, lam


# ---------------------------------------------------------------- 2. model (Bayesian Poisson regression)
def _targets(led, att):
    """Follower target per matured reel: attributed follows blended with an engagement-based estimate."""
    t = now()
    rows = []
    for m, p in led["posts"].items():
        age = (t - dt.datetime.fromisoformat(p["posted_at"])).total_seconds() / 3600
        if age < MATURE_HOURS or not p.get("series"):
            continue
        s = [x for x in p["series"] if x[0] <= MATURE_HOURS + 6] or p["series"]
        last = s[-1]
        engagement = last[1] + 20 * last[3] + 10 * last[4]  # views + 20 x shares + 10 x saves at ~72h
        skip = (p.get("insights") or {}).get("reels_skip_rate")
        hold = (1 - float(skip) / 100) if isinstance(skip, (int, float)) else None  # share who stayed past 3s
        rows.append((m, p, att.get(m, 0.0), engagement, hold))
    if not rows:
        return [], 0.0
    total_f = sum(r[2] for r in rows)
    total_e = sum(r[3] for r in rows)
    k = (total_f + 1) / (total_e + 2000)  # follows per unit of engagement, calibrated on your own data
    w = min(1.0, total_f / lc().get("trust_follows_after", 60))
    # Skip rate: reels that hold people past the first 3 seconds score higher than ones with the same views that
    # get skipped (a better hook). Compared with your own typical hold rate, capped so it nudges rather than rules.
    holds = sorted(r[4] for r in rows if r[4])
    base = holds[len(holds) // 2] if holds else None
    sw = lc().get("skip_weight", 1.0)

    def hf(hold, power):
        if not hold or not base:
            return 1.0
        return max(0.6, min(1.6, (hold / base) ** (sw * power)))
    return [(m, p, w * f * hf(h, 0.5) + (1 - w) * k * e * hf(h, 1.0)) for m, p, f, e, h in rows], w


class Model:
    def __init__(self, levels, beta, cov, prior_sd, n, trust):
        self.levels, self.beta, self.cov, self.prior_sd, self.n, self.trust = levels, beta, cov, prior_sd, n, trust
        self.index = {lv: i for i, lv in enumerate(levels)}

    def marginal(self, group, level):
        i = self.index.get((group, level))
        if i is None:
            return 0.0, self.prior_sd[group] ** 2
        return float(self.beta[i]), float(self.cov[i, i])

    def draw(self, rng=None):
        """One Thompson sample: a plausible value for every known effect."""
        rng = rng or np.random.default_rng()
        try:
            b = rng.multivariate_normal(self.beta, self.cov, method="cholesky")
        except Exception:
            b = self.beta + rng.normal(0, np.sqrt(np.clip(np.diag(self.cov), 1e-9, None)))
        return Draw(self, b, rng)


class Draw:
    def __init__(self, model, b, rng):
        self.m, self.b, self.rng, self.unseen = model, b, rng, {}

    def effect(self, group, level):
        i = self.m.index.get((group, level))
        if i is not None:
            return float(self.b[i])
        key = (group, level)  # never-tried option: sample from the prior, so it gets explored
        if key not in self.unseen:
            self.unseen[key] = float(self.rng.normal(0, self.m.prior_sd[group]))
        return self.unseen[key]

    def score(self, feats):
        return sum(self.effect(g, lv) for g, lv in feats.items() if g in PRIOR_SD)

    def best(self, group, options):
        return max(options, key=lambda o: self.effect(group, o))


def fit(led):
    """Fit the model on matured reels. Returns a Model (works with zero data: everything is prior)."""
    att, lam = attribute(led)
    rows, trust = _targets(led, att)
    for m, p in led["posts"].items():
        p["est_follows"] = round(att.get(m, 0.0), 2)
    levels = sorted({(g, lv) for _, p, _ in rows for g, lv in features(p).items()})
    prior_sd = dict(PRIOR_SD)
    if not rows:
        return Model([], np.zeros(0), np.zeros((0, 0)), prior_sd, 0, trust)
    idx = {lv: i for i, lv in enumerate(levels)}
    X = np.zeros((len(rows), len(levels) + 1)); X[:, -1] = 1.0  # last column = intercept
    y = np.array([r[2] for r in rows])
    for r, (_, p, _) in enumerate(rows):
        for g, lv in features(p).items():
            X[r, idx[(g, lv)]] = 1.0
    lam_diag = np.array([1 / prior_sd[g] ** 2 for g, _ in levels] + [1e-4])
    beta = np.zeros(X.shape[1]); beta[-1] = math.log(max(y.mean(), 1e-3))
    for _ in range(50):  # penalised IRLS
        eta = np.clip(X @ beta, -20, 20); mu = np.exp(eta)
        z = eta + (y - mu) / mu
        H = X.T @ (X * mu[:, None]) + np.diag(lam_diag)
        new = np.linalg.solve(H, X.T @ (mu * z))
        if np.max(np.abs(new - beta)) < 1e-6:
            beta = new; break
        beta = new
    mu = np.exp(np.clip(X @ beta, -20, 20))
    H = X.T @ (X * mu[:, None]) + np.diag(lam_diag)
    cov = np.linalg.inv(H)
    dof = max(len(rows) - min(len(levels), len(rows) - 1), 1)
    phi = float(np.clip(np.sum((y - mu) ** 2 / np.maximum(mu, 1e-6)) / dof, 1.0, 10.0))  # overdispersion
    cov = cov * phi
    model = Model(levels, beta[:-1], cov[:-1, :-1], prior_sd, len(rows), trust)
    led["model"] = summary(model, att, lam, len(rows), trust)
    return model


def summary(model, att, lam, n, trust):
    """Plain numbers for the dashboard and the weekly analyst."""
    eff = []
    for (g, lv) in model.levels:
        m, v = model.marginal(g, lv)
        sd = math.sqrt(v)
        p_better = 0.5 * (1 + math.erf(m / (sd * math.sqrt(2)))) if sd > 0 else 0.5
        eff.append({"group": g, "level": lv, "x": round(math.exp(m), 2), "sure": round(max(p_better, 1 - p_better), 2),
                    "better": p_better > 0.5})
    return {"updated": now().isoformat(timespec="minutes"), "reels_learned_from": n,
            "follower_data_weight": round(trust, 2), "background_follows_per_day": round(lam * 24, 1),
            "attributed_follows": round(sum(att.values()), 1), "effects": eff}


# ---------------------------------------------------------------- 3. decisions helpers
def choose_times(draw, n, candidates, min_gap_min=90):
    """Pick n posting times: best sampled slot effects, kept at least min_gap apart."""
    def mins(t):
        h, m = map(int, t.split(":")); return h * 60 + m
    ranked = sorted(candidates, key=lambda t: draw.effect("slot", t.split(":")[0].zfill(2)), reverse=True)
    out = []
    for t in ranked:
        if all(abs(mins(t) - mins(o)) >= min_gap_min for o in out):
            out.append(t)
        if len(out) == n:
            break
    return sorted(out, key=mins)


def retire_candidates(model, threshold=-0.4, sure=0.8):
    """Topics the model is confident under-perform (effect below threshold with probability > sure)."""
    out = []
    for (g, lv) in model.levels:
        if g != "topic":
            continue
        m, v = model.marginal(g, lv)
        sd = math.sqrt(v)
        p_bad = 0.5 * (1 + math.erf((threshold - m) / (sd * math.sqrt(2))))
        if p_bad > sure:
            out.append(lv)
    return out

