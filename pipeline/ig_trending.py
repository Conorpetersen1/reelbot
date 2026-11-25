"""Instagram's own trending music (Audio API), checked daily, so sound sweeps target what's trending now.

Read only: nothing is attached to reels (tests showed attached sounds start at a random point), the bot keeps
baking sounds from Conor's sweeps. This just tells him which trending songs his library is missing.
"""
import requests
from . import ig
from .common import load_ledger, log, now, save_ledger


def fetch():
    r = requests.get(f"{ig.graph()}/ig_audio", timeout=60,
                     params={"audio_type": "music", "user_id": ig.user_id(), "access_token": ig._tok()})
    r.raise_for_status()
    j = r.json()
    return j.get("audio") or j.get("data") or []


def main():
    led = load_ledger()
    try:
        items = fetch()
    except Exception as e:
        print("Couldn't fetch Instagram's trending music:", e); return
    today = now().date().isoformat()
    seen = led.setdefault("ig_trending_seen", {})  # title -> first day it showed up in the trending list
    out = []
    for rank, a in enumerate(items[:25], 1):
        title = f"{a.get('title', '').strip()} - {(a.get('display_artist') or '').strip()}".strip(" -")
        if not title:
            continue
        first = seen.setdefault(title.lower(), today)
        out.append({"sound": title, "rank": rank, "first_seen": first, "new": first >= today})
    led["ig_trending"] = {"date": now().isoformat(), "items": out}
    log(led, f"Instagram trending music: {len(out)} songs, {sum(x['new'] for x in out)} new today")
    save_ledger(led)


if __name__ == "__main__":
    main()
