# Scoped artifact MCP bridge

This fork adds a small stdio MCP server for direct artifact editing from Codex
or another MCP client. It calls the existing Python frame client, without
starting Claude Code or making a model inference request. This still uses the
Claude account's authentication and Anthropic's **unofficial, private API**.

## Start

After installing this fork, start:

~~~bash
claude-artifact-mcp --artifact-id YOUR_ARTIFACT_UUID --workspace /absolute/path/to/html
~~~

For a source checkout without installing:

~~~bash
PYTHONPATH=src python3 -m claude_artifact_cli.mcp \
  --artifact-id YOUR_ARTIFACT_UUID --workspace /absolute/path/to/html
~~~

An MCP host can launch that same command over stdio. Configure its working
directory or an absolute PYTHONPATH pointing to this fork's src directory.
Do not put tokens in the MCP configuration: the server uses the existing Claude
Code login. The MCP entry point does not invoke the CLI's update checker.

## Tools and workflow

1. artifact_read({}) returns the configured artifact's live index.html, version
   and SHA-256.
2. Edit a UTF-8 .html file inside the configured workspace.
3. artifact_preview({"local_path":"index.html","base_version":"VERSION_FROM_READ"})
   returns a diff and a preview_id, without publishing.
4. artifact_publish({"preview_id":"ID_FROM_PREVIEW"}) verifies that neither the
   local file nor the remote version has changed and publishes index.html in
   patch mode, preserving the live title (required by the server). The server
   also checks baseVersion to catch a concurrent update.

Both preview and publish must happen in the same running server process.
Published previews are single-use; restarting the server discards every preview.
This is a transport bridge, not an independent human approval system: the MCP
host must enforce the user's authorization before calling the publish tool.

The target is fixed at startup, with no per-call URL or API-host override. Only
index.html is updated; supporting files, stored runtime capabilities and database
documents are not part of the patch. There are no delete, share, force-publish,
arbitrary-shell, database-write or create-artifact tools. Local source files have
a 1 MiB limit. Absolute paths, hidden paths, parent traversal and symlinks are
refused. Errors never return raw server bodies or credential lookup contents.
This is intentionally a narrow prototype, not a replacement for the full CLI.

## Verification

The standard offline suite includes MCP protocol and change-boundary checks:

~~~bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
~~~

To explicitly create **one new private scratch artifact** and run a real MCP
round trip, use a new workspace:

~~~bash
PYTHONPATH=src CLAUDE_ARTIFACT_NO_UPDATE_CHECK=1 \
  python3 examples/verify_mcp_live.py --workspace /absolute/path/to/new-scratch
~~~

The live script creates a two-file demo, starts the stdio MCP server, checks
read/preview/publish, verifies the same artifact URL and supporting CSS, and
attempts stale and altered-source writes that must be refused. It saves a
sanitized receipt immediately after creation so a partial failure remains
recoverable. It leaves the private scratch artifact available for inspection;
it does not modify the production session board or automatically delete anything.
After a partial failure, --resume reuses that scratch artifact only if its source
is still the initial demo. A changed or successfully verified artifact is refused.
