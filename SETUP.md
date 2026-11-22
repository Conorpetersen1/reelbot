# reelbot setup (about 1–2 hours, once)

What it does every day, on its own:
- **5pm:** it tags any new clips you've dropped in Drive, pulls yesterday's stats, then builds tomorrow's 5 reels
  (3 AI mascot, 2 of your clips) and puts them in **Drive › reelbot › queue**.
- **Next day, hourly:** it posts each reel at its time as a **trial reel with MANUAL graduation**.
  If you deleted a reel from the queue, it gets skipped.
- **Mondays:** it generates about 25 new mascot clips and refreshes your Instagram login.
- **Dashboard:** a web page (GitHub Pages) with followers, views, best topics and a "graduate these" list.

- **Trend inbox:** reels you save to Drive › reelbot › trends get analysed each evening. The bot copies the
  *format* (hook, pacing, text style) into new ideas for your account, makes mascot clips for them straight away,
  and gives them priority for a week.

Your ongoing job (about 10 min a week): skim the queue if you want to, check the dashboard, graduate winners in the
Instagram app, and drop new clips into Drive › reelbot › real.

---

## 1. Instagram (5 min)
1. @conor_sends must be **public** and a **Professional account** (Creator is fine).
2. In the app, go to Settings › Account Status and check you're eligible for recommendations.
3. Post one trial reel by hand to confirm the trial option shows for you.

## 2. Google Drive (15 min)
1. In Drive, create a folder called `reelbot` and open it. The folder ID is the last part of the URL
   (`drive.google.com/drive/folders/<THIS>`). Save it as **GDRIVE_FOLDER_ID**.
2. Go to console.cloud.google.com and create a project called "reelbot".
   - In APIs & Services › Library, enable the **Google Drive API**.
   - On the OAuth consent screen, pick External, give it any app name, and add your Gmail as a test user.
     Then click **Publish app** (to "In production"). This matters, because in Testing mode the login expires after 7 days.
   - Go to Credentials › Create credentials › OAuth client ID. Choose **Web application** and add the redirect URI
     `https://developers.google.com/oauthplayground`. Save the **client ID** and **client secret**.
3. Go to developers.google.com/oauthplayground.
   - Click the gear icon, tick "Use your own OAuth credentials", and paste the ID and secret.
   - In the left box, type `https://www.googleapis.com/auth/drive`, click Authorize, and sign in. Google will say the app is
     unverified. Click Advanced › continue, which is fine because it's your own app.
   - Click "Exchange authorization code for tokens" and copy the **refresh token**.
   - You now have **GOOGLE_CLIENT_ID**, **GOOGLE_CLIENT_SECRET** and **GOOGLE_REFRESH_TOKEN**.

## 3. Instagram API token (20 min, the fiddly bit)
1. On developers.facebook.com, go to My Apps › Create app. Choose the use case for managing content on Instagram
   (it's the Instagram API with Instagram Login).
2. In the app, open **API setup with Instagram login**, then go to **Generate access tokens** › Add account and log in as @conor_sends.
   Approve the permissions, including content publishing and insights.
3. Copy the generated token. That's **IG_ACCESS_TOKEN**. It lasts 60 days, and the bot refreshes it every Monday.
4. The app can stay in development mode, because it only ever posts to your own account.

## 4. AI keys (10 min)
- console.anthropic.com: create an API key (**ANTHROPIC_API_KEY**), add US$10 credit, and set a monthly limit of about US$15.
- fal.ai: create an API key (**FAL_KEY**) and add US$30–50 credit. Expect roughly US$40–50 a month at 25 clips a week.
  Set a spending cap in your billing settings.

## 5. GitHub (15 min)
1. Create a new repository called `reelbot` and upload everything in this folder (keep the `.github` folder).
   - **Public** is simplest: Actions minutes are free and the dashboard can go on GitHub Pages. Nothing secret is in the code.
   - If you'd rather keep it private, the dashboard on Pages needs GitHub Pro (US$4/month).
2. Go to Settings › Secrets and variables › Actions › New repository secret, and add:
   `ANTHROPIC_API_KEY`, `FAL_KEY`, `IG_ACCESS_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REFRESH_TOKEN`, `GDRIVE_FOLDER_ID`, `GH_PAT`.
   - **GH_PAT** lets the bot save the refreshed Instagram token. Create it under GitHub › Settings › Developer settings ›
     Fine-grained tokens, limited to this repo only, with the permission **Secrets: Read and write**.
3. Go to Settings › Actions › General › Workflow permissions and choose **Read and write**.
4. Go to Settings › Pages, set Source to **Deploy from a branch**, choose branch `main` and folder `/docs`.
   That page is your dashboard, so bookmark it on your phone.

## 6. First run (spread over a couple of days)
1. **Mascot:** go to Actions › *Mascot options* › Run workflow. Six designs appear in Drive › reelbot › mascot.
   **Delete all but your favourite.**
2. **AI clips:** go to Actions › *Weekly AI clips* › Run workflow. It takes about 30–60 minutes. Watch a few clips in
   Drive › reelbot › ai_library, and delete any that look wrong (the planner skips deleted clips automatically).
3. **Your clips:** drop 20–30 clips into Drive › reelbot › real, trimmed to the best 5–10 seconds.
   Only use people who are happy to be on your account.
4. **Go:** Actions › *Daily plan* › Run workflow. Tomorrow's 5 reels land in Drive › reelbot › queue. Watch them, and delete
   any you don't like. From then on everything runs on schedule.

## Saving trending reels (about 15 seconds each)
Instagram doesn't let apps read your Saved folder, so the trend inbox is a Drive folder instead:
1. On a reel you like, screen-record it (iPhone: Control Centre › record), or use Share › Download if it's offered.
2. Open the video in Photos › Share › **Drive** › reelbot › **trends**.
3. Optional: rename the file with a note, like `use this for catching edges`. The bot reads the name.
Screenshots work too. Only the format is copied: their footage, audio, wording and name are never used.
Analysed reels move to `trends_done`, and the results show in the dashboard's *Trend inbox* section.

## Changing things later
- **Topics:** edit `content/topics.csv`. Each row is a tip or snow-life idea, and the `|` character separates description lines.
- **Rules the writer must follow** (the APSI manual guardrails): `content/rules.md`.
- **How many reels, posting times, timezone:** `config.yaml`. Switch to `Asia/Tokyo` while you're in Niseko if you want.
- **Background music for AI reels:** put tracks you have the rights to in Drive › reelbot › audio.
- If posting starts failing with a trial reel limit error, drop `reels_per_day` in `config.yaml`.
