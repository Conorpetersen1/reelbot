"""Build docs/index.html (served by GitHub Pages) from the ledger. Runs after every daily job."""
import datetime as dt, html, json, re, statistics
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
                     f"<td class='n'>{(str(round(i['reels_skip_rate'])) + '%') if isinstance(i.get('reels_skip_rate'), (int, float)) else '–'}</td>"
                     f"<td class='n'>{p.get('est_follows', '–')}</td></tr>")
        return "".join(r) or "<tr><td colspan=10 class='muted'>Nothing posted yet.</td></tr>"

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
    # ---- Review: every reel for yesterday, today and tomorrow, with the video, on-screen words, sound, caption
    def sound_name(n):
        if not n:
            return "No added sound (clip's own audio)"
        n = re.sub(r"^sweep \S+ - ", "", n.rsplit(".", 1)[0])
        return n

    def review_card(d, e):
        fid = e.get("file_id", "")
        post = led["posts"].get(e.get("media_id") or "", {})
        link = (f" · <a href='{E(post['permalink'])}'>open on Instagram</a>" if post.get("permalink") else "")
        lines = "".join(f"<li>{E(l)}</li>" for l in e.get("description", []))
        src = {"written": "written by Claude", "topic_bank": "from your topic list"}.get(e.get("text_source"), "")
        cr = e.get("critic") or {}
        if cr:
            src += f" · editor score {cr.get('score')}/25" + (f" · {cr['angle']}" if cr.get("angle") else "")
        if e.get("category") == "moment":
            src += " · moment reel (about the footage)"
        dd = dt.date.fromisoformat(d)
        when = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get((dd - today).days, f"{dd:%a %d %b}")
        return (f"<details class='rv'><summary><b>{when} {E(e['time'])}</b> · {'Mascot' if e['kind']=='ai' else 'Real'}"
                f" · {E(e['hook'])} <span class='st st-{E(e['status'])}'>{E(e['status'])}</span></summary>"
                f"<div class='rvb'><div class='vbox'><iframe loading='lazy' src='https://drive.google.com/file/d/{E(fid)}/preview' "
                f"allow='autoplay' title='Reel video'></iframe></div><div>"
                f"<div class='k'>a. Video</div><div>{E(e.get('category', '').replace('_', ' '))} · fit check "
                f"{E(str(e.get('fit', '?')))}/5 · <a href='https://drive.google.com/file/d/{E(fid)}/view'>open in Drive</a>{link}</div>"
                f"<div class='k'>b. Words on screen <span class='muted'>({E(src)})</span></div>"
                f"<div><b>{E(e['hook'])}</b><ul>{lines}</ul></div>"
                f"<div class='k'>c. Sound</div><div>{E(sound_name(e.get('sound')))}</div>"
                f"<div class='k'>d. Caption</div><div class='cap'>{E(e.get('caption', ''))}</div>"
                f"{rate_form(d, e)}"
                f"<div class='muted' style='margin-top:8px;font-size:12px'>To stop a reel posting, delete it from Drive › queue.</div>"
                f"</div></div></details>")

    def rate_form(d, e):
        key = f"{d}#{e.get('slot')}"
        got = (led.get("ratings") or {}).get(key, {})
        rows = ""
        for part, lab in (("video", "a. Video"), ("words", "b. Words"), ("sound", "c. Sound"), ("caption", "d. Caption")):
            opts = "".join(f"<label><input type='radio' name='{part}' value='{n}'{' checked' if got.get(part) == n else ''}>"
                           f"<span>{n}</span></label>" for n in range(1, 6))
            rows += f"<div class='rrow'><span class='rl'>{lab}</span><span class='stars'>{opts}</span></div>"
        done = ("✓ Saved: " + " · ".join(f"{p[0]}{got[p]}" for p in ("video", "words", "sound", "caption") if p in got)
                if got else "")
        return (f"<form class='rate' data-key='{E(key)}' data-hook='{E(e['hook'])}'><div class='k'>Your rating (1 = bad, 5 = great)</div>"
                f"{rows}<textarea name='why' rows='2' placeholder='Why? e.g. hook too long, mascot looks off (optional)'>"
                f"{E(got.get('why', ''))}</textarea>"
                f"<div><button type='submit'>{'Update rating' if got else 'Save rating'}</button> "
                f"<span class='saved'>{E(done)}</span></div>"
                f"<div class='muted small hint' hidden>A GitHub page opened. Tap <b>Submit new issue</b> there; the bot picks it up within the hour.</div></form>")
    yday = (today - dt.timedelta(days=1)).isoformat()
    review_html = "".join(review_card(d, e) for d in (tomorrow, today.isoformat(), yday)
                          for e in led["queue"].get(d, [])) or "<p class='muted'>Nothing queued.</p>"
    older = [(d, e) for d in sorted(led["queue"], reverse=True)
             if (today - dt.date.fromisoformat(d)).days in range(2, 8) for e in led["queue"][d] if e["status"] == "posted"]
    if older:
        review_html += ("<details class='older'><summary><b>Earlier this week</b> <span class='muted'>"
                        f"({len(older)} posted reels to rate)</span></summary>"
                        + "".join(review_card(d, e) for d, e in older) + "</details>")
    # ---- Your ratings vs the audience
    from . import ratings as rt
    rated = []
    views = [((p.get("insights") or {}).get("views", 0)) for p in led["posts"].values() if p.get("insights")]
    med_v = statistics.median(views) if views else 0
    for key, r in sorted((led.get("ratings") or {}).items(), reverse=True):
        e = rt.entry_for(led, key)
        if not e:
            continue
        o = rt.overall(r)
        post = led["posts"].get(e.get("media_id") or "", {})
        v = (post.get("insights") or {}).get("views")
        flag = ""
        if v is not None and o is not None and views:
            if o <= 2.5 and v > med_v:
                flag = "⚑ you rated it low, the audience liked it"
            elif o >= 4 and v < med_v:
                flag = "⚑ you liked it, the audience didn't"
        rated.append(f"<tr><td>{E(key.split('#')[0])}</td><td>{E(e['hook'])}</td>"
                     + "".join(f"<td class='n'>{r.get(p, '–')}</td>" for p in rt.PARTS)
                     + f"<td class='n'>{'–' if v is None else f'{v:,}'}</td><td class='small'>{E(flag)}"
                     + (f"<br><span class='muted'>“{E(r['why'])}”</span>" if r.get("why") else "") + "</td></tr>")
    ratings_html = ("<div class='card'><h2>Your ratings vs the audience</h2><div class='wrap'><table><tr><th>Date</th><th>Hook</th>"
                    "<th class='n'>Video</th><th class='n'>Words</th><th class='n'>Sound</th><th class='n'>Caption</th>"
                    "<th class='n'>Views</th><th></th></tr>" + "".join(rated) + "</table></div>"
                    "<p class='muted small' style='margin:8px 0 0'>Your ratings steer quality and taste; followers stay the main "
                    "signal. A ⚑ means you and the audience disagree, worth a look.</p></div>") if rated else \
        ("<div class='card'><h2>Your ratings vs the audience</h2><p class='muted' style='margin:0'>No ratings yet. "
         "Rate reels in the <a href='#review' data-go='review'>Review</a> tab.</p></div>")

    # ---- What the bot has learned from mascot clips Conor binned
    tst, binned = led.get("ai_taste") or {}, led.get("ai_binned", [])
    taste_html = ""
    if binned:
        pending = sum(1 for b in binned if not b.get("reviewed"))
        taste_html = ("<div class='card'><h2>Learned from mascot clips you binned</h2>"
                      f"<p class='muted' style='margin:0 0 6px'>{len(binned)} binned so far"
                      + (f", {pending} still to look at (tomorrow's run)" if pending else "") + ". "
                      "New clips are made to avoid:</p>"
                      + ("<ul>" + "".join(f"<li>{E(a)}</li>" for a in tst.get("avoid", [])) + "</ul>"
                         if tst.get("avoid") else "<p class='muted'>Nothing yet.</p>")
                      + (f"<p style='margin:8px 0 0'>{E(tst['summary'])}</p>" if tst.get("summary") else "")
                      + "</div>")

    # ---- Reminders and costs
    from . import reminders, usage
    rem = reminders.active(led)
    rem_html = ("<div class='card rem'><h2>📌 To do</h2><ul>" + "".join(f"<li>{E(r['text'])}</li>" for r in rem)
                + "</ul></div>") if rem else ""
    us = usage.summary()
    nice = {"claude": "Claude (writing)", "gemini": "Gemini (watching video)", "fal": "fal (mascot clips)"}
    def money(x, sym="A$"):
        return "–" if x is None else f"{sym}{x:,.2f}"

    def left_cell(a):
        v, own = us[a]["aud"]["left"], us[a]["left"]
        extra = (f" <span class='muted'>({us[a]['sym']}{own:,.2f})</span>"
                 if own is not None and us[a]["sym"] != "A$" else "")
        low = v is not None and v < 4.5
        return f"<td class='n {'down' if low else ''}'>{money(v)}{extra}</td>"
    cost_rows = "".join(
        f"<tr><td>{nice[a]}</td>" + "".join(f"<td class='n'>{money(us[a]['aud'][k])}</td>"
                                            for k in ("today", "week", "month", "total"))
        + left_cell(a) + "</tr>" for a in usage.APIS)
    cost_rows += ("<tr><td><b>Total</b></td>" + "".join(f"<td class='n'><b>{money(us['aud_total'][k])}</b></td>"
                                              for k in ("today", "week", "month", "total")) + "<td></td></tr>")
    cost_html = (f"<div class='card'><h2>Costs</h2><div class='wrap'><table><tr><th>API</th><th class='n'>Today</th>"
                 f"<th class='n'>7 days</th><th class='n'>This month</th><th class='n'>Project total</th>"
                 f"<th class='n'>Credit left (est.)</th></tr>{cost_rows}</table></div>"
                 f"<div class='k' style='margin-top:12px'>Spend per day (A$)</div>{line_chart(us['daily'], 'Spend per day (A$)')}"
                 f"<p class='muted' style='margin:8px 0 0'>All in A$ (Claude and fal bill in US$, converted at about {usage.CFG.get('usd_to_aud', 1.54)} A$ per US$; their US$ balance is in brackets). Estimates from the bot's own records, tracked since 22 Nov 2026. "
                 f"Your billing pages are the final word. When you top up, update the balance under <code>api_credits</code> "
                 f"in config.yaml (or tell Claude) so 'credit left' stays right.</p></div>")
    trend_rows = "".join(
        f"<li><b>{E(t.get('format') or '?')}</b> <span class='muted'>({E(t['date'])}, from {E(t['file'])})</span>"
        f"<br><span class='muted'>{E(t.get('why') or '')}</span>"
        + "".join(f"<br>→ {E(topics[i]['hook'])}" for i in t.get('ideas', []) if i in topics) + "</li>"
        for t in reversed(led.get("trends", [])[-8:])) or "<li class='muted'>Save trending reels to Drive › reelbot › trends.</li>"
    last_scroll = next((t for t in reversed(led.get("trends", [])) if t.get("reels")), None)
    viral_rows = ""
    if last_scroll:
        vr = sorted(last_scroll["reels"], key=lambda r: (not r.get("viral"), r.get("t", 0)))
        viral_rows = "".join(
            f"<tr><td>{'🔥' if r.get('viral') else ''}</td><td>{E(r.get('hook') or '–')}</td><td>{E(r.get('format') or '')}</td>"
            f"<td class='small'>{E(r.get('numbers') or '')}</td><td class='small muted'>{E(r.get('sound') or '')}</td></tr>"
            for r in vr[:30])
        viral_rows = (f"<div class='card'><h2>Your last feed scroll ({E(last_scroll['date'])})</h2>"
                      f"<p class='muted small' style='margin:0 0 6px'>{len(last_scroll['reels'])} reels watched; 🔥 = clearly viral.</p>"
                      "<div class='wrap'><table><tr><th></th><th>Hook on screen</th><th>Format</th><th>Numbers</th><th>Sound</th></tr>"
                      + viral_rows + "</table></div></div>")
    # ---- sounds trending in Conor's feed that aren't in his library yet
    from .plan_day import find_sound
    lib = [{"name": n} for n in led.get("sound_names", [])]
    wanted = led.get("sounds_wanted") or []
    grab_rows = "".join(
        f"<li>{'✓ ' if find_sound(w['sound'], lib) else ''}<b>{E(w['sound'])}</b> <span class='muted'>"
        f"(seen {w.get('seen', 1)}×" + (f", needed for {len(w['for'])} trend idea{'s' if len(w['for']) != 1 else ''}" if w.get("for") else "")
        + ")</span></li>" for w in wanted) or "<li class='muted'>Drop a feed scroll in Drive › trends to see what's trending.</li>"
    igt = (led.get("ig_trending") or {}).get("items") or []
    ig_rows = "".join(
        f"<tr><td class='n'>{x['rank']}</td><td>{'✓ ' if find_sound(x['sound'], lib) else ''}<b>{E(x['sound'])}</b>"
        f"{' <span class=\'badge\'>new</span>' if x.get('new') else ''}</td>"
        f"<td class='small muted'>{'in your library' if find_sound(x['sound'], lib) else 'grab it'}</td></tr>" for x in igt[:20])
    missing = sum(1 for x in igt[:20] if not find_sound(x["sound"], lib))
    ig_html = (f"<div class='card'><h2>Trending on Instagram now</h2><p class='muted small' style='margin:0 0 6px'>Instagram's own "
               f"trending music, checked daily. {missing} of the top {min(20, len(igt))} aren't in your library yet: play those in "
               f"your next sound sweep.</p><div class='wrap'><table><tr><th class='n'>#</th><th>Song</th><th></th></tr>{ig_rows}"
               "</table></div></div>") if igt else ""
    # ---- what the follower learner has found
    m = led.get("model") or {}
    names = {"category": {"tip": "Technique tips", "snow_life": "Snow-life reels", "niseko": "Niseko reels",
                          "gear": "Gear & setup reels", "freestyle": "Freestyle reels",
                          "first_timer": "First-timer reels", "instructor": "Instructor-life reels"},
             "kind": {"ai": "Mascot reels", "real": "Your own footage"},
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
    # ---- your clips and the moments the bot found in them
    def mmss(x):
        x = int(round(float(x))); return f"{x // 60}:{x % 60:02d}"
    clip_items = sorted(led.get("clips", {}).values(), key=lambda c: c.get("added", ""), reverse=True)
    tag_count = {}
    for c in clip_items:
        for sg in c.get("segments", []):
            for tg in sg.get("tags", []):
                tag_count[tg] = tag_count.get(tg, 0) + 1
    clip_rows = "".join(
        f"<tr><td>{E(c['name'])}<br><span class='muted'>{c.get('duration', 0):.0f}s · used {c.get('uses', 0)}×</span></td><td>"
        + "<br>".join(f"{mmss(sg['start'])}–{mmss(sg['end'])} {E(sg.get('summary', ''))} "
                      f"<span class='muted'>({E(', '.join(sg.get('tags', [])))}"
                      f"{'' if sg.get('usable', True) else ', not used'}"
                      f"{', mistake' if sg.get('technique') == 'mistake' else ''})</span>"
                      for sg in c.get("segments", [])) + "</td></tr>"
        for c in clip_items) or "<tr><td colspan=2 class='muted'>No clips analysed yet.</td></tr>"
    moments = sum(len(c.get("segments", [])) for c in clip_items)
    tags_txt = ", ".join(f"{k} {v}" for k, v in sorted(tag_count.items(), key=lambda kv: -kv[1])[:10])
    clips_html = (f"<div class='card'><details><summary><b>Your clips</b> · {len(clip_items)} analysed · "
                  f"{moments} moments found <span class='muted'>(tap to open)</span></summary>"
                  f"<p class='muted'>Most common: {E(tags_txt) or 'n/a'}</p><div class='wrap'><table>"
                  f"<tr><th>Clip</th><th>Moments the bot will use</th></tr>{clip_rows}</table></div>"
                  f"</details></div>")
    log_rows = "".join(f"<li>{E(l)}</li>" for l in reversed(led.get("log", [])[-15:]))
    delta = fol_now - fol_week
    grad_html = "".join(
        f"<li><b>{link(p)}</b> <span class='muted'>({E(p['date'])}, {p['insights'].get('views',0):,} views, "
        f"{p['insights'].get('shares',0)} shares)</span></li>" for _, p in grad) or "<li class='muted'>No candidates yet.</li>"

    sk = [float((p.get("insights") or {}).get("reels_skip_rate")) for p in led["posts"].values()
          if isinstance((p.get("insights") or {}).get("reels_skip_rate"), (int, float))]
    avg_skip = f"{sum(sk) / len(sk):.0f}%" if sk else "–"
    # ---- Cost per follower: everything spent since the bot started ÷ followers gained since then
    fh_all = led.get("followers_hourly") or []
    gained = (fh_all[-1][1] - fh_all[0][1]) if len(fh_all) > 1 else 0
    spent_all = us["aud_total"]["total"]
    total_views = sum(((p.get("insights") or {}).get("views", 0) or 0) for p in led["posts"].values())
    since = f"{dt.date.fromisoformat(fh_all[0][0][:10]):%-d %b}" if fh_all else "the start"
    if gained > 0:
        cpf_txt = f"A${spent_all / gained:,.2f}"
        cpf_note = f"A${spent_all:,.2f} ÷ {gained:,} follower{'s' if gained != 1 else ''} gained since {since}"
    else:
        cpf_txt = "–"
        cpf_note = f"no net followers yet since {since}"
    if total_views:
        cpf_note += f" · A${spent_all / total_views * 1000:,.2f} per 1,000 views"

    # ---- Home tab pieces
    paused_now = bool(CFG.get("paused"))
    upcoming = [(d, e) for d in (today.isoformat(), tomorrow) for e in led["queue"].get(d, [])]
    nxt = next(((d, e) for d, e in upcoming if e["status"] in ("queued", "retry")), None)
    nxt_txt = (f"{'Today' if nxt[0] == today.isoformat() else 'Tomorrow'} {E(nxt[1]['time'])} · {E(nxt[1]['hook'])}"
               if nxt else "nothing queued")
    sched_rows = "".join(
        f"<tr><td>{'Today' if d == today.isoformat() else 'Tomorrow'}</td><td>{E(e['time'])}</td>"
        f"<td>{'Mascot' if e['kind'] == 'ai' else 'Real'}</td><td>{E(e['hook'])}</td>"
        f"<td><span class='st st-{E(e['status'])}'>{E(e['status'])}</span></td></tr>" for d, e in upcoming) \
        or "<tr><td colspan=5 class='muted'>Nothing queued. Tomorrow's reels are built at 4pm.</td></tr>"
    hb = led.get("publish_heartbeat")
    if hb and not CFG.get("paused") and (now() - dt.datetime.fromisoformat(hb)).total_seconds() > 3 * 3600:
        warn.append(f"Hourly posting last ran {hb[:16].replace('T', ' ')}: reels aren't posting. Tell Claude.")
    if led.get("auto_paused"):
        warn.insert(0, f"Posting stopped itself: {led['auto_paused']}. Tell Claude once it's sorted.")
    todo_items = [f"⚠️ {E(w)}" for w in warn] + [E(r["text"]) for r in rem]
    todo_html = ("<div class='card rem'><h2>To do</h2><ul class='todo'>" + "".join(f"<li>{t}</li>" for t in todo_items)
                 + "</ul></div>") if todo_items else \
        "<div class='card ok'><h2>To do</h2><p class='muted' style='margin:0'>Nothing. The bot's running by itself.</p></div>"
    posted7 = len(recent)
    n_review = len([1 for d in (tomorrow, today.isoformat(), yday) for _ in led["queue"].get(d, [])])
    # Sounds on real reels (queued or posted), most used first, with when each was last used
    su = {}
    for d, items in led["queue"].items():
        for e in items:
            if e.get("sound") and e.get("status") != "vetoed":
                n, last = su.get(e["sound"], (0, ""))
                su[e["sound"]] = (n + 1, max(last, d))
    sounds_used = sorted(((n, c) for n, (c, _) in su.items()), key=lambda kv: -kv[1])
    sound_rows = "".join(f"<tr><td>{E(sound_name(n))}</td><td class='n'>{c}</td></tr>" for n, c in sounds_used[:25]) \
        or "<tr><td colspan=2 class='muted'>No sounds used yet.</td></tr>"
    newest_sound = (led.get("sound_newest") or "")[:10]
    log_all = "".join(f"<li>{E(l)}</li>" for l in reversed(led.get("log", [])[-60:])) or "<li>Nothing yet.</li>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>@conor_sends · reelbot</title>
<style>
:root{{--bg:#f6f6f4;--card:#ffffff;--ink:#0b0b0b;--ink2:#5c5b57;--grid:#e3e2dc;--s1:#2a78d6;--s1soft:#e8f1fc;--good:#006300;--bad:#b42318;--warnbg:#fff4f2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--card:#212120;--ink:#f0efec;--ink2:#b9b8ae;--grid:#2f2f2d;--s1:#3987e5;--s1soft:#1d2a3a;--good:#0ca30c;--bad:#ff6b5e;--warnbg:#3a1f1c}}}}
*{{box-sizing:border-box}}html{{scroll-padding-top:70px}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
header{{background:var(--card);border-bottom:1px solid var(--grid)}}
.hin{{max-width:1040px;margin:0 auto;padding:16px 16px 0}}h1{{font-size:21px;margin:0}}
.sub{{color:var(--ink2);font-size:14px;margin:2px 0 10px}}.pill{{display:inline-block;font-size:13px;padding:2px 9px;border-radius:999px;border:1px solid var(--grid);margin-right:6px}}
.pill.on{{color:var(--good)}}.pill.off{{color:var(--bad);border-color:var(--bad)}}
nav{{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--grid)}}
.tabs{{max-width:1040px;margin:0 auto;padding:0 8px;display:flex;gap:2px;overflow-x:auto;scrollbar-width:none}}
.tabs button{{all:unset;cursor:pointer;padding:12px 12px 10px;font-size:15px;color:var(--ink2);border-bottom:3px solid transparent;white-space:nowrap}}
.tabs button[aria-selected=true]{{color:var(--ink);border-bottom-color:var(--s1);font-weight:600}}
.tabs button:focus-visible{{outline:2px solid var(--s1);outline-offset:-2px}}
.badge{{display:inline-block;min-width:20px;padding:0 6px;margin-left:5px;border-radius:999px;background:var(--s1soft);color:var(--s1);font-size:12px;text-align:center}}
main{{max-width:1040px;margin:0 auto;padding:18px 16px 60px}}section[hidden]{{display:none}}
h2{{font-size:17px;margin:0 0 10px}}.muted{{color:var(--ink2)}}.small{{font-size:13px}}
a{{color:inherit;text-decoration-color:var(--s1);text-underline-offset:3px}}
.card{{background:var(--card);border:1px solid var(--grid);border-radius:14px;padding:16px 18px;margin-bottom:16px}}
.rem{{border-color:var(--bad);background:var(--warnbg)}}.todo li{{margin:6px 0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:16px}}
.tiles .card{{margin:0}}.k{{font-size:13px;color:var(--ink2)}}.v{{font-size:30px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}}
.d{{font-size:13px}}.up{{color:var(--good)}}.down{{color:var(--bad)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
.two .card{{margin-bottom:0}}.stack>.card:last-child{{margin-bottom:0}}.gap{{height:16px}}
svg{{width:100%;height:auto;display:block}}.ln{{fill:none;stroke:var(--s1);stroke-width:2}}.pt{{fill:var(--s1);stroke:var(--card);stroke-width:2}}
.hit{{fill:transparent}}.grid{{stroke:var(--grid);stroke-width:1}}.ax{{fill:var(--ink2);font-size:11px}}
.bar-row{{display:grid;grid-template-columns:minmax(120px,40%) 1fr 70px;gap:10px;align-items:center;margin:7px 0;font-size:14px}}
.bar-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:12px}}.bar{{display:block;height:12px;background:var(--s1);border-radius:0 4px 4px 0}}
.bar-val{{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:8px 6px;border-bottom:1px solid var(--grid);vertical-align:top}}
th{{color:var(--ink2);font-weight:600;font-size:13px}}td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}.wrap{{overflow-x:auto}}
.st{{font-size:12px;padding:1px 8px;border-radius:9px;border:1px solid var(--grid);white-space:nowrap}}.st-posted{{color:var(--good)}}.st-failed,.st-vetoed{{color:var(--bad)}}
ul{{margin:0;padding-left:20px}}li{{margin:4px 0}}summary{{cursor:pointer}}
details.rv{{border-top:1px solid var(--grid);padding:12px 0}}details.rv:first-of-type{{border-top:0}}details.rv summary{{font-size:15px;line-height:1.6}}
.rvb{{display:grid;grid-template-columns:270px 1fr;gap:20px;margin-top:12px}}
.vbox{{width:270px;height:480px;overflow:hidden;border-radius:12px;background:#000}}
.vbox iframe{{width:360px;height:640px;border:0;transform:scale(.75);transform-origin:0 0}}
.rvb .k{{margin-top:12px;font-weight:600;font-size:13px;text-transform:none}}.rvb .k:first-child{{margin-top:0}}.rvb ul{{margin:2px 0}}.cap{{white-space:pre-wrap}}
@media(max-width:640px){{.rvb{{grid-template-columns:1fr}}.vbox{{margin:0 auto}}}}
#tip{{position:fixed;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12px;padding:4px 8px;border-radius:6px;display:none;z-index:9}}
code{{font-size:13px}}
form.rate{{margin-top:14px;padding-top:12px;border-top:1px dashed var(--grid)}}
.rrow{{display:flex;align-items:center;gap:10px;margin:6px 0}}.rl{{width:86px;font-size:14px}}
.stars{{display:flex;gap:6px}}.stars input{{position:absolute;opacity:0;pointer-events:none}}
.stars span{{display:inline-block;width:36px;height:34px;line-height:32px;text-align:center;border:1px solid var(--grid);border-radius:8px;cursor:pointer;font-variant-numeric:tabular-nums}}
.stars input:checked+span{{background:var(--s1);border-color:var(--s1);color:#fff;font-weight:650}}
.stars input:focus-visible+span{{outline:2px solid var(--s1);outline-offset:2px}}
form.rate textarea{{width:100%;margin:8px 0;padding:8px;border:1px solid var(--grid);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit;font-size:14px}}
form.rate button{{font:inherit;font-size:14px;padding:7px 14px;border-radius:8px;border:0;background:var(--s1);color:#fff;cursor:pointer}}
.saved{{color:var(--good);font-size:13px;margin-left:6px}}details.older{{border-top:1px solid var(--grid);padding-top:12px;margin-top:4px}}
@media(max-width:480px){{.tabs{{gap:0;padding:0 4px}}.tabs button{{padding:11px 7px 9px;font-size:13.5px}}.badge{{margin-left:3px;padding:0 5px}}h1{{font-size:19px}}.card{{padding:14px}}}}
</style></head><body>
<header><div class="hin"><h1>@conor_sends · trial reels</h1>
<div class="sub"><span class="pill {'off' if paused_now or led.get('auto_paused') else 'on'}">{'Paused' if paused_now or led.get('auto_paused') else 'Posting'}</span>
Updated {now():%a %d %b, %-I:%M%p}<br><span class="small">Next reel: {nxt_txt}</span></div></div></header>
<nav><div class="tabs" role="tablist">
<button role="tab" data-tab="home" aria-selected="true">Home{f'<span class="badge">{len(todo_items)}</span>' if todo_items else ''}</button>
<button role="tab" data-tab="review">Review<span class="badge">{n_review}</span></button>
<button role="tab" data-tab="results">Results</button>
<button role="tab" data-tab="library">Library</button>
<button role="tab" data-tab="costs">Costs</button>
<button role="tab" data-tab="log">Log</button>
</div></nav>
<main>

<section id="tab-home" role="tabpanel">
{todo_html}
<div class="tiles">
<div class="card"><div class="k">Followers</div><div class="v">{fol_now:,}</div><div class="d {'up' if delta>=0 else 'down'}">{'+' if delta>=0 else ''}{delta:,} in 7 days</div></div>
<div class="card"><div class="k">Views, last 7 days</div><div class="v">{views7:,}</div><div class="d muted">{posted7} reels posted</div></div>
<div class="card"><div class="k">Spent this month</div><div class="v">A${us['aud_total']['month']:,.2f}</div><div class="d muted">A${us['aud_total']['total']:,.2f} on the project so far</div></div>
<div class="card"><div class="k">Cost per follower</div><div class="v">{cpf_txt}</div><div class="d muted">{cpf_note}</div></div>
</div>
<div class="card"><h2>Posting schedule</h2><div class="wrap"><table><tr><th>Day</th><th>Time</th><th>Type</th><th>Hook</th><th>Status</th></tr>{sched_rows}</table></div>
<p class="muted small" style="margin:8px 0 0">Reels go out at 1 minute past the hour they are due (e.g. 1:01pm). The dashboard refreshes every 5 minutes. Check them in the <a href="#review" data-go="review">Review</a> tab.</p></div>
<div class="card"><h2>Graduate these in the Instagram app</h2><ul>{grad_html}</ul>
<p class="muted small" style="margin:8px 0 0">Best 5 of the last 10 days by views, shares and saves. Open the reel in the app, then share it to your profile.</p></div>
<div class="card muted small">Every Monday: check Instagram › Settings › <b>Account Status</b> for recommendation or originality warnings.</div>
</section>

<section id="tab-review" role="tabpanel" hidden>
<div class="card"><h2>Review reels</h2><p class="muted small" style="margin:0 0 6px">Tap a reel to watch it and check (a) the video, (b) the words on screen, (c) the sound and (d) the caption. The video plays when you're signed in to the Google account that owns the Drive folder. To stop a reel, delete it from Drive › queue; mascot clips you bin teach the clip maker.</p>{review_html}</div>
</section>

<section id="tab-results" role="tabpanel" hidden>
<div class="tiles">
<div class="card"><div class="k">Avg views per mascot reel</div><div class="v">{avg['ai']:,.0f}</div><div class="d muted">{len(by_kind['ai'])} reels with stats</div></div>
<div class="card"><div class="k">Avg skip rate (first 3s)</div><div class="v">{avg_skip}</div><div class="d muted">lower = stronger hooks</div></div>
<div class="card"><div class="k">Avg views per real-clip reel</div><div class="v">{avg['real']:,.0f}</div><div class="d muted">{len(by_kind['real'])} reels with stats</div></div>
</div>
<div class="two">
<div class="card"><h2>Followers</h2>{line_chart(fol[-60:], "Followers")}</div>
<div class="card"><h2>Views by posting day</h2>{line_chart(daily_pts, "Views by posting day")}</div>
</div><div class="gap"></div>
{learn_html}
{ratings_html}
<div class="card"><h2>Best topics (avg score)</h2>{bar_chart(board, "Best topics")}
<p class="muted small" style="margin:8px 0 0">Score = views + 20×shares + 10×saves. The planner picks high scorers more often.</p></div>
<div class="card"><h2>Latest 50 reels</h2><div class="wrap"><table><tr><th>Date</th><th>Type</th><th>Hook</th><th class='n'>Views</th><th class='n'>Reach</th><th class='n'>Shares</th><th class='n'>Saves</th><th class='n'>Avg watch</th><th class='n' title='Share of views skipped in the first 3 seconds (lower is better)'>Skip</th><th class='n'>Est. followers</th></tr>{reel_rows(posts[:50])}</table></div></div>
</section>

<section id="tab-library" role="tabpanel" hidden>
{taste_html}
<div class="card"><h2>Trend inbox</h2><ul>{trend_rows}</ul></div>
{viral_rows}
{ig_html}
<div class="card"><h2>Sounds to grab in your next sweep</h2><p class="muted small" style="margin:0 0 6px">Songs trending in your feed (from your scroll recordings). ✓ = already in your library. Trend ideas that need a specific sound wait until it's there.</p><ul>{grab_rows}</ul></div>
<div class="card"><h2>Sounds</h2><p class="muted small" style="margin:0 0 6px">Newest sound added {E(newest_sound) or 'never'}. Drop a new sweep in Drive › sound_sweeps every week or two.</p>
<div class="wrap"><table><tr><th>Sound</th><th class='n'>Reels</th></tr>{sound_rows}</table></div></div>
{clips_html}
</section>

<section id="tab-costs" role="tabpanel" hidden>
{cost_html}
<div class="card muted small">Mascot clip budget: about US${fal_spent:.2f} of the US${fal_cap} monthly cap used (≈A${fal_spent * float(CFG.get('usd_to_aud', 1.54)):.0f} of A${fal_cap * float(CFG.get('usd_to_aud', 1.54)):.0f}). The bot stops making new clips at the cap and reuses existing ones.</div>
</section>

<section id="tab-log" role="tabpanel" hidden>
<div class="card"><h2>Bot log</h2><ul class="muted small">{log_all}</ul></div>
</section>

</main><div id="tip"></div>
<script>
const tabs=[...document.querySelectorAll('[role=tab]')];
function show(name){{
  if(!document.getElementById('tab-'+name)) name='home';
  tabs.forEach(b=>b.setAttribute('aria-selected', b.dataset.tab===name));
  document.querySelectorAll('[role=tabpanel]').forEach(s=>s.hidden = s.id!=='tab-'+name);
  try{{localStorage.setItem('tab', name)}}catch(e){{}}
  if(location.hash!=='#'+name) history.replaceState(null,'','#'+name);
}}
tabs.forEach(b=>b.addEventListener('click',()=>{{show(b.dataset.tab);window.scrollTo(0,0)}}));
document.querySelectorAll('[data-go]').forEach(a=>a.addEventListener('click',e=>{{e.preventDefault();show(a.dataset.go);window.scrollTo(0,0)}}));
let start=location.hash.slice(1);
if(!start){{try{{start=localStorage.getItem('tab')||'home'}}catch(e){{start='home'}}}}
show(start);window.scrollTo(0,0);
const first=document.querySelector('details.rv'); if(first) first.open=true;
const REPO={json.dumps(rt.repo())};
document.querySelectorAll('form.rate').forEach(f=>f.addEventListener('submit',ev=>{{
  ev.preventDefault();
  const parts=['video','words','sound','caption'], lines=['reel: '+f.dataset.key];
  let any=false;
  parts.forEach(p=>{{const c=f.querySelector('input[name='+p+']:checked'); if(c){{lines.push(p+': '+c.value); any=true}}}});
  const why=f.querySelector('textarea').value.trim();
  if(!any && !why){{const h=f.querySelector('.hint');h.textContent='Pick at least one score first.';h.hidden=false;return}}
  lines.push('why: '+why);
  const url='https://github.com/'+REPO+'/issues/new?labels=rating'
    +'&title='+encodeURIComponent('Rating '+f.dataset.key+': '+f.dataset.hook)
    +'&body='+encodeURIComponent(lines.join('\\n'));
  window.open(url,'_blank','noopener');
  f.querySelector('.hint').hidden=false;
}}));
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
