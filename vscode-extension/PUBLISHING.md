# Publishing VibeAI to the VS Code Marketplace

The extension is packaged and marketplace-ready (`vibeai-vscode-0.1.0.vsix`).
The remaining steps are **identity-bound** — they create accounts under your
name and cannot be done by an AI on your behalf. Total time: ~15 minutes.

## One-time setup (steps only you can do)

### 1. Microsoft account + Azure DevOps organization
1. Go to https://dev.azure.com and sign in with a Microsoft account
   (create one if needed — free).
2. If prompted, create an organization (any name; it's never shown publicly).

### 2. Personal Access Token (PAT)
1. In Azure DevOps: click the **User settings** icon (top right) →
   **Personal access tokens** → **New Token**.
2. Name: `vsce-publish`. Organization: **All accessible organizations**.
3. Scopes: **Custom defined** → **Marketplace** → check **Manage**.
4. Create, and **copy the token immediately** (it is shown only once).

### 3. Create your publisher
1. Go to https://marketplace.visualstudio.com/manage
2. **Create publisher**. Choose an ID (lowercase, no spaces — e.g.
   `tamanroy` or `vibeai-team`). This ID is public and permanent.
3. Fill in the display name; add a logo if you like (icon.png works).

### 4. Point the extension at your publisher
In `package.json`, change:
```json
"publisher": "vibeai-local"
```
to your real publisher ID from step 3. Strongly recommended before
publishing: push this project (or at least `vscode-extension/`) to a public
GitHub repo and add to `package.json`:
```json
"repository": { "type": "git", "url": "https://github.com/<you>/<repo>" }
```
Listings without a repository look abandoned and rank worse in search.

## Publish

```bash
cd vscode-extension
npx vsce login <your-publisher-id>     # paste the PAT when asked
npx vsce publish                       # builds + uploads 0.1.0
```

Within ~5–10 minutes the extension is live and **searchable** — in VS Code:
Extensions panel → search "VibeAI". Updates later: bump `version` in
package.json, then `npx vsce publish` again (or `npx vsce publish patch`).

## Before you publish — honest checklist

- [ ] **The extension needs the local server.** Users who install from the
      marketplace WITHOUT the VibeAI Python project get a client with
      nothing to talk to. The README says this clearly, but expect
      confused reviews unless the listing links to a setup guide for the
      server (put the GitHub repo link in `repository` — see above).
- [ ] **Marketplace name collisions:** search "VibeAI" on the marketplace
      first. If taken, pick a distinct display name (e.g. "VibeAI Council").
- [ ] **License:** the bundled LICENSE is all-rights-reserved. That's legal
      to publish, but unusual for a tool whose backend is a source project —
      consider whether you want MIT/Apache-2.0 for the extension.
- [ ] Screenshots/GIF in the README dramatically improve installs — record
      a short clip of `@vibeai` answering in the Chat panel once you're
      happy with it (VS Code: F1 → "Developer: Toggle Screencast Mode"
      makes nice demos).
