"""Render a 9:16 reel: clip + on-screen hook (question) + description lines that appear one by one.

Usage: python -m pipeline.render clip.mp4 out.mp4 --hook "How I fixed my heelside" \
         --desc "Squeeze your glutes|Bend more than on toeside|Folding at the waist = skidding"
"""
import argparse, json, subprocess, tempfile, pathlib
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT_BOLD = str(ROOT / "assets/fonts/Poppins-Bold.ttf")
FONT_MED = str(ROOT / "assets/fonts/Poppins-Medium.ttf")
W, H = 1080, 1920
# Instagram covers roughly the top 220px (header) and bottom 400px (caption/buttons), plus a ~140px strip on the right.
SAFE_TOP, SAFE_SIDE, SAFE_RIGHT = 300, 80, 160


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


STYLES = ("question", "list", "pov")


def _centre_x(d, text, font):
    return (W - SAFE_RIGHT + SAFE_SIDE - d.textlength(text, font=font)) / 2


def _fix_orphan(lines):
    if len(lines) > 1 and len(lines[-1].split()) == 1:  # avoid a lonely last word
        a = lines[-2].split(); lines[-2] = " ".join(a[:-1]); lines[-1] = a[-1] + " " + lines[-1]
    return lines


def make_overlays(hook, desc_lines, tmp, style="question"):
    """Returns list of (png_path, start_seconds).
    question: hook in a white box, each point on a dark card (default)
    list:     hook in a white box, numbered points, left-aligned
    pov:      bare bold captions with an outline, no boxes (POV / storytime trends)
    """
    style = style if style in STYLES else "question"
    max_w = W - SAFE_SIDE - SAFE_RIGHT
    out = []
    f = ImageFont.truetype(FONT_BOLD, 74)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    lines = _wrap(d, hook, f, max_w - 80)
    lh = 92
    y0 = SAFE_TOP
    if style == "pov":
        for i, l in enumerate(lines):
            d.text((_centre_x(d, l, f), y0 + i * lh), l, font=f, fill=(255, 255, 255, 255),
                   stroke_width=5, stroke_fill=(0, 0, 0, 255))
        box_h = lh * len(lines)
    else:
        box_h = lh * len(lines) + 60
        box_w = max(d.textlength(l, font=f) for l in lines) + 80
        x0 = (W - SAFE_RIGHT + SAFE_SIDE - box_w) / 2
        d.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=28, fill=(255, 255, 255, 245))
        for i, l in enumerate(lines):
            tw = d.textlength(l, font=f)
            d.text((x0 + (box_w - tw) / 2, y0 + 26 + i * lh), l, font=f, fill=(15, 15, 15, 255))
    p = tmp / "hook.png"; img.save(p); out.append((p, 0.0))

    f2 = ImageFont.truetype(FONT_MED if style != "pov" else FONT_BOLD, 52 if style != "pov" else 58)
    y = y0 + box_h + 50 if style != "pov" else 1080
    for n, text in enumerate(desc_lines):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
        if style == "list":
            text = f"{n + 1}. {text}"
        lines = _fix_orphan(_wrap(d, text, f2, max_w - 60))
        lh = 68 if style != "pov" else 74
        if style == "pov":
            for i, l in enumerate(lines):
                d.text((_centre_x(d, l, f2), y + i * lh), l, font=f2, fill=(255, 255, 255, 255),
                       stroke_width=5, stroke_fill=(0, 0, 0, 255))
            y += lh * len(lines) + 16
        else:
            bw = (max_w - 20) if style == "list" else max(d.textlength(l, font=f2) for l in lines) + 56
            bh = lh * len(lines) + 34
            bx = (W - SAFE_RIGHT + SAFE_SIDE - bw) / 2
            d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=22, fill=(10, 14, 24, 165))
            for i, l in enumerate(lines):
                tx = bx + 30 if style == "list" else bx + (bw - d.textlength(l, font=f2)) / 2
                d.text((tx, y + 12 + i * lh), l, font=f2, fill=(255, 255, 255, 255))
            y += bh + 18
        p = tmp / f"desc{n}.png"; img.save(p); out.append((p, 1.2 + n * 1.6))
    return out


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def render(clip, out, hook, desc_lines, music=None, start=0.0, mirror=False, style="question",
           music_volume=0.9, clip_volume=0.25, zoom=1.0, pan=0.5, speed=1.0):
    """music: optional sound to lay over the reel (from its start). The clip's own sound stays underneath
    at clip_volume (0 = off). zoom/pan/speed/mirror make reused footage look different
    (zoom 1.0-1.2, pan 0-1 = which part of the frame is kept, speed ~0.9-1.1)."""
    desc_lines = [l for l in desc_lines if l.strip()]
    # Last point appears at 1.2 + 1.6*(n-1)s, then ~3.5s to read it. Short reels loop, which helps watch time.
    dur = max(7.0, min(12.0, 1.2 + 1.6 * (len(desc_lines) - 1) + 3.5))
    clip_len = probe_duration(clip)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        overlays = make_overlays(hook, desc_lines, tmp, style)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        # Loop the clip if it's shorter than the reel.
        if clip_len < dur + start:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-ss", f"{start:.2f}", "-i", str(clip)]
        for p, _ in overlays:
            cmd += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(p)]
        if music:
            cmd += ["-stream_loop", "-1", "-i", str(music)]
        flip = ",hflip" if mirror else ""
        zw, zh = int(W * zoom) // 2 * 2, int(H * zoom) // 2 * 2
        spd = f",setpts=PTS/{speed:.3f}" if abs(speed - 1) > 0.01 else ""
        fc = [f"[0:v]scale={zw}:{zh}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H}:(iw-{W})*{pan:.3f}:(ih-{H})/2,setsar=1{spd},fps=30{flip},"
              f"eq=brightness=-0.04:contrast=1.03[base]"]
        last = "base"
        for i, (_, t0) in enumerate(overlays, start=1):
            fc.append(f"[{i}:v]format=rgba,fade=t=in:st={t0:.2f}:d=0.35:alpha=1[o{i}]")
            fc.append(f"[{last}][o{i}]overlay=0:0:enable='gte(t,{t0:.2f})'[v{i}]")
            last = f"v{i}"
        maps = ["-map", f"[{last}]"]
        a_in = len(overlays) + 1
        if music and has_audio(clip) and clip_volume > 0:
            fc.append(f"[{a_in}:a]volume={music_volume}[mus]")
            tempo = f",atempo={speed:.3f}" if abs(speed - 1) > 0.01 else ""
            fc.append(f"[0:a]volume={clip_volume}{tempo}[nat]")
            fc.append(f"[mus][nat]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={dur-1:.2f}:d=1[aout]")
            maps += ["-map", "[aout]"]
        elif music:
            fc.append(f"[{a_in}:a]volume={music_volume},afade=t=out:st={dur-1:.2f}:d=1[aout]")
            maps += ["-map", "[aout]"]
        elif has_audio(clip):
            if abs(speed - 1) > 0.01:
                fc.append(f"[0:a]atempo={speed:.3f}[nat]"); maps += ["-map", "[nat]"]
            else:
                maps += ["-map", "0:a"]
        else:  # Instagram is happier with an audio track, so add silence
            cmd += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
            maps += ["-map", f"{a_in}:a"]
        cmd += ["-filter_complex", ";".join(fc), *maps, "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    return dur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("out")
    ap.add_argument("--hook", required=True); ap.add_argument("--desc", default="")
    ap.add_argument("--music"); ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--style", default="question", choices=STYLES)
    a = ap.parse_args()
    print(render(a.clip, a.out, a.hook, a.desc.split("|"), a.music, mirror=a.mirror, style=a.style))
