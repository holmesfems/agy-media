"""Run the Antigravity CLI (agy) headless and collect what it made.

- Passes the prompt as one argument (no shell quoting problems on Windows).
- Uses --output-format json and prints a compact JSON summary.
- Copies media files agy saved in its own brain/<conversation_id> folder to --out.
- Never passes --dangerously-skip-permissions.

Examples:
  python agy_run.py --prompt "Generate an image of an apple tree" --out ./out
  python agy_run.py --prompt-file ask.txt --dir ./frames --model gemini-3.8-flash-high
"""
import argparse
import json
import locale
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".wav", ".mp3"}

GUARD = (
    "Rules for this session: you have NO terminal access (any shell command is denied and "
    "ends your turn). Do not search the web. Open only the files named in the task. "
    "Use your file-reading tool to see images and hear audio, and your built-in generation "
    "tools when asked to create media. When done, write the answer directly.\n\n"
)


def brain_root() -> Path:
    env = os.environ.get("AGY_BRAIN_DIR")
    if env:
        return Path(env)
    return Path.home() / ".gemini" / "antigravity-cli" / "brain"


def decode(raw: bytes) -> str:
    """agy writes UTF-8 to a terminal but the console code page (e.g. cp932) to a pipe on Windows."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), "replace")


def parse_json_tail(text: str):
    """agy may print a warning line before the JSON object; take the last JSON line."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    # pretty-printed JSON spanning lines
    start = text.find("{")
    if start >= 0:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            pass
    return None


def collect_media(conv_id: str, since: float, out: Path):
    folder = brain_root() / conv_id
    if not folder.is_dir():
        return []
    copied = []
    for p in folder.rglob("*"):
        if ".system_generated" in p.parts or not p.is_file():
            continue
        if p.suffix.lower() not in MEDIA_EXT or p.stat().st_mtime < since - 5:
            continue
        out.mkdir(parents=True, exist_ok=True)
        dst = out / p.name
        shutil.copy2(p, dst)
        copied.append(str(dst))
    return copied


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt", help="prompt text")
    g.add_argument("--prompt-file", help="UTF-8 text file holding the prompt")
    ap.add_argument("--model", default="gemini-3.8-flash-high", help="see `agy models`")
    ap.add_argument("--dir", action="append", default=[], help="folder agy may read (repeatable)")
    ap.add_argument("--out", help="copy generated media here")
    ap.add_argument("--timeout", type=int, default=600, help="seconds (agy --print-timeout)")
    ap.add_argument("--save", help="also write the JSON summary to this file (UTF-8)")
    ap.add_argument("--no-guard", action="store_true", help="do not prepend the session rules")
    ap.add_argument("--agy", default=shutil.which("agy") or "agy", help="path to agy")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    prompt = a.prompt if a.prompt is not None else Path(a.prompt_file).read_text(encoding="utf-8-sig")
    if not a.no_guard:
        prompt = GUARD + prompt

    cmd = [a.agy, "-p", prompt, "--model", a.model, "--mode", "accept-edits",
           "--output-format", "json", "--print-timeout", f"{a.timeout}s"]
    for d in a.dir:
        cmd += ["--add-dir", str(Path(d).resolve())]

    t0 = time.time()
    try:
        # agy can hang on Windows; keep our own guard above its timeout
        r = subprocess.run(cmd, capture_output=True, timeout=a.timeout + 60)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "TIMEOUT", "seconds": a.timeout + 60}, ensure_ascii=False))
        sys.exit(3)
    stdout = decode(r.stdout)
    stderr = decode(r.stderr)

    res = parse_json_tail(stdout)
    if res is None:
        print(json.dumps({"status": "ERROR", "exit_code": r.returncode,
                          "stdout": stdout[-2000:], "stderr": stderr[-2000:]}, ensure_ascii=False, indent=2))
        sys.exit(2)

    conv = res.get("conversation_id", "")
    summary = {
        "status": res.get("status"),
        "response": res.get("response", ""),
        "conversation_id": conv,
        "seconds": res.get("duration_seconds"),
        "denied_actions": res.get("denied_actions", []),
        "brain_dir": str(brain_root() / conv) if conv else None,
        "media": collect_media(conv, t0, Path(a.out)) if (conv and a.out) else [],
    }
    if summary["denied_actions"]:
        summary["hint"] = ("agy tried a tool that headless mode cannot approve (often a shell command). "
                           "Do that step yourself (e.g. ffmpeg) and ask agy only to look at the results.")
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if a.save:
        Path(a.save).write_text(text, encoding="utf-8")
    print(text)
    sys.exit(0 if summary["response"].strip() or summary["media"] else 1)


if __name__ == "__main__":
    main()
