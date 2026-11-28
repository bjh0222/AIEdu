from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


ROOT = Path(r"D:\桌面\AI Edu 2.0")
PREVIEW = ROOT / "github/apps/web/.splash-preview"
OUT = ROOT / "github/apps/web/public/splash"
SIZE = (2880, 1620)


def fit(path: Path, size: tuple[int, int], centering: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    return ImageOps.fit(Image.open(path).convert("RGB"), size, Image.Resampling.LANCZOS, centering=centering)


def radial(size: tuple[int, int], anchor: tuple[float, float], spread: tuple[float, float], blur: int = 260) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    x, y = int(anchor[0] * width), int(anchor[1] * height)
    rx, ry = int(spread[0] * width), int(spread[1] * height)
    draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def linear(size: tuple[int, int], vertical: bool, start: float, end: float, blur: int = 220) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if vertical:
        for y in range(height):
            value = start + (end - start) * (y / max(1, height - 1))
            draw.line((0, y, width, y), fill=int(255 * value))
    else:
        for x in range(width):
            value = start + (end - start) * (x / max(1, width - 1))
            draw.line((x, 0, x, height), fill=int(255 * value))
    return mask.filter(ImageFilter.GaussianBlur(blur))


def blend_soft(base: Image.Image, layer: Image.Image, mask: Image.Image, opacity: float) -> Image.Image:
    merged = ImageChops.soft_light(base, layer)
    if opacity < 1:
        merged = Image.blend(base, merged, opacity)
    return Image.composite(merged, base, mask)


def finish(image: Image.Image, *, brightness: float = 1.05, color: float = 1.08, contrast: float = 1.06) -> Image.Image:
    image = ImageEnhance.Color(image).enhance(color)
    image = ImageEnhance.Contrast(image).enhance(contrast)
    image = ImageEnhance.Brightness(image).enhance(brightness)
    image = image.filter(ImageFilter.SMOOTH)
    image = image.filter(ImageFilter.UnsharpMask(radius=2.0, percent=64, threshold=4))
    grain = Image.effect_noise(image.size, 12).convert("RGB")
    return ImageChops.soft_light(image, grain)


def make_sky(sources: dict[str, Image.Image]) -> Image.Image:
    image = sources["blue"]
    image = blend_soft(image, sources["sky_photo"], radial(SIZE, (0.24, 0.28), (0.50, 0.48)), 0.56)
    image = blend_soft(image, sources["ink"], radial(SIZE, (0.76, 0.30), (0.44, 0.42)), 0.36)
    image = blend_soft(image, sources["water"], linear(SIZE, True, 0.42, 0.92), 0.16)
    return finish(image)


def make_green(sources: dict[str, Image.Image]) -> Image.Image:
    image = sources["rich"]
    image = blend_soft(image, sources["ink"], radial(SIZE, (0.24, 0.28), (0.48, 0.46)), 0.48)
    image = blend_soft(image, sources["water"], linear(SIZE, True, 0.34, 0.92), 0.18)
    return finish(image)


def make_distance(sources: dict[str, Image.Image]) -> Image.Image:
    image = ImageEnhance.Brightness(sources["boat"]).enhance(0.92)
    image = blend_soft(image, sources["ink"], radial(SIZE, (0.22, 0.30), (0.46, 0.44)), 0.34)
    image = blend_soft(image, sources["blue"], radial(SIZE, (0.82, 0.24), (0.44, 0.42)), 0.22)
    image = blend_soft(image, sources["rich"], linear(SIZE, True, 0.18, 0.62), 0.12)
    return finish(image, brightness=0.96, color=1.02, contrast=1.02)


def main() -> None:
    sources = {
        "ink": fit(PREVIEW / "ref-5.png", SIZE),
        "blue": fit(PREVIEW / "ref-6.png", SIZE),
        "rich": fit(PREVIEW / "ref-7.png", SIZE, (0.56, 0.56)),
        "sky_photo": fit(PREVIEW / "ref-3.png", SIZE),
        "water": fit(PREVIEW / "ref-1.png", SIZE),
        "boat": fit(PREVIEW / "ref-4.png", SIZE, (0.54, 0.48)),
    }
    images = {
        "sky": make_sky(sources),
        "green": make_green(sources),
        "distance": make_distance(sources),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, image in images.items():
        path = OUT / f"{name}.webp"
        image.save(path, "WEBP", quality=82, method=6)
        print(path)


if __name__ == "__main__":
    main()
