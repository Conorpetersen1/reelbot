"""Weekly: generate animated mascot clips for the topics most likely to be used, into Drive/ai_library.

Each clip: (1) place the chosen mascot into the topic's scene, (2) animate it for a few seconds.
"""
import random
from . import drive, fal
from .common import CFG, WORK, load_ledger, save_ledger, load_topics, log, now


def pick_topics(led, n):
    topics = load_topics(led)
    stock = {}
    for c in led["ai_clips"].values():
        if c["uses"] < CFG["ai_clip_max_uses"]:
            stock[c["topic"]] = stock.get(c["topic"], 0) + 1
    # topics with the least unused stock first, weighted toward performing topics
    perf = topic_scores(led)
    ranked = sorted(topics, key=lambda t: (stock.get(t["id"], 0), -perf.get(t["id"], 0), random.random()))
    return [ranked[i % len(ranked)] for i in range(n)]


def topic_scores(led):
    s = {}
    for p in led["posts"].values():
        ins = p.get("insights", {})
        if ins:
            s.setdefault(p["topic"], []).append(ins.get("views", 0) + 20 * ins.get("shares", 0) + 10 * ins.get("saved", 0))
    return {k: sum(v) / len(v) for k, v in s.items()}


def mascot_ref():
    mascots = [f for f in drive.list_files("mascot") if f["name"].lower().endswith((".png", ".jpg", ".jpeg"))]
    if not mascots:
        raise SystemExit("No mascot in Drive/mascot yet. Run the 'Mascot options' workflow and keep one.")
    return drive.download(sorted(mascots, key=lambda f: f["name"])[0]["id"], WORK / "mascot.png")


def make_clip(led, t, ref, n):
    """Mascot placed into the topic's scene, then animated. Saves to Drive/ai_library and the ledger."""
    scene = fal.run(CFG["scene_model"], {
        "prompt": f"Put this exact character in a new scene: {t['ai_scene']}. Keep the character's design, "
                  f"colours and blocky low-poly 3D style identical. Vertical 9:16 composition, no text.",
        "image_url": fal.data_uri(ref), "aspect_ratio": "9:16"})
    vid = fal.run(CFG["video_model"], {
        "prompt": f"{t['ai_scene']}. Stylised blocky 3D animation, smooth motion, camera follows, no text, no logos.",
        "image_url": fal.first_url(scene, "image"), "duration": str(CFG["video_seconds"])})
    name = f"{t['id']}_{now():%Y%m%d}_{n:02d}.mp4"
    path = fal.fetch(fal.first_url(vid, "video"), WORK / name)
    fid = drive.upload(path, "ai_library", name)
    led["ai_clips"][fid] = {"name": name, "topic": t["id"], "uses": 0, "created": now().isoformat()}
    save_ledger(led)
    return fid


def main():
    led = load_ledger()
    ref = mascot_ref()
    made = 0
    for t in pick_topics(led, CFG["ai_clips_per_week"]):
        try:
            make_clip(led, t, ref, made); made += 1
        except Exception as e:  # one bad generation shouldn't stop the batch
            log(led, f"AI clip failed for {t['id']}: {e}")
    log(led, f"Generated {made} AI clips")
    save_ledger(led)


if __name__ == "__main__":
    main()
