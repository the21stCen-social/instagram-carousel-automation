# Setup guide (about 20 minutes, no coding)

You need: a GitHub account (you have one), an Instagram **Business or Creator** account, and a Google account. Everything below is free.

## 1. Get a Gemini API key (the AI that writes the slides)
1. Go to https://aistudio.google.com/apikey and sign in with Google.
2. Click **Create API key**, then copy it. Keep it private.

## 2. Set up Buffer (it posts to Instagram for you)
1. Create a free account at https://buffer.com.
2. **Connect your Instagram** (Channels → Connect a channel → Instagram). It must be a Business or Creator account linked to a Facebook Page.
3. Create an API key at https://publish.buffer.com/settings/api and copy it.
4. In Buffer, open your Instagram channel's **posting schedule** and choose the time of day you want the carousel to go out. The bot only adds posts to the queue; Buffer publishes at those times.

## 3. Add the keys to this repository
Repository → **Settings** → **Secrets and variables** → **Actions**.
- **Secrets** tab → *New repository secret*:
  - `BUFFER_API_KEY` = your Buffer key
  - `GEMINI_API_KEY` = your Gemini key
- **Variables** tab → *New repository variable*:
  - `CARD_NAME` = the name shown on each slide (default `the21stCen`)
  - `CARD_HANDLE` = the handle you want on the slides (default `@the21stCen`, the X handle)

## 4. Let the bot save its images and history
Repository → **Settings** → **Actions** → **General** → **Workflow permissions** → choose **Read and write permissions** → Save.

Also make sure the repository is **Public** (Settings → General → Danger zone → Change visibility). Instagram has to download the slide images from GitHub.

## 5. Add your background photos (recommended)
Upload 5–20 of your own pictures (game screenshots, concept art, wallpapers you own the rights to) into the `backgrounds/` folder (Add file → Upload files). Without photos the bot uses plain dark gradients. If your photos are in Canva: open the folder, select the images, **Share → Download** (JPG), then upload the files here. The bot cannot read Canva directly; it only uses what is in this folder. You can also upload a square logo as `assets/logo.png`; it appears as a round avatar on each slide.

## 6. Test it, then go live
1. **Actions** tab → *Daily Instagram carousel* → **Run workflow**, set `dry_run` to `1`. If you get a message to enable workflows, click the green button.
2. When it finishes, open the run and download the `carousel-preview` file to see the slides. Nothing is posted in a dry run.
3. Run it again with `dry_run` = `0`. Check Buffer: one carousel should be in your Instagram queue.
4. From then on it runs by itself every day at 04:00 UTC (09:30 IST). Change the time in `.github/workflows/carousel.yml` if you like.

## Good to know
- The bot keeps **one** carousel waiting in the queue at a time.
- If Gemini is busy or out of free requests, the run quietly ends and tries again tomorrow.
- Change the topics in `carousel_bot.py` (`CATEGORIES` and `SYSTEM_PROMPT`) if you want a different angle.
- Instagram can add an "AI info" label; the bot leaves it off by default. Set the variable `INSTAGRAM_AI_LABEL` = `1` to turn it on.
