"""Claude calls: writing reel text and tagging real clips. Secret: ANTHROPIC_API_KEY."""
import base64, json, os, re
from .common import CFG, ROOT, env

RULES = (ROOT / "content/rules.md").read_text()


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def _text(msg):
    """The reply's text. Newer Claude models can return thinking blocks before the text block."""
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")


def _json(text):
    """Parse the first JSON object in a model reply (tolerates code fences and text before/after it)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    i = t.find("{")
    if i < 0:
        raise ValueError("no JSON in reply: " + t[:200])
    obj, _ = json.JSONDecoder().raw_decode(t[i:])
    return obj


HOOK_GUIDE = {"how_i": 'starts "How I..." / "The one cue that..." (a personal fix)',
              "why": 'starts "Why..." (names the problem the viewer has)',
              "question": "a question to the viewer, not starting with Why",
              "pov": 'starts "POV:" (a relatable situation)',
              "number": 'starts with a number, e.g. "3 mistakes..."',
              "statement": "a bold statement or hot take, no question mark"}


def write_reel(topic, recent_hooks, winning_hooks, clip_note, hook_type=None):
    """Returns {"hook": str, "description": [str], "caption": str}."""
    if os.environ.get("LOCAL_LLM"):  # offline test mode: use the topic bank text
        return {"hook": topic["hook"], "description": topic["description"].split("|"),
                "caption": f"{topic['hook']} Follow for more snowboard tips. #snowboarding"}
    prompt = f"""Write one Instagram trial reel for Conor's snowboard account.

TOPIC (from his topic bank; manual_ref = the APSI manual section it's based on):
{json.dumps(topic, indent=1)}

The video shows: {clip_note}

Hooks already used recently (don't repeat them word for word):
{json.dumps(recent_hooks[-40:])}

Hooks that performed best so far (learn from their style, don't copy):
{json.dumps(winning_hooks[:10])}

{("Write the hook in this format (the learner is testing it): " + HOOK_GUIDE[hook_type] + "."
  if hook_type in HOOK_GUIDE else "You can reuse the topic's hook or write a fresh variation in the same spirit.")}
The description must say the same thing as the topic's description, reworded if you like, never adding new technique.
End the caption with one call to action from the rules (never promise to DM anything).

Return only JSON: {{"hook": "...", "description": ["line", "line", "line"], "caption": "..."}}"""
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=800, system=RULES,
                                    messages=[{"role": "user", "content": prompt}])
    out = _json(_text(msg))
    out["hook"] = out["hook"].strip()[:60]
    out["description"] = [l.strip() for l in out["description"] if l.strip()][:4]
    return out


def review_prompt(topic, reel):
    return f"""You are reviewing reel text before it's posted. Rules are in the system prompt.
Topic: {json.dumps(topic)}
Reel: {json.dumps(reel)}
Does the reel contradict any rule, add technique not supported by the rules or the topic's manual_ref,
or contain anything misleading, unsafe, or off-brand? Line and caption lengths are checked in code, so don't
reject for length or formatting. Return only JSON: {{"ok": true/false, "reason": "..."}}"""


def check_reel(topic, reel):
    """Second pass by a strict reviewer. Gemini reviews if GEMINI_API_KEY is set (a different model catches
    different mistakes), otherwise Claude. Returns (ok, reason)."""
    if os.environ.get("LOCAL_LLM"):
        return True, "offline"
    prompt = review_prompt(topic, reel)
    from . import gemini
    if gemini.available():
        try:
            r = gemini.ask(prompt, system=RULES)
            return bool(r.get("ok")), "gemini: " + r.get("reason", "")
        except Exception as e:
            print("Gemini review failed, using Claude:", e)
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=300, system=RULES,
                                    messages=[{"role": "user", "content": prompt}])
    r = _json(_text(msg))
    return bool(r.get("ok")), r.get("reason", "")


def trend_prompt(what, duration, cuts, note, existing_hooks):
    return f"""{what} a trending reel Conor saved (any niche, not
necessarily snowboarding). Length {duration:.0f}s, about {cuts} cuts. His note: "{note}".

1. Work out the FORMAT: hook structure, on-screen text layout, pacing, audio/voiceover style if you can hear it,
   and why it holds attention.
2. Adapt that format into 1-3 reel ideas for his snowboard instructor account. Each will be made with either an
   animated blocky mascot clip or his own riding footage, plus on-screen text. Use a technique only if the rules allow it.
   Don't repeat these existing hooks: {json.dumps(existing_hooks[-60:])}
3. Pick the closest text style: "question" (hook in a box + points), "list" (numbered points), or "pov" (bare captions).

Return only JSON:
{{"format_name": "...", "hook_template": "...", "why_it_works": "one sentence", "style": "question|list|pov",
 "ideas": [{{"category": "tip|snow_life", "level": "beginner|new_rider|any", "hook": "max 50 chars",
   "description": ["2-4 lines, max 45 chars each"], "manual_ref": "APSI section or empty",
   "cta_keyword": "ONEWORD", "clip_tags": "main_tag,other_tags from {TAGS}",
   "ai_scene": "what the animated mascot clip should show"}}]}}"""


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
    content.append({"type": "text", "text": trend_prompt("These frames are from", duration, cuts, note, existing_hooks)})
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=1500, system=RULES,
                                    messages=[{"role": "user", "content": content}])
    return _json(_text(msg))


TAGS = ["heelside", "toeside", "turns", "linked_turns", "carving", "sideslip", "falling_leaf", "powder", "park",
        "jump", "ollie", "switch", "moguls", "ice", "chairlift", "falling", "teaching", "stance", "gear",
        "scenery", "lifestyle"]


def fit_prompt(reel, kind):
    return f"""{'This is' if kind == 'video' else 'These frames are from'} a finished Instagram reel for Conor's
snowboard instructor account. On-screen text: hook "{reel['hook']}", points {json.dumps(reel['description'])}.
Score how well the FOOTAGE fits the TEXT, 1-5:
5 = clearly shows what the text is about
3 = generic but doesn't contradict it (e.g. general riding under a mindset tip)
1 = contradicts the text (e.g. text about heelside but it's clearly a toeside turn; text about a mistake but the
    footage shows something unrelated), OR the footage looks broken: for AI animation, a distorted body,
    board detached from feet, melting or glitchy limbs, unreadable mess.
Also flag if the on-screen text is cut off or unreadable. Return only JSON: {{"score": 1-5, "reason": "short"}}"""


def fit_check_frames(frame_paths, reel):
    if os.environ.get("LOCAL_LLM"):
        return {"score": 5, "reason": "offline"}
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(p.read_bytes()).decode()}} for p in frame_paths]
    content.append({"type": "text", "text": fit_prompt(reel, "frames")})
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=300,
                                    messages=[{"role": "user", "content": content}])
    return _json(_text(msg))


def segment_prompt(duration, how):
    return f"""{how} Conor's own snowboarding clip, {duration:.0f}s long.
Find the BEST moments to use as short reel backgrounds: 4-12 seconds each, not overlapping, skipping standing
around, setup, blurry or shaky bits. At most 12 segments; fewer is fine if the clip is short or mostly boring.
For each segment pick tags from: {TAGS}. Judge whether the riding shows good technique or a mistake.
Return only JSON: {{"segments": [{{"start": seconds, "end": seconds, "tags": [...], "summary": "one short sentence",
"technique": "good|mistake|n/a", "usable": true/false}}]}}"""


def segment_clip_frames(frames_with_times, duration):
    """Claude fallback: frames sampled through the clip, each labelled with its timestamp."""
    if os.environ.get("LOCAL_LLM"):
        return {"segments": [{"start": 0, "end": min(duration, 8), "tags": ["turns"], "summary": "offline",
                              "technique": "good", "usable": True}]}
    content = []
    for t, p in frames_with_times:
        content.append({"type": "text", "text": f"t={t:.1f}s"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": segment_prompt(duration, "These frames (with timestamps) are from")})
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=1500,
                                    messages=[{"role": "user", "content": content}])
    return _json(_text(msg))


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
    return _json(_text(msg))
