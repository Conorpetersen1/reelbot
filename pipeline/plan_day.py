"""Evening job: build tomorrow's reels and put them in Drive/queue. Delete any you don't want before they post."""
import datetime as dt, math, random, re, subprocess
from . import drive, gemini, learn, llm
from .common import CFG, WORK, days_since, load_ledger, load_topics, log, now, save_ledger, paused, notify_once
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
        # Tips need the clip to show their main subject (first tag); snow-life topics just need some overlap.
        cands = [(t, (cid, i)) for t in topics if t["id"] not in today
                 and t["id"] not in c.get("misfit", []) and (
                 t["clip_tags"].split(",")[0] in ctags if t["category"] in TECHNIQUE
                 else set(t["clip_tags"].split(",")) & ctags)]
        # Prefer topics whose angle matches the riding: mistakes -> "why do I...", good riding -> "how I fixed..."
        cands = [x for x in cands if technique_ok(x[0], c)] or cands
        rested = [x for x in cands if x[0]["id"] not in used_topics]
        pick = weighted_pick(rested, draw, "real", lambda ref: segments(led["clips"][ref[0]])[ref[1]].get("uses", 0), led) or (min(cands, key=lambda x: last_used_topic(led, x[0]["id"]))
                                               if cands else None)
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


def text_for(topic, led, clip_note, hook_type=None):
    recent = [e["hook"] for d in led["queue"].values() for e in d]
    winners = [p["hook"] for p in sorted(led["posts"].values(),
               key=lambda p: -(p.get("insights") or {}).get("views", 0))]
    for _ in range(2):
        try:
            from . import ratings
            reel = llm.write_reel(topic, recent, winners, clip_note, hook_type, taste=ratings.writer_notes(led))
            ok, why = llm.check_reel(topic, reel)
            if ok:
                return reel, "written"
            log(led, f"Reviewer rejected text for {topic['id']}: {why}")
        except Exception as e:
            log(led, f"Writer error for {topic['id']}: {e}")
    # Fallback: the topic bank's own text, which was checked against the manual by hand.
    return {"hook": topic["hook"], "description": topic["description"].split("|"),
            "caption": bank_caption(topic)}, "topic_bank"


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
    target = n_ai + n_real
    times = learn.choose_times(draw, target, CFG.get("learning", {}).get("candidate_times") or CFG["post_times"])
    target = min(target, len(times))
    # Plan spares too, so a reel that fails the fit check can be swapped for another
    spare = CFG.get("fit_spares", 3)
    picks = choose(led, topics, n_ai + spare, n_real + spare, draw)
    main_picks = [p for p in picks if p[0] == "ai"][:n_ai] + [p for p in picks if p[0] == "real"][:n_real]
    random.shuffle(main_picks)  # mix AI and real across the day's posting times
    picks = main_picks + [p for p in picks if p not in main_picks]
    audio = sound_library(led)
    entries, n = [], 0
    for kind, topic, clip_id in picks:
        if len(entries) >= target:
            break
        if any(e["topic"] == topic["id"] for e in entries):
            continue
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
                note = f"Conor's real footage: {meta.get('summary', '')} ({meta.get('technique', 'n/a')} technique)"
            want_hook = draw.best("hook", learn.HOOK_TYPES)
            reel, source = text_for(topic, led, note, want_hook)
            music = None
            sound = None
            sound_ok = audio and (kind == "ai" or CFG.get("sounds", {}).get("use_on", "all") == "all")
            if sound_ok and draw.best("sound", ["sound", "none"]) == "sound":
                sound = pick_sound(led, audio)
                music = sound["path"]
            out = WORK / f"{day}_{len(entries)+1}_{topic['id']}.mp4"
            snd = CFG.get("sounds", {})
            var = variation(meta.get("uses", 0))
            style = pick_style(topic, led, draw, avoid=meta.get("last_style"))
            style = style if len(reel["description"]) > 1 or style != "list" else "question"
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
                            "text_source": source, "status": "queued", "sound": sound["name"] if sound else None,
                            "style": style, "variation": var, "hook_type": learn.hook_type(reel["hook"]),
                            "category": topic.get("category", "tip"),
                            "asked_hook": want_hook, "sound_kind": "sound" if sound else "none",
                            "source": topic.get("source_kind") or ("trend" if topic["id"].startswith("x") else "bank")})
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't build a reel for {topic['id']}: {e}")
    led["queue"][day] = entries
    log(led, f"Queued {len(entries)} reels for {day}: {[e['topic'] for e in entries]}")
    if len(entries) < max(1, target // 2):
        notify_once(led, f"lowqueue-{day}", f"⚠️ Only {len(entries)} reel(s) queued for {day}",
                    "The bot couldn't build a full day of reels. Usual causes: out of AI clips (budget reached or "
                    "none left), no usable moments in your clips, or a key problem. Recent bot log:\n\n```\n"
                    + "\n".join(led.get("log", [])[-12:]) + "\n```")
    save_ledger(led)


if __name__ == "__main__":
    main()
