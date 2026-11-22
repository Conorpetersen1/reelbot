"""Check the learner on a fake account with hidden 'true' effects.

    python -m tools.simulate_learning            # learner vs random posting, 3 seeds
Followers arrive hourly as noisy counts, like the real API, and the learner never sees the true effects.
"""
import datetime as dt, math, os, random, sys
import numpy as np
from pipeline import learn, common

TOPICS = [f"t{i:02d}" for i in range(1, 31)]
OPTIONS = {"kind": ["ai", "real"], "style": ["question", "list", "pov"], "hook": learn.HOOK_TYPES,
           "sound": ["none", "sound"], "source": ["bank"], "reuse": ["first"]}
TIMES = ["07:00", "08:30", "11:00", "12:30", "15:30", "17:30", "18:30", "19:30", "20:30", "21:30"]


def world(seed):
    r = random.Random(seed)
    truth = {("topic", t): r.gauss(0, 0.5) for t in TOPICS}
    truth.update({("kind", "ai"): -0.3, ("kind", "real"): 0.2, ("style", "pov"): 0.5, ("style", "list"): 0.1,
                  ("style", "question"): -0.2, ("hook", "why"): 0.4, ("hook", "how_i"): 0.3,
                  ("hook", "statement"): -0.4, ("sound", "sound"): 0.3, ("sound", "none"): -0.1})
    for t in TIMES:
        truth[("slot", t[:2])] = {"18": 0.5, "19": 0.4, "20": 0.3, "07": -0.3}.get(t[:2], 0.0)
    return truth


def simulate(policy, seed, days=42, per_day=5):
    rng = np.random.default_rng(seed); r = random.Random(seed)
    truth = world(seed)
    start = dt.datetime(2026, 12, 1, 0, 0, tzinfo=common.TZ)
    clock = {"t": start}
    learn.now = lambda: clock["t"]; common.now = lambda: clock["t"]
    led = {"posts": {}, "followers_hourly": [], "queue": {}}
    followers, daily = 400, []
    reels = []  # (mid, post, true_rate, true_total_views)
    for day in range(days):
        model = learn.fit(led) if policy == "learn" else None
        draw = model.draw(rng) if model else None
        times = learn.choose_times(draw, per_day, TIMES) if draw else sorted(r.sample(TIMES, per_day))
        used = set()
        for i, tm in enumerate(times):
            feats_opts = {}
            if draw:
                topic = max([t for t in TOPICS if t not in used], key=lambda t: draw.effect("topic", t))
                for g, opts in OPTIONS.items():
                    feats_opts[g] = draw.best(g, opts)
            else:
                topic = r.choice([t for t in TOPICS if t not in used])
                for g, opts in OPTIONS.items():
                    feats_opts[g] = r.choice(opts)
            used.add(topic)
            hh, mm = map(int, tm.split(":"))
            posted = start + dt.timedelta(days=day, hours=hh, minutes=mm)
            post = {"topic": topic, "kind": feats_opts["kind"], "style": feats_opts["style"],
                    "hook_type": feats_opts["hook"], "sound_kind": feats_opts["sound"], "time": tm,
                    "source": "bank", "posted_at": posted.isoformat(), "series": []}
            eff = sum(truth.get((g, lv), 0) for g, lv in learn.features(post).items())
            views = float(rng.lognormal(float(os.environ.get("SIM_VIEWS", 6.0)), 0.8)) * math.exp(0.6 * eff)
            rate = float(os.environ.get("SIM_RATE", 0.004)) * math.exp(float(os.environ.get("SIM_EFFECT", 0.7)) * eff)
            mid = f"m{day}_{i}"
            led["posts"][mid] = post
            reels.append((mid, post, rate, views))
        gained = 0
        for h in range(24):
            clock["t"] = start + dt.timedelta(days=day, hours=h, minutes=5)
            t, ins = clock["t"], {}
            lam = float(os.environ.get("SIM_BG", 0.25))  # background follows per hour from elsewhere
            new = rng.poisson(lam)
            for mid, post, rate, views in reels:
                age = (t - dt.datetime.fromisoformat(post["posted_at"])).total_seconds() / 3600
                if age <= 0 or age > 80:
                    continue
                cum = views * (1 - math.exp(-age / 12))
                prev = views * (1 - math.exp(-max(age - 1, 0) / 12))
                new += rng.poisson(rate * max(cum - prev, 0))
                ins[mid] = {"views": int(cum), "reach": int(cum * 0.8), "shares": int(cum * 0.004 * math.exp(eff_of(post, truth))),
                            "saved": int(cum * 0.003)}
            followers += new; gained += new
            learn.record(led, followers, ins)
        daily.append(gained)
    return daily, led, truth


def eff_of(post, truth):
    return 0.5 * sum(truth.get((g, lv), 0) for g, lv in learn.features(post).items())


def main():
    seeds = [int(a) for a in sys.argv[1:]] or [1, 2, 3]
    common.CFG.setdefault("learning", {})
    for seed in seeds:
        d_rand, _, _ = simulate("random", seed)
        d_learn, led, truth = simulate("learn", seed)
        late = slice(28, 42)
        print(f"seed {seed}: followers/day in weeks 5-6  random {np.mean(d_rand[late]):.1f}  "
              f"learner {np.mean(d_learn[late]):.1f}  (+{100*(np.mean(d_learn[late])/np.mean(d_rand[late])-1):.0f}%)  "
              f"| first week random {np.mean(d_rand[:7]):.1f} learner {np.mean(d_learn[:7]):.1f}")
        eff = {(e["group"], e["level"]): math.log(e["x"]) for e in led["model"]["effects"]}
        for g in ["style", "hook", "kind", "slot", "sound"]:
            got = sorted([(round(v, 2), lv) for (gg, lv), v in eff.items() if gg == g], reverse=True)[:3]
            true = sorted([(round(v, 2), lv) for (gg, lv), v in truth.items() if gg == g], reverse=True)[:3]
            print(f"   {g:6s} learned top: {[lv for _, lv in got]}   true top: {[lv for _, lv in true]}")
        t_true = {lv: v for (g, lv), v in truth.items() if g == "topic"}
        t_got = {lv: v for (g, lv), v in eff.items() if g == "topic"}
        common_t = [t for t in t_got if t in t_true]
        if len(common_t) > 3:
            c = np.corrcoef([t_true[t] for t in common_t], [t_got[t] for t in common_t])[0, 1]
            print(f"   topic effects correlation with truth: {c:.2f} over {len(common_t)} topics; "
                  f"follower-data weight {led['model']['follower_data_weight']}")


if __name__ == "__main__":
    main()
