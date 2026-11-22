"""Evening job: build tomorrow's reels and put them in Drive/queue. Delete any you don't want before they post."""
import datetime as dt, random, re, subprocess
from . import drive, gemini, llm
from .common import CFG, WORK, days_since, load_ledger, load_topics, log, now, save_ledger
from .render import render


def score(led):
    """Average performance per topic: views, with shares/saves/follows weighted up (those drive followers)."""
    s = {}
    for p in led["posts"].values():
        i = p.get("insights") or {}
        if i:
            s.setdefault(p["topic"], []).append(i.get("views", 0) + 20 * i.get("shares", 0) + 10 * i.get("saved", 0))
    return {k: sum(v) / len(v) for k, v in s.items()}


def recent_topics(led):
    used = {}
    for day in led["queue"].values():
        for e in day:
            used[e["topic"]] = max(used.get(e["topic"], ""), e["date"])
    return {t for t, d in used.items() if days_since(d + "T12:00:00+00:00") < CFG["topic_cooldown_days"]}


def is_fresh_trend(t):
    return bool(t.get("added")) and days_since(t["added"]) < CFG.get("trend_boost_days", 7)


def weighted_pick(cands, perf):
    """Mostly exploit good topics, sometimes explore untested ones. Fresh trend ideas get a boost."""
    if not cands:
        return None
    fresh = [c for c in cands if is_fresh_trend(c[0]) and c[0]["id"] not in perf]
    if fresh and random.random() < 0.5:
        return random.choice(fresh)
    if random.random() < 0.3:
        untested = [c for c in cands if c[0]["id"] not in perf]
        if untested:
            return random.choice(untested)
    base = sum(perf.values()) / len(perf) if perf else 1
    w = [max(perf.get(c[0]["id"], base), 1) for c in cands]
    return random.choices(cands, weights=w)[0]


CTAS = ['Follow for more snowboard tips', 'Save this for your next day on snow', 'Send this to a mate who needs it', 'Follow for a tip every day', 'Save this and try it next run', 'Tag the mate you ride with']

MISTAKE = re.compile(r"^(why|stop|\d+ mistakes)|mistake|catch|fall|wash|skid|stuck|lock|chatter|crash|"
                     r"won't|wrong|excuse", re.I)


def wants_mistake(topic):
    """'Why do I fall...' style topics suit footage of a mistake; 'How I fixed...' suits good riding."""
    hook = topic["hook"].strip()
    if re.search(r"\bfall(ing)? without|\bhow to fall", hook, re.I):  # falling tips suit footage of a fall
        return True
    if re.match(r"(how|the (one|drill|trick)|where|check)\b", hook, re.I):  # fixes and how-tos want good riding
        return False
    return bool(MISTAKE.search(hook)) and topic.get("category") == "tip"


def technique_ok(topic, seg):
    tech = seg.get("technique", "n/a")
    if tech == "n/a" or topic.get("category") != "tip":
        return True
    return (tech == "mistake") == wants_mistake(topic)


def choose(led, topics, n_ai, n_real):
    perf, cooldown = score(led), recent_topics(led)
    by_id = {t["id"]: t for t in topics}
    picks, used_topics = [], set(cooldown)
    # AI slots: topics that have unused AI clips in the library
    for _ in range(n_ai):
        cands = [(by_id[c["topic"]], cid) for cid, c in led["ai_clips"].items()
                 if c["uses"] < CFG["ai_clip_max_uses"] and not c.get("rejected")
                 and c["topic"] in by_id and c["topic"] not in used_topics
                 and not any(pk[2] == cid for pk in picks)]
        pick = weighted_pick(cands, perf)
        if not pick:
            n_real += 1  # no AI stock: fall back to a real clip
            continue
        picks.append(("ai", pick[0], pick[1])); used_topics.add(pick[0]["id"])
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
        cands = [(t, (cid, i)) for t in topics if t["id"] not in used_topics
                 and t["id"] not in c.get("misfit", []) and (
                 t["clip_tags"].split(",")[0] in ctags if t["category"] == "tip"
                 else set(t["clip_tags"].split(",")) & ctags)]
        # Prefer topics whose angle matches the riding: mistakes -> "why do I...", good riding -> "how I fixed..."
        cands = [x for x in cands if technique_ok(x[0], c)] or cands
        pick = weighted_pick(cands, perf)
        if pick:
            picks.append(("real", pick[0], pick[1])); used_topics.add(pick[0]["id"]); used_clips.add(cid)
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


def text_for(topic, led, clip_note):
    recent = [e["hook"] for d in led["queue"].values() for e in d]
    winners = [p["hook"] for p in sorted(led["posts"].values(),
               key=lambda p: -(p.get("insights") or {}).get("views", 0))]
    for _ in range(2):
        try:
            reel = llm.write_reel(topic, recent, winners, clip_note)
            ok, why = llm.check_reel(topic, reel)
            if ok:
                return reel, "written"
            log(led, f"Reviewer rejected text for {topic['id']}: {why}")
        except Exception as e:
            log(led, f"Writer error for {topic['id']}: {e}")
    # Fallback: the topic bank's own text, which was checked against the manual by hand.
    return {"hook": topic["hook"], "description": topic["description"].split("|"),
            "caption": f"{topic['hook']} {random.choice(CTAS)}. "
                       "#snowboarding #snowboardtips #learntosnowboard #snowboardinstructor"}, "topic_bank"


def main():
    led = load_ledger()
    day = (now() + dt.timedelta(days=1)).date().isoformat()
    if led["queue"].get(day):
        print("Already planned", day); return
    topics = load_topics(led)
    # Forget clips you've deleted from Drive, so they're never picked
    for key, store in (("ai_library", "ai_clips"), ("real", "clips")):
        present = {f["id"] for f in drive.list_files(key)}
        for cid in [c for c in led[store] if c not in present]:
            led[store].pop(cid)
    n_ai, n_real = CFG["reels_per_day"]["ai"], CFG["reels_per_day"]["real"]
    target = min(n_ai + n_real, len(CFG["post_times"]))
    # Plan spares too, so a reel that fails the fit check can be swapped for another
    spare = CFG.get("fit_spares", 3)
    picks = choose(led, topics, n_ai + spare, n_real + spare)
    main_picks = [p for p in picks if p[0] == "ai"][:n_ai] + [p for p in picks if p[0] == "real"][:n_real]
    random.shuffle(main_picks)  # mix AI and real across the day's posting times
    picks = main_picks + [p for p in picks if p not in main_picks]
    audio = [f for f in drive.list_files("audio") if f["name"].lower().endswith((".mp3", ".m4a", ".wav"))]
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
            reel, source = text_for(topic, led, note)
            music = None
            if kind == "ai" and audio:
                a = random.choice(audio); music = drive.download(a["id"], WORK / a["name"])
            out = WORK / f"{day}_{len(entries)+1}_{topic['id']}.mp4"
            render(src, out, reel["hook"], reel["description"], music=music,
                   style=topic.get("style") or "question")
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
            meta["uses"] = meta.get("uses", 0) + 1; meta["last_used"] = now().isoformat()
            if kind == "real":
                clip["uses"] = clip.get("uses", 0) + 1; clip["last_used"] = now().isoformat()
            entries.append({"date": day, "slot": slot + 1, "time": CFG["post_times"][slot], "kind": kind,
                            "topic": topic["id"], "clip": clip_id if kind == "ai" else f"{clip_id[0]}#{clip_id[1]}",
                            "file_id": fid, "file": out.name, "fit": score,
                            "hook": reel["hook"], "description": reel["description"], "caption": reel["caption"],
                            "text_source": source, "status": "queued"})
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't build a reel for {topic['id']}: {e}")
    led["queue"][day] = entries
    log(led, f"Queued {len(entries)} reels for {day}: {[e['topic'] for e in entries]}")
    save_ledger(led)


if __name__ == "__main__":
    main()
