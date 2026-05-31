#!/usr/bin/env python3
"""Génère l'icône macOS (.icns) de Nikon Transfer.

Produit un PNG maître 1024×1024 (camera + arcs Wi-Fi sur fond sombre arrondi),
décline les 10 tailles de l'iconset macOS, puis appelle `iconutil` pour
empaqueter le tout en `.icns`.

Usage :
    python assets/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE     = Path(__file__).resolve().parent
ICONSET  = HERE / "icon.iconset"
ICNS     = HERE / "icon.icns"
MASTER   = HERE / "icon_1024.png"

# Tailles requises par macOS pour un .icns complet
# (cf. `man iconutil` — nom = icon_<size>[<@2x>].png).
SIZES: list[tuple[str, int]] = [
    ("icon_16x16.png",        16),
    ("icon_16x16@2x.png",     32),
    ("icon_32x32.png",        32),
    ("icon_32x32@2x.png",     64),
    ("icon_128x128.png",     128),
    ("icon_128x128@2x.png",  256),
    ("icon_256x256.png",     256),
    ("icon_256x256@2x.png",  512),
    ("icon_512x512.png",     512),
    ("icon_512x512@2x.png", 1024),
]


def _draw_master(size: int = 1024) -> Image.Image:
    """Composition : fond arrondi sombre, objectif central, arcs Wi-Fi en haut."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # Fond — carré arrondi style Big Sur (rayon ≈ 22.5 % du côté).
    radius = int(size * 0.225)
    d.rounded_rectangle([(0, 0), (size, size)], radius=radius,
                        fill=(28, 32, 40, 255))

    # Bandeau de lumière en haut (subtil, ~10 % d'alpha qui s'estompe vers le bas).
    # On compose en alpha plutôt que `paste` pour ne pas écraser le fond là où
    # l'overlay a un alpha < 255.
    grad_col = Image.new("L", (1, size))
    for y in range(size):
        grad_col.putpixel((0, y), max(0, int(40 * (1 - y / (size * 0.55)))))
    grad_alpha = grad_col.resize((size, size))
    # Masquer le bandeau au carré arrondi pour ne pas déborder.
    shape_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(shape_mask).rounded_rectangle(
        [(0, 0), (size, size)], radius=radius, fill=255,
    )
    # alpha finale = min(grad_alpha, shape_mask).
    combined = Image.eval(grad_alpha, lambda v: v)
    combined = Image.composite(grad_alpha,
                               Image.new("L", (size, size), 0),
                               shape_mask)
    overlay = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    overlay.putalpha(combined)
    img = Image.alpha_composite(img, overlay)
    d = ImageDraw.Draw(img)

    # ── Objectif (concentriques : barillet → bague → verre → réflexion).
    cx, cy = size // 2, int(size * 0.58)

    barrel_r = int(size * 0.305)
    d.ellipse([cx - barrel_r, cy - barrel_r, cx + barrel_r, cy + barrel_r],
              fill=(90, 95, 105, 255))

    ring_r = int(size * 0.270)
    d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
              fill=(38, 42, 50, 255))

    glass_r = int(size * 0.230)
    d.ellipse([cx - glass_r, cy - glass_r, cx + glass_r, cy + glass_r],
              fill=(15, 18, 25, 255))

    inner_r = int(size * 0.16)
    d.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
              fill=(26, 34, 48, 255))

    # Reflet spéculaire (haut-gauche).
    hr = int(size * 0.058)
    hcx, hcy = cx - int(size * 0.075), cy - int(size * 0.105)
    d.ellipse([hcx - hr, hcy - hr, hcx + hr, hcy + hr],
              fill=(225, 232, 240, 210))

    # ── Arcs Wi-Fi (au-dessus de l'objectif, jaune type Nikon).
    accent = (255, 200, 50, 255)
    arc_cx = cx
    arc_cy = int(size * 0.165)   # origine ; les arcs s'évasent vers le haut

    for r_frac, th_frac in [(0.075, 0.028), (0.135, 0.032), (0.205, 0.036)]:
        rr = int(size * r_frac)
        th = max(1, int(size * th_frac))
        d.arc([arc_cx - rr, arc_cy - rr, arc_cx + rr, arc_cy + rr],
              start=215, end=325, fill=accent, width=th)

    dot_r = int(size * 0.028)
    d.ellipse([arc_cx - dot_r, arc_cy - dot_r,
               arc_cx + dot_r, arc_cy + dot_r], fill=accent)

    return img


def main() -> int:
    if shutil.which("iconutil") is None:
        print("iconutil introuvable — disponible uniquement sur macOS.", file=sys.stderr)
        return 1

    print("→ Rendu PNG 1024×1024 …")
    master = _draw_master(1024)
    master.save(MASTER, "PNG")

    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()

    print("→ Génération des 10 tailles iconset …")
    for name, sz in SIZES:
        master.resize((sz, sz), Image.LANCZOS).save(ICONSET / name, "PNG")

    print("→ iconutil → icon.icns …")
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)],
        check=True,
    )
    shutil.rmtree(ICONSET)
    print(f"✓ {ICNS.relative_to(HERE.parent)} ({ICNS.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
