"""Evening job: build tomorrow's reels and put them in Drive/queue. Delete any you don't want before they post."""
import datetime as dt, random, subprocess
from . import drive, llm
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


def choose(led, topics, n_ai, n_real):
    perf, cooldown = score(led), recent_topics(led)
    by_id = {t["id"]: t for t in topics}
    picks, used_topics = [], set(cooldown)
    # AI slots: topics that have unused AI clips in the library
    for _ in range(n_ai):
        cands = [(by_id[c["topic"]], cid) for cid, c in led["ai_clips"].items()
                 if c["uses"] < CFG["ai_clip_max_uses"] and c["topic"] in by_id and c["topic"] not in used_topics]
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
        cands = [(t, (cid, i)) for t in topics if t["id"] not in used_topics and (
                 t["clip_tags"].split(",")[0] in ctags if t["category"] == "tip"
                 else set(t["clip_tags"].split(",")) & ctags)]
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
            "caption": f"{topic['hook']} Comment {topic['cta_keyword'] or 'LESSON'} and I'll send you the drill. "
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
    picks = choose(led, topics, CFG["reels_per_day"]["ai"], CFG["reels_per_day"]["real"])
    random.shuffle(picks)
    audio = [f for f in drive.list_files("audio") if f["name"].lower().endswith((".mp3", ".m4a", ".wav"))]
    entries = []
    for slot, (kind, topic, clip_id) in enumerate(picks[:len(CFG["post_times"])]):
        try:
            if kind == "ai":
                meta = led["ai_clips"][clip_id]
                src = drive.download(clip_id, WORK / f"src_{slot}.mp4")
                note = f"an animated blocky cartoon snowboarder: {topic['ai_scene']}"
            else:
                cid, i = clip_id
                clip = led["clips"][cid]; meta = segments(clip)[i]
                full = WORK / f"full_{cid[-12:].replace('/', '_')}_{clip['name']}"
                if not full.exists():
                    drive.download(cid, full)
                src = cut(full, meta["start"], meta["end"], WORK / f"src_{slot}.mp4")
                note = f"Conor's real footage: {meta.get('summary', '')}"
                clip["uses"] = clip.get("uses", 0) + 1; clip["last_used"] = now().isoformat()
            reel, source = text_for(topic, led, note)
            music = None
            if kind == "ai" and audio:
                a = random.choice(audio); music = drive.download(a["id"], WORK / a["name"])
            start = 0
            out = WORK / f"{day}_{slot+1}_{topic['id']}.mp4"
            render(src, out, reel["hook"], reel["description"], music=music, start=start,
                   style=topic.get("style") or "question")
            fid = drive.upload(out, "queue", out.name)
            meta["uses"] += 1; meta["last_used"] = now().isoformat()
            entries.append({"date": day, "slot": slot + 1, "time": CFG["post_times"][slot], "kind": kind,
                            "topic": topic["id"], "clip": clip_id if kind == "ai" else f"{clip_id[0]}#{clip_id[1]}",
                            "file_id": fid, "file": out.name,
                            "hook": reel["hook"], "description": reel["description"], "caption": reel["caption"],
                            "text_source": source, "status": "queued"})
            save_ledger(led)
        except Exception as e:
            log(led, f"Couldn't build slot {slot+1} ({topic['id']}): {e}")
    led["queue"][day] = entries
    log(led, f"Queued {len(entries)} reels for {day}: {[e['topic'] for e in entries]}")
    save_ledger(led)


if __name__ == "__main__":
    main()
