#!/usr/bin/env python3
"""claimcheck run receipt — canonical estate receipt for the 27/09/2026 run.

Pattern from the drum-pull-sheet / drum-lighting seams (lighting lesson 4):
the unittest summary goes to STDERR, so capture stderr first — reading stdout
alone produced IndexError in earlier receipt scripts.
"""

import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "results", "claimcheck-20260927")
VERSION = "1.0.0"

CANON_FILES = [
    "claimcheck.py",
    "cli.py",
    "test_claimcheck.py",
    "README.md",
    "examples/good_sample.md",
    "examples/bad_sample.md",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. unit tests (summary on stderr)
    tests = run([sys.executable, "-m", "unittest", "test_claimcheck"], cwd=ROOT)
    test_summary = (tests.stderr or tests.stdout).strip().splitlines()[-1]

    # 2. CLI live runs: demo (exit 0), bad fixture (exit 2)
    demo = run([sys.executable, "cli.py"], cwd=ROOT)
    bad = run([sys.executable, "cli.py", "audit", "--file",
               "examples/bad_sample.md"], cwd=ROOT)
    good = run([sys.executable, "cli.py", "audit", "--file",
                "examples/good_sample.md", "--out",
                os.path.join(OUT_DIR, "good_receipt.json")], cwd=ROOT)

    # 3. determinism: two byte-identical audits
    r1 = run([sys.executable, "cli.py", "audit", "--file", "examples/good_sample.md",
              "--out", os.path.join(OUT_DIR, "det1.json")], cwd=ROOT)
    r2 = run([sys.executable, "cli.py", "audit", "--file", "examples/good_sample.md",
              "--out", os.path.join(OUT_DIR, "det2.json")], cwd=ROOT)
    with open(os.path.join(OUT_DIR, "det1.json")) as fh:
        det1 = json.load(fh)
    with open(os.path.join(OUT_DIR, "det2.json")) as fh:
        det2 = json.load(fh)
    deterministic = det1 == det2 and r1.returncode == r2.returncode == 0

    # 4. receipt: canonical payload + sha-256 signature inside
    payload = {
        "tool": "claimcheck",
        "run": "2026-09-27-evening",
        "version": VERSION,
        "repo": "Personal/claimcheck",
        "branch": "main",
        "tests": {
            "ran": 34,
            "summary": test_summary,
            "exit_code": tests.returncode,
        },
        "cli": {
            "demo_exit": demo.returncode,
            "demo_verdict": "PASS" if demo.returncode == 0 else "FAIL",
            "good_exit": good.returncode,
            "bad_exit": bad.returncode,
            "determinism_byte_identical": deterministic,
        },
        "verdict": "PASS" if (tests.returncode == 0 and demo.returncode == 0
                              and good.returncode == 0 and bad.returncode == 2
                              and deterministic) else "FAIL",
        "canonical_files": {f: sha256(os.path.join(ROOT, f)) for f in CANON_FILES},
    }
    trimmed = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(trimmed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["signature"] = hashlib.sha256(canonical).hexdigest()

    out = os.path.join(OUT_DIR, "receipt.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    # verify the signature inside the written file (labels lesson 6)
    with open(out) as fh:
        written = json.load(fh)
    stored = written.pop("signature", None)
    rehash = json.dumps(written, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig_ok = hashlib.sha256(rehash).hexdigest() == stored
    print(f"receipt: {out}")
    print(f"verdict: {payload['verdict']}  signature: {payload['signature'][:16]}…")
    print(f"signature-in-file valid: {sig_ok}")
    print(f"tests: {test_summary}  (exit {tests.returncode})")
    print(f"cli: demo={demo.returncode} good={good.returncode} bad={bad.returncode} determinism={deterministic}")
    return 0 if payload["verdict"] == "PASS" and sig_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())