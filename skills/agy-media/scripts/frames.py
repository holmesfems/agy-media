"""Cut a video range into still frames (and an index.json) for agy / Claude to look at.

Examples:
  python frames.py rec.mp4 --out work --start 50 --end 57 --fps 2
  python frames.py rec.mp4 --out work --start 50 --end 57 --fps 2 --crop 1152:648:0:0 --grid 100 --sheet --clip

index.json records each frame's time in the source video and the crop offset, so
coordinates read on a frame can be mapped back:  video_x = image_x + crop.x
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: install ffmpeg or `pip install imageio-ffmpeg`")


def draw_grid(path: Path, step: int, ox: int, oy: int):
    """Label lines with VIDEO coordinates so the numbers can be read off directly."""
    from PIL import Image, ImageDraw
    im = Image.open(path).convert("RGB")
    dr = ImageDraw.Draw(im)
    w, h = im.size
    x0 = (-ox) % step
    for x in range(x0, w, step):
        dr.line([(x, 0), (x, h)], fill=(255, 255, 0), width=1)
        dr.text((x + 3, 3), str(x + ox), fill=(255, 255, 0))
    y0 = (-oy) % step
    for y in range(y0, h, step):
        dr.line([(0, y), (w, y)], fill=(255, 255, 0), width=1)
        dr.text((3, y + 3), str(y + oy), fill=(255, 255, 0))
    im.save(path)


def make_sheet(files, out: Path, cols=4, width=480):
    from PIL import Image, ImageDraw
    ims = [Image.open(f).convert("RGB") for f in files]
    scale = width / ims[0].width
    th = int(ims[0].height * scale)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * width, rows * th), "black")
    for i, im in enumerate(ims):
        im = im.resize((width, th))
        ImageDraw.Draw(im).text((6, 6), Path(files[i]).name, fill=(255, 0, 255))
        sheet.paste(im, ((i % cols) * width, (i // cols) * th))
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--start", type=float, default=0.0, help="seconds in the source video")
    ap.add_argument("--end", type=float, help="seconds in the source video")
    ap.add_argument("--fps", type=float, default=2.0, help="frames per second to keep")
    ap.add_argument("--crop", help="W:H:X:Y in source pixels (ffmpeg crop order)")
    ap.add_argument("--grid", type=int, default=0, help="draw a labelled grid every N px (video coords)")
    ap.add_argument("--sheet", action="store_true", help="also write sheet.png (all frames tiled)")
    ap.add_argument("--clip", action="store_true", help="also write clip.mp4 of the range")
    a = ap.parse_args()

    ff = find_ffmpeg()
    out = Path(a.out)
    fdir = out / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("f_*.png"):
        old.unlink()

    rng = ["-ss", str(a.start)] + (["-to", str(a.end)] if a.end is not None else [])
    vf = [f"fps={a.fps}"]
    cx = cy = 0
    if a.crop:
        cw, ch, cx, cy = (int(v) for v in a.crop.split(":"))
        vf.append(f"crop={cw}:{ch}:{cx}:{cy}")
    base = [ff, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(base + rng + ["-i", a.video, "-vf", ",".join(vf), str(fdir / "f_%03d.png")], check=True)
    if a.clip:
        subprocess.run(base + rng + ["-i", a.video, "-an", "-c:v", "libx264", "-crf", "18",
                                     str(out / "clip.mp4")], check=True)

    files = sorted(fdir.glob("f_*.png"))
    if a.grid:
        for f in files:
            draw_grid(f, a.grid, cx, cy)
    # ffmpeg's fps filter puts frame n at t = n / fps from the range start
    frames = [{"file": f.name, "t_clip": round(i / a.fps, 3), "t_video": round(a.start + i / a.fps, 3)}
              for i, f in enumerate(files)]
    index = {"video": str(Path(a.video).resolve()), "start": a.start, "end": a.end, "fps": a.fps,
             "crop": {"x": cx, "y": cy}, "grid": a.grid, "frames_dir": str(fdir.resolve()), "frames": frames}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.sheet and files:
        make_sheet(files, out / "sheet.png")
    print(json.dumps({"frames": len(files), "index": str((out / "index.json").resolve()),
                      "sheet": str((out / "sheet.png").resolve()) if a.sheet else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
