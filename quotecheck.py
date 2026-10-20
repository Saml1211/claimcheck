"""Locate quoted words in captured Library bodies; never judge their truth."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import claimcheck as cc

MAX_INPUT = 256 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_BODY = 1_000_000
MAX_TOTAL = 4_000_000
MAX_MATCHES = 20


def validate(rows):
    """Validate the whole request before any network or output mutation."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("input must be an array of 1–32 quote objects")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"citation", "quote"}:
            raise ValueError("each object needs exactly citation and quote")
        citation, quote = row["citation"], row["quote"]
        if not isinstance(citation, str) or "/" not in citation:
            raise ValueError("citation must be an explicit source/full-id")
        source, item_id = citation.split("/", 1)
        if not cc.is_known_source(source) or not cc.id_matches(source, item_id):
            raise ValueError(f"unsupported source or malformed full id: {citation}")
        if not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 8000:
            raise ValueError("quote must contain 8–8000 characters after edge whitespace")
    return rows


def fetch_body(citation, base):
    """Fetch only the requested item; previews are never substituted for bodies."""
    source, item_id = citation.split("/", 1)
    url = (base.rstrip("/") + "/api/library/item/" +
           urllib.parse.quote(source, safe="") + "/" +
           urllib.parse.quote(item_id, safe=""))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("item response exceeds 4 MiB")
        item = json.loads(raw.decode("utf-8"))
        if not isinstance(item, dict) or not isinstance(item.get("body"), str):
            raise ValueError("API did not return a string body; preview is not evidence")
        if not item["body"].strip():
            raise ValueError("captured body is empty")
        if len(item["body"]) > MAX_BODY:
            raise ValueError("captured body exceeds one million characters")
        return {"status": "AVAILABLE", "body": item["body"],
                "title": str(item.get("title") or citation),
                "indexed_at": item.get("indexed_at"),
                "created_at": item.get("created_at")}
    except urllib.error.HTTPError as exc:
        exc.close()
        return {"status": "MISSING" if exc.code == 404 else "UNCHECKED",
                "detail": f"HTTP {exc.code}"}
    except (OSError, ValueError, UnicodeError) as exc:
        return {"status": "UNCHECKED", "detail": str(exc)}


def locate(body, quote):
    """Whitespace may vary; case, punctuation, numbers and order may not.

    Offsets are zero-based Python Unicode character indices, end exclusive.
    Ellipses are literal text, not a licence to splice unrelated passages.
    """
    pattern = re.compile(r"\s+".join(re.escape(word) for word in quote.split()))
    matches, count, start = [], 0, 0
    while match := pattern.search(body, start):
        count += 1
        if len(matches) < MAX_MATCHES:
            a, b = match.span()
            matches.append({"start": a, "end": b,
                            "mode": "EXACT" if body[a:b] == quote else "WHITESPACE_ONLY",
                            "line": body.count("\n", 0, a) + 1,
                            "before": body[max(0, a - 180):a],
                            "text": body[a:b], "after": body[b:b + 180]})
        start = match.start() + 1  # Include overlapping occurrences, not just the first.
    return {"status": "MATCHED" if count else "NOT_FOUND",
            "count": count, "matches": matches,
            "omitted_matches": max(0, count - len(matches))}


def audit(rows, loader):
    validate(rows)
    sources, results, total = {}, [], 0
    for row in rows:
        citation = row["citation"]
        if citation not in sources:
            source = dict(loader(citation))
            if source["status"] == "AVAILABLE":
                body = source["body"]
                total += len(body)
                if total > MAX_TOTAL:
                    source = {"status": "UNCHECKED", "detail": "snapshot budget exceeds four million characters"}
                else:
                    source["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            sources[citation] = source
        source = sources[citation]
        result = dict(row)
        result.update(locate(source["body"], row["quote"]) if source["status"] == "AVAILABLE"
                      else {"status": source["status"], "detail": source.get("detail", "")})
        results.append(result)
    states = {row["status"] for row in results}
    verdict = ("MISMATCH" if states & {"NOT_FOUND", "MISSING"} else
               "UNCHECKED" if "UNCHECKED" in states else "MATCHED")
    return cc.sign_receipt({"tool": "claimcheck-quotes", "version": "1.0.0",
                            "verdict": verdict, "rows": results, "sources": sources,
                            "scope": "Words in captured Library text, not truth, authorship, permission, or current upstream content.",
                            "matching": "Case-sensitive contiguous substring; whitespace runs only may differ. Ellipses are literal.",
                            "offsets": "Unicode characters, zero-based, end exclusive; not bytes or JavaScript UTF-16 indices."})


def render(receipt):
    """A script-free, offline view; all captured content is untrusted text."""
    escape = html.escape
    cards = []
    for i, row in enumerate(receipt["rows"], 1):
        occurrences = []
        for j, match in enumerate(row.get("matches", []), 1):
            occurrences.append(f'<details><summary>Occurrence {j} · line {match["line"]} · '
                               f'{match["mode"]} · characters {match["start"]}–{match["end"]}</summary>'
                               f'<pre>{escape(match["before"])}<mark>{escape(match["text"])}</mark>'
                               f'{escape(match["after"])}</pre></details>')
        omitted = row.get("omitted_matches", 0)
        note = f'{row.get("count", 0)} occurrence(s)' if "count" in row else row.get("detail", "")
        if omitted:
            note += f"; first {MAX_MATCHES} shown, {omitted} further occurrences omitted"
        cards.append(f'<article><div class="tag">QUOTE {i:02} · {escape(row["status"])}</div>'
                     f'<h2>{escape(row["citation"])}</h2><blockquote>{escape(row["quote"])}</blockquote>'
                     f'<p>{escape(note)}</p>{"".join(occurrences)}</article>')
    originals = []
    for citation, source in receipt["sources"].items():
        if source["status"] != "AVAILABLE":
            continue
        originals.append(f'<details class="source"><summary>{escape(citation)} · full captured body</summary>'
                         f'<h3>{escape(source["title"])}</h3><p>Body SHA-256 <code>{source["sha256"]}</code></p>'
                         f'<p>Indexed: {escape(str(source.get("indexed_at") or "not supplied"))}. '
                         'Index time is not source freshness. Captured text may itself be incomplete or machine-authored.</p>'
                         f'<pre>{escape(source["body"])}</pre></details>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Source Audition — Claimcheck</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#080c12;color:#e5eaf2;line-height:1.6}
*{box-sizing:border-box}body{max-width:1020px;margin:auto;padding:clamp(18px,5vw,64px)}
h1{font-size:clamp(34px,6vw,64px);line-height:1.05;letter-spacing:-.045em;margin:.4em 0}
h2{font:600 16px ui-monospace,monospace;overflow-wrap:anywhere}h3{overflow-wrap:anywhere}
.tag{font:600 12px ui-monospace,monospace;letter-spacing:.08em;color:#8bdcc7}
.lede{max-width:680px;color:#b3c0d3;font-size:18px}article,.source{border:1px solid #2a3547;border-radius:12px;padding:22px;margin:24px 0;background:#101722}
blockquote{border-left:3px solid #8bdcc7;margin:20px 0;padding:0 0 0 18px;white-space:pre-wrap;overflow-wrap:anywhere}
pre{font:14px/1.75 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:#b3c0d3}
mark{background:#b7efd8;color:#081610;padding:2px 0}summary{cursor:pointer;padding:12px 0;overflow-wrap:anywhere}
summary:focus-visible{outline:3px solid #8bdcc7;outline-offset:5px}code{overflow-wrap:anywhere}
footer{color:#9caec6;font-size:13px} @media print{:root{color-scheme:light;background:white;color:black}article,.source{background:white;break-inside:avoid}pre,.lede,footer{color:#222}}
</style><header><div class="tag">CLAIMCHECK / CAPTURED WORDS</div><h1>Source Audition</h1>
<p class="lede">A quote is a sample. Inspect the cut, then read the original around it. No paraphrase, no invented bridge, no model verdict.</p>
''' + f'<p><strong>Word-match result: {escape(receipt["verdict"])}</strong></p>' + '''
<p>A match proves only that these words occur in the captured text. It does not prove truth, authorship, permission, or that a conclusion follows. NOT_FOUND means absent from this capture, not necessarily from the original.</p>
<p>Only whitespace may differ; punctuation and case are literal. Every occurrence is counted; up to twenty per quote are shown. Open a row with Enter or Space to inspect context.</p></header><main>
''' + "".join(cards) + '<h2>Original captures</h2>' + "".join(originals) + '''</main><footer>
<p>This file contains source excerpts and full captured bodies. Keep it private. No scripts, storage, external requests, or automatic uploads.</p>
<p>receipt.json alongside this file pins the original bodies, character offsets and SHA-256 checksums. Checksums detect accidental changes; they are not authenticated signatures.</p></footer></html>'''


def run(args):
    try:
        with Path(args.file).open("rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("input exceeds 256 KiB")
        rows = validate(json.loads(raw.decode("utf-8")))
        output = Path(args.out)
        if output.exists():
            raise ValueError("output directory already exists; choose a new path")
        receipt = audit(rows, lambda citation: fetch_body(citation, args.base))
        page = render(receipt)
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        # New directory only: never overwrite a source or an earlier receipt.
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "index.html").write_text(page, encoding="utf-8")
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"claimcheck quotes: {exc}", file=sys.stderr)
        return 1
    for row in receipt["rows"]:
        print(f'{row["status"]:10} {row["citation"]} ({row.get("count", "unchecked")} occurrences)')
    print(f'{receipt["verdict"]} — {output / "index.html"}')
    return {"MATCHED": 0, "MISMATCH": 2, "UNCHECKED": 3}[receipt["verdict"]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="JSON array of citation/quote objects")
    parser.add_argument("--out", required=True, help="new private output directory")
    parser.add_argument("--base", default=cc.DEFAULT_BASE_URL)
    raise SystemExit(run(parser.parse_args()))
