#!/usr/bin/env python3
"""Daily Instagram carousel about game development.

Pipeline (each step is a sub-command so the GitHub workflow can put the image
upload between them):

  prepare   pick a topic, have Gemini write 3-7 short slides + a caption,
            render the slides as JPEGs (render.py), write pending_post.json
  publish   check the images are publicly reachable, add the carousel to the
            Instagram channel's Buffer queue, record it in the history

`python carousel_bot.py` (no argument) runs both, which is handy locally.
DRY_RUN=1 only drafts and renders: nothing is saved, uploaded or queued.
"""

import argparse
import calendar
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import feedparser

import render

ROOT = Path(__file__).parent
HISTORY_FILE = ROOT / "posted_history.json"
PENDING_FILE = ROOT / "pending_post.json"
OUT_DIR = ROOT / "out"
BACKGROUNDS_DIR = ROOT / "backgrounds"
LOGO_FILE = ROOT / "assets" / "logo.png"


def env_or(name: str, default: str) -> str:
    """Env var, or the default when it is unset OR empty. GitHub passes an unset
    repo variable as an empty string, which os.environ.get would return as-is."""
    return os.environ.get(name, "").strip() or default


BUFFER_API_URL = "https://api.buffer.com"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = env_or("GEMINI_MODEL", "gemini-3.1-flash-lite")      # free tier: 15 requests/min, 500/day
GEMINI_FALLBACK_MODEL = env_or("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")

CARD_NAME = env_or("CARD_NAME", "the21stCen")
CARD_HANDLE = env_or("CARD_HANDLE", "@the21stCen")
CARD_FOOTER = env_or("CARD_FOOTER", "1") != "0"        # "Follow @handle ..." on the last slide
QUEUE_TARGET = int(env_or("QUEUE_TARGET", "1"))          # own carousels allowed to wait in the queue
MAX_SLIDES = int(env_or("MAX_SLIDES", "12"))
MIN_SLIDES = int(env_or("MIN_SLIDES", "7"))
MIN_WORDS, MAX_WORDS = 5, 15
MAX_AGE_DAYS = float(env_or("MAX_AGE_DAYS", "2"))
MIN_SUMMARY_CHARS = int(env_or("MIN_SUMMARY_CHARS", "120"))
# Instagram's "AI info" label. Off by default: the slide text is written by an AI, the photos are stock-style
# backgrounds. Set the variable INSTAGRAM_AI_LABEL=1 to turn the label on for every post.
INSTAGRAM_AI_LABEL = env_or("INSTAGRAM_AI_LABEL", "0").lower() in ("1", "true", "yes")
INSTAGRAM_POST_TYPE = env_or("INSTAGRAM_POST_TYPE", "post")   # Buffer: "post" (multi-image = carousel)
MEDIA_BRANCH = "media"

# Topic rotation: one per day, so every category comes round about weekly.
CATEGORIES = {
    "learn": "how to start or level up in game development: a practical roadmap step, habit, free resource type, or a common beginner mistake",
    "news": "a recent game development or games-industry news story (use ONLY the story supplied below)",
    "engine_tips": "one practical, well-established tip for a popular game engine (Unity, Unreal, Godot or GameMaker) or for general engine workflow, explained simply",
    "game_design": "one game design principle or lesson: what it is, why it works, and a small example from a generic game",
    "pros_cons": "the advantages and disadvantages of one game development choice (an engine, art style, solo vs team, mobile vs PC, publishing route), balanced",
    "indie_life": "indie developer life: scoping a project, finishing games, marketing a small game, game jams, or launching on Steam or itch.io",
    "career": "game industry careers: skills to learn, building a portfolio, getting a first job, or freelancing",
    "quotes": "an ORIGINAL short, thought-provoking line about making games (never attributed to any real person)",
}
# Suggested slide range per category. The model picks the exact number inside it from how much the
# topic really needs; the hard limits are MIN_SLIDES / MAX_SLIDES.
SLIDE_GUIDE = {
    "learn": (9, 12),
    "news": (7, 9),
    "engine_tips": (8, 10),
    "game_design": (8, 10),
    "pros_cons": (9, 12),
    "indie_life": (8, 11),
    "career": (9, 12),
    "quotes": (7, 8),
}
CATEGORY_ORDER = ["learn", "news", "engine_tips", "game_design", "pros_cons", "indie_life", "career", "quotes"]
PRODUCT_CATEGORIES = set()   # categories that may promote your own game or product (none by default)

PRODUCT_FACTS = ""   # if you add PRODUCT_CATEGORIES, put the only facts the model may use about your project here

FEEDS = [
    "https://www.gamedeveloper.com/rss.xml",
    "https://www.gamesindustry.biz/feed",
    "https://godotengine.org/rss.xml",
    "https://80.lv/feed",
    "https://blog.unity.com/rss",
]
CORE_KEYWORDS = [
    "unity", "unreal", "godot", "gamemaker", "game engine", "indie game", "indie dev", "game dev", "gamedev",
    "game design", "level design", "shader", "pixel art", "game jam", "devlog", "steam", "itch.io",
    "early access", "game developer", "playtest",
]
GENERIC_KEYWORDS = [
    "game development", "studio", "publisher", "launch", "release", "roadmap", "update", "layoffs",
    "funding", "engine", "console", "multiplayer", "mod",
]
MOBILE_RE = re.compile(r"(?!x)x")   # nothing is ranked down for game dev

SYSTEM_PROMPT = """You write Instagram carousel slides for a game development account. \
The audience is aspiring and indie game developers, students and hobbyists.

Rules:
- Use only well-established facts and, when a story is supplied, only that story. Never invent statistics, \
dates, version numbers, sales figures, studio or game names, or quotes. If you are not certain of a number, leave it out.
- Never attribute a quote to a real person. No emoji, no hashtags and no links inside the slides.
- Plain, concrete, practical language. Prefer advice that stays true across engine versions.
- Reply with ONE JSON object and nothing else."""


class FatalError(RuntimeError):
    """A key, model or configuration problem. Stops the run and turns it red."""


class OverloadedError(RuntimeError):
    """Every configured Gemini model kept answering 5xx after all retries."""


class QuotaError(RuntimeError):
    """Gemini free-tier quota is used up; the next run continues."""


class HttpError(RuntimeError):
    def __init__(self, code: int, url: str, detail: str):
        super().__init__(f"HTTP {code} from {url}: {detail}")
        self.code = code
        self.detail = detail


def _annotate(level: str, msg: str) -> None:
    """On GitHub Actions also emit an annotation, which shows on the run page and via the API."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        text = str(msg).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level}::{text[:900]}", flush=True)


def log(msg: str) -> None:
    print(msg, flush=True)
    if str(msg).startswith(("[info]", "[error]")):
        _annotate("notice", msg)


def warn(msg: str) -> None:
    print(f"[warn] {msg}", flush=True)
    _annotate("warning", msg)


# ---------------------------------------------------------------------------
# History and pending state
# ---------------------------------------------------------------------------

def load_history() -> dict:
    data = {}
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("topics", [])
    data.setdefault("links", [])
    data.setdefault("category_index", 0)
    data.setdefault("recent_backgrounds", [])
    data.setdefault("bot_posts", {})
    return data


def save_history(history: dict) -> None:
    history["topics"] = history["topics"][-80:]
    history["links"] = history["links"][-300:]
    history["recent_backgrounds"] = history["recent_backgrounds"][-300:]
    history["bot_posts"] = {k: v[-50:] for k, v in history.get("bot_posts", {}).items()}
    HISTORY_FILE.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def load_pending():
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            warn("pending_post.json is unreadable; discarding it")
            PENDING_FILE.unlink(missing_ok=True)
    return None


def save_pending(pending: dict) -> None:
    PENDING_FILE.write_text(json.dumps(pending, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP, Buffer
# ---------------------------------------------------------------------------

def get_secret(name: str) -> str:
    """Read an env var and strip whitespace (pasted secrets often carry a newline)."""
    return os.environ.get(name, "").strip()


def backoff(attempt: int, why: str, base: int = 5) -> None:
    delay = min(60, base * 2 ** (attempt - 1))
    warn(f"{why}; retrying in {delay}s")
    time.sleep(delay)


def http_post_json(url: str, payload: dict, headers: dict, timeout: int = 45, retries: int = 1,
                   base_delay: int = 5) -> dict:
    """POST JSON, return the parsed body. Credentials go in `headers`, never the URL.
    `retries` is the total number of attempts; only 5xx and network errors are
    retried. Use retries=1 for anything that is not safe to repeat."""
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", "User-Agent": "GameDevCarouselBot/1.0", **headers}
    for attempt in range(1, retries + 1):
        req = Request(url, data=body, headers=all_headers, method="POST")
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except ValueError:
                raise RuntimeError(f"Non-JSON response from {url}: {raw[:200]}") from None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code in (500, 502, 503, 504) and attempt < retries:
                backoff(attempt, f"HTTP {exc.code} from {url}", base_delay)
                continue
            raise HttpError(exc.code, url, detail) from None
        except (TimeoutError, OSError) as exc:
            if attempt < retries:
                backoff(attempt, f"network error calling {url} ({exc})", base_delay)
                continue
            raise RuntimeError(f"Network error calling {url}: {exc}") from None
    raise RuntimeError("unreachable")


CHANNELS_QUERY = """
query Channels($input: ChannelsInput!) {
  channels(input: $input) { id service name isDisconnected isLocked }
}
"""

POSTS_QUERY = """
query WaitingPosts($input: PostsInput!, $first: Int, $after: String) {
  posts(input: $input, first: $first, after: $after) {
    edges { node { id channelId } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status dueAt } }
    ... on MutationError { message }
  }
}
"""


def buffer_request(query: str, variables: dict | None = None, retries: int = 3) -> dict:
    key = get_secret("BUFFER_API_KEY")
    if not key:
        raise FatalError("BUFFER_API_KEY is not set. See .env.example")
    try:
        resp = http_post_json(BUFFER_API_URL, {"query": query, "variables": variables or {}},
                              {"Authorization": f"Bearer {key}"}, retries=retries)
    except HttpError as exc:
        if exc.code in (401, 403):
            raise FatalError(f"Buffer rejected BUFFER_API_KEY (HTTP {exc.code}). Check the secret.") from None
        raise
    if resp.get("errors"):
        msgs = "; ".join(e.get("message", "?") for e in resp["errors"])
        raise RuntimeError(f"Buffer GraphQL error: {msgs}")
    return resp["data"]


def get_org_id() -> str:
    org = get_secret("BUFFER_ORG_ID")
    if org:
        return org
    data = buffer_request("query { account { organizations { id name } } }")
    orgs = data["account"]["organizations"]
    if len(orgs) != 1:
        names = ", ".join(f"{o['name']} ({o['id']})" for o in orgs)
        raise FatalError(f"Account has {len(orgs)} Buffer organizations ({names}); set BUFFER_ORG_ID")
    return orgs[0]["id"]


def resolve_instagram_channel(org_id: str):
    """The connected Instagram channel id, or None when Instagram isn't connected yet."""
    pinned = get_secret("BUFFER_CHANNEL_INSTAGRAM")
    if pinned:
        return pinned
    data = buffer_request(CHANNELS_QUERY, {"input": {"organizationId": org_id}})
    options = [c for c in data["channels"]
               if c["service"] == "instagram" and not c["isDisconnected"] and not c["isLocked"]]
    if not options:
        return None
    if len(options) > 1:
        warn(f"{len(options)} Instagram channels found; using '{options[0]['name']}'. "
             "Set BUFFER_CHANNEL_INSTAGRAM to choose another.")
    return options[0]["id"]


def waiting_own_posts(org_id: str, channel_id: str, own_ids: set) -> int:
    """How many of the bot's OWN carousels are still waiting (queued or draft)."""
    count, after = 0, None
    while True:
        data = buffer_request(POSTS_QUERY, {
            "input": {"organizationId": org_id,
                      "filter": {"channelIds": [channel_id], "status": ["draft", "scheduled"]}},
            "first": 100, "after": after})
        result = data["posts"]
        count += sum(1 for e in result["edges"] or [] if e["node"]["id"] in own_ids)
        if not result["pageInfo"]["hasNextPage"]:
            return count
        after = result["pageInfo"]["endCursor"]


def queue_carousel(channel_id: str, caption: str, image_urls: list, alt_texts: list) -> dict:
    """Add a carousel to the end of the channel's Buffer queue. Never retried:
    a retry after a lost reply could post it twice."""
    data = buffer_request(CREATE_POST_MUTATION, {
        "input": {
            "channelId": channel_id,
            "text": caption,
            "mode": "addToQueue",
            "schedulingType": "automatic",
            "aiAssisted": True,
            "assets": [{"image": {"url": u, "metadata": {"altText": a}}} for u, a in zip(image_urls, alt_texts)],
            "metadata": {"instagram": {"type": INSTAGRAM_POST_TYPE, "shouldShareToFeed": True,
                                       "isAiGenerated": INSTAGRAM_AI_LABEL}},
        }
    }, retries=1)
    result = data["createPost"]
    if result["__typename"] != "PostActionSuccess":
        raise RuntimeError(f"Buffer rejected the carousel ({result['__typename']}): {result.get('message')}")
    return result["post"]


# ---------------------------------------------------------------------------
# News (only used for the "news" category)
# ---------------------------------------------------------------------------

def fetch_news() -> list:
    entries = []
    for url in FEEDS:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (GameDevBot/1.0)"})
            with urlopen(req, timeout=15) as resp:
                raw = resp.read()
            for e in feedparser.parse(raw).entries:
                title = getattr(e, "title", "").strip()
                link = getattr(e, "link", "").strip()
                summary = re.sub("<[^<]+?>", "", getattr(e, "summary", ""))
                if title and link:
                    entries.append({"title": title, "link": link,
                                    "summary": " ".join(summary.split()),
                                    "published": getattr(e, "published_parsed", None)})
        except Exception as exc:
            warn(f"feed failed: {url} ({exc})")
    entries.sort(key=lambda x: x["published"] or (0,), reverse=True)
    return entries


def rank_news(entries: list, used_links: set, now: datetime | None = None) -> list:
    """Fresh, unused stories with enough text. Our own topics count triple;
    mobile-only stories go last. Ties go to the newest (entries arrive newest first)."""
    now = now or datetime.now(timezone.utc)
    scored = []
    for e in entries:
        if e["link"] in used_links or len(e["summary"]) < MIN_SUMMARY_CHARS:
            continue
        if e.get("published"):
            age = (now - datetime.fromtimestamp(calendar.timegm(e["published"]), timezone.utc)).total_seconds() / 86400
            if age > MAX_AGE_DAYS:
                continue
        text = f"{e['title']} {e['summary']}".lower()
        score = 3 * sum(1 for k in CORE_KEYWORDS if k in text) + sum(1 for k in GENERIC_KEYWORDS if k in text)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored.sort(key=lambda x: bool(MOBILE_RE.search(x[1]["title"])))
    return [e for _, e in scored]


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _gemini_call(model: str, prompt: str, key: str) -> str:
    data = http_post_json(
        GEMINI_URL.format(model=model),
        {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
         "contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"}},
        {"x-goog-api-key": key}, timeout=90, retries=4, base_delay=10)
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Unexpected Gemini response: {str(data)[:300]}")


def gemini_generate(prompt: str) -> str:
    key = get_secret("GEMINI_API_KEY")
    if not key:
        raise FatalError("GEMINI_API_KEY is not set")
    models = [GEMINI_MODEL]
    if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
        models.append(GEMINI_FALLBACK_MODEL)
    log(f"[info] Gemini models in use: {', '.join(models)}"
        + ("" if len(models) > 1 else " (no separate fallback model is set; add the GEMINI_FALLBACK_MODEL variable)"))
    for i, model in enumerate(models):
        try:
            if i:
                warn(f"switching to fallback model {model}")
            return _gemini_call(model, prompt, key)
        except HttpError as exc:
            if exc.code == 429:
                # Free-tier quotas are per model, so the fallback model may still have room.
                if i + 1 < len(models):
                    warn(f"{model} quota used up; trying {models[i + 1]}")
                    continue
                raise QuotaError("Gemini quota used up for now") from None
            if exc.code in (400, 401, 403, 404):
                if i:
                    warn(f"fallback model {model} rejected (HTTP {exc.code}); check GEMINI_FALLBACK_MODEL")
                    continue
                raise FatalError(f"Gemini rejected the request (HTTP {exc.code}): {exc.detail}. "
                                 f"Check GEMINI_API_KEY and GEMINI_MODEL ({model}).") from None
            warn(f"{model} still failing after retries (HTTP {exc.code})")
        except RuntimeError as exc:
            if "Unexpected Gemini response" in str(exc):
                raise
            warn(f"{model} still failing after retries: {exc}")
    raise OverloadedError("Gemini is overloaded or unreachable on every configured model")


def parse_json_object(raw: str) -> dict:
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def validate_content(data: dict, recent_topics: list, allow_product: bool):
    """Returns (clean, problem). `clean` = {topic, slides, caption, hashtags}."""
    slides = data.get("slides")
    if not isinstance(slides, list) or not all(isinstance(s, str) for s in slides):
        return None, "slides must be a list of strings"
    slides = [" ".join(s.split()) for s in slides]
    if not MIN_SLIDES <= len(slides) <= MAX_SLIDES:
        return None, f"need {MIN_SLIDES} to {MAX_SLIDES} slides, got {len(slides)}"
    for i, s in enumerate(slides, 1):
        words = len(s.split())
        if not MIN_WORDS <= words <= MAX_WORDS:
            return None, f"slide {i} has {words} words; each slide must have {MIN_WORDS} to {MAX_WORDS}"
        if "#" in s or "http" in s.lower() or "www." in s.lower() or _EMOJI_RE.search(s):
            return None, f"slide {i} contains a hashtag, link or emoji"
    if slides[0].lower().startswith("breaking"):
        return None, 'slide 1 must not start with "Breaking"'

    caption = (data.get("caption") or "").strip()
    if not 60 <= len(caption) <= 1500:
        return None, f"caption must be 60 to 1500 characters, got {len(caption)}"
    if "http" in caption.lower() or "www." in caption.lower() or "#" in caption:
        return None, "caption must not contain links or hashtags (hashtags go in the hashtags list)"
    if not allow_product and re.search(r"link in bio", caption, re.I):
        return None, "this topic must not say 'link in bio'"

    tags = []
    for t in data.get("hashtags") or []:
        t = re.sub(r"[^A-Za-z0-9_]", "", str(t))
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    if not 5 <= len(tags) <= 12:
        return None, f"need 5 to 12 hashtags, got {len(tags)}"

    topic = " ".join(str(data.get("topic") or "").split())
    if not topic or len(topic) > 80:
        return None, "topic must be a short label (1 to 80 characters)"
    if topic.lower() in [t.lower() for t in recent_topics]:
        return None, f'topic "{topic}" was already used recently; pick a different angle'
    return {"topic": topic, "slides": slides, "caption": caption, "hashtags": tags}, None


def build_prompt(category: str, story: dict | None, recent_topics: list, allow_product: bool, feedback: str) -> str:
    parts = [f"Write one Instagram carousel about: {CATEGORIES[category]}."]
    if story:
        parts.append(f"Story (use ONLY these facts):\nHeadline: {story['title']}\nSummary: {story['summary'][:700]}")
    if allow_product:
        parts.append(PRODUCT_FACTS)
    if recent_topics:
        parts.append("Topics already covered recently (do not repeat them):\n- " + "\n- ".join(recent_topics[-25:]))
    lo, hi = SLIDE_GUIDE.get(category, (MIN_SLIDES, MAX_SLIDES))
    lo, hi = max(lo, MIN_SLIDES), min(hi, MAX_SLIDES)
    if lo > hi:
        lo, hi = MIN_SLIDES, MAX_SLIDES
    parts.append(
        f"Slides: you must write between {MIN_SLIDES} and {MAX_SLIDES}; for this topic aim for {lo} to {hi}. "
        "Choose the exact number from how much the topic needs to be delivered clearly: a simple idea gets fewer "
        "slides, a multi-step roadmap or a list of points gets more. Never pad: every slide must add something new "
        "and no two slides may repeat the same point. "
        f"EVERY slide is {MIN_WORDS} to {MAX_WORDS} words, one idea each, written like a short punchy line shown on "
        "a card. Slide 1 is a scroll-stopping hook that creates curiosity. Middle slides give the substance in a "
        "logical order (numbered like '1) ...' when it is a list or steps). The last slide is a takeaway or a "
        "question that invites comments or saves. No emoji, hashtags or links in slides.")
    caption_rule = ("The caption may end with a soft mention of our project and 'Link in bio.' because this topic "
                    "is about what we build. " if allow_product else
                    "Do not say 'link in bio'. ")
    parts.append(
        "Caption: 2 to 4 short sentences that add context the slides do not, ending with a question or a nudge to "
        "save or share. " + caption_rule + "No links and no hashtags in the caption text.")
    parts.append(
        'Return JSON: {"skip": false, "skip_reason": "", "topic": "<short label, max 8 words>", '
        '"slides": ["..."], "caption": "...", "hashtags": ["6 to 10 relevant tags without the # sign"]}. '
        f'If the material is too thin to fill {MIN_SLIDES} slides without padding or inventing facts, return '
        '{"skip": true, "skip_reason": "..."}.')
    if feedback:
        parts.append(f"Your previous answer was rejected: {feedback} Fix that and answer again.")
    return "\n\n".join(parts)


def draft_carousel(category: str, story: dict | None, recent_topics: list):
    """Returns (content, skip_reason). content is None when Gemini skipped or never produced a valid draft."""
    allow_product = category in PRODUCT_CATEGORIES
    feedback = ""
    for attempt in range(1, 4):
        prompt = build_prompt(category, story, recent_topics, allow_product, feedback)
        try:
            parsed = parse_json_object(gemini_generate(prompt))
        except ValueError as exc:
            warn(f"Gemini attempt {attempt} returned unusable JSON: {exc}")
            feedback = "Your reply was not valid JSON. Reply with ONLY the JSON object."
            continue
        if parsed.get("skip") is True:
            return None, str(parsed.get("skip_reason") or "no reason given")
        content, problem = validate_content(parsed, recent_topics, allow_product)
        if content:
            return content, None
        warn(f"draft rejected (attempt {attempt}): {problem}")
        feedback = problem + "."
    return None, None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "carousel"


def build_caption(content: dict) -> str:
    return content["caption"] + "\n\n" + " ".join("#" + t for t in content["hashtags"])


def choose_content(history: dict, rng: random.Random):
    """Walk the category rotation from today's slot until a category yields a draft.
    Returns (category, story, content) or None."""
    start = history["category_index"]
    for step in range(len(CATEGORY_ORDER)):
        category = CATEGORY_ORDER[(start + step) % len(CATEGORY_ORDER)]
        story = None
        if category == "news":
            candidates = rank_news(fetch_news(), set(history["links"]))
            if not candidates:
                log("[info] no fresh news story available; moving to the next category")
                continue
            story = candidates[0]
            log(f"[info] news story: {story['title']}")
        log(f"[info] category: {category}")
        content, skip_reason = draft_carousel(category, story, history["topics"])
        if story and (content or skip_reason is not None):
            history["links"].append(story["link"])        # never retry the same story
        if content:
            history["category_index"] = (CATEGORY_ORDER.index(category) + 1) % len(CATEGORY_ORDER)
            return category, story, content
        if skip_reason is not None:
            log(f"[info] Gemini skipped: {skip_reason}")
        else:
            warn("no valid draft for this category; trying the next one")
    return None


def prepare(dry_run: bool) -> int:
    pending = load_pending()
    if pending and not dry_run:
        if pending.get("publishing"):
            warn("an earlier run started publishing a carousel but did not finish. It may or may not be in "
                 "Buffer. Check the queue; discarding the pending file so nothing posts twice.")
            PENDING_FILE.unlink(missing_ok=True)
        else:
            log(f"[info] a prepared carousel ({pending['id']}) is waiting to be published; not drafting a new one")
            return 0

    history = load_history()
    rng = random.Random()

    if get_secret("BUFFER_API_KEY"):
        org = get_org_id()
        channel = resolve_instagram_channel(org)
        if channel is None:
            warn("no connected Instagram channel in Buffer yet. Connect your Instagram Business/Creator "
                 "account in Buffer (it needs a free channel slot). Nothing drafted this run.")
            return 0
        waiting = waiting_own_posts(org, channel, set(history["bot_posts"].get("instagram", [])))
        log(f"[info] Instagram: {waiting} of the bot's carousels waiting (target {QUEUE_TARGET})")
        if waiting >= QUEUE_TARGET and not dry_run:
            log("[info] the queue already has a carousel; nothing to do")
            return 0
    elif not dry_run:
        raise FatalError("BUFFER_API_KEY is not set. See .env.example")
    else:
        log("[info] no BUFFER_API_KEY: skipping the queue check (dry run)")

    log(f"[info] {datetime.now(timezone.utc).isoformat()} -- drafting")
    try:
        picked = choose_content(history, rng)
    except QuotaError:
        warn("Gemini quota is used up for now. Stopping; the next run continues.")
        return 0
    except OverloadedError as exc:
        warn(f"{exc}. Stopping without posting; the next run tries again.")
        return 0
    if picked is None:
        warn("could not draft a carousel this run")
        return 0
    category, story, content = picked

    photos = render.list_backgrounds(BACKGROUNDS_DIR)
    if not photos:
        warn("backgrounds/ has no photos; using generated gradient backgrounds. Add your images there.")
    chosen = render.pick_backgrounds(photos, len(content["slides"]), history["recent_backgrounds"], rng)

    carousel_id = f"{datetime.now(timezone.utc):%Y-%m-%d}-{slugify(content['topic'])}"
    folder = OUT_DIR / carousel_id
    footer = f"Follow {CARD_HANDLE} for daily game dev tips" if CARD_FOOTER else ""
    seed = rng.randrange(1_000_000)
    files = []
    for i, text in enumerate(content["slides"]):
        path = render.render_slide(text, i, len(content["slides"]), folder / f"slide-{i + 1}.jpg",
                                   background=chosen[i], seed=seed + i, name=CARD_NAME, handle=CARD_HANDLE,
                                   logo=LOGO_FILE if LOGO_FILE.is_file() else None, footer=footer)
        files.append(str(path.relative_to(ROOT)))

    caption = build_caption(content)
    log(f"----- CAROUSEL: {content['topic']} ({category}) -----")
    for i, text in enumerate(content["slides"], 1):
        log(f"slide {i}: {text}")
    log("----- CAPTION -----")
    log(caption)
    log(f"[info] rendered {len(files)} slides in {folder}")

    if dry_run:
        log("[info] DRY_RUN=1 set -- nothing saved, uploaded or queued")
        return 0

    history["topics"].append(content["topic"])
    used = list(dict.fromkeys(p.name for p in chosen if p))
    # Oldest first, newest last, each photo once: this ordering is what lets the picker rotate through them all.
    history["recent_backgrounds"] = [n for n in history["recent_backgrounds"] if n not in used] + used
    save_history(history)
    save_pending({"id": carousel_id, "category": category, "topic": content["topic"], "caption": caption,
                  "slides": content["slides"], "files": files, "created": datetime.now(timezone.utc).isoformat()})
    return 0


def image_urls_for(pending: dict) -> list:
    base = env_or("IMAGES_BASE_URL", "")
    if not base:
        repo = get_secret("GITHUB_REPOSITORY")
        if not repo:
            raise FatalError("Set GITHUB_REPOSITORY (owner/repo) or IMAGES_BASE_URL so image links can be built")
        base = f"https://raw.githubusercontent.com/{repo}/{MEDIA_BRANCH}"
    return [f"{base.rstrip('/')}/{pending['id']}/{Path(f).name}" for f in pending["files"]]


def url_is_image(url: str) -> bool:
    try:
        with urlopen(Request(url, headers={"User-Agent": "GameDevCarouselBot/1.0"}), timeout=20) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            return resp.status == 200 and ctype.startswith("image/")
    except Exception:
        return False


def wait_for_images(urls: list, attempts: int = 8, delay: int = 15) -> bool:
    """raw.githubusercontent.com can lag a little behind a push; Instagram can only use public URLs."""
    pending = list(urls)
    for attempt in range(1, attempts + 1):
        pending = [u for u in pending if not url_is_image(u)]
        if not pending:
            return True
        if attempt < attempts:
            log(f"[info] {len(pending)} image(s) not public yet; checking again in {delay}s")
            time.sleep(delay)
    return False


def publish() -> int:
    pending = load_pending()
    if not pending:
        log("[info] nothing prepared; nothing to publish")
        return 0
    urls = image_urls_for(pending)
    if not wait_for_images(urls):
        raise FatalError("The slide images are not publicly reachable (is the repo public, and did the media "
                         "upload step run?). The prepared carousel is kept; the next run retries.")

    org = get_org_id()
    channel = resolve_instagram_channel(org)
    if channel is None:
        warn("no connected Instagram channel in Buffer; keeping the prepared carousel for the next run")
        return 0

    pending["publishing"] = True      # from here on a retry could double-post, so a crash must not be retried blindly
    save_pending(pending)
    alts = [f"Slide {i}: {t}" for i, t in enumerate(pending["slides"], 1)]
    try:
        post = queue_carousel(channel, pending["caption"], urls, alts)
    except Exception as exc:
        PENDING_FILE.unlink(missing_ok=True)
        raise FatalError(f"Buffer did not accept the carousel: {exc}") from None
    log(f"[ok] queued on Instagram: post id={post['id']} due={post.get('dueAt')}")

    history = load_history()
    history["bot_posts"].setdefault("instagram", []).append(post["id"])
    save_history(history)
    PENDING_FILE.unlink(missing_ok=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", nargs="?", choices=["prepare", "publish", "all"], default="all")
    args = parser.parse_args(argv)
    dry_run = os.environ.get("DRY_RUN") == "1"
    try:
        if args.step in ("prepare", "all"):
            prepare(dry_run)
        if args.step in ("publish", "all") and not dry_run:
            publish()
    except FatalError as exc:
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
