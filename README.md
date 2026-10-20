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

## Source Audition: check the quoted words, not just the citation

`quotecheck.py` checks an explicit list of quotations against the **full captured
body** of each cited Library item and writes an offline context viewer plus a
checksummed JSON receipt. No model, added dependencies, server, or account setup.
The original citation-linting commands above are unchanged.

Try it in one command (requires sams-brain on its usual local port):

```sh
cd ~/Projects/Personal/claimcheck && out="$(mktemp -d /tmp/claimcheck-quotes.XXXXXX)/audition" && { python3 quotecheck.py --file examples/quotes.json --out "$out"; status=$?; [ "$status" -eq 2 ] && open "$out/index.html"; }
```

The demo deliberately changes **quarter notes** to **eighth notes**. Exit `2`
is expected: the real wording matches, its line-wrapped variant matches, and
the invented wording does not. The Reddit example also shows why a genuine
verbatim match is not a verdict that a claim is true. Open any occurrence to
see the surrounding text; expand a full capture below to inspect the rest.
The output is private, stays outside git, and makes no browser network requests.

For your own input, use a JSON array with exactly `citation` and `quote` per row:

```json
[{"citation":"youtube/2q2cmwjxink","quote":"I normally leave it on quarter notes at first"}]
```

```sh
python3 quotecheck.py --file my-quotes.json --out results/my-new-audition
python3 -m unittest -v test_quotecheck
```

- Only whitespace runs may differ. Case, punctuation, numbers, word order and
  ellipses are literal; no fuzzy matching, paraphrase approval or stitched quotes.
- Receipts distinguish `EXACT` from `WHITESPACE_ONLY` occurrences and preserve
  the actual matched text. Every occurrence is counted; the first twenty get
  surrounding context. Offsets are zero-based Unicode characters, end exclusive.
- Exit `0`: all words matched. Exit `2`: a quote was absent or an item returned
  HTTP 404. Exit `3`: a body could not be checked. Exit `1`: invalid input or a
  file error. A definite mismatch takes precedence over unchecked rows; the
  receipt keeps the individual reasons. The tool never treats a preview as a body.
- Inputs require explicit full Library IDs using Claimcheck's existing source
  grammars (not bare observation IDs, shortened UUIDs or arbitrary URLs).
  Limits: 32 quotes, 8–8000 characters per quote, 256 KiB input, 4 MiB per HTTP
  response, one million characters per captured body and four million per run.
- Output directories must be new: earlier receipts and input files are never
  overwritten. API timeouts/server errors produce `UNCHECKED`, not `MISSING`.
- A match proves presence in **the capture only**, not truth, attribution,
  copyright permission, logical support, or that the live upstream page agrees.
  Library bodies can be incomplete, transcribed or machine-authored. Fresh index
  timestamps do not establish fresh source captures. Receipts contain full bodies:
  keep them private. SHA-256 checksums detect changes, not malicious forgery.

### From your library

The connection is **sample audition × quotation provenance**: inspect the cut
and the original around it, instead of trusting a plausible transformation.
These are saved-source leads, not claims of their authors' endorsement.

| Saved item | What was applied or deliberately left out |
| --- | --- |
| [Will Hatton's sampling techniques](https://www.youtube.com/watch?v=2q2cmwjxink), youtube/2q2cmwjxink | Slice a source into specific passages and audition them in context; supplies the quarter/eighth-note demonstration. |
| [Sampling guide](https://www.youtube.com/watch?v=xaQX4cCZSdA), youtube/xaQX4cCZSdA | Companion sampling material read while selecting the seam; no audio DSP or new music tool was built. |
| [130 bpm B-minor vocal sample](https://www.instagram.com/p/DFvHMTnMjFj/), instagram/DFvHMTnMjFj | Sampling is offered with context and reuse terms; the viewer explicitly does **not** infer permission from a word match. Captured text, not an audio inspection, informed this boundary. |
| [Clank Chaos / Proteo](https://www.instagram.com/p/CuUOMggv05Z/), instagram/CuUOMggv05Z | Captured description of continually sampled waveforms suggested freezing one source body per run rather than matching against a moving source. No visual or hardware claims were tested. |
| [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw), github-stars/bea17e727e41b86f | Citation verification as a research gate; implemented only deterministic quoted-word checks, not an autonomous research pipeline. |
| [MCP Inspector](https://github.com/modelcontextprotocol/inspector), github-stars/7665e4161209b465 | An inspection surface beside a machine-readable result; here native HTML details show what the checker actually saw. No MCP dependency added. |
| [ShellCheck](https://github.com/koalaman/shellcheck), github-stars/0b1fed5998c74047 | Deterministic diagnostics and nonzero exits rather than a language-model judgment. No shell-analysis feature added. |
| Artifact Extraction Request, chatgpt/687305b8-76c0-8008-bed9-5e50a72db406 | Extraction rather than advice; delivered runnable code and source excerpts, not a reading list. |
| [WebFetch research caution](https://www.reddit.com/r/ClaudeAI/comments/1vim8b7/), reddit/1vim8b7 | The reported fabricated quotations motivated looking in captured bodies, never generated fetch summaries. The thread's claims about particular models/tools remain unverified. |

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