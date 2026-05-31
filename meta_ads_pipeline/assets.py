from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def ensure_sample_assets(base_dir: str) -> list[str]:
    base = Path(base_dir)
    asset_dir = base / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        asset_dir / "spring_launch_hero.png",
        asset_dir / "retargeting_offer.png",
    ]
    _make_card(paths[0], "SPRING", "Launch Collection", (34, 91, 122), (244, 184, 96))
    _make_card(paths[1], "15% OFF", "Return Offer", (90, 79, 207), (255, 221, 87))
    return [str(path) for path in paths]


def _make_card(path: Path, title: str, subtitle: str, bg: tuple[int, int, int], accent: tuple[int, int, int]) -> None:
    if path.exists():
        return
    img = Image.new("RGB", (1200, 628), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle((56, 56, 1144, 572), outline=accent, width=8)
    draw.rectangle((80, 390, 1120, 528), fill=accent)
    try:
        font_large = ImageFont.truetype("Arial.ttf", 132)
        font_medium = ImageFont.truetype("Arial.ttf", 58)
    except OSError:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
    draw.text((92, 170), title, fill=(255, 255, 255), font=font_large)
    draw.text((105, 432), subtitle, fill=(25, 25, 25), font=font_medium)
    img.save(path)
