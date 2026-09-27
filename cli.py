#!/usr/bin/env python3
"""claimcheck CLI — audit a document's citations against the sams-brain library.

Usage:
  claimcheck audit --file <doc.md> [--base URL] [--out results/receipt.json]
  claimcheck resolve <source>/<id> [--base URL]
  claimcheck selfcheck

Exit codes: 0 = audit passed (all citations resolve), 2 = audit failed
(missing / malformed / unknown-source citations), 1 = usage or file error.
With no arguments, runs the built-in demo: audit examples/good_sample.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

import claimcheck as cc

VERSION = "1.0.0"


def _render_report(result: cc.AuditResult) -> str:
    lines: List[str] = []
    lines.append(f"claimcheck {VERSION} — {result.path}")
    lines.append(f"verdict: {result.verdict}")
    lines.append(f"citations: {len(result.citations)}  "
                 f"found={result.found} missing={result.missing} "
                 f"unknown-source={result.unknown_source} "
                 f"short-id={result.short_id} "
                 f"malformed={result.malformed} "
                 f"unchecked={sum(1 for c in result.citations if c.status == 'UNCHECKED')}")
    if result.counts_by_source:
        lines.append("by source: " + ", ".join(
            f"{k}={v}" for k, v in sorted(result.counts_by_source.items())))
    lines.append(f"paragraphs: {result.total_paragraphs} total, "
                 f"{result.cited_paragraphs} cited, "
                 f"{result.uncited_paragraphs} uncited (advisory)")
    for cit in result.citations:
        status = f"[{cit.status}]"
        line = f"  {status:<14} {cit.raw}"
        if cit.title:
            line += f"  — {cit.title[:70]}"
        if cit.detail:
            line += f"  ({cit.detail})"
        lines.append(line)
    if result.uncited_preview:
        lines.append("uncited paragraph preview (advisory):")
        for p in result.uncited_preview:
            lines.append(f"  · {p}")
    return "\n".join(lines)


def cmd_audit(args: argparse.Namespace) -> int:
    resolver = cc.LibraryResolver(base_url=args.base)
    try:
        result = cc.audit_document(args.file, resolver)
    except OSError as exc:
        print(f"claimcheck: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    print(_render_report(result))

    payload = cc.build_receipt(result, VERSION)
    signed = cc.sign_receipt(payload)
    out_path = getattr(args, "out", None)
    if out_path:
        import os
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(signed, fh, indent=2, sort_keys=True)
        print(f"receipt: {out_path} (sha-256 "
              f"{str(signed.get('signature', ''))[:16]}…)")
    return 0 if result.verdict == "PASS" else 2


def cmd_resolve(args: argparse.Namespace) -> int:
    if "/" not in args.token:
        print("usage: claimcheck resolve <source>/<id>", file=sys.stderr)
        return 1
    source, item_id = args.token.split("/", 1)
    if not cc.is_known_source(source):
        print(f"unknown source '{source}' (known: "
              f"{', '.join(sorted(cc._SOURCES))})", file=sys.stderr)
        return 2
    if not cc.id_matches(source, item_id):
        print(f"malformed id for {source}", file=sys.stderr)
        return 2
    resolver = cc.LibraryResolver(base_url=args.base)
    title, detail = resolver.lookup(source, item_id)
    if detail == "ok":
        print(f"{source}/{item_id}  FOUND  — {title}")
        return 0
    print(f"{source}/{item_id}  {'MISSING' if detail == 'not found' else 'UNCHECKED'}  ({detail})",
          file=sys.stderr)
    return 2 if detail == "not found" else 0


def cmd_selfcheck() -> int:
    failures: List[str] = []
    # shape table sanity
    for source in ("chatgpt", "perplexity", "reddit", "github-stars"):
        if cc.source_grammar(source) is None:
            failures.append(f"grammar missing for {source}")
    # extraction sanity
    sample = "see chatgpt/67e77ede-b5c4-8008-81af-bc032ed5311c and obs-23546"
    cites = cc.extract_citations(sample)
    if len(cites) != 2:
        failures.append(f"expected 2 citations, got {len(cites)}")
    # receipt lifecycle
    payload = {"tool": "claimcheck", "verdict": "PASS", "rows": []}
    signed = cc.sign_receipt(payload)
    if not cc.verify_receipt(signed):
        failures.append("receipt signature verify failed")
    if "signature" not in signed:
        failures.append("receipt missing signature key")
    if failures:
        print("selfcheck FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("selfcheck PASS")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claimcheck",
        description="Audit citation provenance in agent-authored documents.")
    parser.add_argument("--base", default=cc.DEFAULT_BASE_URL,
                        help="sams-brain library base URL")
    sub = parser.add_subparsers(dest="command")

    a = sub.add_parser("audit", help="audit a document's citations")
    a.add_argument("--file", required=True, help="markdown/text document to audit")
    a.add_argument("--out", default=None, help="receipt output path")

    r = sub.add_parser("resolve", help="resolve a single citation")
    r.add_argument("token", help="source/id to resolve")

    sub.add_parser("selfcheck", help="run the internal assertion battery")

    args = parser.parse_args(argv)

    if args.command == "audit":
        return cmd_audit(args)
    if args.command == "resolve":
        return cmd_resolve(args)
    if args.command == "selfcheck":
        return cmd_selfcheck()

    # Default: demo against the shipped examples.
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    demo = os.path.join(here, "examples", "good_sample.md")
    if not os.path.exists(demo):
        print("claimcheck: no demo fixture (examples/good_sample.md)",
              file=sys.stderr)
        return 1
    print("demo run: examples/good_sample.md")
    args.file = demo
    return cmd_audit(args)


if __name__ == "__main__":
    raise SystemExit(main())