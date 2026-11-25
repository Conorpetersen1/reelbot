"""Weekly: generate animated mascot clips for the topics most likely to be used, into Drive/ai_library.

Each clip: (1) place the chosen mascot into the topic's scene, (2) animate it for a few seconds.
"""
import os
from . import drive, fal, learn
from .common import CFG, WORK, days_since, load_ledger, save_ledger, load_topics, log, now, paused


def pick_topics(led, n):
    topics = load_topics(led)
    stock = {}
    for c in led["ai_clips"].values():
        from .economy import max_uses
        if c["uses"] < max_uses(c):
            stock[c["topic"]] = stock.get(c["topic"], 0) + 1
    # topics with the least unused stock first, then the ones the learner rates most promising for followers
    draw = learn.fit(led).draw()
    retired = {t for t, d in led.get("retired", {}).items() if days_since(d) < CFG.get("learning", {}).get("retire_days", 30)}
    topics = [t for t in topics if t["id"] not in retired] or topics
    # Topics where Conor has binned 2+ clips go to the back of the line
    from . import taste
    bad = taste.binned_topics(led)
    ranked = sorted(topics, key=lambda t: (t["id"] in bad, stock.get(t["id"], 0),
                                           -draw.score({"topic": t["id"], "kind": "ai"})))
    return [ranked[i % len(ranked)] for i in range(n)]


def mascot_ref():
    mascots = [f for f in drive.list_files("mascot") if f["name"].lower().endswith((".png", ".jpg", ".jpeg"))]
    if not mascots:
        raise SystemExit("No mascot in Drive/mascot yet. Run the 'Mascot options' workflow and keep one.")
    return drive.download(sorted(mascots, key=lambda f: f["name"])[0]["id"], WORK / "mascot.png")


def make_clip(led, t, ref, n):
    """Mascot placed into the topic's scene, then animated. Saves to Drive/ai_library and the ledger."""
    from . import taste
    fal.charge(led, "image", "video_clip"); save_ledger(led)
    scene = fal.run(CFG["scene_model"], {
        "prompt": f"Put this exact character in a new scene: {t['ai_scene']}. Keep the character's design, "
                  f"colours and blocky low-poly 3D style identical. Vertical 9:16 composition, no text."
                  + taste.scene_extra(led),
        "image_url": fal.data_uri(ref), "aspect_ratio": "9:16"})
    vid = fal.run(CFG["video_model"], {
        "prompt": f"{t['ai_scene']}. Stylised blocky 3D animation, smooth motion, camera follows, no text, no logos.",
        "image_url": fal.first_url(scene, "image"), "duration": str(CFG["video_seconds"]),
        "negative_prompt": taste.negative(led)})
    name = f"{t['id']}_{now():%Y%m%d}_{n:02d}.mp4"
    path = fal.fetch(fal.first_url(vid, "video"), WORK / name)
    fid = drive.upload(path, "ai_library", name)
    led["ai_clips"][fid] = {"name": name, "topic": t["id"], "uses": 0, "created": now().isoformat(),
                            "scene": t["ai_scene"]}
    save_ledger(led)
    return fid


def main():
    if paused():
        print("reelbot is paused (paused: true in config.yaml)"); return
    led = load_ledger()
    # If you ran a batch by hand in the last few days, skip the automatic Monday one (saves money).
    last = led.get("last_batch") or ""
    if not os.environ.get("FORCE_BATCH") and last and days_since(last) < 5:
        log(led, "Skipped weekly AI clips: a batch was made in the last 5 days"); save_ledger(led); return
    # Learn from any clips binned since the last daily run before making new ones
    from . import taste
    taste.note_deleted(led)
    try:
        taste.review(led)
    except Exception as e:
        log(led, f"Taste review skipped: {e}")
    save_ledger(led)
    ref = mascot_ref()
    made = 0
    from . import economy
    economy.rate_clips(led)
    n = economy.weekly_clip_count(led, learn.fit(led))
    log(led, f"This week's mascot clip budget: {n} clips (based on clips left and how mascot reels convert)")
    for t in pick_topics(led, n):
        try:
            make_clip(led, t, ref, made); made += 1
        except fal.BudgetReached as e:
            log(led, str(e) + ". Reusing existing clips until next month."); break
        except Exception as e:  # one bad generation shouldn't stop the batch
            log(led, f"AI clip failed for {t['id']}: {e}")
    led["last_batch"] = now().isoformat()
    log(led, f"Generated {made} AI clips")
    save_ledger(led)


if __name__ == "__main__":
    main()
