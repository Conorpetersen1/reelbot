"""Weekly analyst (Mondays): Claude reads what the learner has found and turns it into
- 3 plain-English insights for the dashboard ("POV-style mascot reels in the evening bring the most followers")
- 3-5 new topic ideas modelled on your winners (each checked against the APSI rules before it's used)
Costs a few cents a week.
"""
import json, os
from . import learn, llm
from .common import CFG, load_ledger, load_topics, log, now, save_ledger

PROMPT = """You're the growth analyst for Conor's snowboard-instructor Instagram account. The goal is FOLLOWERS.
A statistical learner has estimated how each choice affects followers per reel (x = multiplier vs average,
sure = how confident it is). Reel-level numbers are estimated followers per reel plus engagement.

LEARNER EFFECTS:
{effects}

BEST REELS (most estimated followers):
{best}

WORST REELS:
{worst}

EXISTING TOPIC HOOKS (don't duplicate): {hooks}

1. Write 3 short, concrete insights for Conor (one sentence each, plain English, no jargon).
   Only claim what the numbers support; say "early signs" if sure < 0.8.
2. Propose {n} NEW topic ideas that follow the patterns of the winners. Tips must stay within the rules.
3. One sentence: what the bot should lean into next week.

Return only JSON:
{{"insights": ["...", "...", "..."], "focus": "...",
 "new_topics": [{{"category": "tip|snow_life", "level": "beginner|new_rider|any", "hook": "max 50 chars",
   "description": ["2-4 lines, max 45 chars"], "manual_ref": "APSI section or empty", "cta_keyword": "ONEWORD",
   "clip_tags": "main_tag,others", "ai_scene": "what the animated mascot clip should show"}}]}}"""


def main():
    led = load_ledger()
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("LOCAL_LLM"):
        print("No Claude key, skipping analyst"); return
    learn.fit(led)
    s = led.get("model", {})
    if s.get("reels_learned_from", 0) < CFG.get("learning", {}).get("analyst_min_reels", 10):
        print("Not enough matured reels yet for the analyst"); save_ledger(led); return
    topics = {t["id"]: t for t in load_topics(led)}
    eff = sorted(s["effects"], key=lambda e: -abs(e["x"] - 1))[:25]
    eff_txt = "\n".join(f"- {e['group']}={topics.get(e['level'], {}).get('hook', e['level']) if e['group'] == 'topic' else e['level']}: "
                        f"x{e['x']} (sure {e['sure']})" for e in eff)
    posts = [p for p in led["posts"].values() if "est_follows" in p and p.get("series")]
    posts.sort(key=lambda p: -p["est_follows"])

    def row(p):
        last = p["series"][-1]
        return (f"- \"{p['hook']}\" | {p.get('kind')} | style {p.get('style')} | {p.get('time')} | "
                f"est followers {p['est_follows']} | views {last[1]} | shares {last[3]} | saves {last[4]}")
    prompt = PROMPT.format(effects=eff_txt, best="\n".join(row(p) for p in posts[:6]),
                           worst="\n".join(row(p) for p in posts[-5:]),
                           hooks=json.dumps([t["hook"] for t in topics.values()][-120:]),
                           n=CFG.get("learning", {}).get("analyst_new_topics", 4))
    if os.environ.get("LOCAL_LLM"):
        out = {"insights": ["offline"], "focus": "offline", "new_topics": []}
    else:
        msg = llm._client().messages.create(model=CFG["claude_model"], max_tokens=6000, system=llm.RULES,
                                            messages=[{"role": "user", "content": prompt}])
        out = llm._json(llm._text(msg))
    added = []
    for idea in out.get("new_topics", [])[:6]:
        idea["description"] = idea["description"] if isinstance(idea["description"], list) else str(idea["description"]).split("|")
        ok, why = llm.check_reel(idea, {"hook": idea["hook"], "description": idea["description"]})
        if not ok:
            log(led, f"Analyst idea rejected ({why}): {idea['hook']}"); continue
        tid = f"a{len([k for k in led.setdefault('trend_topics', {}) if k.startswith('a')]) + 1:03d}"
        led["trend_topics"][tid] = {"id": tid, "category": idea.get("category", "tip"), "level": idea.get("level", "any"),
                                    "hook": idea["hook"], "description": "|".join(idea["description"]),
                                    "manual_ref": idea.get("manual_ref", ""), "cta_keyword": idea.get("cta_keyword", ""),
                                    "clip_tags": idea.get("clip_tags", "turns"), "ai_scene": idea.get("ai_scene", ""),
                                    "added": now().isoformat(), "source": "weekly analyst", "source_kind": "analyst"}
        added.append(tid)
    led["analyst"] = {"date": now().date().isoformat(), "insights": out.get("insights", [])[:3],
                      "focus": out.get("focus", ""), "added": added}
    log(led, f"Weekly analyst: {len(added)} new topics {added}")
    save_ledger(led)


if __name__ == "__main__":
    main()
