# Direct artifact MCP verification

Verified on macOS with Python 3.14.7 on 2026-10-30.
Upstream baseline: 5cecd0c8b4fcfb94575b6d581b17b50f6d13441d.

## Local checks

- 188 existing tests passed before adding the bridge.
- All 200 tests passed after the bridge was added, including 12 MCP boundary tests.
- Ruff passed.
- Wheel and sdist built successfully and passed strict Twine checks.
- Installed wheel: CLI publish help and the new MCP entry point help both worked.

## Live, authenticated verification

An explicitly invoked script created one private scratch artifact containing
index.html and proof.css. It then launched a separate Python MCP server process
and communicated over JSON-RPC stdio.

All ten checks passed:

1. MCP initialization and tool discovery.
2. Exact source read, verified by SHA-256.
3. Diff preview against the live version.
4. A new version published at the same artifact URL; its source hash matched.
5. The supporting CSS file remained byte-for-byte unchanged.
6. A preview against a stale version was refused.
7. A local file edited after preview could not be published.
8. A per-call artifact target override was refused.
9. The real server refused a publish with a stale baseVersion (HTTP 409).
10. Refused writes left the live version unchanged.

The scratch artifact also appeared as private in the Claude desktop artifact
library. Opening it in the app displayed the new PC/OS/AI badges and the updated
MCP verification message, on the same version whose source hash was verified.

The initial publish attempt exposed an undocumented server requirement: a patch
must include a title. The bridge now preserves the title read from the live
artifact instead of omitting or inventing it, with a regression test.

No Claude chat session, Claude Code inference process or model inference API was
used by the bridge. It used the existing Claude account's login to call the
artifact service directly. Credentials and raw metadata were never written into
this repository. Scratch IDs and the detailed receipt remain in the local
verification workspace, not in this public report.

This verifies direct artifact publishing, not production deployment of the
company hub. The production session board and company hub configuration were
not modified. The artifact API is unofficial and may change independently.
