"""Extract product catalog (images + metadata) from TDANDMORE.pdf."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import fitz

PDF = Path(r"C:\Users\amorb\Downloads\TDMORE.pdf")
OUT = Path(r"C:\Users\amorb\OneDrive\Desktop\PROJECT\ORDER\T&D")
IMG_DIR = OUT / "images"
ZOOM = 2.0  # render scale for crisp crops

# Dollar amount or a decimal price. Bare integers are product codes (70, 121, 150).
PRICE_RE = re.compile(r"^(?:\$\d+(?:\.\d{1,2})?|\d+\.\d{2})$")
SIZE_RE = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)?\s*(?:oz|fl\s*oz|ml|g|ct|pk|pack|pcs?).*"
    r"|\d+\s*x\s*\d+.*|call for special price)$"
)
# B-1, NU-11, NU-11R, NU-BR2, H1, 70, 70-4, 197-11
CODE_RE = re.compile(
    r"^(?:[A-Z]{1,4}(?:-[A-Z0-9]{1,5})+|[A-Z]\d{1,4}|\d{1,4}(?:-\d{1,2})?)$",
    re.I,
)
PAGE_NUM_RE = re.compile(r"^\d{1,2}$")
SKIP_WORDS = {
    "PRODUCT CATALOG",
    "NATURAL & DAILY ESSENTIALS",
    "CALL FOR SPECIAL PRICE",
    "OUT",
    "OF",
    "STOCK",
    "OUT OF",
    "OUT OF STOCK",
}
BRAND_MAP = {
    "COLOR SILK": "COLORSILK",
    "TRESEMME": "TRESEMME",
    "TRESemme": "TRESEMME",
    "ALIVER OIL": "ALIVER",
    "DOVE MEN+": "DOVE",
    "LADY SPEEDY STICK": "LADY SPEED STICK",
    "HEAD & SHOULDER": "HEAD & SHOULDERS",
    "VEET GOLD & VSC": "VEET GOLD",
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
    if CODE_RE.match(t):
        return "code"
    if PAGE_NUM_RE.match(t) and int(t) <= 80:
        return "page"
    return "name"


def parse_price(text: str):
    m = re.search(r"(\d+(?:\.\d{1,2})?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def is_out_of_stock(img_bbox, text_items):
    ix0, iy0, ix1, iy1 = img_bbox
    for t in text_items:
        if t["text"].strip().upper() != "STOCK":
            continue
        tx0, ty0, tx1, ty1 = t["bbox"]
        tcx, tcy = center(t["bbox"])
        if ix0 - 20 <= tcx <= ix1 + 20 and iy0 - 8 <= tcy <= iy1 + 18:
            return True
    return False


def associate_meta(img_bbox, text_items, page_h):
    ix0, iy0, ix1, iy1 = img_bbox
    icx, icy = center(img_bbox)
    iw = ix1 - ix0

    names, codes, sizes, prices = [], [], [], []

    for t in text_items:
        tx0, ty0, tx1, ty1 = t["bbox"]
        if ty0 > page_h - 48:
            continue
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


def _norm_code(code: str) -> str:
    return (code or "").strip().upper()


def _load_old_products():
    path = OUT / "products.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("products") or []


def _alloc_id(page, idx, used):
    base = f"p{page:02d}-{idx:02d}"
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def main():
    old_products = _load_old_products()
    by_code = defaultdict(list)
    for p in old_products:
        code = _norm_code(p.get("code"))
        if code:
            by_code[code].append(p)

    doc = fitz.open(PDF)
    drafts = []

    for pi in range(len(doc)):
        page = doc[pi]
        if pi == 0:
            continue  # cover

        text_items = collect_text_items(page)
        brand = guess_brand(text_items, pi, page.rect.height)
        brand = BRAND_MAP.get(brand, brand)

        raw_imgs = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 1:
                continue
            bbox = tuple(b["bbox"])
            if is_product_image(bbox, page.rect.height):
                raw_imgs.append(bbox)

        imgs = dedupe_images(raw_imgs)
        page_count = 0

        for idx, bbox in enumerate(imgs):
            name, code, size, price = associate_meta(bbox, text_items, page.rect.height)
            if not name:
                name = f"{brand} item {idx + 1}"
            name = re.sub(r"\s+", " ", name).strip(" -/")
            drafts.append({
                "name": name,
                "brand": brand,
                "code": code,
                "size": size,
                "price": price,
                "page": pi,
                "outOfStock": is_out_of_stock(bbox, text_items),
                "_bbox": bbox,
                "_page_index": pi,
                "_idx": idx,
            })
            page_count += 1

        print(f"page {pi:02d} [{brand}] -> {page_count} products")

    used_ids = set()
    matched = []
    for d in drafts:
        code = _norm_code(d.get("code"))
        old = by_code[code].pop(0) if code and by_code.get(code) else None
        if old and old.get("brand") and d.get("brand"):
            if old["brand"].strip().upper() != d["brand"].strip().upper():
                old = None
        if old:
            d["id"] = old["id"]
            d["image"] = old.get("image") or ""
            used_ids.add(d["id"])
            matched.append(code)
        else:
            d["id"] = ""
            d["image"] = ""

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    products = []
    for d in drafts:
        if not d["id"]:
            d["id"] = _alloc_id(d["_page_index"], d["_idx"], used_ids)
            used_ids.add(d["id"])
        if not d["image"]:
            slug = re.sub(
                r"[^a-zA-Z0-9]+",
                "-",
                f'{d["id"]}-{d["code"] or d["name"]}',
            )[:60].strip("-").lower()
            d["image"] = f"images/{slug}.jpg"
        page = doc[d["_page_index"]]
        crop_image(page, d["_bbox"], IMG_DIR / Path(d["image"]).name)
        if not d["name"]:
            d["name"] = f'{d["brand"]} {d["code"] or d["id"]}'.strip()
        products.append({
            "id": d["id"],
            "name": d["name"],
            "brand": d["brand"],
            "code": d["code"],
            "size": d["size"],
            "price": d["price"],
            "page": d["page"],
            "image": d["image"].replace("\\", "/"),
            "outOfStock": d["outOfStock"],
        })

    old_by_code = {}
    for p in old_products:
        code = _norm_code(p.get("code"))
        if code and code not in old_by_code:
            old_by_code[code] = p
    new_codes = {_norm_code(p["code"]) for p in products if _norm_code(p["code"])}
    old_codes = set(old_by_code)
    added = sorted(new_codes - old_codes)
    removed = sorted(old_codes - new_codes)
    price_changes = []
    for p in products:
        code = _norm_code(p["code"])
        old = old_by_code.get(code)
        if not old:
            continue
        if old.get("price") != p.get("price"):
            price_changes.append((code, old.get("price"), p.get("price"), p["name"]))

    kept_images = {Path(p["image"]).name for p in products}
    removed_images = []
    for p in old_products:
        image = p.get("image") or ""
        name = Path(image).name
        if not name or name in kept_images:
            continue
        path = IMG_DIR / name
        if path.exists():
            path.unlink()
            removed_images.append(name)

    missing = [p for p in products if not p["code"] or p["price"] is None]
    dupes = []
    seen = defaultdict(int)
    for p in products:
        if p["code"]:
            seen[p["code"].upper()] += 1
    dupes = sorted(c for c, n in seen.items() if n > 1)

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

    report = {
        "oldCount": len(old_products),
        "newCount": len(products),
        "added": [
            {"code": c, "name": next(p["name"] for p in products if _norm_code(p["code"]) == c),
             "price": next(p["price"] for p in products if _norm_code(p["code"]) == c),
             "brand": next(p["brand"] for p in products if _norm_code(p["code"]) == c)}
            for c in added
        ],
        "removed": [
            {"code": c, "name": old_by_code[c]["name"], "brand": old_by_code[c]["brand"]}
            for c in removed
        ],
        "priceChanges": [
            {"code": c, "old": a, "new": b, "name": n} for c, a, b, n in price_changes
        ],
        "outOfStock": [
            {"code": p["code"], "name": p["name"]} for p in products if p["outOfStock"]
        ],
        "missing": [
            {"id": p["id"], "code": p["code"], "name": p["name"], "price": p["price"], "page": p["page"]}
            for p in missing
        ],
        "duplicateCodes": dupes,
        "removedImages": len(removed_images),
    }
    (OUT / "_catalog_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone: {len(old_products)} -> {len(products)} products")
    print(f"added {len(added)} removed {len(removed)} price changes {len(price_changes)}")
    print(f"out of stock {sum(1 for p in products if p['outOfStock'])} missing meta {len(missing)} dupes {dupes}")


if __name__ == "__main__":
    main()
