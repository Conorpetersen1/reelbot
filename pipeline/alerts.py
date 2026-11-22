"""Hourly: warn you when a trial reel takes off, so you can check it's OK before it gets bigger.

When a reel from the last 3 days passes one of the view thresholds in config.yaml (alert_views),
the bot opens an issue in your GitHub repo and @mentions you. GitHub emails you, or pushes to the GitHub app.
"""
import os, requests
from . import ig
from .common import CFG, days_since, load_ledger, log, now, save_ledger


def notify(title, body):
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and tok):
        print("ALERT (no GitHub token here):", title); return
    r = requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=60,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"title": title, "body": body})
    if r.status_code >= 300:
        raise RuntimeError(f"Couldn't create GitHub alert: {r.status_code} {r.text[:200]}")


def main():
    led = load_ledger()
    levels = sorted(CFG.get("alert_views") or [2000, 10000, 50000])
    owner = (os.environ.get("GITHUB_REPOSITORY") or "/").split("/")[0]
    for mid, p in led["posts"].items():
        if days_since(p["posted_at"]) > 3:
            continue
        try:
            p["insights"] = ig.insights(mid); p["insights_at"] = now().isoformat()
            if not p.get("permalink"):
                p["permalink"] = ig.permalink(mid)
        except Exception as e:
            log(led, f"Alert check failed for {mid}: {e}"); continue
        views = p["insights"].get("views", 0) or 0
        new = [lv for lv in levels if views >= lv and lv not in p.get("alerted", [])]
        if not new:
            continue
        lv = max(new)
        i = p["insights"]
        body = (f"@{owner} a trial reel just passed **{lv:,} views** (now {views:,}).\n\n"
                f"**Hook:** {p['hook']}\n**Type:** {'AI mascot' if p['kind'] == 'ai' else 'your clip'} · "
                f"topic `{p['topic']}` · posted {p['posted_at'][:16].replace('T', ' ')}\n"
                f"**Reach:** {i.get('reach', 0):,} · **Shares:** {i.get('shares', 0):,} · "
                f"**Saves:** {i.get('saved', 0):,} · **Comments:** {i.get('comments', 0):,}\n\n"
                f"**Open it:** {p.get('permalink') or '(link not available yet)'}\n\n"
                "Check:\n- [ ] Text is right and nothing looks off\n- [ ] Comments are fine\n"
                "- [ ] Happy for it to keep growing (consider graduating it to your profile in the app)\n\n"
                "If something's wrong, delete the reel in the Instagram app. Close this issue when you've checked it.")
        try:
            notify(f"🔥 Trial reel passed {lv:,} views: {p['hook']}", body)
            p["alerted"] = sorted(set(p.get("alerted", [])) | set(new))
            log(led, f"View alert sent: {p['hook']} ({views:,} views)")
        except Exception as e:
            log(led, str(e))
    save_ledger(led)


if __name__ == "__main__":
    main()
