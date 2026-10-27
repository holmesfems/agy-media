"""Follow one object through a video range with OpenCV template matching.

Give the object's box on the first frame (source-video pixels). Each later frame is
searched near the last position; the original and the latest look are both tried,
so slow changes in pose do not lose it. When an effect covers the object the score
drops; below --hold the last position is kept and the point is marked "held".

  python track.py rec.mp4 --start 50 --end 57 --box 720,110,110,130 --out cv.json

Output (source-video pixels, box centre):
  {"track": [{"frame_index": 3000, "t": 50.0, "x": 775, "y": 175, "score": 0.93}, ...]}
"""
import argparse
import json
import sys

import cv2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--box", required=True, help="x,y,w,h of the object on the start frame")
    ap.add_argument("--search", type=int, default=10, help="px the object may move per source frame")
    ap.add_argument("--every", type=int, default=1, help="keep every Nth frame")
    ap.add_argument("--update", type=float, default=0.7, help="refresh the latest look when score >= this")
    ap.add_argument("--hold", type=float, default=0.45,
                    help="below this score keep the last position (effects covering the object)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {a.video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    first = int(round(a.start * fps))
    last = int(round(a.end * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)

    x, y, w, h = (int(v) for v in a.box.split(","))
    ok, frame = cap.read()
    if not ok:
        sys.exit("cannot read the start frame")
    original = frame[y:y + h, x:x + w].copy()
    latest = original
    track = [{"frame_index": first, "t": round(first / fps, 3), "x": x + w // 2, "y": y + h // 2, "score": 1.0}]

    n = first
    while n < last:
        ok, frame = cap.read()
        n += 1
        if not ok:
            break
        if (n - first) % a.every:
            continue
        H, W = frame.shape[:2]
        r = a.search * a.every
        sx0, sy0 = max(0, x - r), max(0, y - r)
        sx1, sy1 = min(W, x + w + r), min(H, y + h + r)
        area = frame[sy0:sy1, sx0:sx1]
        best = (-1.0, (x - sx0, y - sy0))
        for tpl in (original, latest):
            res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score > best[0]:
                best = (score, loc)
        score, (lx, ly) = best
        held = score < a.hold
        if not held:
            x, y = sx0 + lx, sy0 + ly
        if score >= a.update:
            latest = frame[y:y + h, x:x + w].copy()
        p = {"frame_index": n, "t": round(n / fps, 3), "x": x + w // 2, "y": y + h // 2,
             "score": round(float(score), 3)}
        if held:
            p["held"] = True
        track.append(p)

    out = {"video": a.video, "fps": fps, "box_size": [w, h], "track": track}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    held = sum(1 for p in track if p.get("held"))
    print(json.dumps({"points": len(track), "held_points": held, "out": a.out}))


if __name__ == "__main__":
    main()
