/**
 * VibeAI VS Code extension — minimal, honest version (feature i).
 *
 * What it does:
 *   - Registers a `@vibeai` chat participant: type `@vibeai <question>` in
 *     the VS Code Chat panel and the request goes to your locally-running
 *     VibeAI server (`python main.py`), through the full Free-Council
 *     pipeline, and the answer streams back into the panel.
 *   - Adds a right-click command "VibeAI: Fix/Explain Selected Code" that
 *     sends the current selection (with file path + language for context)
 *     to the same pipeline and shows the answer in the chat-style output.
 *
 * What it deliberately does NOT do (yet): Copilot-style inline ghost-text
 * completions. Those demand sub-second latency, which free-tier cloud
 * providers can't reliably deliver; building them badly would feel worse
 * than not having them. The chat participant is where the multi-agent
 * council genuinely adds value over Copilot.
 */
import * as vscode from "vscode";

interface PromptResponse {
  session_id: string;
  response: string;
}

function config() {
  const cfg = vscode.workspace.getConfiguration("vibeai");
  return {
    serverUrl: (cfg.get<string>("serverUrl") ?? "http://127.0.0.1:8000").replace(/\/+$/, ""),
    apiToken: cfg.get<string>("apiToken") ?? "",
  };
}

async function askVibeAI(prompt: string, token: vscode.CancellationToken): Promise<string> {
  const { serverUrl, apiToken } = config();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiToken) headers["Authorization"] = `Bearer ${apiToken}`;

  const abort = new AbortController();
  // The council pipeline can legitimately take a while on complex prompts;
  // 120s is generous but bounded. Cancellation in the chat UI aborts too.
  const timer = setTimeout(() => abort.abort(), 120_000);
  token.onCancellationRequested(() => abort.abort());

  try {
    const res = await fetch(`${serverUrl}/api/prompt`, {
      method: "POST",
      headers,
      body: JSON.stringify({ prompt }),
      signal: abort.signal,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`VibeAI server returned ${res.status}: ${body.slice(0, 200)}`);
    }
    const data = (await res.json()) as PromptResponse;
    return data.response ?? "(empty response)";
  } finally {
    clearTimeout(timer);
  }
}

function connectionHelp(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/fetch failed|ECONNREFUSED|abort/i.test(msg)) {
    return [
      "**Couldn't reach the VibeAI server.**",
      "",
      "Start it from the VibeAI project directory:",
      "```",
      "python main.py serve",
      "```",
      `(expected at \`${config().serverUrl}\` — change \`vibeai.serverUrl\` in Settings if it runs elsewhere)`,
    ].join("\n");
  }
  return `VibeAI error: ${msg}`;
}

export function activate(context: vscode.ExtensionContext) {
  // ── @vibeai chat participant ──────────────────────────────────────────────
  const participant = vscode.chat.createChatParticipant(
    "vibeai.chat",
    async (request, _chatContext, stream, token) => {
      stream.progress("Asking the VibeAI council…");
      try {
        const answer = await askVibeAI(request.prompt, token);
        stream.markdown(answer);
      } catch (err) {
        stream.markdown(connectionHelp(err));
      }
    }
  );
  context.subscriptions.push(participant);

  // ── Right-click: fix/explain selection ────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("vibeai.fixSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.selection.isEmpty) {
        vscode.window.showInformationMessage("VibeAI: select some code first.");
        return;
      }
      const doc = editor.document;
      const selected = doc.getText(editor.selection);
      const instruction = await vscode.window.showInputBox({
        prompt: "What should VibeAI do with this selection?",
        value: "Find and fix any bugs in this code. Explain what was wrong.",
      });
      if (instruction === undefined) return; // user cancelled

      const prompt = [
        instruction,
        "",
        `File: ${vscode.workspace.asRelativePath(doc.uri)} (${doc.languageId})`,
        "```" + doc.languageId,
        selected,
        "```",
      ].join("\n");

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "VibeAI is working…", cancellable: true },
        async (_progress, token) => {
          const cts = new vscode.CancellationTokenSource();
          token.onCancellationRequested(() => cts.cancel());
          try {
            const answer = await askVibeAI(prompt, cts.token);
            const channel = vscode.window.createOutputChannel("VibeAI");
            channel.appendLine(answer);
            channel.show(true);
          } catch (err) {
            vscode.window.showErrorMessage(connectionHelp(err).replace(/[*`\n]+/g, " ").slice(0, 300));
          }
        }
      );
    })
  );
}

export function deactivate() {}
