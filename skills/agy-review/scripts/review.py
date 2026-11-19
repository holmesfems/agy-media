"""Ask the Antigravity CLI (agy / Gemini) for an independent code review of a git diff.

Gemini is trained apart from Claude, so its blind spots differ. Use it as a second
reviewer: it reads the diff (and the repository around it, read-only) and returns
findings as JSON. The default reviewer is Neon; with --model repeated, several models
run in parallel and findings that land on the same place are merged so you can see
which ones more than one model agrees on.

The answers are candidates, not verdicts: check each one in the code yourself.

- agy runs in plan mode (read-only) and is told not to use a terminal, the web, or
  files outside the repository. Never passes --dangerously-skip-permissions.
  It can still read any file in the repository: keep secrets out of it with agy's own
  deny rules (settings.json).
- Files that usually hold secrets (.env, keys, credentials; any case) are left out of the
  diff, and the run stops if any line of the diff looks like a secret (see --allow-secret-like).

Examples:
  python review.py --repo . --out ../work                       # uncommitted changes vs HEAD
  python review.py --repo . --base origin/main --out ../work    # this branch
  python review.py --repo . --commit 1a2b3c --out ../work
  python review.py --repo . --staged --model gemini-4-neon-high --model gemini-4-hydrogen-high --out ../work
"""
import argparse
import json
import locale
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Neon finds the most (incl. subtle races). Add --model gemini-4-hydrogen-high for a fast second vote.
DEFAULT_MODELS = ["gemini-4-neon-high"]

# left out of the diff sent to agy (case-insensitive: .ENV, Credentials.json, server.KEY ...)
SECRET_PATHS = [
    f":(exclude,glob,icase){p}" for p in (
        "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/id_rsa*", "**/id_ed25519*",
        "**/*credentials*.json", "**/secrets.*", "**/*.secret",
    )
]
SECRET_LINE = re.compile(
    r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{40,}"
    r"|sk-[A-Za-z0-9_\-]{20,}|xox[abprs]-[0-9A-Za-z\-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    # api_key = "…" / "api_key": "…" / 'password': '…' / token: "…"
    r"|(?i:(api[_-]?key|secret|token|password)['\"]?\s*[:=]\s*['\"][^'\"\s]{12,}['\"]))"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "repo-relative path"},
                    "line": {"type": "integer", "description": "line in the NEW version of the file"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {"type": "string", "description": "e.g. correctness, concurrency, security, error-handling, performance"},
                    "summary": {"type": "string", "description": "one sentence, Japanese"},
                    "failure_scenario": {"type": "string", "description": "concrete input/state -> wrong result, Japanese"},
                    "confidence": {"type": "number", "description": "0..1"},
                },
                "required": ["file", "line", "severity", "category", "summary", "failure_scenario", "confidence"],
            },
        },
        "notes": {"type": "string", "description": "anything worth saying that is not a finding (Japanese, short)"},
    },
    "required": ["findings"],
}

GUARD = (
    "Rules for this session: you are a read-only code reviewer. You have NO terminal (any shell "
    "command is denied and ends your turn). Do not search the web. Do not create, edit or delete "
    "files, and do not write a plan. Read only files inside the repository folder and the review "
    "folder named below, and never open .env files, private keys or credential files. "
    "When done, answer directly with the JSON.\n\n"
)

TASK = """Task: review a code change in the repository at {repo}.
The diff is in {diff} (unified diff, paths relative to the repository root).
{intent}
How to work:
1. Read the diff. Then open the changed files (and the code they call or that calls them) inside the repository to understand the context. Do not stop at the diff.
2. Look for real defects introduced or exposed by this change: wrong logic, off-by-one, wrong conditions, missing cases, broken error handling, race conditions, resource leaks, security problems, API misuse, mismatches with the code that calls it, and behaviour that contradicts the stated intent.
3. Before reporting a finding, re-read the code and convince yourself it really fails. Give a concrete failure scenario (input or state -> wrong result). If you cannot, drop it or give it low confidence.
4. Do NOT report style, naming, formatting, missing comments or tests, or matters of taste. Do not report pre-existing problems in code the diff does not touch, unless the change makes them worse.
5. Fewer, real findings are better than many weak ones. An empty list is a valid answer.
Answer with JSON matching the schema: findings[] with file (repo-relative), line (in the new version of the file), severity (high/medium/low), category, summary and failure_scenario (write these two in Japanese), confidence (0..1); and optional notes (Japanese).
"""


def decode(raw: bytes) -> str:
    """agy writes UTF-8 to a terminal but the console code page (e.g. cp932) to a pipe on Windows."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), "replace")


def parse_json_tail(text):
    """agy's own --output-format json result: one JSON line, maybe after a warning line."""
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    start = text.find("{")
    if start >= 0:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            pass
    return None


FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.S)


def parse_answer(text):
    """The model's answer when structured output is missing: find the object that holds `findings`.

    The answer may be plain JSON, JSON in a ```json fence, or JSON after some prose. Never take an
    inner object (a single finding) for the whole answer.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    candidates = [text.strip(), *(m.group(1) for m in FENCE.finditer(text))]
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "findings" in obj:
            return obj
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "findings" in obj:
            return obj
    return None


def clean_findings(raw):
    """Keep only well-formed findings; agy may send null or odd types."""
    if not isinstance(raw, list):
        return []
    out = []
    for f in raw:
        if not isinstance(f, dict) or not f.get("file"):
            continue
        try:
            line = int(f.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        try:
            conf = float(f.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append(dict(f, line=line, confidence=conf))
    return out


def git(repo: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {decode(r.stderr).strip()}")
    return decode(r.stdout)


def build_diff(repo: Path, a) -> tuple[str, str]:
    """Return (diff text, default intent)."""
    common = ["--no-color", "--no-ext-diff", "-U8"]
    intent = ""
    if a.commit:
        parents = git(repo, "rev-list", "--parents", "-n", "1", a.commit).split()[1:]
        if len(parents) > 1:
            # a merge commit: review what it brought into the first parent (git show would give a
            # combined diff, empty for a clean merge)
            diff = git(repo, "diff", *common, f"{a.commit}^1", a.commit, "--", ".", *SECRET_PATHS)
        else:
            diff = git(repo, "show", "--format=", *common, a.commit, "--", ".", *SECRET_PATHS)
        intent = git(repo, "log", "-1", "--format=%s%n%n%b", a.commit).strip()
    elif a.base:
        mb = git(repo, "merge-base", a.base, "HEAD").strip()
        diff = git(repo, "diff", *common, mb, "--", ".", *SECRET_PATHS)
        intent = git(repo, "log", "--format=- %s", f"{mb}..HEAD").strip()
    elif a.staged:
        diff = git(repo, "diff", "--cached", *common, "--", ".", *SECRET_PATHS)
    else:
        diff = git(repo, "diff", "HEAD", *common, "--", ".", *SECRET_PATHS)
    return diff, intent


def secret_like_lines(diff: str):
    """Every line that is sent counts: added, removed and context lines alike."""
    hits = []
    file = None
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            file = line.split(" b/", 1)[-1]
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk:
            if line.startswith("+++ b/"):
                file = line[6:]
        elif line[:1] in ("+", "-", " ") and SECRET_LINE.search(line):
            hits.append({"file": file, "text": line[:120]})
    return hits


def run_model(model: str, prompt: str, repo: Path, work: Path, schema_path: Path, agy: str, timeout: int):
    cmd = [agy, "-p", prompt, "--model", model, "--mode", "plan", "--output-format", "json",
           "--json-schema", str(schema_path), "--print-timeout", f"{timeout}s",
           "--add-dir", str(repo), "--add-dir", str(work)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 60, cwd=str(repo))
    except subprocess.TimeoutExpired:
        return {"model": model, "status": "TIMEOUT", "seconds": round(time.time() - t0, 1), "findings": []}
    res = parse_json_tail(decode(r.stdout))
    out = {"model": model, "seconds": round(time.time() - t0, 1)}
    if res is None:
        out.update(status="ERROR", exit_code=r.returncode, stdout=decode(r.stdout)[-1500:],
                   stderr=decode(r.stderr)[-1500:], findings=[])
        return out
    response = res.get("response") if isinstance(res.get("response"), str) else ""
    data = res.get("structured_output")
    structured = isinstance(data, dict)
    if not structured:
        data = parse_answer(response) or {}
    notes = data.get("notes")
    out.update(status=res.get("status"), conversation_id=res.get("conversation_id"),
               denied_actions=res.get("denied_actions") or [],
               findings=clean_findings(data.get("findings")),
               notes=notes if isinstance(notes, str) else "",
               structured=structured, usage=res.get("usage"))
    if not structured:
        # keep the raw answer so a human can read what parsing may have missed
        out["response"] = response[-3000:]
    return out


def run_model_safe(*args):
    """One model failing must not lose the other models' results."""
    try:
        return run_model(*args)
    except Exception as e:  # noqa: BLE001 - report any failure as this model's result
        return {"model": args[0], "status": "ERROR", "error": f"{type(e).__name__}: {e}", "findings": []}


def merge(results, near: int):
    """Group findings from different models that point at the same file and nearby lines."""
    groups = []
    for r in results:
        for f in r.get("findings", []):
            f = dict(f, model=r["model"])
            for g in groups:
                if g["file"] == f.get("file") and abs(g["line"] - int(f.get("line") or 0)) <= near \
                        and r["model"] not in g["models"]:
                    g["items"].append(f)
                    g["models"].append(r["model"])
                    break
            else:
                groups.append({"file": f.get("file"), "line": int(f.get("line") or 0),
                               "models": [r["model"]], "items": [f]})
    rank = {"high": 0, "medium": 1, "low": 2}
    for g in groups:
        g["agreed"] = len(g["models"])
        g["severity"] = min((i.get("severity", "low") for i in g["items"]), key=lambda s: rank.get(s, 3))
        g["max_confidence"] = max(float(i.get("confidence") or 0) for i in g["items"])
    groups.sort(key=lambda g: (-g["agreed"], rank.get(g["severity"], 3), -g["max_confidence"]))
    for n, g in enumerate(groups, 1):
        g["id"] = n
    return groups


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="git repository (default: current folder)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--base", help="review this branch: diff from merge-base(BASE, HEAD) to the working tree")
    src.add_argument("--commit", help="review one commit")
    src.add_argument("--staged", action="store_true", help="review staged changes")
    ap.add_argument("--intent", help="what the change is meant to do (default: commit message(s))")
    ap.add_argument("--intent-file", help="UTF-8 file with the intent")
    ap.add_argument("--model", action="append", help=f"agy model, repeatable (default: {' + '.join(DEFAULT_MODELS)}); see `agy models`")
    ap.add_argument("--out", required=True, help="work folder for the diff, prompt and results (outside the repo is best)")
    ap.add_argument("--near", type=int, default=3, help="lines apart that still count as the same place when merging")
    ap.add_argument("--max-diff-kb", type=int, default=400, help="refuse diffs larger than this")
    ap.add_argument("--allow-secret-like", action="store_true", help="send the diff even if an added line looks like a secret")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per model")
    ap.add_argument("--agy", default=shutil.which("agy") or "agy", help="path to agy")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    repo = Path(git(Path(a.repo), "rev-parse", "--show-toplevel").strip()).resolve()
    work = Path(a.out).resolve()
    work.mkdir(parents=True, exist_ok=True)

    diff, intent = build_diff(repo, a)
    if not diff.strip():
        sys.exit("empty diff: nothing to review")
    if len(diff.encode("utf-8")) > a.max_diff_kb * 1024:
        sys.exit(f"diff is larger than {a.max_diff_kb} KB: review it in parts (--commit, or a narrower --base), or raise --max-diff-kb")
    hits = secret_like_lines(diff)
    if hits and not a.allow_secret_like:
        print(json.dumps({"status": "STOPPED", "reason": "lines of the diff look like secrets; nothing was sent",
                          "lines": hits}, ensure_ascii=False, indent=2))
        sys.exit(4)

    if a.intent_file:
        intent = Path(a.intent_file).read_text(encoding="utf-8-sig").strip()
    elif a.intent:
        intent = a.intent.strip()

    diff_path = work / "diff.patch"
    diff_path.write_text(diff, encoding="utf-8")
    schema_path = work / "schema.json"
    schema_path.write_text(json.dumps(SCHEMA, ensure_ascii=False), encoding="utf-8")
    intent_text = f"Stated intent of the change (from the author):\n{intent}\n" if intent else ""
    prompt = GUARD + TASK.format(repo=repo, diff=diff_path, intent=intent_text)
    (work / "ask.txt").write_text(prompt, encoding="utf-8")

    models = list(dict.fromkeys(a.model or DEFAULT_MODELS))  # same model twice would overwrite its result file
    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        results = list(ex.map(lambda m: run_model_safe(m, prompt, repo, work, schema_path, a.agy, a.timeout), models))
    for r in results:
        (work / f"result_{r['model']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    groups = merge(results, a.near)
    summary = {
        "repo": str(repo),
        "diff": str(diff_path),
        "diff_files": len(re.findall(r"^\+\+\+ ", diff, flags=re.M)),
        "models": [{k: r.get(k) for k in ("model", "status", "seconds", "denied_actions", "structured", "error")
                    if k in r}
                   | {"findings": len(r.get("findings", []))} for r in results],
        "findings": groups,
        "notes": {r["model"]: r.get("notes", "") for r in results if r.get("notes")},
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    (work / "review.json").write_text(text, encoding="utf-8")
    print(json.dumps({"review": str(work / "review.json"), "models": summary["models"],
                      "groups": len(groups), "agreed_by_2plus": sum(g["agreed"] >= 2 for g in groups)},
                     ensure_ascii=False, indent=2))
    ok = any(r.get("status") == "SUCCESS" for r in results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
