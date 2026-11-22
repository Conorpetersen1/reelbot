"""One-off: generate mascot options into Drive/mascot. Delete all but your favourite.

Run from GitHub: Actions > "Mascot options" > Run workflow.
"""
from . import drive, fal
from .common import CFG, WORK, load_ledger, save_ledger

VARIANTS = ["red jacket, yellow beanie", "teal jacket, orange beanie", "purple jacket, white beanie",
            "orange jacket, navy beanie", "green jacket, pink beanie", "blue jacket, red beanie"]


def main():
    led = load_ledger()
    for i, v in enumerate(VARIANTS):
        fal.charge(led, "image"); save_ledger(led)
        prompt = (f"{CFG['mascot_style']}, {v}, standing on a snowboard on a sunny groomed ski slope, "
                  "full body visible, centred, friendly expression, no text, no logos")
        res = fal.run(CFG["image_model"], {"prompt": prompt, "image_size": "portrait_16_9", "num_images": 1})
        p = fal.fetch(fal.first_url(res, "image"), WORK / f"mascot_{i+1}.png")
        drive.upload(p, "mascot", mime="image/png")
        print("uploaded", p.name)


if __name__ == "__main__":
    main()
