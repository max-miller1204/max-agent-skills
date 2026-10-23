"""Slide image tools for flashcards. Needs Pillow (run with: uv run --no-project --with pillow python -I slides.py ...).

  render <pdf> <page> <out.png>            Render one page at 1200px wide (needs poppler's pdftoppm).
  grid <in.png> <out.png>                  Overlay a 5% grid, labeled every 10%, to read off coordinates.
  crop <in.png> <out.png> L T R B          Crop by fractions (left, top, right, bottom) of the image.
  boxes <in.png> <out.png> <boxes.json>    Draw occlusion boxes [[n, left, top, width, height], ...] to check placement.
  montage <out.png> <in.png>...            Tile several images into one picture for a single review.
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw


def render(pdf, page, out):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-scale-to-x", "1200", "-scale-to-y", "-1", "-png", "-singlefile",
                        "-f", str(page), "-l", str(page), pdf, os.path.join(tmp, "p")], check=True)
        Image.open(os.path.join(tmp, "p.png")).convert("RGB").save(out, optimize=True)


def grid(src, out):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    for i in range(1, 20):
        x, y = w * i / 20, h * i / 20
        c = (255, 0, 0) if i % 2 == 0 else (255, 170, 170)
        d.line([(x, 0), (x, h)], fill=c, width=1)
        d.line([(0, y), (w, y)], fill=c, width=1)
        if i % 2 == 0:
            d.text((x + 2, 2), str(i * 5), fill=(200, 0, 0))
            d.text((2, y + 2), str(i * 5), fill=(200, 0, 0))
    im.save(out)


def crop(src, out, l, t, r, b):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im.crop((int(l * w), int(t * h), int(r * w), int(b * h))).save(out, optimize=True)


def boxes(src, out, boxes_json):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im, "RGBA")
    for n, l, t, bw, bh in json.load(open(boxes_json)):
        d.rectangle([l * w, t * h, (l + bw) * w, (t + bh) * h], fill=(255, 200, 0, 170), outline=(200, 0, 0), width=2)
        d.text((l * w + 3, t * h + 2), f"c{n}", fill=(0, 0, 0))
    im.save(out)


def montage(out, *srcs, width=1400, tile=440):
    ims = []
    for s in srcs:
        im = Image.open(s).convert("RGB")
        im.thumbnail((tile, tile))
        ims.append(im)
    m = Image.new("RGB", (width, 4000), "white")
    x = y = rowh = 0
    for im in ims:
        if x + im.width > width:
            x, y, rowh = 0, y + rowh + 10, 0
        m.paste(im, (x, y))
        x += im.width + 10
        rowh = max(rowh, im.height)
    m.crop((0, 0, width, y + rowh)).save(out)


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "render":
        render(a[0], int(a[1]), a[2])
    elif cmd == "grid":
        grid(*a)
    elif cmd == "crop":
        crop(a[0], a[1], *map(float, a[2:6]))
    elif cmd == "boxes":
        boxes(*a)
    elif cmd == "montage":
        montage(a[0], *a[1:])
    else:
        sys.exit(__doc__)
