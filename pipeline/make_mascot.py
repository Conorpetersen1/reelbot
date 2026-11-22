"""One-off: generate mascot options into Drive/mascot. Delete all but your favourite.

If there are photos of you in Drive/mascot_source (up to 6), the mascot is built from them, one per design, (a blocky cartoon version of you
in your own kit). Otherwise it's made from the outfit description in config.yaml.
Run from GitHub: Actions > "Mascot options" > Run workflow.
"""
import subprocess
from . import drive, fal
from .common import CFG, WORK, load_ledger, save_ledger

# Same character, different takes, so you can pick the style you like.
VARIANTS = ["standing proudly on the board, arms relaxed",
            "mid heelside turn, low and centred, snow spraying",
            "waving at the camera with a big grin",
            "riding towards the camera, slight lean, dynamic pose",
            "chunkier, cuter toy-like proportions",
            "sleeker video-game hero proportions"]
IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def to_png(path):
    out = WORK / (path.stem + "_ref.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1",
                    "-vf", "scale='min(1024,iw)':-2", str(out)], check=True)
    return out


def main():
    led = load_ledger()
    outfit = CFG.get("mascot_outfit", "")
    src = [f for f in drive.list_files("mascot_source") if f["name"].lower().endswith(IMAGE)][:6]
    refs = []
    for f in src:
        try:
            refs.append(to_png(drive.download(f["id"], WORK / f["name"])))
        except Exception as e:
            print(f"Skipping {f['name']} (couldn't read it, use JPG or PNG): {e}")
    print(f"Building mascot from {len(refs)} photo(s) of you" if refs
          else "No photo in mascot_source, using the outfit description")
    for i, v in enumerate(VARIANTS):
        ref = refs[i % len(refs)] if refs else None  # each design uses a different photo
        fal.charge(led, "image"); save_ledger(led)
        if ref:
            prompt = (f"Turn the snowboarder in this photo into an original {CFG['mascot_style']}. "
                      f"Keep their exact outfit and colours: {outfit}. Pose: {v}. Full body on a snowboard on a "
                      "sunny groomed ski slope, centred, helmet and goggles on, friendly, no text, no logos.")
            res = fal.run(CFG["scene_model"], {"prompt": prompt, "image_url": fal.data_uri(ref),
                                               "aspect_ratio": "9:16"})
        else:
            prompt = (f"{CFG['mascot_style']}, wearing {outfit}, {v}, on a snowboard on a sunny groomed ski slope, "
                      "full body visible, centred, friendly, no text, no logos")
            res = fal.run(CFG["image_model"], {"prompt": prompt, "image_size": "portrait_16_9", "num_images": 1})
        p = fal.fetch(fal.first_url(res, "image"), WORK / f"mascot_{i+1}.png")
        drive.upload(p, "mascot", mime="image/png")
        print("uploaded", p.name)


if __name__ == "__main__":
    main()
