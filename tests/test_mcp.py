import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_artifact_cli import api, mcp

ARTIFACT = "11111111-1111-4111-8111-111111111111"


class Client:
    version = "v1"
    source = b"<title>Before</title>\n"

    def __init__(self):
        self.meta = {"title": "Preserved title"}
        self.published = []
        self.fail_publish = None

    def files(self, artifact_id):
        if artifact_id != ARTIFACT:
            raise AssertionError("Unexpected artifact")
        return self

    def fetch(self, path):
        if path != "index.html":
            raise AssertionError("Unexpected published path")
        return self.source

    def current_version(self, artifact_id):
        if artifact_id != ARTIFACT:
            raise AssertionError("Unexpected artifact")
        return self.version

    def publish(self, **kwargs):
        if self.fail_publish:
            raise self.fail_publish
        self.published.append(kwargs)
        return {"slug": ARTIFACT, "version": "v2", "assetToken": "do-not-return-this"}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "index.html"
        self.file.write_text("<title>After</title>\n")
        self.client = Client()
        self.factory = mock.Mock(return_value=self.client)
        self.bridge = mcp.Bridge(ARTIFACT, self.root, self.factory)

    def preview(self):
        return self.bridge.call("artifact_preview", {
            "local_path": "index.html", "base_version": "v1"
        })["preview_id"]

    def call_request(self, name, args):
        return mcp.dispatch(self.bridge, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })["result"]

    def test_read_then_preview_then_publish_keeps_target_and_base(self):
        read = self.bridge.call("artifact_read", {})
        self.assertEqual(read["version"], "v1")
        preview_id = self.preview()
        receipt = self.bridge.call("artifact_publish", {"preview_id": preview_id})
        sent = self.client.published[0]
        self.assertEqual(sent["slug"], ARTIFACT)
        self.assertEqual(sent["base_version"], "v1")
        self.assertEqual(sent["mode"], "patch")
        self.assertEqual(sent["page"].path, "index.html")
        self.assertEqual(sent["meta"], {"title": "Preserved title"})
        self.assertNotIn("force", sent)
        self.assertNotIn("do-not-return-this", json.dumps(receipt))
        with self.assertRaises(mcp.BridgeError):
            self.bridge.call("artifact_publish", {"preview_id": preview_id})

    def test_local_change_after_preview_is_refused(self):
        preview_id = self.preview()
        self.file.write_text("<title>Changed again</title>")
        self.factory.reset_mock()
        with self.assertRaisesRegex(mcp.BridgeError, "changed after preview"):
            self.bridge.call("artifact_publish", {"preview_id": preview_id})
        self.factory.assert_not_called()

    def test_missing_live_title_is_refused_instead_of_renaming(self):
        self.client.meta = {}
        with self.assertRaisesRegex(mcp.BridgeError, "title is unavailable"):
            self.preview()
        self.assertEqual(self.client.published, [])

    def test_stale_preview_and_stale_publish_are_refused(self):
        with self.assertRaisesRegex(mcp.BridgeError, "Version conflict"):
            self.bridge.call("artifact_preview", {
                "local_path": "index.html", "base_version": "old"
            })
        preview_id = self.preview()
        self.client.version = "new"
        with self.assertRaisesRegex(mcp.BridgeError, "Version conflict"):
            self.bridge.call("artifact_publish", {"preview_id": preview_id})
        self.assertEqual(self.client.published, [])

    def test_server_race_conflict_is_sanitized(self):
        preview_id = self.preview()
        self.client.fail_publish = api.ConflictError("private server body", 409)
        result = self.call_request("artifact_publish", {"preview_id": preview_id})
        self.assertTrue(result["isError"])
        self.assertIn("Version conflict", result["content"][0]["text"])
        self.assertNotIn("private server body", json.dumps(result))

    def test_target_override_and_unknown_args_are_refused_before_auth(self):
        result = self.call_request("artifact_read", {"artifact_id": "another"})
        self.assertTrue(result["isError"])
        self.factory.assert_not_called()

    def test_paths_traversal_absolute_hidden_and_symlinks_are_refused(self):
        for path in ("../outside.html", "/tmp/outside.html", ".git/test.html",
                     "a/../index.html", "x\\index.html", "not-html.txt"):
            with self.subTest(path=path), self.assertRaises(mcp.BridgeError):
                self.bridge.call("artifact_preview", {
                    "local_path": path, "base_version": "v1"
                })
        if os.name != "nt":
            link = self.root / "link.html"
            link.symlink_to(self.file)
            with self.assertRaisesRegex(mcp.BridgeError, "Symlink"):
                self.bridge.local_source("link.html")
        self.factory.assert_not_called()

    def test_source_size_and_utf8_are_checked(self):
        self.file.write_bytes(b"x" * (mcp.MAX_SOURCE + 1))
        with self.assertRaisesRegex(mcp.BridgeError, "limit"):
            self.bridge.local_source("index.html")
        self.file.write_bytes(b"\xff")
        with self.assertRaisesRegex(mcp.BridgeError, "UTF-8"):
            self.bridge.local_source("index.html")

    def test_transport_exception_body_is_never_returned(self):
        self.factory.side_effect = RuntimeError("secret-token-in-a-url")
        result = self.call_request("artifact_read", {})
        self.assertTrue(result["isError"])
        self.assertNotIn("secret-token", json.dumps(result))

    def test_notifications_unknown_methods_and_invalid_params(self):
        self.assertIsNone(mcp.dispatch(self.bridge, {"method": "notifications/initialized"}))
        self.assertEqual(mcp.dispatch(self.bridge, {
            "id": 3, "method": "unknown"
        })["error"]["code"], -32601)
        self.assertEqual(mcp.dispatch(self.bridge, {
            "id": 4, "method": "ping", "params": []
        })["error"]["code"], -32602)
        self.assertEqual(len(mcp.tool_definitions()), 3)

    def test_stdio_initialization_and_parse_error_do_not_load_auth(self):
        requests = [
            ('{"jsonrpc":"2.0","id":1,"method":"initialize",'
             '"params":{"protocolVersion":"2025-03-26"}}\n'),
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            'bad-json\n',
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("".join(requests))), \
                mock.patch("sys.stdout", output), \
                mock.patch("claude_artifact_cli.auth.get_token") as token:
            self.assertEqual(mcp.main([
                "--artifact-id", ARTIFACT, "--workspace", str(self.root)
            ]), 0)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(rows[-1]["error"]["code"], -32700)
        token.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Windows symlinks can require extra privileges")
    def test_symlink_workspace_is_refused(self):
        link = self.root / "workspace"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(mcp.BridgeError):
            mcp.Bridge(ARTIFACT, link, self.factory)
