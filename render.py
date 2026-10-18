"""Draws Instagram carousel slides with Pillow (free, no Canva needed).

Look: a full-bleed background photo, a dark fade towards the bottom, and a
rounded white card carrying the brand name, handle and the slide text, in the
style of "tweet-like" carousel slides. Output is a 1080x1350 (4:5) JPEG, the
only image format Instagram accepts for carousels.
"""

import random
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1350
FONT_DIR = Path(__file__).parent / "fonts"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

CARD_X = 110
CARD_W = W - 2 * CARD_X
PAD = 46
AVATAR = 104
MAX_LINES = 5
SS = 2   # supersampling factor for smooth card edges

INK = (15, 20, 25)
MUTED = (96, 108, 120)

# Dark, moody palettes for the generated fallback background.
PALETTES = [
    ("#050b1a", "#0f3a5f"),
    ("#07110f", "#0e4b45"),
    ("#0b0a1f", "#3b2a7a"),
    ("#10080a", "#5a1f2b"),
    ("#04101c", "#12466e"),
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def list_backgrounds(folder: Path) -> list:
    if not folder.is_dir():
        return []
    exts = set(IMAGE_EXTS)
    if ".avif" in Image.registered_extensions():     # newer Pillow builds can read AVIF
        exts.add(".avif")
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)


def _cover(img: Image.Image) -> Image.Image:
    """Fill the canvas, cropping from slightly above centre so faces stay in frame."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    return ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.3))


def fallback_background(seed: int) -> Image.Image:
    """A deterministic dark gradient with a faint dot grid, used when no photos exist."""
    rng = random.Random(seed)
    c1, c2 = rng.choice(PALETTES)
    mask = Image.linear_gradient("L").rotate(rng.choice([0, 45, 135, 180]), expand=False).resize((W, H))
    img = ImageOps.colorize(mask, black=c1, white=c2).convert("RGB")
    dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    step = 54
    for x in range(step // 2, W, step):
        for y in range(step // 2, H, step):
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 255, 255, 22))
    return Image.alpha_composite(img.convert("RGBA"), dots).convert("RGB")


def _bottom_fade(img: Image.Image) -> Image.Image:
    """Darken towards the bottom (to near black) so the card and text stand out."""
    fade_h = int(H * 0.68)
    grad = Image.linear_gradient("L").resize((W, fade_h))
    grad = grad.point(lambda v: min(255, int(((v / 255) ** 1.35) * 255 * 1.02)))
    mask = Image.new("L", (W, H), 0)
    mask.paste(grad, (0, H - fade_h))
    out = img.copy()
    out.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), mask)
    return out


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_text(text: str, max_w: int):
    """Largest font size (48 down to 34) that keeps the text within MAX_LINES."""
    for size in range(48, 33, -2):
        font = _font("Inter-Medium.otf", size)
        lines = _wrap(text, font, max_w)
        if len(lines) <= MAX_LINES:
            return font, lines, size
    return font, lines, size


def _avatar(size: int, initial: str, logo: Path | None) -> Image.Image:
    s = size * SS
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, s - 1, s - 1), fill=255)
    if logo and logo.is_file():
        face = ImageOps.fit(Image.open(logo).convert("RGBA"), (s, s), method=Image.LANCZOS)
        layer.paste(face, (0, 0), mask)
        ImageDraw.Draw(layer).ellipse((SS, SS, s - 1 - SS, s - 1 - SS), outline=(205, 210, 216, 255), width=SS * 3)
    else:
        d = ImageDraw.Draw(layer)
        d.ellipse((0, 0, s - 1, s - 1), fill=(10, 18, 34, 255))
        d.ellipse((s * 0.06, s * 0.06, s * 0.94, s * 0.94), outline=(46, 160, 255, 255), width=SS * 4)
        f = _font("Inter-Bold.otf", int(s * 0.5))
        bbox = d.textbbox((0, 0), initial, font=f)
        d.text(((s - (bbox[2] - bbox[0])) / 2 - bbox[0], (s - (bbox[3] - bbox[1])) / 2 - bbox[1]),
               initial, font=f, fill=(255, 255, 255, 255))
    return layer.resize((size, size), Image.LANCZOS)


def _arrow_button(size: int) -> Image.Image:
    s = size * SS
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = SS * 4
    d.ellipse((w // 2, w // 2, s - w // 2, s - w // 2), outline=INK + (255,), width=w)
    cy, x0, x1 = s // 2, int(s * 0.26), int(s * 0.74)
    d.line((x0, cy, x1, cy), fill=INK + (255,), width=w)
    head = int(s * 0.17)
    d.line((x1 - head, cy - head, x1, cy), fill=INK + (255,), width=w)
    d.line((x1 - head, cy + head, x1, cy), fill=INK + (255,), width=w)
    return layer.resize((size, size), Image.LANCZOS)


def _card(lines: list, font, size: int, name: str, handle: str, logo: Path | None, arrow: bool) -> Image.Image:
    line_h = int(size * 1.3)
    header_h = AVATAR
    h = PAD + header_h + 30 + line_h * len(lines) + PAD - (line_h - size) // 2
    s = SS
    big = Image.new("RGBA", (CARD_W * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    radius = 40 * s
    d.rounded_rectangle((0, 0, CARD_W * s - 1, h * s - 1), radius=radius, fill=(0, 0, 0, 255))
    d.rounded_rectangle((4 * s, 4 * s, CARD_W * s - 1 - 4 * s, h * s - 1 - 4 * s),
                        radius=radius - 4 * s, fill=(255, 255, 255, 255))
    card = big.resize((CARD_W, h), Image.LANCZOS)

    initial = (name.strip() or "S")[0].upper()
    card.alpha_composite(_avatar(AVATAR, initial, logo), (PAD, PAD))
    d2 = ImageDraw.Draw(card)
    tx = PAD + AVATAR + 24
    d2.text((tx, PAD + 14), name, font=_font("Inter-Bold.otf", 40), fill=INK)
    d2.text((tx, PAD + 14 + 54), handle, font=_font("Inter-Regular.otf", 32), fill=MUTED)
    if arrow:
        btn = _arrow_button(76)
        card.alpha_composite(btn, (CARD_W - PAD - 76, PAD + (AVATAR - 76) // 2))
    y = PAD + header_h + 30
    for line in lines:
        d2.text((PAD, y), line, font=font, fill=INK)
        y += line_h
    return card


def render_slide(text: str, index: int, total: int, out_path: Path, *, background: Path | None = None,
                 seed: int = 0, name: str = "the21stCen", handle: str = "@the21stCen",
                 logo: Path | None = None, footer: str = "") -> Path:
    """Render one slide to `out_path` (JPEG) and return the path."""
    if background is not None:
        base = _cover(Image.open(background))
    else:
        base = fallback_background(seed)
    base = _bottom_fade(base)

    font, lines, size = _fit_text(text, CARD_W - 2 * PAD)
    card = _card(lines, font, size, name, handle, logo, arrow=(index == 0 and total > 1))
    top = min(int(H * 0.60), H - 190 - card.height)
    canvas = base.convert("RGBA")
    canvas.alpha_composite(card, (CARD_X, top))

    if footer and index == total - 1:
        d = ImageDraw.Draw(canvas)
        f = _font("Inter-SemiBold.otf", 34)
        tw = f.getlength(footer)
        d.text(((W - tw) / 2, H - 120), footer, font=f, fill=(255, 255, 255, 235))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=90, optimize=True)
    return out_path


def pick_backgrounds(photos: list, count: int, recent: list, rng: random.Random) -> list:
    """One background per slide, rotating through the whole folder.

    Photos are ranked by how long ago they were used (`recent` is ordered oldest to newest): photos
    never used come first (so newly added ones are used straight away), then the longest-unused.
    The `count` top-ranked photos are used, so a carousel never repeats a photo while the folder has
    enough of them, and every photo gets a turn before any is reused. If there are fewer photos
    than slides, the folder is cycled again, never putting the same photo on adjacent slides.
    Returns Paths, or None entries (fallback) when there are no photos."""
    if not photos:
        return [None] * count
    last_used = {name: i for i, name in enumerate(recent)}      # later index = used more recently
    order = sorted(photos, key=lambda p: (last_used.get(p.name, -1), rng.random()))
    chosen = []
    while len(chosen) < count:
        block = order[:count - len(chosen)]
        rng.shuffle(block)
        if chosen and block[0] == chosen[-1]:
            swap = next((i for i, p in enumerate(block) if p != chosen[-1]), None)
            if swap is not None:
                block[0], block[swap] = block[swap], block[0]
            elif len(order) > 1:
                block = [next(p for p in order if p != chosen[-1])]
        chosen += block
    return chosen
