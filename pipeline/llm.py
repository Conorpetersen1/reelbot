"""Claude calls: writing reel text and tagging real clips. Secret: ANTHROPIC_API_KEY."""
import base64, json, os, re
from .common import CFG, ROOT, env

RULES = (ROOT / "content/rules.md").read_text()


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def _text(msg):
    """The reply's text. Newer Claude models can return thinking blocks before the text block.
    Every Claude reply passes through here, so this is also where its cost is recorded."""
    from . import usage
    usage.claude(msg)
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
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[i:])
    except json.JSONDecodeError:
        # Repair the usual slips: trailing commas and raw line breaks inside strings
        fixed = re.sub(r",\s*([}\]])", r"\1", t[i:])
        obj, _ = json.JSONDecoder(strict=False).raw_decode(fixed)
    return obj


HOOK_GUIDE = {"how_i": 'starts "How I..." / "The one cue that..." (a personal fix)',
              "why": 'starts "Why..." (names the problem the viewer has)',
              "question": "a question to the viewer, not starting with Why",
              "pov": 'starts "POV:" (a relatable situation)',
              "number": 'starts with a number, e.g. "3 mistakes..."',
              "statement": "a bold statement or hot take, no question mark"}


BANNED_OPENERS = ("how to ", "what ", "here's", "tips for", "the best way", "you need to know", "did you know")


CTA_GUIDE = {
    "follow": "FOLLOW, giving a reason to (e.g. 'Follow for a tip every day', 'Follow for part 2', 'Follow if this is you')",
    "save": "SAVE (e.g. 'Save this for your next day on snow', 'Save it and try it first run')",
    "share": "SHARE (e.g. 'Send this to the mate who does this', 'Tag who needs to hear it')",
}


def writer_prompt(topic, mode, clip_note, recent_hooks, winning_hooks, hook_type, taste, voice, feed="", cta="follow"):
    if mode == "moment":
        job = f"""This is a MOMENT reel: Conor's real footage, and the text is about what is actually happening in it.
The footage: {clip_note} (tags: {topic.get('clip_tags', '')}).
Write something his audience relates to or reacts to: a POV, an inside joke about snow/instructor/season life,
a feeling, a tiny story, a Thredbo-vs-Niseko observation, a hot take. It must make sense with THIS footage.
Do NOT teach technique or name a fault in his riding (the clip wasn't chosen to show one). Only use facts about
Conor and places that are in the rules."""
    elif mode == "life":
        facts = [c for c in str(topic.get("description", "")).split("|") if c.strip()]
        job = f"""This is a LIFE reel ({topic.get('category', '').replace('_', ' ')}): snow life, instructor life, gear or
Niseko/Thredbo, not a riding lesson. The footage: {clip_note}. It sets the vibe; never claim it shows something it
doesn't. Facts may ONLY come from these checked points (and the rules): {json.dumps(facts)}
The topic's own hook, for reference only: {json.dumps(topic.get('hook', ''))}
Make it relatable, funny or genuinely useful in his voice: how-it-feels, expectation vs reality, a satirical list,
an instructor's inside joke, a quick honest take. It has to make sense with this footage on screen."""
    else:
        cues = [c for c in str(topic.get("description", "")).split("|") if c.strip()]
        job = f"""This is a TEACHING reel ({topic.get('category')}, level {topic.get('level')}). The footage: {clip_note}.
The teaching content may ONLY come from these checked cues (APSI manual {topic.get('manual_ref', '')}):
{json.dumps(cues)}
The topic's own hook, for reference only: {json.dumps(topic.get('hook', ''))}
Make it make sense to a real learner: ONE problem -> ONE fix -> how it should feel. The hook names the problem or
promise; the lines deliver exactly that, in order, so someone could try it on their next run. You may use 1-2 of
the cues (not all), and frame them with an instructor's angle: the mistake you see every lesson, what you tell
students, a before/after feeling, a myth you bust. No technique beyond the cues; no jargon a beginner wouldn't get."""
    hint = (f"Make at least one candidate use this hook format (the learner is testing it): {HOOK_GUIDE[hook_type]}."
            if hook_type in HOOK_GUIDE else "")
    return f"""Write on-screen text for one Instagram trial reel on Conor's snowboard account (@conor_sends).
Trial reels are shown to NON-followers: the text must stop a stranger scrolling in the first second.

{voice or "No voice guide yet: write like a laid-back Aussie snowboard instructor talking to a mate."}

{job}

ON-SCREEN TEXT RULES
- Hook: max 8 words / 45 characters. Specific, with tension, curiosity or a laugh. Sounds like Conor talking, not
  a coach's handout. Never start with: {", ".join(BANNED_OPENERS)}.
- Lines under the hook: 0-2 for moment reels, 1-3 for teaching reels. Max 38 characters each. Each line must add
  something (the fix, the feel, the punchline); no filler, no repeating the hook.
- No hashtags or emojis on screen. Plain Aussie English.
- Caption: 1-2 short sentences that add context or a question, then ONE call to action of this type (never
  promise to DM anything): {CTA_GUIDE.get(cta, CTA_GUIDE['follow'])}. Then 3-5 relevant hashtags.
- Don't reuse these recent hooks: {json.dumps(recent_hooks[-40:])}
- His best performing bot reels so far (learn, don't copy): {json.dumps(winning_hooks[:8])}
{hint}
{taste}
{feed}
Write 3 clearly DIFFERENT candidates (different angles and formats, not rewordings). Use the "How it feels..."
format for at most one of them, and not at all if a recent hook already starts that way.
Return only JSON: {{"candidates": [{{"angle": "2-4 words", "hook": "...", "description": ["..."], "caption": "..."}}]}}"""


def _clean(c, mode):
    c["hook"] = str(c.get("hook", "")).strip()[:60]
    c["description"] = [str(l).strip() for l in (c.get("description") or []) if str(l).strip()][:(2 if mode == "moment" else 3)]
    c["caption"] = str(c.get("caption", "")).strip()
    return c


def write_candidates(topic, mode, clip_note, recent_hooks, winning_hooks, hook_type=None, taste="", voice="", feed="",
                     cta="follow"):
    """Three different on-screen texts for one reel: [{"angle", "hook", "description": [..], "caption"}]."""
    if os.environ.get("LOCAL_LLM"):  # offline test mode
        return [{"angle": "offline", "hook": topic.get("hook") or "Offline hook",
                 "description": [x for x in str(topic.get("description", "")).split("|") if x][:2],
                 "caption": "Offline caption. Follow for more. #snowboarding"}]
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=6000, system=RULES,
                                    messages=[{"role": "user", "content": writer_prompt(
                                        topic, mode, clip_note, recent_hooks, winning_hooks, hook_type, taste, voice, feed, cta)}])
    out = _json(_text(msg))
    cands = [_clean(c, mode) for c in (out.get("candidates") or []) if c.get("hook")]
    return [c for c in cands if not c["hook"].lower().startswith(BANNED_OPENERS)] or cands


TEACHING = {"tip", "freestyle", "first_timer"}


def reel_mode(topic):
    c = topic.get("category", "tip")
    return "moment" if c == "moment" else "teaching" if c in TEACHING else "life"


def write_reel(topic, recent_hooks, winning_hooks, clip_note, hook_type=None, taste=""):
    """Older single-text interface (kept for anything still using it)."""
    mode = reel_mode(topic)
    return write_candidates(topic, mode, clip_note, recent_hooks, winning_hooks, hook_type, taste)[0]


def critic_prompt(cands, topic, mode, voice, with_video):
    return f"""You are the editor for Conor's snowboard Instagram (@conor_sends). {'Watch this clip: it is the footage the text will sit over.' if with_video else 'The footage: ' + str(topic.get('clip_note', ''))}
This is a { {'moment': 'MOMENT reel (text about what is happening in the footage)', 'life': 'LIFE reel (snow/instructor/travel life; the footage sets the vibe and only needs to not contradict the text)'}.get(mode, 'TEACHING reel (one problem, one fix, from the APSI manual)') }.
{voice}

Score each candidate 1-5 on:
- hook: would a stranger who doesn't follow him stop scrolling? (5 = specific, tense or funny; 1 = generic handout)
- sense: does it make sense and deliver EXACTLY what the hook promises, in order? If the hook names one thing
  (e.g. the back foot) and the lines are about something else (e.g. shoulders), sense = 1. If the hook says
  "2 things" there must be 2. For teaching: could a learner try it on their next run? (1 = confusing,
  contradictory, vague or off-topic)
- match: does it fit what is actually in the footage? (1 = contradicts it)
- voice: does it sound like Conor's best reels?
- first: the FIRST SECOND. Viewers decide in about 1.5 seconds, before reading the points: is the hook short
  enough to take in at a glance, and does it fit what is on screen in the opening frames (and the mascot or
  rider's expression, if visible)? 5 = the opening shot and the hook instantly make sense together and create a
  question; 1 = the hook describes something the opening doesn't show, or needs the later lines to make sense.
Also set "problem" to a short note if anything is wrong, misleading, unsafe or not snowboarding-accurate, else "".
Candidates: {json.dumps(cands, ensure_ascii=False)}
Return only JSON: {{"scores": [{{"i": 0, "hook": n, "sense": n, "match": n, "voice": n, "first": n, "problem": "", "note": "short"}}]}}"""


def review_prompt(topic, reel):
    return f"""You are reviewing reel text before it's posted. Rules are in the system prompt.
Topic: {json.dumps(topic)}
Reel: {json.dumps(reel)}
{"This is a MOMENT reel (text about Conor's real footage, category 'moment'): it has no manual cues. Reject only if "
 "it teaches technique, invents facts about Conor or places beyond the rules, or breaks a rule. Humour and "
 "opinions are fine." if topic.get("category") == "moment" else "Does the reel contradict any rule or add technique not supported by the topic's cues or manual_ref?"}
Is anything misleading, unsafe, or off-brand? Line and caption lengths are checked in code, so don't
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
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=2000, system=RULES,
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
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=4000, system=RULES,
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
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=2000,
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
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=4000,
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
    msg = _client().messages.create(model=CFG["claude_model"], max_tokens=2000,
                                    messages=[{"role": "user", "content": content}])
    return _json(_text(msg))
