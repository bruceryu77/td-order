"""Re-crop product images from PDF with price ribbons kept. Preserves products-data.js."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import fitz

from extract_catalog import (
    PDF,
    OUT,
    IMG_DIR,
    ZOOM,
    collect_text_items,
    is_product_image,
    dedupe_images,
    crop_image,
)

BRAND_MAP = {
    "COLOR SILK": "COLORSILK",
    "TRESemme": "TRESEMME",
    "ALIVER OIL": "ALIVER",
    "DOVE MEN+": "DOVE",
    "Page 63": "KRAZY GLUE",
    "Page 64": "OTHERS",
    "Page 65": "OTHERS",
    "LADY SPEEDY STICK": "LADY SPEED STICK",
    "HEAD & SHOULDER": "HEAD & SHOULDERS",
    "VEET GOLD & VSC": "VEET GOLD",
}


def main():
    # Keep current catalog metadata (admin edits, etc.)
    data_path = OUT / "products-data.js"
    raw = data_path.read_text(encoding="utf-8")
    m = re.search(r"window\.PRODUCTS_DATA\s*=\s*(\{.*\})\s*;?\s*$", raw, re.S)
    if not m:
        raise SystemExit("Could not parse products-data.js")
    catalog = json.loads(m.group(1))
    old_by_id = {p["id"]: p for p in catalog.get("products", [])}

    tmp = OUT / "_images_restore"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    doc = fitz.open(PDF)
    restored = 0

    for pi in range(1, len(doc)):
        page = doc[pi]
        text_items = collect_text_items(page)
        raw_imgs = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 1:
                continue
            bbox = tuple(b["bbox"])
            if is_product_image(bbox, page.rect.height):
                raw_imgs.append(bbox)
        imgs = dedupe_images(raw_imgs)

        for idx, bbox in enumerate(imgs):
            pid = f"p{pi:02d}-{idx:02d}"
            old = old_by_id.get(pid)
            if old and old.get("image"):
                fname = Path(old["image"]).name
            else:
                fname = f"{pid}.jpg"
            out_path = tmp / fname
            crop_image(page, bbox, out_path)
            restored += 1
        print(f"page {pi:02d} -> {len(imgs)} images")

    if IMG_DIR.exists():
        # Prefer copy into place (Windows/OneDrive can block folder rename)
        for f in tmp.glob("*.jpg"):
            target = IMG_DIR / f.name
            shutil.copy2(f, target)
        src_names = {f.name for f in tmp.glob("*.jpg")}
        for f in list(IMG_DIR.glob("*.jpg")):
            if f.name not in src_names:
                try:
                    f.unlink()
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        tmp.rename(IMG_DIR)

    print(f"Restored {restored} images with price ribbons into {IMG_DIR}")


if __name__ == "__main__":
    main()
