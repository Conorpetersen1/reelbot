"""Weekly job: refresh the Instagram token (they last 60 days) and save it back into GitHub secrets."""
import os, subprocess
from . import ig
from .common import load_ledger, log, save_ledger


def main():
    led = load_ledger()
    new = ig.refresh_token()
    if new is None:
        print("Token never expires, nothing to refresh"); return
    subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--repo", os.environ["GITHUB_REPOSITORY"]],
                   input=new, text=True, check=True)
    log(led, "Instagram token refreshed")
    save_ledger(led)


if __name__ == "__main__":
    main()
