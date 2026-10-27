"""Draw tracked points on the frames from frames.py and tile them into one image to check by eye.

track.json: {"track": [{"frame": "f_001.png", "x": 765, "y": 170}, ...]}
  or points with "t" (seconds in the source video) instead of "frame" (as track.py writes).
Coordinates are in source-video pixels unless --space image (pixels of the cropped frame).
Several tracks can be compared: each gets its own colour.

  python overlay.py work/index.json agy.json cv.json --out work/check.png
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

COLORS = [(255, 0, 255), (0, 255, 0), (0, 200, 255), (255, 160, 0)]


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def point_for(track, fr, half):
    for p in track:
        if p.get("frame") and Path(str(p["frame"])).stem == Path(fr["file"]).stem:
            return p
    timed = [p for p in track if "t" in p and "frame" not in p]
    if timed:
        best = min(timed, key=lambda p: abs(p["t"] - fr["t_video"]))
        if abs(best["t"] - fr["t_video"]) <= half:
            return best
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("index")
    ap.add_argument("tracks", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--space", choices=["video", "image"], default="video")
    ap.add_argument("--zoom", help="X:Y:W:H region of each frame to keep (image pixels)")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=600, help="tile width in the sheet")
    a = ap.parse_args()

    idx = load(a.index)
    fdir = Path(idx["frames_dir"])
    ox, oy = idx["crop"]["x"], idx["crop"]["y"]
    half = 0.5 / idx["fps"]
    tracks = [load(t)["track"] for t in a.tracks]

    tiles = []
    for fr in idx["frames"]:
        im = Image.open(fdir / fr["file"]).convert("RGB")
        dr = ImageDraw.Draw(im)
        label = [fr["file"], f"t={fr['t_video']}"]
        for k, tr in enumerate(tracks):
            p = point_for(tr, fr, half)
            col = COLORS[k % len(COLORS)]
            if not p or p.get("x") is None:
                label.append(f"#{k + 1}: -")
                continue
            x, y = p["x"], p["y"]
            if a.space == "video":
                x, y = x - ox, y - oy
            dr.ellipse([x - 14, y - 14, x + 14, y + 14], outline=col, width=4)
            dr.line([x - 30, y, x + 30, y], fill=col, width=2)
            dr.line([x, y - 30, x, y + 30], fill=col, width=2)
            label.append(f"#{k + 1}: ({p['x']},{p['y']})")
        if a.zoom:
            zx, zy, zw, zh = (int(v) for v in a.zoom.split(":"))
            im = im.crop((zx, zy, zx + zw, zy + zh))
        ImageDraw.Draw(im).text((8, 8), "  ".join(label), fill=(255, 255, 0))
        tiles.append(im)

    scale = a.width / tiles[0].width
    th = int(tiles[0].height * scale)
    rows = (len(tiles) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (a.cols * a.width, rows * th), "black")
    for i, t in enumerate(tiles):
        sheet.paste(t.resize((a.width, th)), ((i % a.cols) * a.width, (i // a.cols) * th))
    sheet.save(a.out)
    print(str(Path(a.out).resolve()))


if __name__ == "__main__":
    main()
