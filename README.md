# gamedev-carousel-bot

Posts one game development carousel a day to Instagram, at no cost. New here? Follow [SETUP.md](SETUP.md).

Each day a GitHub Action picks the next topic, Gemini writes 7–12 short slides (5–15 words each; it picks the count from how much the topic needs) and a caption, Pillow draws each slide as a white rounded "tweet-style" card over one of your background photos, and the finished carousel goes into your Buffer queue for Instagram.

Topic rotation (one per day): how to start in game dev, game dev news, engine tips (Unity, Unreal, Godot, GameMaker), game design lessons, pros and cons of a dev choice, indie life, careers, original quotes. News days use a real, fresh story from the RSS feeds and nothing invented.

## One-time setup

1. **Repository must be public.** Slides are served from the `media` branch via `raw.githubusercontent.com`, which Buffer and Instagram must be able to download.
2. **Instagram.** Use a Business or Creator account and connect it as a channel in Buffer. Free Buffer plans allow 3 channels; check that carousels are allowed on your plan.
3. **Secrets** (Settings → Secrets and variables → Actions → Secrets):
   - `BUFFER_API_KEY`
   - `GEMINI_API_KEY`
4. **Variables** (same page, Variables tab; all optional):
   - `CARD_NAME` (default `the21stCen`)
   - `CARD_HANDLE` (default `@the21stCen`)
   - `CARD_FOOTER` (shown on the last slide, e.g. `Follow @yourhandle for daily game dev tips`)
   - `GEMINI_MODEL` (default `gemini-3.1-flash-lite`), `GEMINI_FALLBACK_MODEL` (default `gemini-3.8-flash`)
   - `INSTAGRAM_POST_TYPE` (default `post`; if Buffer refuses multi-image posts, set `carousel`)
   - `INSTAGRAM_AI_LABEL` (default `0`; `1` marks every post with Instagram's "AI info" label)
   - `BUFFER_CHANNEL_INSTAGRAM` (only if you have several Instagram channels)
5. **Photos.** Put your background images (JPG/PNG/WebP) in `backgrounds/`. Without them the bot uses plain dark gradients. Recently used photos are avoided and no photo repeats on adjacent slides. Optionally add `assets/logo.png` as the round avatar on the card.
6. **Workflow permissions.** Settings → Actions → General → Workflow permissions → *Read and write* (the bot pushes the `media` branch and commits its history file).

## Running

- **Dry run first:** Actions → *Daily Instagram carousel* → Run workflow (`dry_run` = 1). It drafts and renders but queues nothing; download the `carousel-preview` artifact to check the slides.
- **Live:** run it with `dry_run` = 0, or let the daily schedule (04:00 UTC, 09:30 IST) do it. Scheduled runs are always live.

The bot queues only one carousel at a time (`QUEUE_TARGET`, default 1); if one is still waiting in Buffer it drafts nothing.

## Safety behaviour

- `createPost` is never retried, so a failure can't double-post. A crash after publishing started is discarded on the next run.
- If the images aren't reachable yet, nothing is queued and the next run tries again.
- Gemini overload or quota errors end the run quietly; a bad key or model name fails loudly.
- Old image folders on the `media` branch are pruned after 30 days; the branch is always a single commit.

## Local use

```
pip install -r requirements.txt
cp .env.example .env     # fill in keys
DRY_RUN=1 python carousel_bot.py prepare
python tests/test_carousel.py   # no network or keys needed
```

## Caveats

- Instagram captions can't contain clickable links. By default no topic promotes a product; to promote your own game, add its category to `PRODUCT_CATEGORIES` and your facts to `PRODUCT_FACTS` in `carousel_bot.py`.
- The news feed list in `FEEDS` was not tested before delivery; a feed that fails only logs a warning. Edit the list freely.
- Whether Buffer's free plan publishes Instagram carousels, and whether it treats `type: post` with several images as a carousel, is unverified. Check the first dry run and first live post.
- The Gemini free tier has a daily request cap shared by everything using the same key.
- Fonts: Inter, SIL Open Font License (see `fonts/LICENSE-Inter.txt`).
