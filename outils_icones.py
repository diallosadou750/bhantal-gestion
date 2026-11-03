"""Génère les icônes PNG (192 et 512) de l'application avec Pillow (outil de développement, pas nécessaire pour l'utilisation)."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "gestion" / "static"


def draw(size: int) -> Image.Image:
    k = size / 512
    img = Image.new("RGB", (size, size), "#1e5b2b")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([120 * k, 96 * k, 392 * k, 416 * k], radius=28 * k, fill="white")
    for y, w in ((152, 184), (212, 184), (272, 120)):
        d.rounded_rectangle([164 * k, y * k, (164 + w) * k, (y + 22) * k], radius=11 * k, fill="#1e5b2b")
    d.ellipse([(338 - 40) * k, (338 - 40) * k, (338 + 40) * k, (338 + 40) * k], fill="#b3167a")
    d.line([(318 * k, 338 * k), (332 * k, 352 * k), (360 * k, 322 * k)], fill="white", width=int(12 * k), joint="curve")
    return img


for s in (192, 512):
    draw(s).save(OUT / f"icon-{s}.png")
print("ok")
