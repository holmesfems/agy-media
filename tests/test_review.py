"""Tests for skills/agy-review/scripts/review.py that do not call agy.

Run: python -m unittest discover -s tests
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review", ROOT / "skills/agy-review/scripts/review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


# Dummy secrets are put together at run time, so this file itself does not look like it
# holds secrets when review.py scans a diff that touches it.
KEY, PW, TOK, VAL = "api" + "_key", "pass" + "word", "to" + "ken", "abcdefghijkl" + "1234"


class SecretLine(unittest.TestCase):
    def test_assignment_forms(self):
        for line in [f'+{KEY} = "{VAL}"',
                     f'+  "{KEY}": "{VAL}",',
                     f"+  '{PW}': '{VAL}',",
                     f'+  {TOK}: "{VAL}"']:
            with self.subTest(line=line):
                self.assertTrue(review.SECRET_LINE.search(line))

    def test_ordinary_code_passes(self):
        for line in ['+const tokenCount = countTokens(text)', '+  "password": "",', '+api_key = os.environ["API_KEY"]']:
            with self.subTest(line=line):
                self.assertFalse(review.SECRET_LINE.search(line))

    def test_removed_and_context_lines_count(self):
        key = "AIza" + "A" * 35
        diff = (f"diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1,3 +1,3 @@\n"
                f" const keep = \"{key}\"\n-const old = \"{key}\"\n+const x = 1\n")
        hits = review.secret_like_lines(diff)
        self.assertEqual(len(hits), 2)
        self.assertEqual({h["file"] for h in hits}, {"a.ts"})

    def test_header_lines_are_not_content(self):
        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(review.secret_like_lines(diff), [])


class ParseAnswer(unittest.TestCase):
    def test_pretty_json_is_not_cut_to_one_finding(self):
        text = '{\n  "findings": [\n    {"file": "a.py", "line": 10}\n  ]\n}'
        self.assertEqual(review.parse_answer(text), {"findings": [{"file": "a.py", "line": 10}]})

    def test_fenced_multiline_json(self):
        text = '結果です\n```json\n{\n  "findings": [],\n  "notes": "x"\n}\n```\n'
        self.assertEqual(review.parse_answer(text), {"findings": [], "notes": "x"})

    def test_json_after_prose(self):
        text = 'I looked at {the diff}. {"findings": [{"file": "b.ts", "line": 3}]}'
        self.assertEqual(review.parse_answer(text)["findings"][0]["file"], "b.ts")

    def test_none_and_garbage(self):
        self.assertIsNone(review.parse_answer(None))
        self.assertIsNone(review.parse_answer("no json here"))
        self.assertIsNone(review.parse_json_tail(None))


class NullsFromAgy(unittest.TestCase):
    def run_with(self, agy_json):
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(agy_json).encode(), stderr=b"")
        with mock.patch.object(review.subprocess, "run", return_value=done):
            return review.run_model("m", "p", Path("."), Path("."), Path("s.json"), "agy", 10)

    def test_null_response(self):
        r = self.run_with({"status": "ERROR", "response": None})
        self.assertEqual(r["findings"], [])

    def test_null_findings(self):
        r = self.run_with({"status": "SUCCESS", "response": "", "structured_output": {"findings": None, "notes": None}})
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["notes"], "")

    def test_bad_finding_items_are_dropped(self):
        r = self.run_with({"status": "SUCCESS", "structured_output": {"findings": [
            None, {"file": "a.py", "line": "12", "confidence": None}, {"line": 3}]}})
        self.assertEqual(r["findings"], [{"file": "a.py", "line": 12, "confidence": 0.0}])

    def test_one_model_crashing_keeps_the_others(self):
        with mock.patch.object(review, "run_model", side_effect=RuntimeError("boom")):
            r = review.run_model_safe("m", "p")
        self.assertEqual(r["status"], "ERROR")
        self.assertEqual(r["findings"], [])
        self.assertEqual(review.merge([r, {"model": "n", "findings": [{"file": "a", "line": 1}]}], 3)[0]["models"], ["n"])


class BuildDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        return Namespace(**({"commit": None, "base": None, "staged": False} | kw))

    def test_merge_commit_has_a_diff(self):
        git(self.repo, "switch", "-qc", "f")
        (self.repo / "a.txt").write_text("a\nb\n")
        git(self.repo, "commit", "-qam", "f")
        git(self.repo, "switch", "-q", "main")
        (self.repo / "c.txt").write_text("c\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "m2")
        git(self.repo, "merge", "-q", "--no-edit", "f")
        diff, _ = review.build_diff(self.repo, self.args(commit="HEAD"))
        self.assertIn("+b", diff)
        self.assertNotIn("c.txt", diff)

    def test_secret_files_left_out_in_any_case(self):
        for name in (".ENV", "Credentials.json", "server.KEY", "sub/.env.local"):
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("X=1\n")
        (self.repo / "b.txt").write_text("b\n")
        git(self.repo, "add", ".")
        diff, _ = review.build_diff(self.repo, self.args(staged=True))
        self.assertIn("b.txt", diff)
        for name in (".ENV", "Credentials.json", "server.KEY", ".env.local"):
            self.assertNotIn(name, diff)


if __name__ == "__main__":
    unittest.main()
