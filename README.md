# claimcheck

Deterministic, zero-dependency citation-provenance linter for agent-authored
documents. It audits the citation grammar this estate actually writes in —
`chatgpt/<uuid>`, `perplexity/<uuid>`, `obs-<nnnnn>`, `reddit/<id>`,
`instagram/<id>`, `youtube/<id>`, `github-stars/<hex16>`,
`bookmarks/<hex16>`, `notes/<hex16>`, `obsidian-vault/<hex16>`,
`threads/<snowflake>`, `meetings/<slug>`, `voice-memos/<stamp>`,
`claude-mem/obs-<n>`, `claude-mem-work/obs-<n>`, plus bare `obs-<n>` /
`session-<n>` tokens and the URL forms that map to library ids — and verifies
every citation resolves against the live sams-brain library
(`http://127.0.0.1:8600`).

The rule it enforces is the estate's own: *cite what you found, or say there
were no hits*. A citation that cannot be looked up is a hole in the record,
not a style choice — the same standard Sam applies to any "got them all"
claim. The miners (agents) get mined (audited) by a linter.

Python 3.10+ (works on 3.13/3.14). Zero dependencies beyond the standard
library. No network at import time; the API is only touched per-lookup.

## What it checks

| Check | Verdict effect |
| --- | --- |
| Citation id shape matches its source's grammar | malformed → FAIL |
| Citation resolves to a real library item | missing → FAIL |
| Corpus source prefix is a known source | unknown → advisory (prose-looking tokens) |
| 8-hex abbreviated id (`chatgpt/67b66ffe`) | short-id → advisory — the library only resolves full ids |
| Bare `obs-<n>` exists in claude-mem-work or claude-mem (tried in that order) | missing → FAIL |
| Paragraph-level citation coverage | advisory only — prose legitimately carries none |

A live library that cannot be reached reports `UNCHECKED`, never a fake
MISSING: an offline corpus cannot prove a citation is bad, it can only say it
was not checked. Fenced code blocks, inline code spans and double-quoted
string literals are masked before scanning — JSON examples and shell output
are not prose. Unknown-source
tokens (`N/A`, table slashes) are advisory warnings, not verdict failures;
only a real citation in the known grammar that cannot be found (missing) or
is misshapen (malformed) fails the audit.

## Usage

```
python3 cli.py audit --file doc.md [--base URL] [--out results/receipt.json]
python3 cli.py resolve chatgpt/67e77ede-b5c4-8008-81af-bc032ed5311c
python3 cli.py selfcheck
```

Exit codes: `0` = audit passed, `2` = audit failed (missing / malformed /
unknown-source citations), `1` = usage or file error. With no arguments it
runs the shipped demo (`examples/good_sample.md`, which passes).

The demo fixture cites only real library items, verified live on 27/09/2026;
`examples/bad_sample.md` plants every defect class (missing id, malformed id,
unknown source, invented UUID, bare obs that is nowhere) and exits 2.

## Receipts

Every audit can emit a canonical JSON receipt: sorted keys, sha-256 signature
computed over the payload *without* the signature key, signature stored
inside the file. `claimcheck.verify_receipt()` re-hashes and compares without
mutating the payload.

## Provenance

The methodology is Sam's own work-history corpus, not web knowledge:

| Body | What it contributed |
| --- | --- |
| claude-mem-work/obs-31612 | Multi-agent assumption-audit workflow: every claim must cite a command run or file:line; null results must name their search scope — the spine of this tool |
| claude-mem-work/obs-21972 | Run-3 rulings audit: claims register with 12 verified claims, each with evidence citations, plus an explicit not-done section — the "verified vs asserted" accounting model |
| claude-mem-work/obs-21369 | Claims register expanded from 13 to 19 VERIFIED claims — the register pattern in motion |
| claude-mem-work/obs-20323 | Secret audit: 8 files scanned, 5 findings, metadata-only — the audit-scan convention |
| claude-mem-work/obs-23246 | Audit plan: guidance first, baseline second — staged audit discipline |
| claude-mem-work/obs-22004 | Work frozen pending ruling-authenticity audit — provenance gates real decisions, it is not decoration |
| claude-mem-work/obs-31612 (again) | Eight investigation lanes with structured CLAIMS output and independent skeptic refutation of high-impact claims |

The estate receipts the tool validates (sha-256 canonical, signature inside,
verify-by-rehash) follow the drum-pull-sheet / avixa-mcp seam contract. The
idea ("mining the miners") was first flagged by the lld-package run of
25/09/2026 as the next candidate seam.

## Verified

- 34 unittest pass on python3.14; CLI subprocess tests exercise the live
  library (demo exit 0, bad fixture exit 2, resolve found/miss paths).
- Determinism: two audits of the same doc are byte-identical.
- Real-doc proof: `avixa-mcp/README.md` (837 lines, 304 candidate tokens) —
  its 40 genuine citations all resolve, 0 missing, 0 malformed, PASS verdict.
- Receipt: `results/claimcheck-20260927/receipt.json` verdict PASS, signature
  re-hash valid.

`results/` is gitignored — receipts are run artifacts, like avixa-mcp's.

## Do not rebuild

The seam is the citation-grammar provenance audit against the library. Do not
rebuild a generic "markdown lint" tool, a URL checker, or an LLM-based claim
verifier — determinism is the point.