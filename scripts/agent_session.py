"""Capture a real agent session over this package's MCP server and render it as Markdown.

    python scripts/agent_session.py capture read  "<prompt>"   # server without --write
    python scripts/agent_session.py capture write "<prompt>"   # server with --write
    python scripts/agent_session.py render docs/agent-session.md session-*.jsonl

`capture` runs Claude Code headless (`claude -p`) with only this package's MCP server
attached and stores the stream-json transcript; `render` turns transcripts into the
document in docs/agent-session.md. Nothing in the rendered file is typed by hand: every
tool call, tool result and reply is what the model and the server exchanged.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY = ROOT / "demos" / "metamorpho_base_sepolia"
RESULT_LIMIT = 1200


def capture(mode: str, prompt: str, out: Path) -> int:
    args = ["mcp", "-d", str(STRATEGY), "--chain", "base_sepolia"] + (["--write"] if mode == "write" else [])
    config = {"mcpServers": {"almanak": {"command": str(ROOT / ".venv" / "bin" / "almanak-keeperhub"), "args": args}}}
    config_path = out.with_suffix(".mcp.json")
    config_path.write_text(json.dumps(config))
    with out.open("w") as sink:
        proc = subprocess.run(  # noqa: S603 - fixed argv, our own prompt
            [
                "claude",
                "-p",
                prompt,
                "--mcp-config",
                str(config_path),
                "--strict-mcp-config",
                "--allowedTools",
                "mcp__almanak__*",
                "--max-turns",
                "10",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            check=False,
        )
    return proc.returncode


def _tidy(text: str) -> str:
    return text.replace(str(ROOT), "<repo>")


def render_session(path: Path) -> list[str]:
    """Replies in order; each MCP call followed by its own result, whatever order results arrived in."""
    calls: dict[str, dict[str, str]] = {}
    items: list[tuple[str, str]] = []  # ("text", reply) or ("call", tool_use_id)
    ended = ""
    for raw in path.read_text().splitlines():
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "assistant":
            for block in event["message"]["content"]:
                if block["type"] == "text":
                    items.append(("text", block["text"]))
                elif block["type"] == "tool_use" and block["name"].startswith("mcp__almanak__"):
                    args = json.dumps(block["input"])[1:-1]
                    calls[block["id"]] = {"call": f"{block['name'].removeprefix('mcp__almanak__')}({args})"}
                    items.append(("call", block["id"]))
        elif kind == "user":
            for block in event["message"]["content"]:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                call = calls.get(block.get("tool_use_id", ""))
                if call is None:
                    continue  # the client's own tools, not a server exchange
                content = block["content"]
                if not isinstance(content, str):
                    content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
                call["result"] = content
        elif kind == "result":
            ended = f"_{event.get('num_turns')} turns, ended: {event.get('subtype')}_"
    lines: list[str] = []
    for kind, value in items:
        if kind == "text":
            lines += ["", "**Claude**", "", _tidy(value), ""]
            continue
        call = calls[value]
        result = _tidy(call.get("result", ""))
        if len(result) > RESULT_LIMIT:
            result = result[:RESULT_LIMIT] + " ..."
        lines += ["", f"`{call['call']}`", "", "```json", result, "```"]
    return lines + ["", ended, ""]


def render(out: Path, sessions: list[tuple[str, str, Path]]) -> None:
    doc = [
        "# An agent at the controls",
        "",
        "Three real sessions of Claude over `almanak-keeperhub mcp`, captured with"
        " `scripts/agent_session.py` on 15 Sep 2026 and rendered without editing. The prompt is the only"
        " human input; every call, result and reply below is what the model and the server exchanged."
        " Execution ids and hashes resolve on the [console](https://prashant-thakur77.github.io/almanak-keeperhub/).",
        "",
        "Session 1 was recorded after 2 and 3, so the counts it reports include their executions: its first"
        " recording found that `list_dry_runs` answered with an empty list after a dry run, which was a bug in"
        " this package, fixed the same hour and re-recorded.",
        "",
    ]
    for title, prompt, path in sessions:
        doc += [f"## {title}", "", f"> {prompt}", ""]
        doc += render_session(path)
    out.write_text("\n".join(doc).rstrip() + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "capture":
        sys.exit(capture(sys.argv[2], sys.argv[3], Path(sys.argv[4])))
    manifest = json.loads(Path(sys.argv[3]).read_text())
    render(Path(sys.argv[2]), [(s["title"], s["prompt"], Path(s["file"])) for s in manifest])
