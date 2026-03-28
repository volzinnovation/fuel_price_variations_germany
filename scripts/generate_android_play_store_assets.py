#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "play-store" / "android"
ASSET_DIR = OUTPUT_DIR / "assets"
METADATA_DIR = OUTPUT_DIR / "metadata" / "de-DE"
ICON_SOURCE = (
    ROOT
    / "iphone"
    / "Tankzeit"
    / "Resources"
    / "Assets.xcassets"
    / "AppIcon.appiconset"
    / "icon-1024.png"
)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def generate_icon() -> Path:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ICON_SOURCE).convert("RGBA")
    icon = icon.resize((512, 512), Image.Resampling.LANCZOS)
    out_path = ASSET_DIR / "app-icon-512.png"
    icon.save(out_path, optimize=True)
    return out_path


def generate_feature_graphic() -> Path:
    feature_size = (1024, 500)
    canvas = Image.new("RGBA", feature_size, "#0f766e")
    overlay = Image.new("RGBA", feature_size, 0)
    draw = ImageDraw.Draw(overlay)
    draw.ellipse((-120, -160, 520, 420), fill="#14b8a6")
    draw.ellipse((640, 80, 1220, 620), fill="#f59e0b")
    draw.rounded_rectangle((48, 56, 520, 444), radius=40, fill="#f8f4ec")
    canvas.alpha_composite(overlay)

    draw = ImageDraw.Draw(canvas)
    title_font = load_font(62, bold=True)
    subtitle_font = load_font(28)
    chip_font = load_font(22, bold=True)

    draw.text((84, 92), "Tankzeit", font=title_font, fill="#11312e")
    draw.text((84, 170), "Die bessere Zeit zum Tanken", font=subtitle_font, fill="#204240")
    draw.text(
        (84, 214),
        "Historische Preisprofile, nahe Tankstellen\nund Marktstatistik für Deutschland.",
        font=subtitle_font,
        fill="#204240",
        spacing=8,
    )

    chips = ["Diesel", "E10", "Favoriten", "Statistik"]
    x = 278
    y = 312
    for chip in chips:
        bbox = draw.textbbox((0, 0), chip, font=chip_font)
        width = bbox[2] - bbox[0] + 36
        draw.rounded_rectangle((x, y, x + width, y + 42), radius=20, fill="#d7f3ef")
        draw.text((x + 18, y + 9), chip, font=chip_font, fill="#0f766e")
        x += width + 12

    icon = Image.open(ICON_SOURCE).convert("RGBA").resize((180, 180), Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", (200, 200), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((10, 14, 188, 192), radius=44, fill=(0, 0, 0, 88))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.alpha_composite(shadow, (72, 292))
    canvas.alpha_composite(icon, (82, 282))

    chart_card = Image.new("RGBA", (360, 240), "#ffffff")
    card_draw = ImageDraw.Draw(chart_card)
    card_draw.rounded_rectangle((0, 0, 360, 240), radius=28, fill="#ffffff", outline="#d1d5db")
    card_draw.text((26, 22), "Heute meist guenstig", font=load_font(20, bold=True), fill="#111827")
    points = [(32, 170), (86, 156), (140, 126), (194, 146), (248, 106), (302, 86)]
    card_draw.line(points, fill="#0f766e", width=8, joint="curve")
    for x0, y0 in points:
        card_draw.ellipse((x0 - 6, y0 - 6, x0 + 6, y0 + 6), fill="#f59e0b")
    card_draw.text((28, 190), "Preisprofil fuer Diesel / E10", font=load_font(18), fill="#4b5563")
    chart_shadow = Image.new("RGBA", (390, 270), 0)
    ImageDraw.Draw(chart_shadow).rounded_rectangle((14, 14, 374, 254), radius=30, fill=(0, 0, 0, 86))
    chart_shadow = chart_shadow.filter(ImageFilter.GaussianBlur(16))
    canvas.alpha_composite(chart_shadow, (598, 122))
    canvas.alpha_composite(chart_card, (612, 136))

    out_path = ASSET_DIR / "feature-graphic-1024x500.png"
    canvas.convert("RGB").save(out_path, optimize=True)
    return out_path


def write_metadata() -> None:
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    short_description = "Beste Tankzeiten fuer Diesel und E10 in Deutschland finden"
    full_description = """Tankzeit hilft dir dabei, in Deutschland bessere Tankzeitfenster zu finden. Die App kombiniert den veröffentlichten Tankerkönig-Datenbestand mit historischen Tagesprofilen und zeigt dir, wann Diesel oder E10 an einer Tankstelle typischerweise günstiger sind.

Mit Tankzeit kannst du:
- offene Tankstellen in deiner Nähe für Diesel und E10 durchsuchen
- historische Preisprofile pro Station ansehen
- Favoriten lokal auf deinem Gerät speichern
- Marktstatistiken für Diesel, E10 und E5 abrufen
- deinen Standort optional nutzen, um nahe Tankstellen schneller zu laden

Tankzeit ist bewusst schlank:
- kein Nutzerkonto
- keine Werbung
- keine In-App-Käufe

Wenn du deinen Standort freigibst, wird er genutzt, um Tankstellen in deiner Nähe zu sortieren. Favoriten bleiben lokal auf deinem Gerät."""

    (METADATA_DIR / "short-description.txt").write_text(short_description + "\n", encoding="utf-8")
    (METADATA_DIR / "full-description.txt").write_text(full_description + "\n", encoding="utf-8")


def main() -> None:
    generate_icon()
    generate_feature_graphic()
    write_metadata()
    print(f"Generated Play Store assets under {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
