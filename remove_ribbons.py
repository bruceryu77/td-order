"""Remove top-right red price ribbons from catalog product images."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

IMG_DIR = Path(r"C:\Users\amorb\OneDrive\Desktop\PROJECT\ORDER\T&D\images")


def is_cream(r, g, b):
    return r >= 220 and g >= 215 and b >= 200 and abs(int(r) - int(g)) < 25


def sample_bg(arr: np.ndarray) -> tuple[int, int, int]:
    h, w, _ = arr.shape
    samples = []
    for y, x in [
        (2, 2), (2, 8), (8, 2),
        (2, w // 2), (h // 3, 4), (4, w // 3),
    ]:
        if 0 <= y < h and 0 <= x < w:
            samples.append(arr[y, x])
    cream = [tuple(map(int, p)) for p in samples if is_cream(*p)]
    if cream:
        return cream[0]
    light = [p for p in samples if int(p[0]) + int(p[1]) + int(p[2]) > 600]
    if light:
        m = np.mean(light, axis=0)
        return tuple(int(v) for v in m)
    return (247, 246, 242)


def dilate(mask: np.ndarray, iterations: int = 3) -> np.ndarray:
    out = mask.copy()
    h, w = mask.shape
    for _ in range(iterations):
        nxt = out.copy()
        # 4-neighborhood expand via shifts
        nxt[1:, :] |= out[:-1, :]
        nxt[:-1, :] |= out[1:, :]
        nxt[:, 1:] |= out[:, :-1]
        nxt[:, :-1] |= out[:, 1:]
        # diagonals
        nxt[1:, 1:] |= out[:-1, :-1]
        nxt[1:, :-1] |= out[:-1, 1:]
        nxt[:-1, 1:] |= out[1:, :-1]
        nxt[:-1, :-1] |= out[1:, 1:]
        out = nxt
    return out


def remove_ribbon(path: Path) -> bool:
    img = Image.open(path).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape
    bg = sample_bg(arr)

    y_max = max(28, int(h * 0.14))
    x_min = int(w * 0.42)

    region = arr[:y_max, x_min:, :]
    rr = region[:, :, 0].astype(np.int16)
    gg = region[:, :, 1].astype(np.int16)
    bb = region[:, :, 2].astype(np.int16)
    red = (rr >= 120) & (rr > gg + 55) & (rr > bb + 45) & (gg < 110) & (bb < 110)
    if not red.any():
        red = (rr >= 90) & (rr > gg + 40) & (rr > bb + 35) & (gg < 130) & (bb < 130) & (rr > 140)
    if not red.any():
        return False

    mask = np.zeros((h, w), dtype=bool)
    mask[:y_max, x_min:] = red
    mask = dilate(mask, iterations=3)

    # Keep expansion within the top-right band
    keep = np.zeros_like(mask)
    keep[: min(h, y_max + 8), max(0, x_min - 4) :] = True
    mask &= keep

    # Clear bright price glyphs sitting on the ribbon
    y2 = min(h, y_max + 10)
    x0 = max(0, x_min - 6)
    top = arr[:y2, x0:, :]
    local = mask[:y2, x0:]
    bright = (top[:, :, 0] > 210) & (top[:, :, 1] > 210) & (top[:, :, 2] > 200)
    near = dilate(local, iterations=4)
    local = local | (bright & near)
    mask[:y2, x0:] = local

    arr[mask] = bg
    Image.fromarray(arr).save(path, quality=92, optimize=True)
    return True


def main():
    files = sorted(IMG_DIR.glob("*.jpg"))
    changed = 0
    for i, f in enumerate(files, 1):
        if remove_ribbon(f):
            changed += 1
        if i % 50 == 0:
            print(f"... {i}/{len(files)}")
    print(f"Done: removed ribbon on {changed}/{len(files)} images")


if __name__ == "__main__":
    main()
