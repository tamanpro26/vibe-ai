# VibeAI — Multi-Agent Coding Team for VS Code

**Your own AI coding team, running on your machine.** VibeAI orchestrates a
council of specialized models — planner, coder, debugger, reviewer, designer —
built entirely on free-tier providers, and this extension brings that team
into VS Code.

> **Privacy by architecture:** your prompts go to `127.0.0.1`, to a server
> *you* run. No third-party extension backend, no telemetry, no account.

## Features

### 💬 `@vibeai` in the Chat panel
Ask anything. The request runs through the full multi-agent pipeline — a
router classifies it, specialist teams handle it, a verifier checks it — and
the answer comes back in your Chat panel.

```
@vibeai why does my FastAPI endpoint return 422 on valid JSON?
```

### 🔧 Fix/Explain Selected Code
Select code → right-click → **VibeAI: Fix/Explain Selected Code**. The
selection is sent with its file path and language for context; you choose
the instruction (fix bugs, explain, refactor, add tests…).

### ⚙️ Honest scope (what this is not)
No inline ghost-text completions yet — free-tier model latency can't make
those feel good, and a laggy Copilot imitation is worse than none. Chat and
explicit code actions are where a multi-agent council genuinely helps.

## Requirements

This extension is a **client**. It needs the VibeAI server running locally:

```bash
# from the VibeAI project root
python main.py serve        # → http://127.0.0.1:8000
```

Get the VibeAI project + free API keys (Groq, Cerebras, Gemini — no credit
card) — see the main project README.

## Extension Settings

| Setting            | Default                 | Description                                   |
|--------------------|-------------------------|-----------------------------------------------|
| `vibeai.serverUrl` | `http://127.0.0.1:8000` | Where your VibeAI server listens              |
| `vibeai.apiToken`  | *(empty)*               | `VIBE_API_TOKEN` from `.env`, if you set one  |

Tokenless mode works only while the server binds to localhost (its default).
Expose it anywhere else and the server itself will refuse requests until a
token is set on both ends — that's by design.

## Release Notes

See the bundled `CHANGELOG.md` (shown in the Changelog tab on the
Marketplace listing).

---

**Building from source:** `npm install && npm run compile`, then F5 in this
folder for an Extension Development Host — or `npm run package` to produce an
installable `.vsix`.
