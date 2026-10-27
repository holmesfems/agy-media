"""Prepare an audio range for agy (which can listen) and for Claude (which can only read numbers and images).

Writes into --out:
  audio.wav     mono 16 kHz cut of the range, small enough to hand to agy
  wave.png      waveform
  spectrum.png  spectrogram with time / frequency axes
  stats.json    loudness (LUFS, true peak), sound / silence spans in source-file seconds

Works on wav, mp3, and the audio track of a video.

  python audio.py talk.wav --out work --start 0 --end 40
  python audio.py rec.mp4 --out work --start 50 --end 57 --silence-db -40
"""
import argparse
import json
import re
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


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        sys.exit(r.stderr.decode("utf-8", "replace")[-2000:])
    return r.stderr.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--silence-db", type=float, default=-35.0, help="quieter than this counts as silence")
    ap.add_argument("--silence-min", type=float, default=0.25, help="shortest silence to report (s)")
    ap.add_argument("--width", type=int, default=1600)
    a = ap.parse_args()

    ff = find_ffmpeg()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = ["-ss", str(a.start)] + (["-to", str(a.end)] if a.end is not None else [])
    base = [ff, "-hide_banner", "-y"] + rng + ["-i", a.src, "-vn"]
    wav = out / "audio.wav"

    run(base + ["-ac", "1", "-ar", "16000", str(wav)])
    run([ff, "-hide_banner", "-y", "-i", str(wav), "-filter_complex",
         f"showwavespic=s={a.width}x240:colors=0x3fa7ff", "-frames:v", "1", str(out / "wave.png")])
    run([ff, "-hide_banner", "-y", "-i", str(wav), "-lavfi",
         f"showspectrumpic=s={a.width}x480:legend=1:scale=log", str(out / "spectrum.png")])

    log = run([ff, "-hide_banner", "-i", str(wav), "-af",
               f"silencedetect=noise={a.silence_db}dB:d={a.silence_min},loudnorm=print_format=json",
               "-f", "null", "-"])
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", log)]
    dur_m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", log)
    dur = int(dur_m[1]) * 3600 + int(dur_m[2]) * 60 + float(dur_m[3]) if dur_m else None

    silences = [[max(0.0, s), e] for s, e in zip(starts, ends)]
    if len(starts) > len(ends) and dur:  # silent until the end
        silences.append([starts[-1], dur])
    sounds, t = [], 0.0
    for s, e in silences:
        if s - t > 0.01:
            sounds.append([t, s])
        t = e
    if dur and dur - t > 0.01:
        sounds.append([t, dur])

    loud = {}
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", log)
    if m:
        j = json.loads(m.group(0))
        loud = {"integrated_lufs": float(j["input_i"]), "true_peak_dbtp": float(j["input_tp"]),
                "loudness_range_lu": float(j["input_lra"])}

    off = a.start
    stats = {
        "src": str(Path(a.src).resolve()), "start": a.start, "end": a.end, "duration": dur,
        "note": "times are seconds in the source file",
        "loudness": loud,
        "sound_spans": [[round(s + off, 3), round(e + off, 3)] for s, e in sounds],
        "silence_spans": [[round(s + off, 3), round(e + off, 3)] for s, e in silences],
        "files": {k: str((out / k).resolve()) for k in ("audio.wav", "wave.png", "spectrum.png")},
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"duration": dur, "loudness": loud, "sound_spans": len(sounds),
                      "stats": str((out / "stats.json").resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
