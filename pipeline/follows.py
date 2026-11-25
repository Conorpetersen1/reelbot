"""Real follows per reel, read from Conor's screenshots of Instagram's insights.

Instagram's API doesn't say how many follows each reel brought, but the app does (Professional dashboard ›
Views or Interactions › "Most follows"). Once a week Conor drops a screenshot of that list into Drive › insights.
Gemini reads each thumbnail's on-screen text and follow count, matches it to the reel the bot posted, and the
learner then uses those real numbers instead of its estimate. Screenshots move to Drive › insights_done.
Runs in the 5-minute dashboard refresh, so a new screenshot is picked up within minutes. ~A$0.002 a screenshot.
"""
import datetime as dt, json, subprocess
from . import drive, gemini
from .common import WORK, log, now

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp")


def _jpeg(path):
    """Gemini gets a JPEG (iPhone screenshots are PNG, photos can be HEIC)."""
    out = path.with_name(path.stem + "_s.jpg")
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((1600, 3200))
        im.save(out, "JPEG", quality=90)
    except Exception:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "scale='min(1600,iw)':-2",
                        str(out)], check=True)
    return out


def prompt(posts):
    listing = "\n".join(f"{i}. [{p['date']}, {'mascot' if p['kind'] == 'ai' else 'real footage'}] {p['hook']}"
                        for i, p in enumerate(posts))
    return f"""This is a screenshot from Instagram's app (insights for @conor_sends), showing reels and how many
follows each one brought, usually a list titled "Most follows" or similar, with a thumbnail and a number per reel.
For every reel visible in the screenshot:
- read the text written on its thumbnail (each reel has a hook in a white box or bold caption at the top),
- read its follow count (the number shown for follows; if a row shows several numbers, use the one for follows),
- find the matching reel in this list of reels the bot posted (match on the hook text; -1 if none matches):
{listing}

Also say what time period the screenshot covers if it's shown (e.g. "Last 7 days"), and whether this really is
a follows list (is_follows_list false if it shows views or something else).
Return only JSON: {{"is_follows_list": true, "period": "Last 7 days", "items": [{{"post": 3, "thumbnail_text": "...", "follows": 2}}]}}"""


def read(led, path):
    """Returns (n_matched, note)."""
    cutoff = (now() - dt.timedelta(days=45)).date().isoformat()
    mids = [m for m, p in sorted(led["posts"].items(), key=lambda x: x[1].get("posted_at", ""))
            if p.get("date", "") >= cutoff]
    posts = [led["posts"][m] for m in mids]
    if not posts:
        return 0, "no recent posts to match"
    r = gemini.ask(prompt(posts), images=[_jpeg(path)])
    if r.get("is_follows_list") is False:
        return 0, "doesn't look like a follows list"
    n, seen = 0, led.setdefault("follows_screens", [])
    for it in r.get("items") or []:
        try:
            k, f = int(it.get("post", -1)), int(it.get("follows"))
        except Exception:
            continue
        if not (0 <= k < len(mids)) or f < 0:
            continue
        p = led["posts"][mids[k]]
        # Counts within a rolling window only shrink as a reel ages out, so keep the highest seen
        p["follows_app"] = max(f, p.get("follows_app") or 0)
        p["follows_app_seen"] = now().date().isoformat()
        n += 1
    seen.append({"date": now().isoformat(timespec="minutes"), "period": r.get("period"), "matched": n,
                 "items": len(r.get("items") or [])})
    led["follows_screens"] = seen[-30:]
    return n, f"{n} of {len(r.get('items') or [])} reels matched ({r.get('period') or 'period not shown'})"


def main(led):
    """Process any new screenshots in Drive › insights. Returns True if anything changed."""
    if not gemini.available():
        return False
    try:
        files = [f for f in drive.list_files("insights") if f["name"].lower().endswith(IMAGE_EXT)
                 or str(f.get("mimeType", "")).startswith("image/")]
    except Exception as e:
        print("Couldn't list Drive › insights:", e); return False
    changed = False
    for f in files:
        path = WORK / ("ins_" + f["name"].replace("/", "_"))
        try:
            drive.download(f["id"], path)
            n, note = read(led, path)
            log(led, f"Follows screenshot {f['name']}: {note}")
        except Exception as e:
            log(led, f"Couldn't read follows screenshot {f['name']}: {e}")
        try:
            drive.move(f["id"], "insights_done")
        except Exception as e:
            print("Couldn't move the screenshot:", e)
        changed = True
    return changed


def due(led):
    """True when the weekly screenshot hasn't been dropped in 7 days (for the dashboard to-do and reminders)."""
    seen = led.get("follows_screens") or []
    if not seen:
        return True
    last = dt.datetime.fromisoformat(seen[-1]["date"])
    return (now() - last).days >= 7


if __name__ == "__main__":
    from .common import load_ledger, save_ledger
    led = load_ledger()
    if main(led):
        save_ledger(led)
    print(json.dumps(led.get("follows_screens", [])[-3:], indent=1))
