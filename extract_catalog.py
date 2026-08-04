"""Extract product catalog (images + metadata) from TDANDMORE.pdf."""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import fitz

PDF = Path(r"C:\Users\amorb\OneDrive\Desktop\PROJECT\ORDER\T&D\TDANDMORE.pdf")
OUT = Path(r"C:\Users\amorb\OneDrive\Desktop\PROJECT\ORDER\T&D")
IMG_DIR = OUT / "images"
ZOOM = 2.0  # render scale for crisp crops

PRICE_RE = re.compile(r"^\$?\d+(?:\.\d{2})?$")
SIZE_RE = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)?\s*(?:oz|fl\s*oz|ml|g|ct|pk|pack|pcs?).*"
    r"|\d+\s*x\s*\d+.*|call for special price)$"
)
CODE_RE = re.compile(r"^(?:[A-Z]-?\d{1,4}|\d{1,4}(?:-\d{1,2})?)$", re.I)
PAGE_NUM_RE = re.compile(r"^\d{1,2}$")
SKIP_WORDS = {
    "PRODUCT CATALOG",
    "NATURAL & DAILY ESSENTIALS",
    "CALL FOR SPECIAL PRICE",
    "OTHERS",
}


def center(bbox):
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def overlaps(a, b, iou_thresh=0.35):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return False
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / min(area_a, area_b) >= iou_thresh


def is_product_image(bbox, page_h):
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w < 95 or h < 110:
        return False
    if w > 280 or h > 320:
        return False
    if y0 < 55:  # header deco
        return False
    if y1 > page_h - 55:  # footer / page art
        return False
    if x0 > 470 and y0 > page_h - 200:  # corner logo
        return False
    return True


def dedupe_images(bboxes):
    """Keep largest non-overlapping product frames."""
    scored = sorted(bboxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    kept = []
    for b in scored:
        if any(overlaps(b, k) for k in kept):
            continue
        kept.append(b)
    # reading order: top-to-bottom, left-to-right
    kept.sort(key=lambda b: (round(b[1] / 40), b[0]))
    return kept


def collect_text_items(page):
    items = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            text = "".join(sp["text"] for sp in line.get("spans", [])).strip()
            if not text:
                continue
            items.append({"text": text, "bbox": tuple(line["bbox"])})
    return items


def guess_brand(text_items, page_index, page_h):
    # Prefer large header near top (y < 100) that looks like a brand
    candidates = []
    for t in text_items:
        x0, y0, x1, y1 = t["bbox"]
        txt = t["text"].strip()
        if y0 > 110:
            continue
        if PAGE_NUM_RE.match(txt) or PRICE_RE.match(txt.replace(" ", "")):
            continue
        if txt.upper() in SKIP_WORDS:
            continue
        if len(txt) < 2 or len(txt) > 40:
            continue
        # brand headers are usually short-ish uppercase words
        score = (x1 - x0) * (y1 - y0)
        if txt.isupper() or txt.istitle():
            score *= 1.5
        candidates.append((score, txt))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1].strip()
    # fallback: last non-meta line often is brand on these pages
    bottoms = [
        t["text"].strip()
        for t in text_items
        if t["bbox"][1] > page_h - 80
        and not PAGE_NUM_RE.match(t["text"].strip())
        and t["text"].strip().upper() not in SKIP_WORDS
    ]
    if bottoms:
        return bottoms[-1]
    return f"Page {page_index}"


def classify_line(text: str):
    t = text.strip()
    tu = t.upper()
    if tu in SKIP_WORDS:
        return "skip"
    if PRICE_RE.match(t.replace(" ", "")) or (t.startswith("$") and re.search(r"\d", t)):
        return "price"
    if SIZE_RE.match(t):
        return "size"
    if CODE_RE.match(t) and not re.search(r"[A-Za-z]{3,}", t):
        return "code"
    if PAGE_NUM_RE.match(t) and int(t) <= 66:
        return "page"
    return "name"


def parse_price(text: str):
    m = re.search(r"(\d+(?:\.\d{1,2})?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def associate_meta(img_bbox, text_items):
    ix0, iy0, ix1, iy1 = img_bbox
    icx, icy = center(img_bbox)
    iw = ix1 - ix0

    names, codes, sizes, prices = [], [], [], []

    for t in text_items:
        tx0, ty0, tx1, ty1 = t["bbox"]
        tcx, tcy = center(t["bbox"])
        kind = classify_line(t["text"])
        if kind in ("skip", "page"):
            continue

        # horizontal alignment with product column
        in_col = abs(tcx - icx) <= iw * 0.72
        # price often sits on upper edge of card
        near_price = (iy0 - 30 <= tcy <= iy0 + 55) and in_col
        # name/size/code typically under image
        under = (iy1 - 25 <= ty0 <= iy1 + 130) and in_col
        # code sometimes overlaid on lower image area
        on_lower = (iy1 - 90 <= tcy <= iy1 + 10) and in_col

        if kind == "price" and (near_price or (in_col and abs(tcy - icy) < (iy1 - iy0))):
            prices.append((abs(tcx - icx) + abs(tcy - iy0), t["text"]))
        elif kind == "code" and (under or on_lower):
            codes.append((abs(tcx - icx) + abs(tcy - iy1), t["text"]))
        elif kind == "size" and under:
            sizes.append((abs(tcx - icx) + abs(tcy - iy1), t["text"]))
        elif kind == "name" and under:
            names.append((ty0, tcx, t["text"]))

    names.sort()
    # merge consecutive name fragments
    name_parts = []
    for _, _, txt in names:
        if txt.upper() in SKIP_WORDS:
            continue
        if classify_line(txt) != "name":
            continue
        name_parts.append(txt)

    name = " ".join(name_parts).strip()
    name = re.sub(r"\s+", " ", name)

    code = codes[0][1] if codes else ""
    size = sizes[0][1] if sizes else ""
    price = None
    if prices:
        prices.sort()
        price = parse_price(prices[0][1])

    return name, code, size, price


def crop_image(page, bbox, out_path: Path):
    # Expand upward/right so the red price ribbon is fully included.
    pad_x = 4
    pad_top = 38
    pad_right = 14
    pad_bottom = 4
    x0, y0, x1, y1 = bbox
    rect = fitz.Rect(x0 - pad_x, y0 - pad_top, x1 + pad_right, y1 + pad_bottom) & page.rect
    mat = fitz.Matrix(ZOOM, ZOOM)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
    pix.save(str(out_path))


def main():
    if IMG_DIR.exists():
        shutil.rmtree(IMG_DIR)
    IMG_DIR.mkdir(parents=True)

    doc = fitz.open(PDF)
    products = []
    brand_pages = defaultdict(list)

    for pi in range(len(doc)):
        page = doc[pi]
        if pi == 0:
            continue  # cover

        text_items = collect_text_items(page)
        brand = guess_brand(text_items, pi, page.rect.height)

        raw_imgs = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 1:
                continue
            bbox = tuple(b["bbox"])
            if is_product_image(bbox, page.rect.height):
                raw_imgs.append(bbox)

        imgs = dedupe_images(raw_imgs)
        page_products = []

        for idx, bbox in enumerate(imgs):
            name, code, size, price = associate_meta(bbox, text_items)
            if not name:
                # last resort: any name-like text under image
                name = f"{brand} item {idx + 1}"

            # Prefer brand prefix if name doesn't already start with it
            display = name
            if brand and brand.upper() not in name.upper() and len(name) < 60:
                # keep extracted name as-is; brand is separate field
                pass

            slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"p{pi:02d}-{idx:02d}-{code or name}")[:60].strip("-").lower()
            fname = f"{slug}.jpg"
            out_path = IMG_DIR / fname
            crop_image(page, bbox, out_path)

            product = {
                "id": f"p{pi:02d}-{idx:02d}",
                "name": display,
                "brand": brand,
                "code": code,
                "size": size,
                "price": price,
                "page": pi,
                "image": f"images/{fname}",
            }
            products.append(product)
            page_products.append(product)

        brand_pages[brand].append(pi)
        print(f"page {pi:02d} [{brand}] -> {len(page_products)} products")

    # cleanup weak names that are only size/code leftovers
    for p in products:
        p["name"] = re.sub(r"\s+", " ", p["name"]).strip(" -/")
        if not p["name"]:
            p["name"] = f'{p["brand"]} {p["code"] or p["id"]}'.strip()

    catalog = {
        "title": "T&D and more",
        "source": PDF.name,
        "productCount": len(products),
        "brands": sorted({p["brand"] for p in products}),
        "products": products,
    }
    (OUT / "products.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "products-data.js").write_text(
        "window.PRODUCTS_DATA = " + json.dumps(catalog, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"\nDone: {len(products)} products, {len(catalog['brands'])} brands")
    print(f"Wrote {OUT / 'products.json'}, {OUT / 'products-data.js'} and {IMG_DIR}")


if __name__ == "__main__":
    main()
