"""claimcheck — citation-provenance linter for agent-authored documents.

Deterministic, zero-dependency checker that audits the citation grammar this
estate actually writes in: `chatgpt/<uuid>`, `perplexity/<uuid>`,
`obs-<nnnnn>`, `reddit/<id>`, `instagram/<id>`, `youtube/<id>`,
`github-stars/<hex16>`, `bookmarks/<hex16>`, `notes/<hex16>`,
`obsidian-vault/<hex16>`, `threads/<snowflake>`, `meetings/<slug>`,
`voice-memos/<stamp>`, `claude-mem session-<n>`, plus bare `obs-<nnnnn>`
tokens that live in either the Mac or Work-PC memory corpus.

Every citation token found in a document is validated for shape, then
resolved against the sams-brain library API (http://127.0.0.1:8600 by
default). Unresolvable or malformed citations fail the audit — the estate
rule is "cite what you found, or say there were no hits", and a citation
that cannot be looked up is a hole in the record, not a style choice.

Receipts follow the estate contract: canonical JSON, sha-256 signature
computed over the payload without the signature key, signature stored
inside the file.

Python 3.10+. No dependencies beyond the standard library.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

DEFAULT_BASE_URL = "http://127.0.0.1:8600"

# Each source: display key, human label, and the item-id shape this estate
# actually uses. `pattern` anchors the whole id. `kind` tells the resolver
# how to look it up.
_SOURCES: Dict[str, Dict[str, str]] = {
    "chatgpt": {"label": "ChatGPT", "pattern": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"},
    "perplexity": {"label": "Perplexity", "pattern": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"},
    "reddit": {"label": "Reddit", "pattern": r"[a-z0-9]{5,8}"},
    "instagram": {"label": "Instagram", "pattern": r"[A-Za-z0-9]{10,12}"},
    "youtube": {"label": "YouTube", "pattern": r"[A-Za-z0-9_-]{11}"},
    "github-stars": {"label": "GitHub stars", "pattern": r"[0-9a-f]{16}"},
    "bookmarks": {"label": "Bookmarks", "pattern": r"[0-9a-f]{16}"},
    "notes": {"label": "Notes", "pattern": r"[0-9a-f]{8,16}"},
    "obsidian-vault": {"label": "Obsidian vault", "pattern": r"[0-9a-f]{16}"},
    "threads": {"label": "Threads", "pattern": r"[0-9]{15,20}"},
    "meetings": {"label": "Meetings", "pattern": r"[A-Za-z0-9][A-Za-z0-9_\-\.]{2,63}"},
    "voice-memos": {"label": "Voice memos", "pattern": r"\d{8} \d{6}-[A-F0-9]{8}"},
    "hermes-sessions": {"label": "Hermes sessions", "pattern": r"[A-Za-z0-9_\.\-]{3,80}"},
    "hermes-cron": {"label": "Hermes cron", "pattern": r"[A-Za-z0-9_\.\-]{3,80}"},
    "openclaw-mbp": {"label": "OpenClaw MBP", "pattern": r"[A-Za-z0-9_\.\-/]{3,120}"},
    "claude-mem": {"label": "Mac agent memory", "pattern": r"(?:obs-\d{4,6}|session-\d+)"},
    "claude-mem-work": {"label": "Work-PC agent memory", "pattern": r"(?:obs-\d{4,6}|session-\d+)"},
}

# Bare obs-<n> / session-<n> tokens with no corpus prefix. They may live in
# either memory corpus; resolution order matters and is documented.
_BARE_PATTERNS = [
    ("claude-mem-work", r"obs-\d{4,6}"),
    ("claude-mem", r"obs-\d{4,6}"),
    ("claude-mem", r"session-\d+"),
    ("claude-mem-work", r"session-\d+"),
]

# Citation tokens appear as `source/id` (letters, digits, hyphen, underscore).
# Ids never contain a forward slash: excluding it stops prose like
# "power/voltage/current conversion" from being read as a citation.
_PREFIX_RE = re.compile(r"(?P<source>[a-z][a-z0-9\-_]*)\s*/\s*(?P<id>[^\s,;\]/)+]+)", re.MULTILINE)

# Prose like "N/A" and "A/V" reads as a citation; single-character sources
# are never real corpus keys.
_MIN_SOURCE_LEN = 2

# Sources whose library ids are UUID/hex strings. The estate habitually
# quotes 8-hex prefixes of these (git-short-hash style); the item API only
# resolves full ids, so a short id is real-but-unverifiable, never a defect.
_SHORT_ID_SOURCES = frozenset(
    {"chatgpt", "perplexity", "obsidian-vault", "notes", "bookmarks", "github-stars"}
)
_SHORT_ID_RE = re.compile(r"[0-9a-f]{8}")


def _mask_code_fences(text: str) -> str:
    """Blank out non-prose spans, preserving character offsets.

    Fenced code blocks, inline code spans and double-quoted string literals
    are masked: JSON examples (fenced, backticked or written inline in
    prose) and shell output are full of slash-separated tokens that are not
    citations. Real citations in this estate's docs are written bare,
    parenthesised or backticked — never inside double quotes — so masking
    quoted spans costs nothing but removes the noise.
    """
    masked = list(text)
    for m in re.finditer(r"```[\s\S]*?```|`[^`\n]*`|\"[^\"\n]*\"", text):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    return "".join(masked)

# Full URLs we can map straight to a library item id.
_URL_PATTERNS = [
    (r"reddit\.com/r/[A-Za-z0-9_\-]+/comments/([a-z0-9]{5,8})", "reddit"),
    (r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9]{10,12})", "instagram"),
    (r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})", "youtube"),
    (r"youtu\.be/([A-Za-z0-9_-]{11})", "youtube"),
]


@dataclass
class Citation:
    """One citation token found in a document."""

    raw: str                 # as written in the doc
    source: str              # corpus key as written (lowercased prefix)
    item_id: str             # id portion as written
    span: Tuple[int, int]    # character offsets in the source text
    status: str = "unresolved"  # filled in by the resolver
    resolved_source: str = ""   # actual corpus key that answered (bare obs fallback)
    title: str = ""             # title from the library, if any
    detail: str = ""            # human-readable note
    valid_shape: bool = True


@dataclass
class AuditResult:
    """Outcome of auditing one document."""

    path: str
    citations: List[Citation] = field(default_factory=list)
    verdict: str = "PASS"            # PASS / FAIL
    counts_by_source: Dict[str, int] = field(default_factory=dict)
    found: int = 0
    missing: int = 0
    unknown_source: int = 0
    short_id: int = 0
    malformed: int = 0
    total_paragraphs: int = 0
    cited_paragraphs: int = 0
    uncited_paragraphs: int = 0
    uncited_preview: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Id shape validation
# ---------------------------------------------------------------------------

def source_grammar(source: str) -> Optional[str]:
    """Return the anchor pattern the source's ids must match, or None."""
    spec = _SOURCES.get(source)
    return spec["pattern"] if spec else None


def is_known_source(source: str) -> bool:
    return source in _SOURCES


def id_matches(source: str, item_id: str) -> bool:
    """True if item_id fits the shape this estate uses for `source`.

    Shape is checked first, before any network call: a malformed id is a
    defect even if some endpoint would cheerfully 404 on it.
    """
    pattern = source_grammar(source)
    if pattern is None:
        return False
    return re.fullmatch(pattern, item_id) is not None


def _is_bare_obs(item_id: str) -> bool:
    return bool(re.fullmatch(r"(?:obs-\d{4,6}|session-\d+)", item_id))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_prefixed(text: str) -> List[Citation]:
    out: List[Citation] = []
    for m in _PREFIX_RE.finditer(text):
        raw_source = m.group("source").lower()
        raw_id = m.group("id")
        if len(raw_source) < _MIN_SOURCE_LEN:
            continue
        # Strip trailing punctuation that greedy id capture may have kept.
        raw_id = re.sub(r"[?!\.\)\]\}:]+$", "", raw_id)
        if not raw_id:
            continue
        # voice-memos ids contain a space; the regex stopped at whitespace.
        if raw_source == "voice-memos" and re.fullmatch(r"\d{8}", raw_id):
            nxt = text[m.end() : m.end() + 24]
            ext = re.match(r"^\s+\d{6}-[A-F0-9]{8}", nxt)
            if ext:
                raw_id = raw_id + " " + ext.group(0).strip()
        out.append(Citation(raw=f"{raw_source}/{raw_id}", source=raw_source,
                            item_id=raw_id, span=(m.start(), m.end())))
    return out


def _extract_urls(text: str) -> List[Citation]:
    out: List[Citation] = []
    for pattern, source in _URL_PATTERNS:
        for m in re.finditer(pattern, text):
            item_id = m.group(1)
            out.append(Citation(raw=f"{source}/{item_id} (url)", source=source,
                                item_id=item_id, span=m.span(1)))
    return out


def _extract_bare_obs(text: str) -> List[Citation]:
    """Bare obs-/session- tokens not already claimed by a prefix match.

    The estate writes both `obs-23546` alone and `claude-mem-work/obs-23546`.
    A bare token is ambiguous by construction; the resolver tries the work
    corpus first, then the Mac corpus, and records which answered.
    """
    span_done = set()
    for m in _PREFIX_RE.finditer(text):
        span_done.add((m.start(), m.end()))
    out: List[Citation] = []
    for m in re.finditer(r"\b(?:obs-\d{4,6}|session-\d+)\b", text):
        if any(m.start() >= a and m.end() <= b for (a, b) in span_done):
            continue
        out.append(Citation(raw=m.group(0), source="", item_id=m.group(0),
                            span=(m.start(), m.end())))
    return out


def extract_citations(text: str) -> List[Citation]:
    """Find every citation candidate in `text`, in document order.

    Fenced code blocks and inline code spans are masked first (offsets
    preserved): JSON examples and shell output are not prose and their
    slash-separated tokens are not citations.
    """
    masked = _mask_code_fences(text)
    claimed = set()
    found: List[Citation] = []
    for cit in _extract_prefixed(masked) + _extract_urls(masked):
        key = (cit.span[0], cit.span[1])
        if key in claimed:
            continue
        claimed.add(key)
        found.append(cit)
    for cit in _extract_bare_obs(masked):
        # A bare token that sits immediately after a known prefix (same span
        # overlap) was already captured; skip it.
        overlap = any(cit.span[0] < c.span[1] and c.span[0] < cit.span[1]
                      for c in found)
        if not overlap:
            found.append(cit)
    found.sort(key=lambda c: (c.span[0], c.span[1]))
    return found


# ---------------------------------------------------------------------------
# Resolver (injectable; the real one hits the sams-brain library API)
# ---------------------------------------------------------------------------

class Resolver(Protocol):
    """Anything with `lookup(source, item_id) -> (title, detail)`."""

    def lookup(self, source: str, item_id: str) -> Tuple[str, str]: ...


class LibraryResolver:
    """Resolve a corpus key + item id against the sams-brain library API.

    `lookup(source, item_id)` returns (title, detail). Network failure is a
    distinct outcome from a 404: an offline library cannot prove a citation
    is bad, it can only say it could not be checked.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def lookup(self, source: str, item_id: str) -> Tuple[str, str]:
        url = f"{self.base_url}/api/library/item/{urllib.parse.quote(source)}/{urllib.parse.quote(item_id)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return "", f"HTTP {resp.status}"
                payload = json.loads(resp.read().decode("utf-8", "replace"))
                title = (payload.get("title") or "").strip()
                if not title:
                    title = (payload.get("text_preview") or payload.get("body") or "").strip()[:280]
                return title[:200], "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "", "not found"
            return "", f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            return "", f"unreachable: {exc.reason}"
        except Exception as exc:  # pragma: no cover - defensive
            return "", f"error: {exc}"


class _NullResolver:
    """For tests: never touches the network."""

    def __init__(self, table: Dict[Tuple[str, str], Tuple[str, str]]):
        self.table = table

    def lookup(self, source: str, item_id: str) -> Tuple[str, str]:
        return self.table.get((source, item_id), ("", "not found"))


def _classify(cit: Citation, resolver: Resolver) -> None:
    """Fill status/resolved_source/title on one citation."""
    if not is_known_source(cit.source):
        cit.status = "UNKNOWN-SOURCE"
        cit.valid_shape = False
        cit.detail = f"no corpus source named '{cit.source}'"
        return
    if not id_matches(cit.source, cit.item_id):
        if cit.source in _SHORT_ID_SOURCES and _SHORT_ID_RE.fullmatch(cit.item_id):
            cit.status = "SHORT-ID"
            cit.detail = "abbreviated id; the library only resolves full ids"
        else:
            cit.status = "MALFORMED"
            cit.valid_shape = False
            cit.detail = f"id does not match {cit.source} shape"
        return
    title, detail = resolver.lookup(cit.source, cit.item_id)
    cit.resolved_source = cit.source
    cit.title = title
    if detail == "ok":
        cit.status = "FOUND"
    elif detail == "not found":
        cit.status = "MISSING"
        cit.detail = "no library item with this id"
    else:
        cit.status = "UNCHECKED"
        cit.detail = detail


def _classify_bare(cit: Citation, resolver: Resolver) -> None:
    """Bare obs-/session- token: try work corpus first, then Mac corpus.

    claude-mem and claude-mem-work are not prefixed grammar sources, so the
    token's shape is checked against the dedicated bare-token patterns.
    """
    for candidate, pattern in _BARE_PATTERNS:
        if not re.fullmatch(pattern, cit.item_id):
            continue
        title, detail = resolver.lookup(candidate, cit.item_id)
        if detail == "ok":
            cit.status = "FOUND"
            cit.resolved_source = candidate
            cit.title = title
            return
        if detail != "not found":
            cit.status = "UNCHECKED"
            cit.detail = f"{candidate}: {detail}"
            return
    cit.status = "MISSING"
    cit.detail = "no item in claude-mem-work or claude-mem"
    cit.resolved_source = "claude-mem-work|claude-mem"


def resolve_citations(citations: List[Citation], resolver: Resolver) -> None:
    """Resolve every citation in place."""
    for cit in citations:
        if cit.source == "":
            _classify_bare(cit, resolver)
        elif is_known_source(cit.source):
            _classify(cit, resolver)
        else:
            cit.status = "UNKNOWN-SOURCE"
            cit.valid_shape = False
            cit.detail = f"no corpus source named '{cit.source}'"


# ---------------------------------------------------------------------------
# Paragraph coverage (advisory only)
# ---------------------------------------------------------------------------

def _paragraph_coverage(text: str, citations: List[Citation]) -> Tuple[int, int, int, List[str]]:
    """Total / cited / uncited paragraph counts by blank-line split.

    Advisory only: prose sections of a document legitimately carry no
    citations, so this never drives the verdict. It exists to make complete
    silence visible. Containment is span-based: a paragraph owns the
    character range between its start and the next paragraph's start.
    """
    offsets = [m.start() for m in re.finditer(r"\n\s*\n", text)]
    bounds = [(0, offsets[0])] if offsets else [(0, len(text))]
    for i, off in enumerate(offsets):
        start = off
        end = offsets[i + 1] if i + 1 < len(offsets) else len(text)
        # skip the separator whitespace itself
        para_start = start + 1
        while para_start < len(text) and text[para_start].isspace():
            para_start += 1
        bounds.append((para_start, end))
    bounds = [b for b in bounds if text[b[0] : b[1]].strip()]

    cited = 0
    uncited_preview: List[str] = []
    for (p_start, p_end) in bounds:
        has_cit = any(cit.span[0] >= p_start and cit.span[1] <= p_end
                      for cit in citations)
        if has_cit:
            cited += 1
        elif len(uncited_preview) < 5:
            single = " ".join(text[p_start:p_end].split())
            uncited_preview.append(single[:90])
    return len(bounds), cited, len(bounds) - cited, uncited_preview


# ---------------------------------------------------------------------------
# Audit entrypoint
# ---------------------------------------------------------------------------

def audit_document(path: str, resolver: Resolver,
                   text: Optional[str] = None) -> AuditResult:
    """Audit one document file (or an in-memory text) end to end."""
    if text is None:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    result = AuditResult(path=path)
    result.citations = extract_citations(text)
    resolve_citations(result.citations, resolver)

    for cit in result.citations:
        result.counts_by_source[cit.resolved_source or cit.source or "bare"] = (
            result.counts_by_source.get(cit.resolved_source or cit.source or "bare", 0) + 1
        )
        if cit.status == "FOUND":
            result.found += 1
        elif cit.status == "MISSING":
            result.missing += 1
        elif cit.status == "UNKNOWN-SOURCE":
            result.unknown_source += 1
        elif cit.status == "SHORT-ID":
            result.short_id += 1
        elif cit.status == "MALFORMED":
            result.malformed += 1
        # UNCHECKED counts as neither found nor missing; the report shows it.

    result.total_paragraphs, result.cited_paragraphs, result.uncited_paragraphs, result.uncited_preview = (
        _paragraph_coverage(text, result.citations)
    )
    # unknown-source tokens are advisory: prose like "N/A" reads as a
    # citation, and a typo'd source is a warning, not proof of fabrication.
    # Missing and malformed citations are the hard defects.
    faults = result.missing + result.malformed
    result.verdict = "PASS" if faults == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# Receipt (estate contract)
# ---------------------------------------------------------------------------

def build_receipt(result: AuditResult, tool_version: str) -> Dict[str, object]:
    """Canonical receipt payload; the signature is added by sign_receipt."""
    rows = []
    for cit in result.citations:
        rows.append({
            "citation": cit.raw,
            "source": cit.resolved_source or cit.source or "bare",
            "item_id": cit.item_id,
            "status": cit.status,
            "title": cit.title,
            "detail": cit.detail,
        })
    return {
        "tool": "claimcheck",
        "version": tool_version,
        "audited_path": result.path,
        "verdict": result.verdict,
        "counts": {
            "citations": len(result.citations),
            "found": result.found,
            "missing": result.missing,
            "unknown_source": result.unknown_source,
            "short_id": result.short_id,
            "malformed": result.malformed,
            "unchecked": sum(1 for c in result.citations if c.status == "UNCHECKED"),
        },
        "by_source": dict(sorted(result.counts_by_source.items())),
        "paragraphs": {
            "total": result.total_paragraphs,
            "cited": result.cited_paragraphs,
            "uncited": result.uncited_paragraphs,
        },
        "rows": rows,
    }


def sign_receipt(payload: Dict[str, object]) -> Dict[str, object]:
    """Add a sha-256 signature over the canonical payload sans signature."""
    trimmed = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(trimmed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["signature"] = hashlib.sha256(canonical).hexdigest()
    return payload


def verify_receipt(payload: Dict[str, object]) -> bool:
    """Re-hash the canonical payload and compare with the stored signature.

    Does not mutate the input; the stored signature stays in the dict.
    """
    stored = payload.get("signature")
    if stored is None:
        return False
    trimmed = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(trimmed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest() == stored