"""
tools/agent_tools.py
All tools the AI agents can use autonomously.

Tools:
  create_file    — Write a new file (creates dirs too)
  read_file      — Read a file's contents
  edit_file      — Replace a specific string inside a file
  delete_file    — Delete a file
  list_dir       — List directory contents recursively
  bash           — Run any shell command (cwd=workspace, credential-shaped env
                   vars stripped, destructive-pattern tripwire — best-effort
                   containment, NOT a security boundary; see _BLOCKED)
  search_web     — Search DuckDuckGo for current info
  fetch_url      — Fetch a web page's content
  git            — Run git commands in the workspace
  vision_analyze — Analyse a video or image file through VisionTeam (Gemini 2.5 Flash + Whisper)
  ssh_connect    — Open an SSH connection to a remote server
  ssh_exec       — Run a shell command on a connected remote server
  ssh_upload     — Upload a file to a remote server via SFTP
  ssh_download   — Read a remote file's content via SFTP
  ssh_list       — List active SSH connections
  ssh_disconnect — Close an SSH connection
  github         — Full GitHub REST API (repos, PRs, issues, files, CI/CD, releases, search)

Safety (honest scope):
  - File-path tools ARE sandboxed to the workspace (_safe_path, boundary-checked)
  - bash is CONTAINED, not sandboxed: cwd=workspace, credential-shaped env vars
    stripped, timeout-limited, destructive patterns tripwired — but it can still
    reference paths outside the workspace and reach the network. For untrusted
    inputs, run the whole system in a container.
  - Output is truncated at 10KB to prevent context overflow
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

# ── Default workspace (override via env VIBE_WORKSPACE) ──────────────────────
DEFAULT_WORKSPACE = Path(os.getenv("VIBE_WORKSPACE", "./workspace")).resolve()


@dataclass
class ToolResult:
    tool_name:  str
    call_id:    str
    success:    bool
    output:     str
    duration_ms: float


# ── Tool JSON schemas (OpenAI function-calling format) ────────────────────────

TOOL_SCHEMAS_CORE = [
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the specified content. Creates parent directories automatically. Use this to generate code files, configs, READMEs etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace (e.g. src/main.py)"},
                    "content": {"type": "string", "description": "Full content to write to the file"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file. Use before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string in a file. old_str must match the file exactly (including whitespace). Always read_file first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "File path relative to workspace"},
                    "old_str": {"type": "string", "description": "Exact text to find and replace"},
                    "new_str": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or empty directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List all files and directories in a path. Use to understand project structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: . = workspace root)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a terminal command and get its output. Use for: running Python scripts, installing packages, running tests, git commands, compiling, etc. Commands run in the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 90)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for current information, docs, examples, or solutions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch the text content of any URL — useful for reading documentation, GitHub files, or API references.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file or directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src":  {"type": "string", "description": "Source path relative to workspace"},
                    "dest": {"type": "string", "description": "Destination path relative to workspace"},
                },
                "required": ["src", "dest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run a git command in the workspace. Use for: git init, git add, git commit, git status, git log, git diff, git checkout, git push, git pull.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {"type": "string", "description": "Git arguments (e.g. 'status', 'add .', 'commit -m \"initial\"', 'log --oneline -5')"},
                },
                "required": ["args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": (
                "Analyse a video or image file using the VisionTeam AI pipeline. "
                "For videos: runs motion analysis, extracts key frames, transcribes audio (Whisper), "
                "and sends everything to Llama 4 Maverick + a vision model for detailed analysis. "
                "For images/screenshots: runs through Gemini 2.5 Flash (UI/UX critic) and Gemini 2.0 Flash (OCR). "
                "Use this whenever the user asks you to look at, describe, analyse, or debug a video or image file. "
                "Supported: .mp4 .mov .avi .mkv .webm .frames .png .jpg .jpeg .gif .webp .bmp"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the video or image file — relative to workspace or absolute",
                    },
                    "question": {
                        "type": "string",
                        "description": "Optional specific question or instruction for the analysis (e.g. 'What bugs are visible?', 'Describe the UI layout')",
                    },
                },
                "required": ["path"],
            },
        },
    },

    # ── Design Team (AI image generation) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "design_asset",
            "description": (
                "Generate AI images for websites, landing pages, and UIs using FLUX (free, no key needed). "
                "Returns direct image URLs you can embed with <img src='URL'> or use as CSS background-image. "
                "ALWAYS call this BEFORE writing HTML when building any landing page, website, dashboard, or UI — "
                "use it for: hero images, background textures, banners, icons, card illustrations, section images. "
                "The generated URLs work directly in browsers — no download needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            # Deliberately theme-neutral example: this schema text is the
                            # ONLY task-shaped string a context-starved model still sees,
                            # and it WILL copy it — verified live 2026-07-06, when a
                            # 'Minecraft server hosting website' example here became an
                            # entire hallucinated Minecraft-hosting project after
                            # truncation deleted the real task from context.
                            "Rich visual description of the image to generate, matching THE USER'S CURRENT PROJECT. "
                            "Include: subject, mood, color palette, style "
                            "(e.g. 'dark abstract hero banner, glowing gradient waves, deep blue and purple, 16:9')"
                        ),
                    },
                    "asset_type": {
                        "type": "string",
                        "description": "Type of asset: banner, hero, background, icon, card, illustration, animation",
                    },
                    "width":  {"type": "integer", "description": "Width in pixels (default 1280)"},
                    "height": {"type": "integer", "description": "Height in pixels (default 720 for hero/banner, 512 for icon)"},
                },
                "required": ["description"],
            },
        },
    },
]

_SSH_SCHEMAS: list[dict] = [
    # ── SSH Remote Terminal ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "ssh_connect",
            "description": (
                "Open an SSH connection to a remote server. "
                "Returns a connection_id you must pass to ssh_exec, ssh_upload, ssh_download, ssh_disconnect. "
                "Provide either key_path (path to private key file) or password. "
                "Optional alias lets you name the connection (e.g. 'prod', 'staging')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string",  "description": "Remote hostname or IP address"},
                    "port":     {"type": "integer", "description": "SSH port (default 22)"},
                    "username": {"type": "string",  "description": "SSH username (default 'root')"},
                    "key_path": {"type": "string",  "description": "Path to private key file (supports ~/ expansion)"},
                    "password": {"type": "string",  "description": "SSH password (use key_path instead when possible)"},
                    "alias":    {"type": "string",  "description": "Optional name for the connection, e.g. 'prod'"},
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": "Run a shell command on a connected remote server. Returns stdout + stderr with the exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string",  "description": "Connection ID returned by ssh_connect"},
                    "command":       {"type": "string",  "description": "Shell command to execute on the remote server"},
                    "timeout":       {"type": "integer", "description": "Timeout in seconds (default 60)"},
                },
                "required": ["connection_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_upload",
            "description": "Upload a file from the local workspace to a remote server via SFTP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "Connection ID returned by ssh_connect"},
                    "local_path":    {"type": "string", "description": "Local file path (relative to workspace or absolute)"},
                    "remote_path":   {"type": "string", "description": "Absolute path on the remote server (e.g. /home/user/app/file.py)"},
                },
                "required": ["connection_id", "local_path", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_download",
            "description": "Read a remote file's text content via SFTP and return it. Useful for inspecting remote logs, configs, or code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string",  "description": "Connection ID returned by ssh_connect"},
                    "remote_path":   {"type": "string",  "description": "Absolute path to the remote file"},
                    "max_kb":        {"type": "integer", "description": "Max kilobytes to return (default 50)"},
                },
                "required": ["connection_id", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_list",
            "description": "List all currently active SSH connections with their IDs, usernames, and hosts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_disconnect",
            "description": "Close and remove an SSH connection from the registry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "Connection ID returned by ssh_connect"},
                },
                "required": ["connection_id"],
            },
        },
    },
]

_GITHUB_SCHEMA: list[dict] = [
    # ── GitHub Deep Integration ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "github",
            "description": (
                "Full GitHub REST API integration. Requires GITHUB_TOKEN in .env. "
                "Actions: repo_info, repo_create, branch_list, branch_create, "
                "pr_create, pr_list, pr_merge, pr_review, pr_diff, pr_comments, "
                "issue_create, issue_list, issue_close, issue_comment, "
                "file_read, file_write, search_code, search_repos, "
                "actions_list, actions_run, release_create, release_list, "
                "commit_list, commit_diff. "
                "Use for ANY GitHub operation — reading files, creating PRs, managing issues, "
                "triggering CI workflows, creating releases, or searching code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "Which action to perform. One of: "
                            "repo_info, repo_create, branch_list, branch_create, "
                            "pr_create, pr_list, pr_merge, pr_review, pr_diff, pr_comments, "
                            "issue_create, issue_list, issue_close, issue_comment, "
                            "file_read, file_write, search_code, search_repos, "
                            "actions_list, actions_run, release_create, release_list, "
                            "commit_list, commit_diff"
                        ),
                    },
                    "owner":          {"type": "string",  "description": "GitHub username or org (required for most actions)"},
                    "repo":           {"type": "string",  "description": "Repository name (required for most actions)"},
                    "branch":         {"type": "string",  "description": "Branch name (for branch_create, file_read/write, commit_list)"},
                    "from_ref":       {"type": "string",  "description": "Source branch or SHA for branch_create (default 'main')"},
                    "title":          {"type": "string",  "description": "Title (for pr_create, issue_create, release_create)"},
                    "body":           {"type": "string",  "description": "Body/description text"},
                    "head":           {"type": "string",  "description": "Source branch for pr_create"},
                    "base":           {"type": "string",  "description": "Target branch for pr_create (default 'main')"},
                    "pr_number":      {"type": "integer", "description": "PR number for pr_merge, pr_review, pr_diff, pr_comments"},
                    "issue_number":   {"type": "integer", "description": "Issue number for issue_close, issue_comment"},
                    "path":           {"type": "string",  "description": "File path for file_read, file_write"},
                    "content":        {"type": "string",  "description": "File content for file_write"},
                    "message":        {"type": "string",  "description": "Commit message for file_write"},
                    "ref":            {"type": "string",  "description": "Git ref for file_read, actions_run (default 'main')"},
                    "sha":            {"type": "string",  "description": "Commit SHA for commit_diff"},
                    "query":          {"type": "string",  "description": "Search query for search_code, search_repos"},
                    "state":          {"type": "string",  "description": "Filter state: 'open' or 'closed' (for pr_list, issue_list)"},
                    "event":          {"type": "string",  "description": "Review event: APPROVE, REQUEST_CHANGES, or COMMENT"},
                    "merge_method":   {"type": "string",  "description": "Merge strategy: merge, squash, or rebase"},
                    "workflow":       {"type": "string",  "description": "Workflow file name or ID for actions_run"},
                    "inputs":         {"type": "object",  "description": "Workflow dispatch inputs for actions_run"},
                    "tag":            {"type": "string",  "description": "Tag name for release_create"},
                    "name":           {"type": "string",  "description": "Release name for release_create"},
                    "draft":          {"type": "boolean", "description": "Create as draft (for pr_create, release_create)"},
                    "private":        {"type": "boolean", "description": "Make repo private (for repo_create)"},
                    "description":    {"type": "string",  "description": "Repo description for repo_create"},
                    "labels":         {"type": "array",   "items": {"type": "string"}, "description": "Labels for issue_create"},
                    "assignees":      {"type": "array",   "items": {"type": "string"}, "description": "Assignees for issue_create"},
                    "limit":          {"type": "integer", "description": "Max results to return (default 20)"},
                },
                "required": ["action"],
            },
        },
    },
]

# Full schema — all tools combined (backward compat)
TOOL_SCHEMAS = TOOL_SCHEMAS_CORE + _SSH_SCHEMAS + _GITHUB_SCHEMA


# ── Budget-aware, relevance-ordered tool selection ────────────────────────────
# Wingman-inspired "progressive disclosure" adapted to VibeAI's constraints:
# instead of dropping tight-budget (Groq) runs to a fixed 6-tool minimal set
# — which silently STRIPS design_asset, vision, git, github, ssh so a design
# task that falls to Groq literally cannot generate an image — send the
# ESSENTIAL coding tools plus the tools actually RELEVANT to this task, as many
# as fit the model's real token budget. (The old "CORE ≈ 8k tokens" assumption
# behind minimal-mode is stale: measured 2026-07-10, CORE ≈ 1.5k and FULL
# ≈ 3.2k tokens, so the actual Groq tiers — gpt-oss-120b @ 8k TPM, llama-3.3
# @ 12k — can now fit far more than 6 tools.) Static, deterministic, pure
# function; no LLM round-trip, no change to the fragile loop structure.

# Always-present coding essentials (by name), in priority order.
_ESSENTIAL_TOOLS = ("create_file", "edit_file", "read_file", "list_dir", "bash")

# Optional tools -> keyword signals that make them relevant to a task.
_TOOL_RELEVANCE: dict[str, tuple[str, ...]] = {
    "design_asset":   ("design", "image", "hero", "banner", "icon", "logo", "picture",
                       "landing", "website", "ui", "illustration", "background"),
    "vision_analyze": ("screenshot", "image", "photo", "look at", "vision", "diagram",
                       "picture", "see the", "visual"),
    "search_web":     ("search", "look up", "latest", "current", "docs", "documentation",
                       "find out", "google"),
    "fetch_url":      ("url", "http", "webpage", "fetch", "download page", "scrape"),
    "git":            ("git", "commit", "branch", "diff", "stage", "version control"),
    "github":         ("github", "repo", "pull request", " pr ", "issue", "workflow"),
    "move_file":      ("move", "rename", "relocate"),
    "delete_file":    ("delete", "remove file", "rm "),
    "ssh_connect":    ("ssh", "remote server", "remote host", "sftp", "deploy to server"),
    "ssh_exec":       ("ssh", "remote server", "remote host", "deploy to server"),
    "ssh_upload":     ("sftp", "upload to server", "scp"),
    "ssh_download":   ("sftp", "download from server", "scp"),
}

_ALL_SCHEMAS_BY_NAME = {s["function"]["name"]: s for s in TOOL_SCHEMAS}


def _tool_tokens(schema: dict) -> int:
    # chars/3 — same conservative estimate groq_conn uses for TPM clamping.
    return max(1, len(json.dumps(schema)) // 3)


def select_tools_for_budget(task: str, max_tool_tokens: int) -> list[dict]:
    """Essentials + task-relevant optional tools, greedily filled to fit
    max_tool_tokens. Essentials are always included even if that exceeds the
    budget (a coding agent with no file/bash tools is useless — better a
    slightly-over request the clamp can still handle than a crippled one)."""
    selected: list[dict] = []
    used = 0
    for name in _ESSENTIAL_TOOLS:
        s = _ALL_SCHEMAS_BY_NAME.get(name)
        if s:
            selected.append(s)
            used += _tool_tokens(s)

    low = (task or "").lower()
    # Score optional tools by how many of their signals appear in the task.
    scored: list[tuple[int, int, str]] = []   # (relevance, -size, name)
    for name, signals in _TOOL_RELEVANCE.items():
        if name in _ESSENTIAL_TOOLS or name not in _ALL_SCHEMAS_BY_NAME:
            continue
        rel = sum(1 for kw in signals if kw in low)
        if rel > 0:
            scored.append((rel, -_tool_tokens(_ALL_SCHEMAS_BY_NAME[name]), name))
    # Highest relevance first; cheaper first on ties.
    scored.sort(reverse=True)
    for rel, neg_size, name in scored:
        s = _ALL_SCHEMAS_BY_NAME[name]
        cost = _tool_tokens(s)
        if used + cost <= max_tool_tokens:
            selected.append(s)
            used += cost
    return selected


# Ultra-compact schema for Groq fallback (≈400 tokens vs 8k for CORE).
# Strips verbose descriptions so schema + system prompt fit inside 10k TPM budget.
# NOTE: superseded by select_tools_for_budget() above for the agent loop's
# compact path; kept for any caller that still wants a fixed tiny set.
TOOL_SCHEMAS_MINIMAL: list[dict] = [
    {"type": "function", "function": {
        "name": "create_file",
        "description": "Write a new file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace old_str with new_str inside a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"},
        }, "required": ["path", "old_str", "new_str"]},
    }},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List directory contents.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "search_web",
        "description": "Search the web.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
]


# ── Tool executor ─────────────────────────────────────────────────────────────

class ToolExecutor:
    """Executes tool calls requested by the model."""

    # Commands that will always be blocked in bash.
    # HONEST LIMIT: this is a best-effort tripwire against the most destructive
    # patterns, NOT a security boundary — a determined adversary can trivially
    # phrase around a regex blocklist. Real isolation means running the whole
    # system in a container. See also the env scrub in _run_shell.
    _BLOCKED = [
        r"rm\s+-[rf]{2}\s+(/|~|\.|\*)\s*($|;|&)",   # rm -rf on /, ~, ., or * (bare targets)
        r"dd\s+if=",           r"mkfs",
        r":(){",               r">\s*/dev/sd",
        r"chmod\s+777\s+/",   r"chown\s+.*\s+/",
        r"curl.*\|\s*(ba)?sh", r"wget.*\|\s*(ba)?sh",
        r"\bsudo\b",                                  # any sudo, not just sudo rm
        r"\bshutdown\b|\breboot\b",
        r"\bformat\s+[a-z]:",                         # windows drive format
    ]
    _BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKED]

    # Env vars whose NAMES look credential-shaped are stripped from the shell's
    # environment (defense in depth — .env values are not exported to os.environ
    # by pydantic, but system-wide exported keys would otherwise leak through).
    _SENSITIVE_ENV_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)

    # Dev-server commands — they run forever and will hang the agent.
    # Block them with a helpful explanation so the model tries build instead.
    _DEVSERVER = [
        r"\bnpm\s+(run\s+)?(dev|start|serve)\b",
        r"\byarn\s+(dev|start|serve)\b",
        r"\bpnpm\s+(dev|start|serve)\b",
        r"\bvite\b(?!\s+build)",           # bare 'vite' without 'build'
        r"\bnpx\s+vite\b(?!\s+build)",
        r"\bnpx\s+react-scripts\s+start\b",
        r"\bnpx\s+webpack(-dev-server)?\s*$",
        r"\bwpx?\s+serve\b",
    ]
    _DEVSERVER_RE = [re.compile(p, re.IGNORECASE) for p in _DEVSERVER]

    # One-shot scaffolding commands legitimately contain the word "vite" (e.g.
    # "npm create vite@latest my-app") but terminate on their own — they must
    # NOT be caught by the dev-server blocklist above, which would otherwise
    # match the literal substring "vite" inside "vite@latest".
    _SCAFFOLD_RE = re.compile(r"\bcreate[ -]vite\b|\bcreate-react-app\b", re.IGNORECASE)

    # Extracts the target project directory name from a scaffold command, e.g.
    # "npm create vite@latest pixel-and-co -- --template react" -> "pixel-and-co".
    # Used by agent_loop.py to detect when a model has just scaffolded a project
    # into a subdirectory, so it can immediately remind the model to keep using
    # that directory — observed repeatedly (3/3 multi-file React test runs)
    # abandoning the scaffold and building at the workspace root instead.
    SCAFFOLD_NAME_RE = re.compile(
        r"(?:create[ -]vite(?:@\S+)?|create-react-app)\s+([A-Za-z0-9_.-]+)",
        re.IGNORECASE,
    )

    # On Windows, asyncio.create_subprocess_shell() defaults to cmd.exe, which
    # doesn't understand POSIX idioms models routinely emit — "mkdir -p",
    # "touch file.txt", forward slashes being misparsed as switches (cmd.exe's
    # "mkdir src/components" can fail with "The syntax of the command is
    # incorrect" purely because of the "/"). Observed directly burning several
    # iterations across multiple test runs. Git Bash ships with any Windows
    # machine that has Git installed (this one does) and gives real POSIX
    # semantics — resolved once at import time, falls back to the platform
    # default shell if Git Bash isn't present.
    _POSIX_SHELL = shutil.which("bash") if os.name == "nt" else None

    def __init__(self, workspace: Path = DEFAULT_WORKSPACE) -> None:
        # Always resolve, even if the caller didn't — _safe_path() compares
        # against this path, and an unresolved self.workspace can mismatch a
        # resolved candidate path on case/symlinks/relative segments alone,
        # rejecting every legitimate path inside the workspace.
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        # Set by AgentLoop.run() so change-history entries can say WHICH task
        # motivated each file mutation ("" outside an agent run).
        self.current_task = ""

    def _safe_path(self, rel: str) -> Path:
        """Resolve path and ensure it stays inside workspace."""
        p = (self.workspace / rel.lstrip("/")).resolve()
        # relative_to() checks actual path-segment boundaries (and is case-insensitive
        # on Windows) — a naive str.startswith() comparison would both false-positive
        # on case mismatches and false-negative-block a sibling dir like "workspace_evil"
        # that merely shares a string prefix with "workspace".
        try:
            p.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"Path escape attempt blocked: {rel}")
        return p

    # ── Individual tools ──────────────────────────────────────────────────────

    async def create_file(self, path: str, content: str) -> str:
        p = self._safe_path(path)
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        logger.info(f"[agent/tool] created {p.relative_to(self.workspace)} ({lines} lines)")
        try:
            from core.change_history import record
            record(self.workspace, "overwrite" if existed else "create",
                   path, task=self.current_task, lines=lines, chars=len(content))
        except Exception:
            pass
        return f"✓ Created {path} ({lines} lines, {len(content)} chars)"

    async def read_file(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 12_000:
            content = content[:12_000] + f"\n... (truncated, {len(content)} chars total)"
        return content

    async def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"
        content = p.read_text(encoding="utf-8")
        if old_str == "":
            # "" is a substring of everything, so content.replace("", new_str, 1)
            # would silently PREPEND new_str at position 0 instead of erroring or
            # doing anything sensible — observed directly, repeatedly, today: weak
            # models pass old_str="" when they mean "set the file to this content",
            # and each such call corrupts the file further by stacking another full
            # copy in front of the previous one (e.g. vite.config.js ending up with
            # three concatenated `export default defineConfig(...)` blocks after
            # three "edits" each silently prepending instead of replacing). Treat it
            # as the model's evident intent — replace the whole file — instead of
            # letting it silently corrupt the file.
            p.write_text(new_str, encoding="utf-8")
            logger.warning(f"[agent/tool] edit_file({path}) called with old_str='' — replaced whole file instead of prepending")
            try:
                from core.change_history import record, preview
                record(self.workspace, "replace", path, task=self.current_task,
                       lines=new_str.count("\n") + 1)
            except Exception:
                pass
            return f"✓ Replaced entire contents of {path} (old_str was empty — use create_file for this next time)"
        if old_str not in content:
            # Show context to help the model fix the mismatch
            return (
                f"ERROR: old_str not found in {path}.\n"
                f"File starts with:\n{content[:300]}"
            )
        updated = content.replace(old_str, new_str, 1)
        p.write_text(updated, encoding="utf-8")
        try:
            from core.change_history import record, preview
            record(self.workspace, "edit", path, task=self.current_task,
                   old_preview=preview(old_str), new_preview=preview(new_str))
        except Exception:
            pass
        return f"✓ Edited {path}"

    async def delete_file(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"ERROR: Not found: {path}"
        is_dir = p.is_dir()
        if is_dir:
            shutil.rmtree(p)
        else:
            p.unlink()
        try:
            from core.change_history import record
            record(self.workspace, "delete", path, task=self.current_task,
                   kind="dir" if is_dir else "file")
        except Exception:
            pass
        return f"✓ Deleted {'directory ' if is_dir else ''}{path}"

    # Directories that are never useful to list — they contain thousands of generated files
    _IGNORE_DIRS = {
        "node_modules", ".git", "__pycache__", ".next", "dist", "build",
        "out", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
        ".turbo", ".svelte-kit", ".nuxt", "coverage", ".cache",
        # The agent's own change journal — the model must not read, reason
        # about, or corrupt its own history mid-run (core/change_history.py).
        ".vibeai",
    }

    async def list_dir(self, path: str = ".") -> str:
        p = self._safe_path(path)
        if not p.exists():
            return f"ERROR: Directory not found: {path}"
        lines: list[str] = []
        truncated = False
        # os.walk with in-place dirnames pruning instead of sorted(p.rglob("*")) --
        # found in code review (2026-07-13): rglob fully materialized the entire
        # tree, including node_modules/dist (this class's own _IGNORE_DIRS comment
        # calls them "thousands of generated files"), before the filter discarded
        # them -- descending in and immediately backing out was the expensive
        # part. Also dropped the second full rglob('*') that ran just to print an
        # exact truncated-item count; "truncated" without a possibly-stale count
        # from a second full walk says the same useful thing for far less work.
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            dp = Path(dirpath)
            for name in dirnames:
                rel = (dp / name).relative_to(self.workspace)
                lines.append(f"📁 {rel}")
                if len(lines) >= 500:
                    truncated = True
                    break
            if truncated:
                break
            for name in sorted(filenames):
                rel = (dp / name).relative_to(self.workspace)
                lines.append(f"📄 {rel}")
                if len(lines) >= 500:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            lines.append("... (truncated at 500 items)")
        return "\n".join(lines) if lines else "(empty directory)"

    # Matches a bare "npm install"/"npm ci"/"npm run X" with no "cd " already in
    # the command — the exact shape that fails with ENOENT when the project
    # actually lives in a subdirectory the model created/scaffolded earlier.
    _NPM_BARE_RE = re.compile(r"^\s*npm\s+(install|ci|run\s+\S+)", re.IGNORECASE)

    async def _run_shell(self, command: str, timeout: int) -> tuple[int, str]:
        """Execute a command, already validated, and return (exit_code, formatted_output)."""
        # Strip credential-shaped variables from the child environment. Everything
        # else passes through — npm/node/git need PATH, APPDATA, TEMP etc. to work.
        scrubbed_env = {
            k: v for k, v in os.environ.items()
            if not self._SENSITIVE_ENV_RE.search(k)
        }
        # stdin=DEVNULL is load-bearing, not cosmetic: without it the child
        # inherits this process's stdin, and a command that hits an
        # interactive prompt (e.g. "npm create vite@latest <dir>" when <dir>
        # already exists non-empty from a previous failed/interrupted attempt
        # -- create-vite's "not empty, overwrite?" prompt -- ignores
        # --template and asks anyway) hangs reading input that never arrives
        # until the timeout kills it. Found live (2026-07-15): the same
        # scaffold command timed out 3x in a row at 90s each, because the
        # first attempt left a partial my-app/ directory and every retry
        # re-hit the identical prompt. DEVNULL gives an immediate EOF instead,
        # so the tool fails fast with the real "not empty" error the agent
        # can actually act on (rename/remove the dir) instead of a silent
        # timeout that looks identical to a slow build.
        if self._POSIX_SHELL:
            proc = await asyncio.create_subprocess_exec(
                self._POSIX_SHELL, "-c", command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
                env=scrubbed_env,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
                env=scrubbed_env,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return -1, f"ERROR: Command timed out after {timeout}s: {command[:60]}"

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + ("\nSTDERR:\n" + err if err.strip() else "")).strip()
        combined = combined[:10_000]   # cap output

        exit_code = proc.returncode
        prefix = "✓" if exit_code == 0 else f"✗ (exit {exit_code})"
        formatted = f"{prefix} $ {command}\n{combined}" if combined else f"{prefix} $ {command}\n(no output)"
        return exit_code, formatted

    async def bash(self, command: str, timeout: int = 60) -> str:
        # Block dev-server commands — they run forever and hang the agent loop.
        # Scaffolding commands (e.g. "npm create vite@latest") are exempt — they
        # terminate on their own and only contain "vite" as part of the package name.
        if not self._SCAFFOLD_RE.search(command):
            for pattern in self._DEVSERVER_RE:
                if pattern.search(command):
                    return (
                        "ERROR: Dev-server commands (npm run dev, npm start, vite, etc.) "
                        "cannot be used here — they never terminate and will block the agent. "
                        "Use 'npm run build' to compile and verify the project instead. "
                        "The user can start the dev server manually once files are ready."
                    )
        # Safety check
        for pattern in self._BLOCKED_RE:
            if pattern.search(command):
                return f"ERROR: Blocked command pattern detected: {command[:60]}"

        # Package installs legitimately run longer than the general 90s ceiling —
        # observed live: a cold-cache `npm install` was killed at 90s mid-download,
        # leaving a corrupted partial node_modules (3 packages) that made every
        # subsequent build fail with unresolvable imports. Installs are safe to
        # allow longer: they terminate on their own, unlike dev servers.
        _is_install = bool(re.search(
            r"\b(npm|pnpm|yarn)\s+(install|ci|add)\b|\bpip3?\s+install\b", command, re.IGNORECASE
        ))
        ceiling = 300 if _is_install else 90
        timeout = max(5, min(timeout, ceiling))
        logger.info(f"[agent/bash] $ {command[:80]}")

        try:
            exit_code, formatted = await self._run_shell(command, timeout)

            # Auto-correct the single most common path-confusion failure observed
            # in live testing: the model creates an entire project inside a
            # subdirectory (hand-built or scaffolded) but then runs a bare
            # "npm install"/"npm run build" from the workspace root, where there's
            # no package.json — and keeps repeating the same mistake for the rest
            # of the run instead of recovering. If there's no package.json at root
            # and EXACTLY ONE subdirectory has one, retry inside it automatically
            # rather than let the model rediscover this by trial and error.
            if (exit_code != 0 and self._NPM_BARE_RE.match(command)
                    and "cd " not in command.split("&&")[0]
                    and not (self.workspace / "package.json").exists()):
                candidates = [
                    d for d in self.workspace.iterdir()
                    if d.is_dir() and d.name not in self._IGNORE_DIRS
                    and (d / "package.json").exists()
                ]
                if len(candidates) == 1:
                    subdir = candidates[0].name
                    logger.warning(
                        f"[agent/bash] '{command}' failed with no package.json at "
                        f"workspace root — auto-retrying inside '{subdir}/'"
                    )
                    _, retry_formatted = await self._run_shell(f"cd {subdir} && {command}", timeout)
                    return (
                        f"NOTE: no package.json at the workspace root — your project lives "
                        f"in '{subdir}/'. Auto-retried as: cd {subdir} && {command}\n"
                        f"{retry_formatted}\n\n"
                        f"From now on, prefix ALL npm/build commands yourself with "
                        f"'cd {subdir} &&' — this auto-correction won't always be available."
                    )

            return formatted

        except Exception as exc:
            return f"ERROR running command: {exc}"

    async def search_web(self, query: str) -> str:
        try:
            from tools.search import search_stack
            results = await search_stack.search(query)
            return search_stack.format_for_prompt(results)
        except Exception as exc:
            return f"ERROR: Web search failed: {exc}"

    async def move_file(self, src: str, dest: str) -> str:
        s = self._safe_path(src)
        d = self._safe_path(dest)
        if not s.exists():
            return f"ERROR: Source not found: {src}"
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"✓ Moved {src} → {dest}"

    async def git(self, args: str) -> str:
        # Block dangerous git operations
        blocked_args = ["--force", "-f", "push --mirror", "filter-branch"]
        for b in blocked_args:
            if b in args:
                return f"ERROR: Blocked git operation: {args[:60]}"
        command = f"git {args}"
        logger.info(f"[agent/git] $ {command[:80]}")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,  # same reasoning as _run_shell above -- credential/pager prompts must fail fast, not hang
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            combined = out + ("\n" + err if err else "")
            exit_code = proc.returncode
            prefix = "✓" if exit_code == 0 else f"✗ (exit {exit_code})"
            return f"{prefix} git {args}\n{combined}" if combined else f"{prefix} git {args}"
        except asyncio.TimeoutError:
            return f"ERROR: git {args[:40]} timed out"
        except Exception as exc:
            return f"ERROR running git {args[:40]}: {exc}"

    async def fetch_url(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    text = await resp.text()
            try:
                import trafilatura
                clean = trafilatura.extract(text) or text[:8000]
            except ImportError:
                from html.parser import HTMLParser
                class _Strip(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                    def handle_data(self, d):
                        self.parts.append(d)
                parser = _Strip()
                parser.feed(text)
                clean = " ".join(parser.parts)[:8000]
            return clean[:8000]
        except Exception as exc:
            return f"ERROR fetching {url}: {exc}"

    # ── Design Team (image generation) ───────────────────────────────────────

    # Per-task cap on generated assets. Observed live: a model called
    # design_asset 94 times in a row with ever-different descriptions and never
    # wrote a single file — the repetition guard couldn't fire because the args
    # were never identical. A landing page needs ~3-6 images; 10 is generous.
    _DESIGN_ASSET_BUDGET = 10

    async def design_asset(
        self,
        description: str,
        asset_type:  str = "banner",
        width:       int = 1280,
        height:      int = 720,
    ) -> str:
        self._design_asset_calls = getattr(self, "_design_asset_calls", 0) + 1
        if self._design_asset_calls > self._DESIGN_ASSET_BUDGET:
            return (
                "ERROR: design_asset budget exhausted for this task "
                f"({self._DESIGN_ASSET_BUDGET} assets already generated). You have more "
                "than enough image URLs — STOP generating assets and BUILD the site now: "
                "create the project files with create_file, embedding the URLs you already have."
            )
        import re
        from core.imcp import (
            TaskJSON, Classification, TaskType, Complexity,
            TaskContext, TeamActivation, Priority,
        )
        task_type = TaskType.ANIMATION if asset_type == "animation" else TaskType.UI_DESIGN
        task_json = TaskJSON(
            original_prompt=description,
            refined_prompt=description,
            classification=Classification(
                primary_type=task_type,
                complexity=Complexity.SIMPLE,
            ),
            active_teams={
                "design": TeamActivation(
                    active=True,
                    models=["flux_asset", "flux_world"],
                    instruction=description,
                    priority=Priority.HIGH,
                )
            },
            team_instructions={"design": description},
            context=TaskContext(),
        )
        logger.info(f"[agent/design] generating {asset_type}: {description[:60]}")
        try:
            from teams.design import DesignTeam
            raw = await DesignTeam().run(
                task_json=task_json,
                instruction=description,
                extra={
                    "needs_animation":      asset_type == "animation",
                    "needs_text_rendering": asset_type == "icon",
                    "width":  width,
                    "height": height,
                },
            )
        except Exception as exc:
            return f"ERROR generating design asset: {exc}"

        # Extract image URLs and format cleanly for embedding in HTML
        urls = re.findall(r"https://[^\s]+pollinations[^\s]+|https://[^\s]+\.png|https://[^\s]+\.jpg", raw)
        if not urls:
            return raw  # raw output still contains useful info even without parsed URLs

        lines = [
            f"✓ {len(urls)} design asset(s) generated — embed directly in HTML:",
        ]
        for i, url in enumerate(urls, 1):
            lines.append(f"  [{i}] {url}")
        lines.append(f'\nExample usage:')
        lines.append(f'  <img src="{urls[0]}" alt="{description[:40]}" style="width:100%">')
        lines.append(f'  background-image: url("{urls[0]}")')
        return "\n".join(lines)

    # ── SSH Remote Terminal ───────────────────────────────────────────────────

    async def ssh_connect(
        self,
        host:     str,
        port:     int  = 22,
        username: str  = "root",
        key_path: str  = "",
        password: str  = "",
        alias:    str  = "",
    ) -> str:
        from tools.remote_terminal import ssh_connect as _connect
        return await _connect(host=host, port=port, username=username,
                              key_path=key_path, password=password, alias=alias)

    async def ssh_exec(self, connection_id: str, command: str, timeout: int = 60) -> str:
        from tools.remote_terminal import ssh_exec as _exec
        return await _exec(connection_id=connection_id, command=command, timeout=timeout)

    async def ssh_upload(self, connection_id: str, local_path: str, remote_path: str) -> str:
        from tools.remote_terminal import ssh_upload as _upload
        return await _upload(connection_id=connection_id, local_path=local_path, remote_path=remote_path)

    async def ssh_download(self, connection_id: str, remote_path: str, max_kb: int = 50) -> str:
        from tools.remote_terminal import ssh_download as _download
        return await _download(connection_id=connection_id, remote_path=remote_path, max_kb=max_kb)

    async def ssh_list(self) -> str:
        from tools.remote_terminal import ssh_list as _list
        return _list()

    async def ssh_disconnect(self, connection_id: str) -> str:
        from tools.remote_terminal import ssh_disconnect as _disc
        return await _disc(connection_id=connection_id)

    # ── GitHub Deep Integration ───────────────────────────────────────────────

    async def github(self, action: str, **params) -> str:
        from tools.github_tool import github as _gh
        return await _gh.execute(action, **params)

    async def vision_analyze(self, path: str, question: str = "") -> str:
        """Route a video or image file through VisionTeam and return the analysis."""
        # Resolve: workspace-relative first, then absolute
        candidate = self.workspace / path.lstrip("/")
        resolved  = candidate if candidate.exists() else Path(path)
        if not resolved.exists():
            return f"ERROR: File not found: {path}"

        ext      = resolved.suffix.lower()
        is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".frames")
        is_image = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        if not is_video and not is_image:
            return f"ERROR: Unsupported file type '{ext}'. Use a video or image file."

        logger.info(f"[agent/vision] analysing {resolved.name} ({'video' if is_video else 'image'})")

        try:
            from core.imcp import (
                TaskJSON, Classification, TaskType, Complexity,
                TaskContext, TeamActivation, Priority,
            )

            desc = question or f"Analyse this {'video' if is_video else 'image'} in detail — describe what is happening, the visual content, audio (if video), and anything notable."
            task_json = TaskJSON(
                original_prompt=desc,
                refined_prompt=desc,
                classification=Classification(
                    primary_type=TaskType.VIDEO_ANALYSIS if is_video else TaskType.UI_DESIGN,
                    complexity=Complexity.MODERATE,
                ),
                success_criteria=["Clear, detailed description of the visual content"],
                active_teams={
                    "vision": TeamActivation(
                        active=True,
                        models=["gemini_flash_vision", "nemotron_vl", "llama4_maverick"],
                        instruction=desc,
                        priority=Priority.HIGH,
                    )
                },
                team_instructions={"vision": desc},
                context=TaskContext(),
            )

            if is_image:
                import base64 as _b64
                extra = {"image_b64": _b64.b64encode(resolved.read_bytes()).decode()}
            elif ext == ".frames":
                extra = {"frames_path": str(resolved)}
            else:
                extra = {"video_path": str(resolved)}

            from teams.vision import VisionTeam
            result = await VisionTeam().run(
                task_json=task_json,
                instruction=question or f"Analyse this {'video' if is_video else 'image'} in detail.",
                extra=extra,
            )
            return result or "Vision analysis returned no output."

        except Exception as exc:
            logger.warning(f"[agent/vision] failed: {exc}")
            return f"ERROR in vision_analyze: {exc}"

    # ── Dispatcher ────────────────────────────────────────────────────────────

    async def execute(self, tool_name: str, call_id: str, args: dict) -> ToolResult:
        t0 = time.perf_counter()
        try:
            fn = {
                "create_file": lambda: self.create_file(**args),
                "read_file":   lambda: self.read_file(**args),
                "edit_file":   lambda: self.edit_file(**args),
                "delete_file": lambda: self.delete_file(**args),
                "list_dir":    lambda: self.list_dir(**args),
                "bash":        lambda: self.bash(**args),
                "search_web":  lambda: self.search_web(**args),
                "fetch_url":   lambda: self.fetch_url(**args),
                "move_file":      lambda: self.move_file(**args),
                "git":            lambda: self.git(**args),
                "vision_analyze": lambda: self.vision_analyze(**args),
                # Design Team
                "design_asset":   lambda: self.design_asset(**args),
                # SSH Remote Terminal
                "ssh_connect":    lambda: self.ssh_connect(**args),
                "ssh_exec":       lambda: self.ssh_exec(**args),
                "ssh_upload":     lambda: self.ssh_upload(**args),
                "ssh_download":   lambda: self.ssh_download(**args),
                "ssh_list":       lambda: self.ssh_list(),
                "ssh_disconnect": lambda: self.ssh_disconnect(**args),
                # GitHub Deep Integration
                "github":         lambda: self.github(**args),
            }.get(tool_name)

            if not fn:
                output  = f"ERROR: Unknown tool: {tool_name}"
                success = False
            else:
                output  = await fn()
                success = not output.startswith("ERROR")

        except Exception as exc:
            output  = f"ERROR in {tool_name}: {exc}"
            success = False

        ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[agent/tool] {tool_name} → {'ok' if success else 'fail'} ({ms:.0f}ms)")
        return ToolResult(tool_name=tool_name, call_id=call_id, success=success,
                          output=output, duration_ms=ms)

    async def execute_parallel(self, calls: list[dict]) -> list[ToolResult]:
        """Execute multiple tool calls in parallel (asyncio.gather)."""
        tasks = [
            self.execute(c["name"], c["id"], c.get("args", {}))
            for c in calls
        ]
        return await asyncio.gather(*tasks)


# Singleton with default workspace
tool_executor = ToolExecutor()
