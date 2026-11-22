"""Claude calls: writing reel text and tagging real clips. Secret: ANTHROPIC_API_KEY."""
import base64, json, os, re
from .common import CFG, ROOT, env

RULES = (ROOT / "content/rules.md").read_text()


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def write_reel(topic, recent_hooks, winning_hooks, clip_note):
    """Returns {"hook": str, "description": [str], "caption": str}."""
    if os.environ.get("LOCAL_LLM"):  # offline test mode: use the topic bank text
        return {"hook": topic["hook"], "description": topic["description"].split("|"),
                "caption": f"{topic['hook']} Comment {topic['cta_keyword']} for the drill. #snowboarding"}
    prompt = f"""Write one Instagram trial reel for Conor's snowboard account.

TOPIC (from his topic bank; manual_ref = the APSI manual section it's based on):
{json.dumps(topic, indent=1)}

The video shows: {clip_note}

Hooks already used recently (don't repeat them word for word):
{json.dumps(recent_hooks[-40:])}

Hooks that performed best so far (learn from their style, don't copy):
{json.dumps(winning_hooks[:10])}

You can reuse the topic's hook or write a fresh variation in the same spirit
(question, "How I fixed...", "Why do I...", "POV:", or a bold statement).
The description must say the same thing as the topic's description, reworded if you like, never adding new technique.
Use the CTA keyword "{topic.get('cta_keyword') or 'LESSON'}" in the caption.

Return only JSON: {{"hook": "...", "description": ["line", "line", "line"], "caption": "..."}}"""
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=800, system=RULES,
                                    messages=[{"role": "user", "content": prompt}])
    out = _json(msg.content[0].text)
    out["hook"] = out["hook"].strip()[:60]
    out["description"] = [l.strip() for l in out["description"] if l.strip()][:4]
    return out


def check_reel(topic, reel):
    """Second pass: a strict reviewer that rejects anything contradicting the rules. Returns (ok, reason)."""
    if os.environ.get("LOCAL_LLM"):
        return True, "offline"
    prompt = f"""You are reviewing reel text before it's posted. Rules are in the system prompt.
Topic: {json.dumps(topic)}
Reel: {json.dumps(reel)}
Does the reel contradict any rule, add technique not supported by the rules or the topic's manual_ref,
or contain anything misleading, unsafe, or off-brand? Return only JSON: {{"ok": true/false, "reason": "..."}}"""
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=300, system=RULES,
                                    messages=[{"role": "user", "content": prompt}])
    r = _json(msg.content[0].text)
    return bool(r.get("ok")), r.get("reason", "")


def analyze_trend(frame_paths, duration, cuts, note, existing_hooks):
    """Work out why a saved trending reel works and adapt its format into 1-3 topic ideas for Conor."""
    if os.environ.get("LOCAL_LLM"):
        return {"format_name": "POV list", "hook_template": "POV: [relatable situation]", "style": "pov",
                "why_it_works": "offline test", "ideas": [{
                    "category": "snow_life", "level": "any", "hook": "POV: your mate is teaching you to snowboard",
                    "description": ["Stares at their feet all run", "Never gets on their toes", "No glute squeeze on heelside"],
                    "manual_ref": "Combines t01-t03", "cta_keyword": "LESSON", "clip_tags": "turns,teaching",
                    "ai_scene": "mascot being taught by a clueless friend mascot, both falling over, comedic"}]}
    content = []
    for p in frame_paths:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": f"""These frames are from a trending reel Conor saved (any niche, not
necessarily snowboarding). Length {duration:.0f}s, about {cuts} cuts. His note: "{note}".

1. Work out the FORMAT: hook structure, on-screen text layout, pacing, and why it holds attention.
2. Adapt that format into 1-3 reel ideas for his snowboard instructor account. Each will be made with either an
   animated blocky mascot clip or his own riding footage, plus on-screen text. Use a technique only if the rules allow it.
   Don't repeat these existing hooks: {json.dumps(existing_hooks[-60:])}
3. Pick the closest text style: "question" (hook in a box + points), "list" (numbered points), or "pov" (bare captions).

Return only JSON:
{{"format_name": "...", "hook_template": "...", "why_it_works": "one sentence", "style": "question|list|pov",
 "ideas": [{{"category": "tip|snow_life", "level": "beginner|new_rider|any", "hook": "max 50 chars",
   "description": ["2-4 lines, max 45 chars each"], "manual_ref": "APSI section or empty",
   "cta_keyword": "ONEWORD", "clip_tags": "main_tag,other_tags from {TAGS}",
   "ai_scene": "what the animated mascot clip should show"}}]}}"""})
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=1500, system=RULES,
                                    messages=[{"role": "user", "content": content}])
    return _json(msg.content[0].text)


TAGS = ["heelside", "toeside", "turns", "linked_turns", "carving", "sideslip", "falling_leaf", "powder", "park",
        "jump", "ollie", "switch", "moguls", "ice", "chairlift", "falling", "teaching", "stance", "gear",
        "scenery", "lifestyle"]


def tag_clip(frame_paths, duration):
    content = []
    for p in frame_paths:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": f"""These are frames from a {duration:.0f}s snowboarding clip.
Pick the tags that fit from: {TAGS}.
Also judge: is the riding shown good technique, or does it show a mistake? Is it usable as a reel background
(not too dark/blurry)? Are there clearly identifiable faces of people other than a rider in goggles?
Return only JSON: {{"tags": [...], "summary": "one short sentence", "technique": "good|mistake|n/a",
"usable": true/false, "faces": true/false}}"""})
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=400,
                                    messages=[{"role": "user", "content": content}])
    return _json(msg.content[0].text)
