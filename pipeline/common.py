"""Shared helpers: config, the ledger (the bot's memory), time, topics."""
import csv, datetime as dt, json, os, pathlib
from zoneinfo import ZoneInfo
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
TZ = ZoneInfo(CFG["timezone"])
LEDGER_PATH = ROOT / "data/ledger.json"
WORK = ROOT / ".work"
WORK.mkdir(exist_ok=True)


def env(name, required=True):
    v = os.environ.get(name)
    if required and not v:
        raise SystemExit(f"Missing secret/env var {name}. Add it in GitHub > Settings > Secrets > Actions.")
    return v


def now():
    return dt.datetime.now(TZ)


def load_ledger():
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {"clips": {}, "ai_clips": {}, "queue": {}, "posts": {}, "followers": {}, "log": [],
            "trends": [], "trend_topics": {}}


def save_ledger(led):
    LEDGER_PATH.parent.mkdir(exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(led, indent=1, ensure_ascii=False, default=str))


def log(led, msg):
    stamp = now().strftime("%Y-%m-%d %H:%M")
    print(stamp, msg)
    led["log"] = (led.get("log", []) + [f"{stamp} {msg}"])[-300:]


def load_topics(led=None):
    """Topic bank (content/topics.csv) plus any ideas the trend inbox has added."""
    with (ROOT / "content/topics.csv").open() as f:
        topics = [r for r in csv.DictReader(f) if r["id"].strip()]
    led = led if led is not None else load_ledger()
    return topics + [t for t in led.get("trend_topics", {}).values() if not t.get("retired")]


def days_since(iso):
    if not iso:
        return 9999
    return (now() - dt.datetime.fromisoformat(iso)).total_seconds() / 86400


def paused():
    return bool(CFG.get("paused"))


def notify_once(led, key, title, body):
    """Send a GitHub notification (issue that @mentions you) once per key."""
    sent = led.setdefault("notified", {})
    if key in sent:
        return
    from .alerts import notify
    owner = (os.environ.get("GITHUB_REPOSITORY") or "/").split("/")[0]
    notify(title, (f"@{owner} " if owner else "") + body)
    sent[key] = now().isoformat()
    log(led, f"Notification sent: {title}")
