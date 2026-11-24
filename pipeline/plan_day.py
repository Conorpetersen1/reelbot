"""Evening job: build tomorrow's reels and put them in Drive/queue. Delete any you don't want before they post."""
import datetime as dt, math, random, re, subprocess
from . import drive, gemini, learn, llm
from .common import CFG, ROOT, WORK, days_since, load_ledger, load_topics, log, now, save_ledger, paused, notify_once
from .render import render


def recent_topics(led):
    used = {}
    for day in led["queue"].values():
        for e in day:
            used[e["topic"]] = max(used.get(e["topic"], ""), e["date"])
    return {t for t, d in used.items() if days_since(d + "T12:00:00+00:00") < CFG["topic_cooldown_days"]}


def is_fresh_trend(t):
    return bool(t.get("added")) and days_since(t["added"]) < CFG.get("trend_boost_days", 7)


def last_used_topic(led, tid):
    return max([e["date"] for d in led["queue"].values() for e in d if e["topic"] == tid] or [""])


def weighted_pick(cands, draw, kind, uses_of, led=None):
    """Pick the candidate the learner's current Thompson draw rates highest for followers.
    Fresh trend ideas get a head start because trends fade."""
    if not cands:
        return None
    boost = math.log(CFG.get("learning", {}).get("fresh_trend_boost", 1.5))

    def val(c):
        t, ref = c
        feats = {"category": t.get("category", "tip"), "topic": t["id"], "kind": kind, "reuse": "reuse" if uses_of(ref) else "first",
                 "source": t.get("source_kind") or ("trend" if t["id"].startswith("x") else "bank")}
        from . import ratings
        taste = ratings.bonus(led, t) if led else 0.0  # Conor's own 1-5 ratings: a small nudge
        return draw.score(feats) + (boost if is_fresh_trend(t) else 0) + taste
    return max(cands, key=val)


# Categories where the footage must show the reel's subject and its technique angle must match
TECHNIQUE = {"tip", "freestyle", "first_timer"}

CTAS = ['Follow for more snowboard tips', 'Save this for your next day on snow', 'Send this to a mate who needs it', 'Follow for a tip every day', 'Save this and try it next run', 'Tag the mate you ride with']
# Calls to action that suit reels that aren't technique tips
LIFE_CTAS = ['Follow for more snow life', 'Send this to a mate who needs a snow trip', 'Tag the mate you ride with',
             'Save this for your next snow trip', 'Follow for more from the mountain']


def bank_caption(topic):
    hook = topic["hook"].strip()
    if hook[-1:] not in "?!.":
        hook += "."
    cta = random.choice(CTAS if topic.get("category") in ("tip", "first_timer", "freestyle") else LIFE_CTAS)
    return f"{hook} {cta}. #snowboarding #snowboardtips #learntosnowboard #snowboardinstructor"

MISTAKE = re.compile(r"^(why|stop|\d+ mistakes)|mistake|catch|fall|wash|skid|stuck|lock|chatter|crash|"
                     r"won't|wrong|excuse", re.I)


def wants_mistake(topic):
    """'Why do I fall...' style topics suit footage of a mistake; 'How I fixed...' suits good riding."""
    hook = topic["hook"].strip()
    if re.search(r"\bfall(ing)? without|\bhow to fall", hook, re.I):  # falling tips suit footage of a fall
        return True
    if re.match(r"(how|the (one|drill|trick)|where|check)\b", hook, re.I):  # fixes and how-tos want good riding
        return False
    return bool(MISTAKE.search(hook)) and topic.get("category") in TECHNIQUE


def technique_ok(topic, seg):
    tech = seg.get("technique", "n/a")
    if tech == "n/a" or topic.get("category") not in TECHNIQUE:
        return True
    return (tech == "mistake") == wants_mistake(topic)


def choose(led, topics, n_ai, n_real, draw):
    cooldown = recent_topics(led)
    by_id = {t["id"]: t for t in topics}
    picks, used_topics, today = [], set(cooldown), set()
    # AI slots: topics that have unused AI clips in the library
    for _ in range(n_ai):
        stock = [(by_id[c["topic"]], cid) for cid, c in led["ai_clips"].items()
                 if c["uses"] < CFG["ai_clip_max_uses"] + (1 if c.get("rated_high") else 0) and not c.get("rejected")
                 and c["topic"] in by_id and c["topic"] not in today
                 and not any(pk[2] == cid for pk in picks)]
        cands = [x for x in stock if x[0]["id"] not in used_topics]
        # If every topic with clips is on cooldown, reuse the one rested longest rather than skip the slot
        pick = weighted_pick(cands, draw, "ai", lambda cid: led["ai_clips"][cid]["uses"], led) or (min(stock, key=lambda x: last_used_topic(led, x[0]["id"]))
                                              if stock else None)
        if not pick:
            n_real += 1  # no AI stock: fall back to a real clip
            continue
        picks.append(("ai", pick[0], pick[1])); used_topics.add(pick[0]["id"]); today.add(pick[0]["id"])
    # Real slots: least-used usable moments (segments) of your clips, matched to a topic by tags
    moments = sorted([((cid, i), seg) for cid, c in led["clips"].items() for i, seg in enumerate(segments(c))
                      if seg.get("usable", True)
                      and days_since(seg.get("last_used")) >= CFG["real_clip_cooldown_days"]],
                     key=lambda x: (x[1].get("uses", 0), random.random()))
    used_clips = set()
    # First pass: one moment per clip per day. Second pass: allow more from the same clip if short on clips.
    for (cid, i), c in moments + moments:
        if n_real <= 0:
            break
        if any(pk[0] == "real" and pk[2] == (cid, i) for pk in picks):
            continue
        if cid in used_clips and len(used_clips) < len({k[0] for k, _ in moments}):
            continue
        ctags = set(c.get("tags", []))
        # Footage first: some real reels are written about what's actually happening in the clip
        if random.random() < CFG.get("real_moment_share", 0.5):
            picks.append(("real", moment_topic(cid, i, c), (cid, i))); used_clips.add(cid); n_real -= 1
            continue
        # Real footage is only used for teaching reels whose main subject (first tag) the clip shows. Life topics
        # (Niseko, gear, instructor life) go to mascot clips, which are made for them; otherwise it's a moment reel.
        cands = [(t, (cid, i)) for t in topics if t["id"] not in today and t["category"] in TECHNIQUE
                 and t["id"] not in c.get("misfit", []) and t["clip_tags"].split(",")[0] in ctags]
        # Prefer topics whose angle matches the riding: mistakes -> "why do I...", good riding -> "how I fixed..."
        cands = [x for x in cands if technique_ok(x[0], c)] or cands
        rested = [x for x in cands if x[0]["id"] not in used_topics]
        pick = weighted_pick(rested, draw, "real", lambda ref: segments(led["clips"][ref[0]])[ref[1]].get("uses", 0), led) or (min(cands, key=lambda x: last_used_topic(led, x[0]["id"]))
                                               if cands else None)
        if not pick:  # nothing in the bank fits this footage: write about the footage instead
            pick = (moment_topic(cid, i, c), (cid, i))
        if pick:
            picks.append(("real", pick[0], pick[1])); used_topics.add(pick[0]["id"]); today.add(pick[0]["id"])
            used_clips.add(cid)
            n_real -= 1
    return picks


def segments(clip):
    """A clip's best moments. Clips tagged before segments existed count as one moment."""
    if not clip.get("segments"):
        clip["segments"] = [{"start": 0, "end": clip.get("duration") or 12, "tags": clip.get("tags", []),
                             "summary": clip.get("summary", ""), "technique": clip.get("technique", "n/a"),
                             "usable": clip.get("usable", True), "uses": clip.get("uses", 0),
                             "last_used": clip.get("last_used")}]
    return clip["segments"]


def cut(src, start, end, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{end - start:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(out)], check=True)
    return out


def fit_check(out, reel):
    """Watch the finished reel and score how well the footage fits the text (1-5)."""
    if gemini.available():
        try:
            return gemini.ask(llm.fit_prompt(reel, "video"), video=gemini.small_video(out))
        except Exception as e:
            print("Gemini fit check failed, using Claude:", e)
    frames = []
    for n, t in enumerate((1.0, 3.0, 5.5)):
        f = WORK / f"{out.stem}_fit{n}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                        "-vf", "scale=540:-2", str(f)], check=True)
        frames.append(f)
    return llm.fit_check_frames(frames, reel)


AUDIO = (".mp3", ".m4a", ".wav", ".aac")
VIDEO = (".mp4", ".mov", ".m4v")


def sound_library(led):
    """Your sounds in Drive/audio. Audio files are used as they are; for screen recordings the sound is pulled out."""
    out = []
    files = drive.list_files("audio")
    led["sound_names"] = [f["name"] for f in files]  # for the dashboard's "sounds to grab" ticks
    stamps = [f["createdTime"] for f in files if f.get("createdTime")]
    if stamps:
        led["sound_newest"] = max(stamps)  # for the "time for a sound sweep" reminder
    for f in files:
        n = f["name"].lower()
        if not n.endswith(AUDIO + VIDEO):
            continue
        created = f.get("createdTime")
        if created and days_since(created.replace("Z", "+00:00")) > CFG.get("sounds", {}).get("max_age_days", 45):
            continue  # old trend: retired from rotation
        out.append({"id": f["id"], "name": f["name"], "created": created, "video": n.endswith(VIDEO)})
    return out


def _words(t):
    return set(re.findall(r"[a-z0-9]+", re.sub(r"^sweep \S+ - ", "", t.lower()).rsplit(".", 1)[0])) - {"feat", "ft", "the", "a", "official"}


def find_sound(name, lib):
    """The library sound matching a song name like 'Young Folks - Peter Bjorn and John' (None if not there)."""
    want = _words(name)
    best, score = None, 0.0
    for s in lib:
        have = _words(s["name"])
        if want and have:
            overlap = len(want & have) / len(want)
            if overlap > score:
                best, score = s, overlap
    return best if score >= 0.6 else None


def pick_sound(led, lib):
    """Newer sounds are used much more (trends fade); less-used sounds are preferred."""
    fresh_days = CFG.get("sounds", {}).get("fresh_days", 14)
    uses = led.get("sound_uses", {})
    from . import ratings
    lib = [s for s in lib if ratings.sound_weight(led, s["name"]) > 0] or lib  # sounds Conor rated 1-2 are dropped
    w = []
    for s in lib:
        age = days_since(s["created"].replace("Z", "+00:00")) if s.get("created") else 30
        w.append((4 if age <= fresh_days else 1) / (1 + uses.get(s["name"], 0)) * ratings.sound_weight(led, s["name"]))
    s = dict(random.choices(lib, weights=w)[0])
    path = WORK / f"snd_{s['name']}"
    if not path.exists():
        drive.download(s["id"], path)
    if s["video"]:  # screen recording: keep just the sound
        a = WORK / f"snd_{s['name']}.m4a"
        if not a.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-c:a", "aac", "-b:a", "160k",
                            str(a)], check=True)
        path = a
    s["path"] = path
    return s


def variation(uses):
    """First use: footage as shot. Reuses: a different crop, zoom, mirror and speed so it isn't a duplicate."""
    if not uses:
        return {}
    return {"zoom": round(random.uniform(1.08, 1.22), 2), "pan": round(random.choice([0.15, 0.3, 0.7, 0.85]), 2),
            "mirror": random.random() < 0.5, "speed": round(random.choice([0.9, 0.95, 1.05, 1.1]), 2)}


def pick_style(topic, led, draw, avoid=None):
    """Topics with a set style (from trends) keep it. Otherwise the learner picks, never repeating a clip's
    last style (so reused footage looks different)."""
    if topic.get("style"):
        return topic["style"]
    options = ["question", "list", "pov"]
    options = [o for o in options if o != avoid] or options
    return draw.best("style", options)


def active_retired(led):
    days = CFG.get("learning", {}).get("retire_days", 30)
    return {t for t, d in led.get("retired", {}).items() if days_since(d) < days}


def retire_losers(led, model):
    """Rest topics that have clearly under-performed on followers (enough reels to be sure) for 30 days."""
    counts = {}
    for p in led["posts"].values():
        counts[p["topic"]] = counts.get(p["topic"], 0) + 1
    lc = CFG.get("learning", {})
    for t in learn.retire_candidates(model, threshold=lc.get("retire_below", -0.4), sure=lc.get("retire_sure", 0.8)):
        if counts.get(t, 0) >= lc.get("retire_min_reels", 4) and t not in active_retired(led):
            led.setdefault("retired", {})[t] = now().isoformat()
            log(led, f"Learner rested topic {t} for 30 days (consistently few followers)")


def critique(cands, topic, mode, src, led):
    """Score each candidate on hook, sense, footage match and voice. Gemini watches the clip; Claude reads only."""
    import os
    from . import voice
    if os.environ.get("LOCAL_LLM"):
        return []  # offline test mode
    v = voice.guide(led)
    if gemini.available() and src is not None:
        try:
            return gemini.ask(llm.critic_prompt(cands, topic, mode, v, True), video=gemini.small_video(src)).get("scores", [])
        except Exception as e:
            print("Gemini critic failed, using Claude:", e)
    try:
        msg = llm._client().messages.create(model=CFG["claude_model"], max_tokens=4000, system=llm.RULES,
                                            messages=[{"role": "user", "content": llm.critic_prompt(cands, topic, mode, v, False)}])
        return llm._json(llm._text(msg)).get("scores", [])
    except Exception as e:
        print("Critic failed:", e)
        return []


def rank(cands, scores, mode):
    """Best first; candidates that don't make sense, don't fit the footage or have a problem are dropped."""
    by_i = {int(x.get("i", -1)): x for x in scores if isinstance(x, dict)}
    out = []
    for n, c in enumerate(cands):
        sc = by_i.get(n)
        if sc is None:
            if not scores:  # no critic available: keep the writer's order
                out.append((0.0, n, {}))
            continue
        g = lambda k: int(sc.get(k, 0) or 0)
        if sc.get("problem") or g("sense") < 3 or g("match") < (3 if mode == "moment" else 2) or g("hook") < 2:
            continue
        out.append((1.5 * g("hook") + 1.5 * g("sense") + g("match") + g("voice"), n, sc))
    return sorted(out, key=lambda x: -x[0])


def text_for(topic, led, clip_note, hook_type=None, src=None, mode=None):
    """Write 3 candidates, let the critic rank them, then the strict reviewer checks the winner against the
    rules. Returns (reel, source) or (None, reason) for a moment reel nobody could write well."""
    from . import ratings, voice
    mode = mode or llm.reel_mode(topic)
    recent = [e["hook"] for d in led["queue"].values() for e in d]
    winners = [p["hook"] for p in sorted(led["posts"].values(),
               key=lambda p: -(p.get("insights") or {}).get("views", 0))]
    for attempt in range(2):
        try:
            cands = llm.write_candidates(topic, mode, clip_note, recent, winners, hook_type,
                                         taste=ratings.writer_notes(led), voice=voice.guide(led),
                                         feed=voice.feed_formats(led))
        except Exception as e:
            log(led, f"Writer error for {topic['id']}: {e}"); continue
        ranked = rank(cands, critique(cands, topic, mode, src, led), mode)
        if not ranked:
            log(led, f"Critic rejected all {len(cands)} texts for {topic['id']} (attempt {attempt + 1})"); continue
        for total, n, sc in ranked:
            reel = {k: cands[n][k] for k in ("hook", "description", "caption")}
            ok, why = llm.check_reel(topic, reel)
            if ok:
                reel["critic"] = {"score": round(total, 1), "angle": cands[n].get("angle", ""),
                                  **{k: sc.get(k) for k in ("hook", "sense", "match", "voice", "note") if k in sc}}
                return reel, "written"
            log(led, f"Reviewer rejected text for {topic['id']}: {why}")
    if mode != "teaching":
        return None, f"no good {mode} text"
    # Fallback for teaching reels: the topic bank's own text, which was checked against the manual by hand.
    return {"hook": topic["hook"], "description": topic["description"].split("|"),
            "caption": bank_caption(topic)}, "topic_bank"


def moment_topic(cid, i, seg):
    """A reel written about what's in a real clip (footage first), rather than a topic from the bank."""
    import hashlib
    return {"id": "m" + hashlib.md5(f"{cid}#{i}".encode()).hexdigest()[:6], "category": "moment", "level": "all",
            "hook": "", "description": "", "manual_ref": "", "cta_keyword": "",
            "clip_tags": ",".join(seg.get("tags", [])), "ai_scene": "", "source_kind": "moment",
            "clip_note": seg.get("summary", "")}


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
    led = load_ledger()
    day = (now() + dt.timedelta(days=1)).date().isoformat()
    if led["queue"].get(day):
        print("Already planned", day); return
    topics = load_topics(led)
    # Clips you've deleted from Drive are never picked again; binned mascot clips teach the clip maker
    from . import taste
    taste.note_deleted(led)
    try:
        taste.review(led)
    except Exception as e:
        log(led, f"Taste review skipped: {e}")
    save_ledger(led)
    n_ai, n_real = CFG["reels_per_day"]["ai"], CFG["reels_per_day"]["real"]
    model = learn.fit(led)
    draw = model.draw()
    retire_losers(led, model)
    topics = [t for t in topics if t["id"] not in active_retired(led)]
    # Shift the mascot/real split by one reel towards whichever the learner currently rates higher
    gap = draw.effect("kind", "real") - draw.effect("kind", "ai")
    if gap > 0.15 and n_ai > 1 and led["clips"]:
        n_ai, n_real = n_ai - 1, n_real + 1
    elif gap < -0.15 and n_real > 1 and led["ai_clips"]:
        n_ai, n_real = n_ai + 1, n_real - 1
    # Trend ideas that only work with one sound wait until that sound is in Drive/audio
    audio = sound_library(led)
    held = [t["id"] for t in topics if t.get("needs_sound") and not find_sound(t["needs_sound"], audio)]
    if held:
        topics = [t for t in topics if t["id"] not in held]
        print("Waiting for sounds:", held)
    target = n_ai + n_real
    times = learn.choose_times(draw, target, CFG.get("learning", {}).get("candidate_times") or CFG["post_times"])
    target = min(target, len(times))
    # Plan spares too, so a reel that fails the fit check can be swapped for another
    spare = CFG.get("fit_spares", 3)
    picks = choose(led, topics, n_ai + spare, n_real + spare, draw)
    main_picks = [p for p in picks if p[0] == "ai"][:n_ai] + [p for p in picks if p[0] == "real"][:n_real]
    random.shuffle(main_picks)  # mix AI and real across the day's posting times
    spares = {k: [p for p in picks if p[0] == k and p not in main_picks] for k in ("ai", "real")}
    want = {"ai": n_ai, "real": n_real}
    entries, n = [], 0
    queue = list(main_picks)
    while queue:
        kind, topic, clip_id = queue.pop(0)
        if len(entries) >= target:
            break
        if any(e["topic"] == topic["id"] for e in entries):
            continue
        done_kind = sum(1 for e in entries if e["kind"] == kind)
        if done_kind >= want[kind] and any(spares[k] for k in want if k != kind):
            continue
        # If this one fails, the next spare of the same kind takes its place (then the other kind, if none left)
        backup = spares[kind].pop(0) if spares[kind] else (spares["ai" if kind == "real" else "real"].pop(0)
                                                            if any(spares.values()) else None)
        before = len(entries)
        n += 1
        try:
            if kind == "ai":
                meta = led["ai_clips"][clip_id]
                src = drive.download(clip_id, WORK / f"src_{n}.mp4")
                note = f"an animated blocky cartoon snowboarder: {topic['ai_scene']}"
            else:
                cid, i = clip_id
                clip = led["clips"][cid]; meta = segments(clip)[i]
                full = WORK / f"full_{cid[-12:].replace('/', '_')}_{clip['name']}"
                if not full.exists():
                    drive.download(cid, full)
                src = cut(full, meta["start"], meta["end"], WORK / f"src_{n}.mp4")
                note = (f"Conor's real footage: {meta.get('summary', '')} (tags: {', '.join(meta.get('tags', []))}; "
                        f"riding looks: {meta.get('technique', 'n/a')})")
            want_hook = draw.best("hook", learn.HOOK_TYPES)
            reel, source = text_for(topic, led, note, want_hook, src=src)
            if reel is None:
                log(led, f"Skipped {topic['id']}: {source}"); continue
            critic = reel.pop("critic", None)
            music = None
            sound = None
            # Every reel gets a sound from your trending sounds (a real clip's own audio stays quietly underneath)
            if audio:
                need = find_sound(topic["needs_sound"], audio) if topic.get("needs_sound") else None
                sound = pick_sound(led, [need] if need else audio)
                music = sound["path"]
            out = WORK / f"{day}_{len(entries)+1}_{topic['id']}.mp4"
            snd = CFG.get("sounds", {})
            var = variation(meta.get("uses", 0))
            style = pick_style(topic, led, draw, avoid=meta.get("last_style"))
            style = style if len(reel["description"]) > 1 or style != "list" else "question"
            if topic.get("category") == "moment" and style == "list":
                style = "pov" if reel["hook"].lower().startswith("pov") else "question"
            today_styles = [e.get("style") for e in entries]
            if today_styles.count(style) >= 3 and not topic.get("style"):
                style = "list" if style == "question" and topic.get("category") == "tip" else "question"
            render(src, out, reel["hook"], reel["description"], music=music, style=style,
                   music_volume=snd.get("sound_volume", 0.9), clip_volume=snd.get("clip_sound_volume", 0.25),
                   **var)
            fit = fit_check(out, reel)
            score = int(fit.get("score", 3))
            if score < CFG.get("fit_min_score", 3):
                log(led, f"Fit check {score}/5, swapped out {topic['id']} ({kind}): {fit.get('reason')}")
                if kind == "ai" and score <= 1:
                    meta["rejected"] = fit.get("reason", "failed fit check")  # broken animation: never use again
                elif kind == "real":
                    meta.setdefault("misfit", []).append(topic["id"])  # don't pair this moment with this topic again
                save_ledger(led)
                continue
            slot = len(entries)
            fid = drive.upload(out, "queue", out.name)
            meta["uses"] = meta.get("uses", 0) + 1; meta["last_used"] = now().isoformat(); meta["last_style"] = style
            if kind == "real":
                clip["uses"] = clip.get("uses", 0) + 1; clip["last_used"] = now().isoformat()
            if sound:
                u = led.setdefault("sound_uses", {}); u[sound["name"]] = u.get(sound["name"], 0) + 1
            entries.append({"date": day, "slot": slot + 1, "time": times[slot], "kind": kind,
                            "topic": topic["id"], "clip": clip_id if kind == "ai" else f"{clip_id[0]}#{clip_id[1]}",
                            "file_id": fid, "file": out.name, "fit": score,
                            "hook": reel["hook"], "description": reel["description"], "caption": reel["caption"],
                            "text_source": source, "critic": critic, "status": "queued", "sound": sound["name"] if sound else None,
                            "style": style, "variation": var, "hook_type": learn.hook_type(reel["hook"]),
                            "category": topic.get("category", "tip"),
                            "asked_hook": want_hook, "sound_kind": "sound" if sound else "none",
                            "source": topic.get("source_kind") or ("trend" if topic["id"].startswith("x") else "bank")})
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't build a reel for {topic['id']}: {e}")
        finally:  # runs even when the attempt ends early (text skipped, fit check failed)
            if len(entries) == before and backup:
                queue.insert(0, backup)
            elif backup:
                spares[backup[0]].insert(0, backup)  # not needed: put it back
    led["queue"][day] = entries
    log(led, f"Queued {len(entries)} reels for {day}: {[e['topic'] for e in entries]}")
    if len(entries) < max(1, target // 2):
        notify_once(led, f"lowqueue-{day}", f"⚠️ Only {len(entries)} reel(s) queued for {day}",
                    "The bot couldn't build a full day of reels. Usual causes: out of AI clips (budget reached or "
                    "none left), no usable moments in your clips, or a key problem. Recent bot log:\n\n```\n"
                    + "\n".join(led.get("log", [])[-12:]) + "\n```")
    save_ledger(led)


def preview(n=8):
    """Write and score texts for a few planned reels without rendering or queuing anything (data/preview.json)."""
    import json
    from . import ratings, voice
    led = load_ledger()
    topics = [t for t in load_topics(led) if t["id"] not in active_retired(led)]
    draw = learn.fit(led).draw()
    picks = choose(led, topics, n // 2 + 1, n // 2 + 1, draw)[:n]
    out = []
    for k, (kind, topic, clip_id) in enumerate(picks):
        try:
            if kind == "ai":
                src = drive.download(clip_id, WORK / f"pv_{k}.mp4")
                note = f"an animated blocky cartoon snowboarder: {topic['ai_scene']}"
            else:
                cid, i = clip_id
                clip = led["clips"][cid]; meta = segments(clip)[i]
                full = drive.download(cid, WORK / f"pvfull_{k}_{clip['name']}")
                src = cut(full, meta["start"], meta["end"], WORK / f"pv_{k}.mp4")
                note = (f"Conor's real footage: {meta.get('summary', '')} (tags: {', '.join(meta.get('tags', []))}; "
                        f"riding looks: {meta.get('technique', 'n/a')})")
            mode = llm.reel_mode(topic)
            cands = llm.write_candidates(topic, mode, note, [], [], draw.best("hook", learn.HOOK_TYPES),
                                         taste=ratings.writer_notes(led), voice=voice.guide(led),
                                         feed=voice.feed_formats(led))
            scores = critique(cands, topic, mode, src, led)
            ranked = rank(cands, scores, mode)
            out.append({"kind": kind, "topic": topic["id"], "category": topic.get("category"), "footage": note,
                        "candidates": cands, "scores": scores, "winner": ranked[0][1] if ranked else None})
        except Exception as e:
            out.append({"kind": kind, "topic": topic["id"], "error": str(e)})
    (ROOT / "data" / "preview.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False)[:20000])


if __name__ == "__main__":
    import sys
    if "--preview" in sys.argv:
        preview()
    else:
        main()
