"""Build docs/index.html (served by GitHub Pages) from the ledger. Runs after every daily job."""
import datetime as dt, html, statistics
from .common import ROOT, load_ledger, load_topics, now

E = html.escape


def score(i):
    return i.get("views", 0) + 20 * i.get("shares", 0) + 10 * i.get("saved", 0)


def line_chart(points, label):
    """points: [(date_str, value)] -> SVG line with hover dots. Single series: no legend, title names it."""
    if len(points) < 2:
        return f'<p class="muted">Not enough data yet for {E(label.lower())}.</p>'
    W, H, P = 640, 200, 36
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    xs = [P + i * (W - 2 * P) / (len(points) - 1) for i in range(len(points))]
    ys = [H - P - (v - lo) * (H - 2 * P) / (hi - lo) for v in vals]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(f'<circle class="pt" cx="{x:.1f}" cy="{y:.1f}" r="5" data-tip="{E(d)}: {v:,}"/>'
                   f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="14" data-tip="{E(d)}: {v:,}"/>'
                   for x, y, (d, v) in zip(xs, ys, points))
    grid = "".join(f'<line class="grid" x1="{P}" x2="{W-P}" y1="{y}" y2="{y}"/>' for y in (P, H / 2, H - P))
    axis = (f'<text class="ax" x="{P}" y="{H-8}">{E(points[0][0])}</text>'
            f'<text class="ax" x="{W-P}" y="{H-8}" text-anchor="end">{E(points[-1][0])}</text>'
            f'<text class="ax" x="4" y="{P+4}">{hi:,}</text><text class="ax" x="4" y="{H-P+4}">{lo:,}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{E(label)}">{grid}{axis}<path class="ln" d="{path}"/>{dots}</svg>'


def bar_chart(rows, label):
    """rows: [(name, value)] horizontal bars."""
    if not rows:
        return f'<p class="muted">No data yet for {E(label.lower())}.</p>'
    mx = max(v for _, v in rows) or 1
    out = []
    for name, v in rows:
        out.append(f'<div class="bar-row" data-tip="{E(name)}: {v:,.0f}"><span class="bar-label">{E(name)}</span>'
                   f'<span class="bar-track"><span class="bar" style="width:{max(2, 100*v/mx):.1f}%"></span></span>'
                   f'<span class="bar-val">{v:,.0f}</span></div>')
    return "".join(out)


def main():
    led = load_ledger()
    topics = {t["id"]: t for t in load_topics(led)}
    today = now().date()
    posts = sorted(led["posts"].items(), key=lambda kv: kv[1]["posted_at"], reverse=True)

    daily_f = dict(led.get("followers", {}))
    for ts, n in led.get("followers_hourly", []):  # hourly snapshots fill any gaps (last value per day)
        daily_f[ts[:10]] = n
    fol = sorted(daily_f.items())
    fol_now = fol[-1][1] if fol else 0
    fol_week = next((v for d, v in reversed(fol) if dt.date.fromisoformat(d) <= today - dt.timedelta(days=7)),
                    fol[0][1] if fol else 0)
    recent = [p for _, p in posts if (today - dt.date.fromisoformat(p["date"])).days <= 7]
    views7 = sum((p.get("insights") or {}).get("views", 0) for p in recent)
    by_kind = {k: [ (p.get("insights") or {}).get("views", 0) for _, p in posts if p["kind"] == k and p.get("insights")]
               for k in ("ai", "real")}
    avg = {k: (statistics.mean(v) if v else 0) for k, v in by_kind.items()}

    # Graduate candidates: best 5 of the last 10 days that beat the median, not already marked graduated
    fresh = [(mid, p) for mid, p in posts if (today - dt.date.fromisoformat(p["date"])).days <= 10
             and p.get("insights") and not p.get("graduated")]
    med = statistics.median([score(p["insights"]) for _, p in fresh]) if fresh else 0
    grad = sorted([kv for kv in fresh if score(kv[1]["insights"]) > med],
                  key=lambda kv: -score(kv[1]["insights"]))[:5]

    # Daily views by post date, last 30 days
    daily = {}
    for _, p in posts:
        daily[p["date"]] = daily.get(p["date"], 0) + (p.get("insights") or {}).get("views", 0)
    daily_pts = sorted((d, v) for d, v in daily.items() if (today - dt.date.fromisoformat(d)).days <= 30)

    # Topic leaderboard
    tscore = {}
    for _, p in posts:
        if p.get("insights"):
            tscore.setdefault(p["topic"], []).append(score(p["insights"]))
    board = sorted(((topics.get(t, {}).get("hook", t), statistics.mean(v)) for t, v in tscore.items()),
                   key=lambda r: -r[1])[:10]

    def link(p):
        return (f"<a href='{E(p['permalink'])}' target='_blank' rel='noopener'>{E(p['hook'])}</a>"
                if p.get("permalink") else E(p["hook"]))

    def reel_rows(items):
        r = []
        for mid, p in items:
            i = p.get("insights") or {}
            r.append(f"<tr><td>{E(p['date'])}</td><td>{'AI' if p['kind']=='ai' else 'Real'}</td><td>{'🔥 ' if p.get('alerted') else ''}{link(p)}</td>"
                     f"<td class='n'>{i.get('views',0):,}</td><td class='n'>{i.get('reach',0):,}</td>"
                     f"<td class='n'>{i.get('shares',0):,}</td><td class='n'>{i.get('saved',0):,}</td>"
                     f"<td class='n'>{(i.get('ig_reels_avg_watch_time',0) or 0)/1000:.1f}s</td>"
                     f"<td class='n'>{p.get('est_follows', '–')}</td></tr>")
        return "".join(r) or "<tr><td colspan=9 class='muted'>Nothing posted yet.</td></tr>"

    from .common import CFG
    fal_spent = led.get("fal_spend", {}).get(now().strftime("%Y-%m"), 0)
    fal_cap = CFG.get("fal_monthly_budget_usd", 30)
    warn = []
    for key, d in (CFG.get("key_expiry") or {}).items():
        days = (dt.date.fromisoformat(str(d)) - today).days
        if days <= 7:
            warn.append(f"{key} {'expired' if days < 0 else 'expires in ' + str(days) + ' days'} ({d}). "
                        "Make a new key, update the GitHub secret, then update key_expiry in config.yaml.")
    warn_html = "".join(f"<div class='card warn'>⚠️ {E(w)}</div>" for w in warn)
    tomorrow = (today + dt.timedelta(days=1)).isoformat()
    queue_rows = "".join(
        f"<tr><td>{E(d)}</td><td>{E(e['time'])}</td><td>{'AI' if e['kind']=='ai' else 'Real'}</td>"
        f"<td>{E(e['hook'])}</td><td><span class='st st-{E(e['status'])}'>{E(e['status'])}</span></td></tr>"
        for d in (today.isoformat(), tomorrow) for e in led["queue"].get(d, [])) \
        or "<tr><td colspan=5 class='muted'>Nothing queued.</td></tr>"
    trend_rows = "".join(
        f"<li><b>{E(t.get('format') or '?')}</b> <span class='muted'>({E(t['date'])}, from {E(t['file'])})</span>"
        f"<br><span class='muted'>{E(t.get('why') or '')}</span>"
        + "".join(f"<br>→ {E(topics[i]['hook'])}" for i in t.get('ideas', []) if i in topics) + "</li>"
        for t in reversed(led.get("trends", [])[-8:])) or "<li class='muted'>Save trending reels to Drive › reelbot › trends.</li>"
    # ---- what the follower learner has found
    m = led.get("model") or {}
    names = {"kind": {"ai": "Mascot reels", "real": "Your own footage"},
             "style": {"question": "Question-box style", "list": "Numbered-list style", "pov": "POV caption style"},
             "hook": {"how_i": '"How I…" hooks', "why": '"Why…" hooks', "question": "Question hooks",
                      "pov": '"POV:" hooks', "number": 'Number hooks ("3 mistakes…")', "statement": "Bold-statement hooks"},
             "sound": {"sound": "With a sound", "none": "No added sound"},
             "reuse": {"reuse": "Reused footage", "first": "Fresh footage"},
             "source": {"bank": "Your topic bank", "trend": "Trend-inbox ideas", "analyst": "Analyst ideas"}}

    def label(e):
        g, lv = e["group"], e["level"]
        if g == "topic":
            return "Topic: " + topics.get(lv, {}).get("hook", lv)
        if g == "slot":
            return f"Posting around {int(lv)}:00"
        return names.get(g, {}).get(lv, f"{g}: {lv}")
    strong = [e for e in m.get("effects", []) if e["sure"] >= 0.7 and abs(e["x"] - 1) >= 0.15]
    strong.sort(key=lambda e: -e["x"])
    learn_rows = "".join(
        f"<tr><td>{E(label(e))}</td><td class='n'>{e['x']:.2f}×</td><td class='n'>{e['sure']:.0%}</td></tr>"
        for e in strong[:6] + [e for e in strong[-4:] if e not in strong[:6]]) \
        or "<tr><td colspan=3 class='muted'>Nothing confident yet. Learning starts once reels are 3 days old.</td></tr>"
    an = led.get("analyst") or {}
    an_html = ("".join(f"<li>{E(i)}</li>" for i in an.get("insights", []))
               + (f"<li><b>Next week:</b> {E(an['focus'])}</li>" if an.get("focus") else "")) \
        or "<li class='muted'>The weekly analyst starts after about 10 reels have matured.</li>"
    rested = ", ".join(E(topics.get(t, {}).get("hook", t)) for t in led.get("retired", {})) or "none"
    w = m.get("follower_data_weight", 0)
    learn_html = (f"<div class='card'><h2>What's bringing followers</h2>"
                  f"<p class='muted' style='margin:0 0 8px'>Learned from {m.get('reels_learned_from', 0)} reels · "
                  f"{m.get('attributed_follows', 0)} followers traced to reels · based {w:.0%} on follower data, "
                  f"{1 - w:.0%} on engagement · ~{m.get('background_follows_per_day', 0)} followers/day from elsewhere</p>"
                  f"<div class='wrap'><table><tr><th>Factor</th><th class='n'>Followers vs average</th>"
                  f"<th class='n'>Confidence</th></tr>{learn_rows}</table></div>"
                  f"<h2 style='margin-top:14px'>Weekly analyst {('(' + E(an['date']) + ')') if an.get('date') else ''}</h2>"
                  f"<ul>{an_html}</ul><p class='muted' style='margin:8px 0 0'>Resting (few followers): {rested}</p></div>")
    log_rows = "".join(f"<li>{E(l)}</li>" for l in reversed(led.get("log", [])[-15:]))
    delta = fol_now - fol_week
    grad_html = "".join(
        f"<li><b>{link(p)}</b> <span class='muted'>({E(p['date'])}, {p['insights'].get('views',0):,} views, "
        f"{p['insights'].get('shares',0)} shares)</span></li>" for _, p in grad) or "<li class='muted'>No candidates yet.</li>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>@conor_sends reel dashboard</title>
<style>
:root{{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0d9;--s1:#2a78d6;--good:#006300;--bad:#b42318}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#232322;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--s1:#3987e5;--good:#0ca30c;--bad:#ff6b5e}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:0 0 10px}}
.muted{{color:var(--ink2)}}a{{color:inherit;text-decoration-color:var(--s1);text-underline-offset:3px}}.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}}
.card{{background:var(--card);border:1px solid var(--grid);border-radius:12px;padding:14px 16px;margin-bottom:14px}}
.k{{font-size:13px;color:var(--ink2)}}.v{{font-size:28px;font-weight:650;margin-top:2px}}.d{{font-size:13px}}.up{{color:var(--good)}}.down{{color:var(--bad)}}
svg{{width:100%;height:auto;display:block}}.ln{{fill:none;stroke:var(--s1);stroke-width:2}}.pt{{fill:var(--s1);stroke:var(--card);stroke-width:2}}
.hit{{fill:transparent;cursor:default}}.grid{{stroke:var(--grid);stroke-width:1}}.ax{{fill:var(--ink2);font-size:11px}}
.bar-row{{display:grid;grid-template-columns:minmax(120px,38%) 1fr 70px;gap:10px;align-items:center;margin:6px 0;font-size:13px}}
.bar-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:12px}}.bar{{display:block;height:12px;background:var(--s1);border-radius:0 4px 4px 0}}
.bar-val{{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:7px 6px;border-bottom:1px solid var(--grid);vertical-align:top}}
th{{color:var(--ink2);font-weight:600}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}.wrap{{overflow-x:auto}}
.st{{font-size:12px;padding:1px 7px;border-radius:9px;border:1px solid var(--grid)}}.st-posted{{color:var(--good)}}.st-failed,.st-vetoed{{color:var(--bad)}}
ul{{margin:0;padding-left:18px}}li{{margin:3px 0}}#tip{{position:fixed;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12px;padding:4px 8px;border-radius:6px;display:none;z-index:9}}
.warn{{border-color:var(--bad);color:var(--bad);font-weight:600}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}@media(max-width:720px){{.two{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>@conor_sends trial reels</h1><div class="muted">Updated {now():%a %d %b %Y, %H:%M} · Delete a reel from Drive/queue to stop it posting. · AI video spend this month: about US${fal_spent:.2f} of US${fal_cap} cap</div>
{warn_html}<div class="tiles">
<div class="card"><div class="k">Followers</div><div class="v">{fol_now:,}</div><div class="d {'up' if delta>=0 else 'down'}">{'+' if delta>=0 else ''}{delta:,} in 7 days</div></div>
<div class="card"><div class="k">Views, last 7 days</div><div class="v">{views7:,}</div><div class="d muted">{len(recent)} reels posted</div></div>
<div class="card"><div class="k">Avg views per AI reel</div><div class="v">{avg['ai']:,.0f}</div><div class="d muted">{len(by_kind['ai'])} reels with stats</div></div>
<div class="card"><div class="k">Avg views per real-clip reel</div><div class="v">{avg['real']:,.0f}</div><div class="d muted">{len(by_kind['real'])} reels with stats</div></div>
</div>
<div class="card muted">Weekly: check Instagram › Settings › <b>Account Status</b> for any recommendation or originality warnings.</div>
<div class="card"><h2>Graduate these in the Instagram app</h2><ul>{grad_html}</ul>
<p class="muted" style="margin:8px 0 0">Best 5 of the last 10 days by views, shares and saves. Open the reel in the app, then share it to your profile.</p></div>
{learn_html}
<div class="two">
<div class="card"><h2>Followers</h2>{line_chart(fol[-60:], "Followers")}</div>
<div class="card"><h2>Views by posting day</h2>{line_chart(daily_pts, "Views by posting day")}</div>
</div>
<div class="card"><h2>Best topics (avg score)</h2>{bar_chart(board, "Best topics")}
<p class="muted" style="margin:8px 0 0">Score = views + 20×shares + 10×saves. The planner picks high scorers more often.</p></div>
<div class="card"><h2>Trend inbox</h2><ul>{trend_rows}</ul></div>
<div class="card"><h2>Today and tomorrow</h2><div class="wrap"><table><tr><th>Date</th><th>Time</th><th>Type</th><th>Hook</th><th>Status</th></tr>{queue_rows}</table></div></div>
<div class="card"><h2>Latest 50 reels</h2><div class="wrap"><table><tr><th>Date</th><th>Type</th><th>Hook</th><th class='n'>Views</th><th class='n'>Reach</th><th class='n'>Shares</th><th class='n'>Saves</th><th class='n'>Avg watch</th><th class='n'>Est. followers</th></tr>{reel_rows(posts[:50])}</table></div></div>
<div class="card"><h2>Bot log</h2><ul class="muted">{log_rows or '<li>Nothing yet.</li>'}</ul></div>
</main><div id="tip"></div>
<script>
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{{const t=e.target.closest('[data-tip]');
 if(!t){{tip.style.display='none';return}}tip.textContent=t.dataset.tip;tip.style.display='block';
 tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';}});
</script></body></html>"""
    out = ROOT / "docs/index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page)
    print("dashboard ->", out)


if __name__ == "__main__":
    main()
