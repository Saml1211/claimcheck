# claimcheck bad sample — every defect class planted

This fixture deliberately fails. It is the fail path for the CLI: exit 2,
verdict FAIL. Missing and malformed citations are hard defects; the
unknown-source token is advisory noise (a warning, not a fabrication), so
the verdict here rests on the four hard defects below.

## Missing citation

This finding is attributed to a session that does not exist anywhere in the
library: claude-mem-work/obs-999999. It was never written.

## Malformed id

A reddit post id that breaks the shape the estate uses:
reddit/1V35X85UPPER — the shape gate must reject it before any lookup.

## Unknown source

This cites a corpus that does not exist: twitter/12345.

## Invented UUID

A Plausible-looking chatgpt id that resolves to nothing:
chatgpt/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.

## Bare obs that is nowhere

A bare token that neither memory corpus holds: obs-00000.

## Advisory block

This paragraph has no citation at all — advisory only, never a verdict
driver on its own.