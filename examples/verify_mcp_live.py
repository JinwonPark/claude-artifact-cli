"""Create a private scratch artifact and verify the scoped MCP round trip.

Run explicitly; this is not part of the offline test suite. The report contains
only artifact IDs, versions, hashes and checks, never credentials or raw metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from claude_artifact_cli import api, auth

SHELL = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codex 직접 수정 검증</title><link rel="stylesheet" href="proof.css">
<style>body{margin:0;background:#f3f6fa;color:#243248;font:16px system-ui}
main{max-width:820px;margin:8vh auto;padding:36px;background:white;border:1px solid #dce3ec;
border-radius:18px}h1{font-size:30px;letter-spacing:-.04em}p{line-height:1.8;color:#526178}
.label{font:12px monospace;color:var(--accent)}.badges{display:flex;flex-wrap:wrap;gap:10px;
margin:28px 0}.badge{padding:10px 14px;border:1px solid;border-radius:9px;font-weight:600}
.pc{background:#eef1f5;border-color:#cdd4df;color:#3b4555}
.os{background:#e8f1fc;border-color:#b9d3f2;color:#1d4b85}
.ai{background:#e7f5ec;border-color:#b3dbc0;color:#1c6436}
.pass{color:#1c6436;font-weight:700}footer{border-top:1px solid #e5eaf2;margin-top:30px;
padding-top:20px;font-size:13px;color:#64748b}@media(max-width:700px){main{margin:20px;
padding:24px}}</style><main><div class="label">CODEX → MCP → ARTIFACT</div>
<h1>Claude 대리 요청 없이 직접 수정</h1>
<p>개인 포크의 Python 코드가 기존 Claude 계정으로 저장합니다.<br>
Claude 모델이나 Claude 채팅 세션을 호출하지 않습니다.</p>
REPLACE_BADGES
<p class="pass">REPLACE_STATUS</p>
<footer>검증용 비공개 아티팩트 · 실제 세션 현황판과 분리</footer></main></html>"""
BEFORE = SHELL.replace("REPLACE_BADGES", "<p>PC 검증 PC · OS macOS · AI Codex</p>").replace(
    "REPLACE_STATUS", "1단계: 직접 생성 완료"
)
AFTER = SHELL.replace("REPLACE_BADGES", """<div class="badges">
<span class="badge pc">PC | 검증 PC</span><span class="badge os">OS | macOS</span>
<span class="badge ai">AI | Codex</span></div>""").replace(
    "REPLACE_STATUS", "2단계: MCP로 읽기·미리보기·직접 재게시 완료"
)
CSS = b":root{--accent:#3266c8}\n"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def run(workspace: Path, resume=False):
    workspace.mkdir(parents=True, exist_ok=True)
    receipt_path = workspace / "receipt.json"
    client = api.FrameClient(auth.get_token(), timeout=30)
    if receipt_path.exists():
        if not resume:
            raise RuntimeError("Use --resume or a new workspace; an artifact already exists.")
        report = json.loads(receipt_path.read_text())
        artifact_id = report["artifact_id"]
        if report.get("status") == "passed":
            raise RuntimeError("This verification has already passed.")
        existing = client.files(artifact_id)
        if existing.fetch("index.html") != BEFORE.encode():
            raise RuntimeError("The scratch source changed; refusing to resume publishing.")
    else:
        created = client.publish(
            api.Asset("index.html", BEFORE.encode(), "text/html"),
            extra=[api.Asset("proof.css", CSS, "text/css")],
            meta={"title": "Codex 직접 수정 검증", "favicon": "🧪"},
        )
        artifact_id = created["slug"]
        report = {
            "artifact_id": artifact_id,
            "url": api.ARTIFACT_URL.format(slug=artifact_id),
            "initial_version": created.get("version"),
            "checks": {},
        }
    # Save the ID immediately so even a failed verification is recoverable.
    receipt_path.write_text(json.dumps(report, indent=2) + "\n")
    local = workspace / "index.html"
    local.write_text(AFTER, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "claude_artifact_cli.mcp", "--artifact-id", artifact_id,
         "--workspace", str(workspace)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "CLAUDE_ARTIFACT_NO_UPDATE_CHECK": "1"},
    )
    sequence = 0

    def rpc(method, params):
        nonlocal sequence
        sequence += 1
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": sequence, "method": method, "params": params
        }) + "\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        assert reply["id"] == sequence and "result" in reply
        return reply["result"]

    def tool(name, args, error=False):
        result = rpc("tools/call", {"name": name, "arguments": args})
        if error:
            assert result.get("isError"), "Expected a refused operation"
            return result["content"][0]["text"]
        assert not result.get("isError"), "MCP operation failed (see sanitized MCP error)"
        return json.loads(result["content"][0]["text"])

    def passed(name):
        report["checks"][name] = True
        receipt_path.write_text(json.dumps(report, indent=2) + "\n")

    try:
        rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "codex-verification", "version": "1"}})
        assert len(rpc("tools/list", {})["tools"]) == 3
        passed("mcp_initialization")
        original = tool("artifact_read", {})
        assert original["sha256"] == sha(BEFORE.encode())
        original_version = original["version"]
        passed("source_read")
        preview = tool("artifact_preview", {
            "local_path": "index.html", "base_version": original_version
        })
        assert preview["diff"] and preview["sha256"] == sha(AFTER.encode())
        passed("change_preview")
        published = tool("artifact_publish", {"preview_id": preview["preview_id"]})
        current = tool("artifact_read", {})
        assert current["sha256"] == sha(AFTER.encode())
        assert current["version"] != original_version
        report.update(final_version=current["version"], final_sha256=current["sha256"])
        assert published["version"] == current["version"]
        passed("same_artifact_republished")
        assert sha(client.files(artifact_id).fetch("proof.css")) == sha(CSS)
        passed("supporting_file_preserved")
        assert "conflict" in tool("artifact_preview", {
            "local_path": "index.html", "base_version": original_version
        }, error=True).lower()
        passed("stale_preview_refused")
        local.write_text(AFTER + "\n<!-- preview-only -->", encoding="utf-8")
        second = tool("artifact_preview", {
            "local_path": "index.html", "base_version": current["version"]
        })
        local.write_text(AFTER + "\n<!-- changed-after-preview -->", encoding="utf-8")
        assert "changed after preview" in tool("artifact_publish", {
            "preview_id": second["preview_id"]
        }, error=True)
        local.write_text(AFTER, encoding="utf-8")
        passed("post_preview_local_change_refused")
        tool("artifact_read", {"artifact_id": "another-target"}, error=True)
        passed("target_override_refused")
        try:
            client.publish(api.Asset("index.html", BEFORE.encode(), "text/html"),
                           slug=artifact_id, base_version=original_version, mode="patch",
                           meta={"title": "Codex 직접 수정 검증"})
        except api.ConflictError:
            passed("server_version_conflict_refused")
        else:
            raise RuntimeError("Server unexpectedly accepted a stale version.")
        assert client.current_version(artifact_id) == current["version"]
        passed("failed_writes_left_live_version_unchanged")
    finally:
        proc.stdin.close()
        proc.wait(timeout=40)
        if proc.returncode:
            raise RuntimeError("MCP subprocess did not exit cleanly.")
    report["status"] = "passed"
    receipt_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--resume", action="store_true",
                        help="Resume only if the saved scratch artifact is still the initial demo")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.workspace.resolve(), args.resume), indent=2))
    except Exception as error:  # noqa: BLE001 - never print upstream credential-bearing errors
        # Never print upstream error bodies or credential lookup contents.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
