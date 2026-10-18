"""Mocked end-to-end tests. Run: python tests/test_carousel.py  (no network, no keys needed)."""
import contextlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import carousel_bot as cb  # noqa: E402
import media  # noqa: E402
import render  # noqa: E402

cb.time.sleep = lambda s: None
IG = "i" * 24
ORG = "o" * 24
REAL = {k: getattr(cb, k) for k in ("gemini_generate", "buffer_request", "url_is_image", "fetch_news", "http_post_json")}


def good(topic="Why scope creep kills indie games", n=8):
    texts = ["Scope creep is the quiet killer of indie games",
             "1) Write your core loop in one sentence before anything else",
             "2) Cut every feature that does not serve that loop",
             "3) Ship a tiny playable version before adding anything new",
             "4) Playtest with strangers early, because friends are too polite",
             "5) Set a hard deadline and cut scope to meet it",
             "6) Keep a parking lot list for ideas that can wait",
             "7) Celebrate finishing small games before starting big ones",
             "8) Review what you cut and what players never missed",
             "9) Treat every new idea as a cost, not a gift",
             "10) Remember that a finished small game beats an unfinished epic",
             "Which feature in your project could you cut today?"][:n]
    return json.dumps({"skip": False, "skip_reason": "", "topic": topic, "slides": texts,
                       "caption": "Most unfinished games die from too many ideas, not too few. "
                                  "Save this for your next project plan. What is the one feature you keep refusing to cut?",
                       "hashtags": ["gamedev", "indiedev", "gamedesign", "unity", "godot", "indiegame"]})


class Env:
    def __init__(self, tmp, gemini, waiting=None, channels="default", news=None, photos=0):
        tmp = Path(tmp)
        cb.HISTORY_FILE = tmp / "h.json"
        cb.PENDING_FILE = tmp / "pending.json"
        cb.OUT_DIR = tmp / "out"
        cb.BACKGROUNDS_DIR = tmp / "bg"
        cb.ROOT = tmp          # pending 'files' are stored relative to ROOT
        (tmp / "bg").mkdir(exist_ok=True)
        for i in range(photos):
            Image.new("RGB", (1600, 1200), (30 * i % 255, 80, 120)).save(tmp / "bg" / f"p{i}.jpg")
        for k in ("GITHUB_REPOSITORY", "IMAGES_BASE_URL", "BUFFER_CHANNEL_INSTAGRAM", "BUFFER_ORG_ID", "DRY_RUN"):
            os.environ.pop(k, None)
        os.environ.update({"BUFFER_API_KEY": "k\n", "GEMINI_API_KEY": "g\n", "GITHUB_REPOSITORY": "the21stCen-social/instagram-carousel-automation"})
        cb.QUEUE_TARGET, cb.MIN_SLIDES, cb.MAX_SLIDES, cb.CARD_FOOTER = 1, 7, 12, True
        self.created, self.prompts, self.checked = [], [], []
        self.waiting = waiting or []
        self.channels = [{"id": IG, "service": "instagram", "name": "gamedevdaily", "isDisconnected": False,
                          "isLocked": False}] if channels == "default" else channels
        self.queue = list(gemini)
        self.news = news or []
        self.reject = None
        self.images_ok = True
        cb.buffer_request = self.buffer
        cb.gemini_generate = self.gemini
        cb.fetch_news = lambda: list(self.news)
        cb.url_is_image = lambda u: (self.checked.append(u), self.images_ok)[1]

    def buffer(self, query, variables=None, retries=3):
        if "organizations" in query:
            return {"account": {"organizations": [{"id": ORG, "name": "My organization"}]}}
        if "Channels(" in query:
            return {"channels": self.channels}
        if "WaitingPosts" in query:
            return {"posts": {"edges": [{"node": n} for n in self.waiting],
                              "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "CreatePost" in query:
            assert retries == 1, "createPost must never be retried"
            if self.reject:
                return {"createPost": {"__typename": self.reject, "message": "nope"}}
            self.created.append(variables["input"])
            return {"createPost": {"__typename": "PostActionSuccess",
                                   "post": {"id": "p" * 23 + str(len(self.created)), "status": "scheduled",
                                            "dueAt": "2026-10-13T04:00:00Z"}}}
        raise AssertionError(query)

    def gemini(self, prompt):
        self.prompts.append(prompt)
        r = self.queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cb.main(list(argv))
        return out.getvalue(), code

    def history(self):
        return json.loads(cb.HISTORY_FILE.read_text()) if cb.HISTORY_FILE.exists() else None


def t(name):
    def deco(fn):
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
        for k, v in REAL.items():
            setattr(cb, k, v)
        print(f"ok  {name}")
    return deco


@t("1 renderer: 1080x1350 JPEG, long text fits, photo and fallback backgrounds both work")
def _(tmp):
    tmp = Path(tmp)
    photo = tmp / "p.jpg"
    Image.new("RGB", (900, 1600), (200, 90, 40)).save(photo)
    words15 = "Beginners finish more games when they scope every project down to one core mechanic"
    for i, (bg, text) in enumerate([(photo, words15), (None, "Short slide with five words"), (photo, "x " * 14 + "end")]):
        p = render.render_slide(text, i, 3, tmp / f"s{i}.jpg", background=bg, seed=i, footer="Follow @x")
        im = Image.open(p)
        assert im.size == (1080, 1350) and im.format == "JPEG" and im.mode == "RGB", (im.size, im.format)
        assert p.stat().st_size < 600_000
    font, lines, size = render._fit_text(words15, render.CARD_W - 2 * render.PAD)
    assert len(lines) <= render.MAX_LINES


@t("2 validation: slide count, words per slide, emoji/links, caption, hashtags, duplicate topic")
def _(tmp):
    base = json.loads(good())

    def check(**changes):
        d = {**base, **changes}
        return cb.validate_content(d, ["Old topic"], allow_product=False)

    assert check()[0]["topic"].startswith("Why scope")
    assert "need 7 to 12" in check(slides=base["slides"][:6])[1]
    assert "need 7 to 12" in check(slides=base["slides"] * 2)[1]
    assert check(slides=json.loads(good(n=7))["slides"])[0]          # 7 is allowed
    assert check(slides=json.loads(good(n=12))["slides"])[0]                        # 12 is allowed
    assert "need 7 to 12" in check(slides=json.loads(good(n=12))["slides"] + ["One more slide than the limit allows"])[1]
    assert "must have 5 to 15" in check(slides=["Too short"] + base["slides"][1:])[1]
    assert "must have 5 to 15" in check(slides=["word " * 16] + base["slides"][1:])[1]
    assert "hashtag, link or emoji" in check(slides=["Check https://x.io for the details today"] + base["slides"][1:])[1]
    assert "hashtag, link or emoji" in check(slides=["Great scoping habits start here \U0001F3AE today"] + base["slides"][1:])[1]
    assert "caption" in check(caption="too short")[1]
    assert "links or hashtags" in check(caption=base["caption"] + " https://x.io")[1]
    assert "must not say 'link in bio'" in check(caption=base["caption"] + " Link in bio.")[1]
    assert cb.validate_content({**base, "caption": base["caption"] + " Link in bio."}, [], True)[0]
    assert "hashtags" in check(hashtags=["a", "b"])[1]
    assert "already used" in check(topic="old TOPIC")[1]
    clean = cb.validate_content({**base, "hashtags": ["#Game Dev", "indiedev", "#indiedev", "a1", "b2", "c3"]}, [], False)[0]
    assert clean["hashtags"] == ["GameDev", "indiedev", "a1", "b2", "c3"], clean["hashtags"]


@t("3 prepare: drafts, renders slides, writes pending + history, advances the category")
def _(tmp):
    e = Env(tmp, [good()] , photos=3)
    e.news = []
    # first category is 'learn' (no news needed)
    out, code = e.run("prepare")
    assert code == 0, out
    pend = json.loads(cb.PENDING_FILE.read_text())
    assert pend["category"] == "learn" and len(pend["files"]) == 8 and len(pend["slides"]) == 8
    for f in pend["files"]:
        assert (Path(tmp) / f).is_file()
    assert pend["caption"].endswith("#gamedev #indiedev #gamedesign #unity #godot #indiegame")
    h = e.history()
    assert h["topics"] == ["Why scope creep kills indie games"] and h["category_index"] == 1
    assert len(h["recent_backgrounds"]) == 3        # 3 photos, each recorded once
    assert "Instagram: 0 of the bot's carousels waiting" in out
    assert len(e.prompts) == 1 and "game development" in e.prompts[0]


@t("4 prepare: an already prepared carousel is not redrafted (no Gemini call)")
def _(tmp):
    e = Env(tmp, [])
    cb.PENDING_FILE.write_text(json.dumps({"id": "2026-10-12-x", "files": [], "slides": [], "caption": "c"}))
    out, code = e.run("prepare")
    assert code == 0 and not e.prompts and "waiting to be published" in out


@t("5 prepare: own carousel already waiting in Buffer -> nothing drafted; hand-made posts don't count")
def _(tmp):
    e = Env(tmp, [good()], waiting=[{"id": "m" * 24, "channelId": IG}])
    e.history()
    cb.HISTORY_FILE.write_text(json.dumps({"bot_posts": {"instagram": ["z" * 24]}}))
    out, code = e.run("prepare")
    assert code == 0 and len(e.prompts) == 1, out            # a manual post waiting does not block the bot
    cb.PENDING_FILE.unlink()
    e2 = Env(tmp, [good("Another topic")], waiting=[{"id": "z" * 24, "channelId": IG}])
    cb.HISTORY_FILE.write_text(json.dumps({"bot_posts": {"instagram": ["z" * 24]}}))
    out, code = e2.run("prepare")
    assert code == 0 and not e2.prompts and "already has a carousel" in out, out


@t("6 prepare: Instagram not connected in Buffer yet -> clear warning, exit 0, no Gemini call")
def _(tmp):
    e = Env(tmp, [good()], channels=[])
    out, code = e.run("prepare")
    assert code == 0 and not e.prompts and "no connected Instagram channel" in out


@t("7 publish: queues one carousel with image assets + instagram metadata; records id; clears pending")
def _(tmp):
    e = Env(tmp, [good()], photos=2)
    e.run("prepare")
    out, code = e.run("publish")
    assert code == 0, out
    assert len(e.created) == 1
    inp = e.created[0]
    assert inp["channelId"] == IG and inp["mode"] == "addToQueue" and inp["schedulingType"] == "automatic"
    assert inp["aiAssisted"] is True and inp["metadata"] == {"instagram": {"type": "post", "shouldShareToFeed": True, "isAiGenerated": False}}
    assert len(inp["assets"]) == 8
    first = inp["assets"][0]["image"]
    assert first["url"].startswith("https://raw.githubusercontent.com/the21stCen-social/instagram-carousel-automation/media/")
    assert first["url"].endswith("/slide-1.jpg") and first["metadata"]["altText"].startswith("Slide 1: ")
    assert inp["text"].startswith("Most unfinished games die")
    assert not cb.PENDING_FILE.exists()
    assert e.history()["bot_posts"]["instagram"] == ["p" * 23 + "1"]
    assert len(e.checked) == 8


@t("8 publish: Buffer rejects it -> exit 1, pending discarded (never retried blindly), nothing recorded")
def _(tmp):
    e = Env(tmp, [good()])
    e.run("prepare")
    e.reject = "LimitReachedError"
    out, code = e.run("publish")
    assert code == 1 and "LimitReachedError" in out, out
    assert not cb.PENDING_FILE.exists() and not e.history()["bot_posts"].get("instagram")


@t("9 publish: images not public yet -> exit 1, pending kept for the next run, nothing queued")
def _(tmp):
    e = Env(tmp, [good()])
    e.run("prepare")
    e.images_ok = False
    out, code = e.run("publish")
    assert code == 1 and "not publicly reachable" in out and not e.created
    assert cb.PENDING_FILE.exists() and not json.loads(cb.PENDING_FILE.read_text()).get("publishing")


@t("10 a crash after publishing started is discarded by the next prepare (no double post)")
def _(tmp):
    e = Env(tmp, [good()])
    cb.PENDING_FILE.write_text(json.dumps({"id": "x", "publishing": True, "files": [], "slides": [], "caption": "c"}))
    out, code = e.run("prepare")
    assert code == 0 and "may or may not be in Buffer" in out and len(e.prompts) == 1, out


@t("11 news category: freshest on-topic story is used and recorded; no news -> next category")
def _(tmp):
    now = datetime.now(timezone.utc)

    def story(title, link, hrs, summary):
        return {"title": title, "link": link, "summary": summary, "published": (now - timedelta(hours=hrs)).timetuple()}

    long = " The studio describes how the change affects developers who ship games on the platform this year."
    stories = [story("Publisher announces a quarterly earnings date", "https://x/weak", 1, "A publisher will report results soon, with a studio update." + long),
               story("Godot releases a new engine update for indie game developers", "https://x/core", 20, "The Godot engine update adds shader tools for indie dev teams." + long),
               story("Old Unity story", "https://x/old", 200, "unity engine update" + long)]
    e = Env(tmp, [good("News topic")], news=stories)
    cb.HISTORY_FILE.write_text(json.dumps({"category_index": 1}))      # next up: news
    out, code = e.run("prepare")
    assert code == 0 and "news story: Godot releases" in out, out
    assert "Headline: Godot releases" in e.prompts[0] and "ONLY these facts" in e.prompts[0]
    h = e.history()
    assert h["links"] == ["https://x/core"] and h["category_index"] == 2
    ranked = cb.rank_news(stories, set(), now)
    assert [s["link"] for s in ranked] == ["https://x/core", "https://x/weak"]   # old dropped, weaker match last
    # no usable news at all: the bot moves on to the next category instead of failing
    cb.PENDING_FILE.unlink()
    e2 = Env(tmp, [good("Vuln topic")], news=[])
    cb.HISTORY_FILE.write_text(json.dumps({"category_index": 1}))
    out, code = e2.run("prepare")
    assert code == 0 and "no fresh news story" in out and "category: engine_tips" in out, out


@t("12 no category may promote a product by default (PRODUCT_CATEGORIES empty); prompts build for every category")
def _(tmp):
    assert not cb.PRODUCT_CATEGORIES
    for cat in cb.CATEGORY_ORDER:
        p = cb.build_prompt(cat, {"title": "T", "summary": "S" * 200} if cat == "news" else None, [], False, "")
        assert "link in bio" not in p.lower().replace("do not say 'link in bio'", ""), cat


@t("13 rejected draft is regenerated with feedback; duplicate topics are refused")
def _(tmp):
    bad = json.loads(good())
    bad["slides"][0] = "Too short"
    e = Env(tmp, [json.dumps(bad), good()])
    out, code = e.run("prepare")
    assert code == 0 and len(e.prompts) == 2 and "rejected" in e.prompts[1] and "slide 1 has 2 words" in e.prompts[1]
    cb.PENDING_FILE.unlink()
    e2 = Env(tmp, [good(), good("A fresh angle")])               # same topic again -> refused, then accepted
    out, code = e2.run("prepare")
    assert code == 0 and "already used" in e2.prompts[1] and e2.history()["topics"][-1] == "A fresh angle"


@t("14 Gemini failures: overloaded/quota -> quiet exit 0 and nothing saved; bad key/model -> exit 1")
def _(tmp):
    for exc, code_expected in [(cb.OverloadedError("busy"), 0), (cb.QuotaError("quota"), 0), (cb.FatalError("bad key"), 1)]:
        e = Env(tmp, [exc, exc, exc, exc, exc, exc, exc, exc])
        out, code = e.run("prepare")
        assert code == code_expected, (exc, out)
        assert not cb.PENDING_FILE.exists() and e.history() is None, out


@t("15 dry run: drafts and renders, but saves no history/pending and publishes nothing")
def _(tmp):
    e = Env(tmp, [good()])
    os.environ["DRY_RUN"] = "1"
    out, code = e.run("all")
    os.environ.pop("DRY_RUN")
    assert code == 0 and "DRY_RUN=1 set" in out and not e.created
    assert not cb.PENDING_FILE.exists() and e.history() is None
    assert len(list((Path(tmp) / "out").rglob("*.jpg"))) == 8


@t("16 gemini fallback model and env_or")
def _(tmp):
    seen = []

    def fake(url, payload, headers, **k):
        model = url.split("/models/")[1].split(":")[0]
        seen.append(model)
        if model == "main":
            raise cb.HttpError(503, url, "busy")
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    cb.http_post_json = fake
    cb.GEMINI_MODEL, cb.GEMINI_FALLBACK_MODEL = "main", "backup"
    os.environ["GEMINI_API_KEY"] = "g"
    with contextlib.redirect_stdout(io.StringIO()):
        assert cb.gemini_generate("p") == "{}"
    assert seen == ["main", "backup"]
    # quota is per model: a 429 on the main model moves on to the fallback; 429 on both stops quietly
    seen.clear()

    def quota_main(url, payload, headers, **k):
        model = url.split("/models/")[1].split(":")[0]
        seen.append(model)
        if model == "main":
            raise cb.HttpError(429, url, "quota")
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    cb.http_post_json = quota_main
    with contextlib.redirect_stdout(io.StringIO()):
        assert cb.gemini_generate("p") == "{}"
    assert seen == ["main", "backup"]

    def quota_all(url, payload, headers, **k):
        raise cb.HttpError(429, url, "quota")
    cb.http_post_json = quota_all
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cb.gemini_generate("p")
        raise AssertionError("expected QuotaError")
    except cb.QuotaError:
        pass
    cb.GEMINI_MODEL, cb.GEMINI_FALLBACK_MODEL = "gemini-3.8-flash", ""
    os.environ["X_EMPTY"] = ""
    assert cb.env_or("X_EMPTY", "d") == "d" and cb.env_or("X_MISSING_VAR", "d") == "d"
    os.environ["X_EMPTY"] = " v "
    assert cb.env_or("X_EMPTY", "d") == "v"


@t("17 background picking: no repeats inside a carousel, whole folder rotates, new photos first, no adjacent repeats")
def _(tmp):
    photos = [Path(f"p{i}.jpg") for i in range(15)]
    for seed in range(50):
        picks = render.pick_backgrounds(photos, 12, [], random.Random(seed))
        assert len(set(picks)) == 12, picks                          # 12 slides, 12 different photos
    # rotation: each carousel uses the longest-unused photos, so every photo gets a turn before any repeats
    recent, seen = [], []
    for day in range(5):
        picks = render.pick_backgrounds(photos, 9, recent, random.Random(day))
        assert len(set(picks)) == 9
        names = [p.name for p in picks]
        recent = [n for n in recent if n not in names] + names
        seen += names
    assert len(set(seen[:18])) == 15, "two carousels (9 + 9 slides) must cover all 15 photos"
    # a photo that was never used (newly added) is always picked before ones used before
    picks = render.pick_backgrounds(photos + [Path("brand_new.jpg")], 3, [p.name for p in photos], random.Random(3))
    assert Path("brand_new.jpg") in picks
    # fewer photos than slides: cycles, still no adjacent repeat
    for seed in range(30):
        two = render.pick_backgrounds(photos[:2], 7, [], random.Random(seed))
        assert len(two) == 7 and all(a != b for a, b in zip(two, two[1:])), two
        five = render.pick_backgrounds(photos[:5], 12, [], random.Random(seed))
        assert len(five) == 12 and all(a != b for a, b in zip(five, five[1:])), five
    assert len(render.pick_backgrounds(photos[:1], 3, [], random.Random(1))) == 3
    assert render.pick_backgrounds([], 4, [], random.Random(1)) == [None] * 4


@t("18 image upload: media branch gets the slides, keeps earlier folders, prunes old ones, stays ONE commit")
def _(tmp):
    tmp = Path(tmp)
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    url = f"file://{remote}"
    src = tmp / "out"
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def make(cid):
        (src / cid).mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), (1, 2, 3)).save(src / cid / "slide-1.jpg")

    def listing():
        return sorted(subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "media"],
                                     capture_output=True, text=True, check=True).stdout.split())

    def commits():
        return int(subprocess.run(["git", "--git-dir", str(remote), "rev-list", "--count", "media"],
                                  capture_output=True, text=True, check=True).stdout)

    make("2026-09-01-old-topic")
    media.publish_media(src, ["2026-09-01-old-topic"], url, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert listing() == ["2026-09-01-old-topic/slide-1.jpg"] and commits() == 1
    make("2026-10-11-recent")
    media.publish_media(src, ["2026-10-11-recent"], url, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert listing() == ["2026-09-01-old-topic/slide-1.jpg", "2026-10-11-recent/slide-1.jpg"] and commits() == 1
    make("2026-10-12-today")
    media.publish_media(src, ["2026-10-12-today"], url, now=now)               # 41 days after the first: pruned
    assert listing() == ["2026-10-11-recent/slide-1.jpg", "2026-10-12-today/slide-1.jpg"] and commits() == 1, listing()
    media.publish_media(src, ["2026-10-12-today"], url, now=now)               # same carousel again is idempotent
    assert listing() == ["2026-10-11-recent/slide-1.jpg", "2026-10-12-today/slide-1.jpg"]


@t("19 media.main needs a token; errors never print the token")
def _(tmp):
    tmp = Path(tmp)
    media.PENDING_FILE = tmp / "none.json"
    assert media.main() == 0
    media.PENDING_FILE = tmp / "p.json"
    media.PENDING_FILE.write_text(json.dumps({"id": "2026-10-12-x", "files": ["out/x.jpg"]}))
    os.environ.pop("GITHUB_TOKEN", None)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert media.main() == 1
    os.environ.update({"GITHUB_TOKEN": "SECRET-TOKEN-123", "GITHUB_REPOSITORY": "no/such-repo-xyz"})
    media.ROOT = tmp
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = media.main()
    assert code == 1 and "SECRET-TOKEN-123" not in err.getvalue(), err.getvalue()
    os.environ.pop("GITHUB_TOKEN")


print("ALL PASSED")
