"""Learn Conor's voice from his own account: what his best reels say on screen and how, versus his weakest.

Pulls his reels (not the bot's) with their stats, has Gemini watch the first seconds of his top 10 and bottom 5
side by side, and stores a short style guide in the ledger ("voice"). Claude writes to it and the critic judges
against it. Runs monthly from the weekly job; `python -m pipeline.voice --force` to redo it now.
"""
import json, subprocess, sys, requests
from . import gemini, ig
from .common import WORK, days_since, load_ledger, log, now, save_ledger

TOP, BOTTOM, SECONDS = 10, 5, 15

PROMPT = """These are the opening {secs} seconds of reels from Conor's own Instagram (@conor_sends): he's an APSI
Level 3 snowboard instructor who works at Thredbo (Australia) and in Niseko (Japan). Each clip is labelled in the
top-left: TOP reels are his best performers, LOW reels his weakest. Details (caption, views, shares, saves):

{details}

Study the TEXT ON SCREEN (exact words, length, tone, humour, format) and how it relates to the footage, plus the
captions. Work out what his best reels do that his weak ones don't. Be concrete and only describe what you see.
Return only JSON:
{{"voice_summary": "2-3 sentences: his voice and what his audience responds to",
 "what_works": ["up to 7 specific patterns from the TOP reels"],
 "what_flops": ["up to 5 patterns from the LOW reels"],
 "top_hooks": ["the exact on-screen text of each TOP reel, in order (empty string if none)"],
 "formats": ["up to 6 reusable formats, e.g. 'POV: <relatable moment> over riding footage'"],
 "tone_rules": ["up to 6 rules: slang, length, punctuation, emoji, swearing, Aussie-isms"],
 "do_not": ["up to 5 things that clearly don't fit his account"]}}"""


def his_reels(led, limit=120):
    g, t, uid = ig.graph(), ig._tok(), ig.user_id()
    url = f"{g}/{uid}/media"
    params = {"fields": "id,caption,media_product_type,media_url,thumbnail_url,timestamp,permalink", "limit": 50,
              "access_token": t}
    out = []
    while url and len(out) < limit:
        r = requests.get(url, params=params, timeout=60).json()
        out += [m for m in r.get("data", []) if m.get("media_product_type") == "REELS" and m["id"] not in led["posts"]]
        url, params = (r.get("paging") or {}).get("next"), None
    return out


def _labelled(src, label, out):
    from .render import FONT_BOLD
    font = FONT_BOLD.replace(":", r"\:")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", str(SECONDS), "-an",
                    "-vf", "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,fps=2,"
                           f"drawtext=fontfile='{font}':text='{label}':x=8:y=8:fontsize=26:fontcolor=yellow:"
                           "box=1:boxcolor=black@0.8:boxborderw=6",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "33", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def study(led):
    reels = his_reels(led)
    scored = []
    for m in reels:
        if not m.get("media_url") and not m.get("thumbnail_url"):
            continue
        try:
            i = ig.insights(m["id"])
        except Exception:
            continue
        m["stats"] = i
        m["score"] = (i.get("views", 0) or 0) + 20 * (i.get("shares", 0) or 0) + 10 * (i.get("saved", 0) or 0)
        scored.append(m)
    if len(scored) < 6:
        log(led, f"Voice study skipped: only {len(scored)} of your reels could be read"); return
    scored.sort(key=lambda m: -m["score"])
    picks = [("TOP", n + 1, m) for n, m in enumerate(scored[:TOP])] + \
            [("LOW", n + 1, m) for n, m in enumerate(scored[-BOTTOM:])]
    parts, details, images = [], [], []
    for kind, n, m in picks:
        label = f"{kind} {n}"
        s = m["stats"]
        how = "video"
        try:
            if m.get("media_url"):
                raw = WORK / f"voice_{kind}{n}.mp4"
                raw.write_bytes(requests.get(m["media_url"], timeout=120).content)
                parts.append(_labelled(raw, label, WORK / f"voice_lab_{kind}{n}.mp4"))
            else:  # licensed music: Instagram only gives the cover image, which usually shows the on-screen text
                img = WORK / f"voice_{kind}{n}.jpg"
                img.write_bytes(requests.get(m["thumbnail_url"], timeout=60).content)
                images.append(img); how = f"cover image {len(images)} (video not available)"
        except Exception as e:
            print("Couldn't fetch", m.get("permalink"), e); continue
        details.append(f"{label} [{how}]: views {s.get('views', 0)}, shares {s.get('shares', 0)}, "
                       f"saves {s.get('saved', 0)}; caption: {json.dumps((m.get('caption') or '')[:220])}")
    if not parts and not images:
        log(led, "Voice study skipped: none of your reels could be fetched"); return
    reel = None
    if parts:
        lst = WORK / "voice_list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        reel = WORK / "voice_study.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        str(reel)], check=True)
    note = ("\nSome reels are given as cover images instead (numbered in order after the video), because "
            "Instagram hides videos with licensed music; read their on-screen text from the image." if images else "")
    r = gemini.ask(PROMPT.format(secs=SECONDS, details="\n".join(details)) + note, video=reel,
                   images=images, video_fps=2)
    keep = ("voice_summary", "what_works", "what_flops", "top_hooks", "formats", "tone_rules", "do_not")
    led["voice"] = {k: r.get(k) for k in keep if r.get(k)}
    led["voice"].update({"updated": now().isoformat(), "reels_studied": len(scored),
                         "top_links": [m.get("permalink") for _, _, m in picks[:TOP]]})
    log(led, f"Studied {len(scored)} of your reels for your voice: {led['voice'].get('voice_summary', '')[:120]}")


def guide(led):
    """The style guide as text for prompts ('' if there isn't one yet)."""
    v = led.get("voice") or {}
    if not v:
        return ""
    keys = ("voice_summary", "what_works", "what_flops", "formats", "tone_rules", "do_not", "top_hooks")
    return "CONOR'S VOICE (learned from his own best and worst reels):\n" + json.dumps(
        {k: v[k] for k in keys if v.get(k)}, ensure_ascii=False, indent=1)


def main(force=False):
    led = load_ledger()
    v = led.get("voice") or {}
    if not force and v and days_since(v.get("updated")) < 28:
        print("Voice study is recent; skipping"); return
    if not gemini.available():
        print("No Gemini key; voice study skipped"); return
    study(led)
    save_ledger(led)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
