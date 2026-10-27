"""Measure how far the voice stands above the BGM in a finished mix, given the voice-only track.

The voice track is aligned to the mix by cross-correlation and subtracted (with a fitted
gain); what is left is BGM + SE. Every --win seconds where the voice is active, it reports
voice and BGM level and their difference. Spans where the difference is below --min-db are
listed as "buried" (the voice may be hard to hear there).

Usually a talk video keeps BGM about 12-20 dB under the voice.
The voice in the mix should be the same take as the voice track (gain changes are fine;
heavy EQ / compression / reverb on the voice makes the BGM estimate too high).

  python mix_balance.py "final.mp4" talk.wav --start 0 --end 40 --out work/balance.json

Needs numpy (comes with opencv-python).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: install ffmpeg or `pip install imageio-ffmpeg`")


def load(ff, src, start=None, end=None):
    rng = []
    if start is not None:
        rng += ["-ss", str(start)]
    if end is not None:
        rng += ["-to", str(end)]
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error"] + rng +
                       ["-i", src, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True)
    if r.returncode:
        sys.exit(r.stderr.decode("utf-8", "replace"))
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768


def best_lag(long, short):
    """Offset k maximising |sum(long[k:k+len(short)] * short)| (FFT cross-correlation)."""
    n = len(long) + len(short)
    size = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(long, size) * np.conj(np.fft.rfft(short, size)), size)
    return int(np.argmax(np.abs(c[: len(long) - len(short) + 1])))


def db(x):
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mix", help="finished video or audio")
    ap.add_argument("voice", help="voice-only track (same timeline or starting earlier)")
    ap.add_argument("--start", type=float, default=0.0, help="range in the mix (s)")
    ap.add_argument("--end", type=float)
    ap.add_argument("--voice-start", type=float, help="where to read the voice track from (default: search near --start)")
    ap.add_argument("--win", type=float, default=0.5, help="window (s)")
    ap.add_argument("--min-db", type=float, default=6.0, help="voice - BGM below this counts as buried")
    ap.add_argument("--out", help="write JSON here")
    a = ap.parse_args()

    ff = find_ffmpeg()
    mix = load(ff, a.mix, a.start, a.end)
    vs = a.voice_start if a.voice_start is not None else max(0.0, a.start - 10)
    voice = load(ff, a.voice, vs, vs + len(mix) / SR + 20)

    # where does the voice track sit in this mix range?  (lag > 0: voice starts later in the mix)
    seg = voice[: SR * 10] if a.voice_start is not None else mix[: SR * 10]
    if a.voice_start is not None:
        lag = best_lag(mix[: SR * 20], seg)
        v = np.zeros_like(mix)
        n = min(len(mix) - lag, len(voice))
        v[lag:lag + n] = voice[:n]
        offset = vs - (a.start + lag / SR)
    else:
        k = best_lag(voice, seg)
        v = np.zeros_like(mix)
        n = min(len(mix), len(voice) - k)
        v[:n] = voice[k:k + n]
        offset = vs + k / SR - a.start
    g = float(np.dot(mix, v) / np.dot(v, v))
    bgm = mix - g * v

    win = int(SR * a.win)
    rows = []
    for i in range(0, len(mix) - win + 1, win):
        lv, lb = db(g * v[i:i + win]), db(bgm[i:i + win])
        if lv > -45:
            rows.append({"t": round(a.start + i / SR, 2), "voice_db": round(lv, 1), "bgm_db": round(lb, 1),
                         "diff_db": round(lv - lb, 1)})

    buried = []
    for r in rows:
        if r["diff_db"] < a.min_db:
            if buried and abs(buried[-1][1] - r["t"]) < 1e-6:
                buried[-1][1] = round(r["t"] + a.win, 2)
            else:
                buried.append([r["t"], round(r["t"] + a.win, 2)])

    diffs = [r["diff_db"] for r in rows]
    out = {
        "mix": a.mix, "voice": a.voice, "range": [a.start, a.end],
        "voice_track_offset_s": round(offset, 3),
        "note": "voice_track_offset_s: mix time t corresponds to voice-track time t + offset",
        "voice_gain": round(g, 3),
        "mean_voice_minus_bgm_db": round(float(np.mean(diffs)), 1) if diffs else None,
        "buried_spans": buried,
        "rows": rows,
    }
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("voice_track_offset_s", "voice_gain", "mean_voice_minus_bgm_db",
                                          "buried_spans")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
