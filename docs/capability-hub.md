# Capability Hub operations

## What is supported

| Source/component | VibeAI status | Runtime authority |
|---|---|---|
| Native VibeAI instruction skill | Native | Bounded instructions only |
| Portable Claude/Codex `SKILL.md` and passive references | Adapted after scan/review | Bounded instructions only |
| VibeAI declarative bundle | Native | Version-pinned member composition |
| GitHub issue comment built-in | Native approved action | Exact confirmation + reviewed adapter |
| Provider hooks, agents, scripts, binaries, LSP, browser UI | Unsupported | None |
| MCP declaration, local stdio, legacy HTTP+SSE | Imported as disabled candidate | None in v1 |

`vibeai_builtin`, `vibeai_reviewed`, `user_imported`, and `community_source` describe provenance.
They do not replace lifecycle states such as awaiting review, reviewed, revoked, archived, or
superseded. Runtime eligibility rechecks both trust and lifecycle on every invocation.

## Security boundaries

- The Vercel proxy authenticates the browser session and forwards the short-lived Clerk token.
  FastAPI independently verifies it and derives ownership from its subject. The shared backend
  token is service authentication, not user authority.
- Packages are acquired at a full Git commit SHA, bounded, scanned without checkout hooks, and
  retained in encrypted quarantine only while a decision is pending. Approval is bound to the
  source, evidence, and adapter digests.
- Imported instructions are untrusted prompt content. They cannot change system policy, grant a
  tool, request credentials, or convert suggested text into an external action.
- Approved actions disclose the exact operation, immutable numeric target, shared data class,
  mutable resource, expiry, and request digest. There is no bulk approval.
- The durable send marker is committed before a network mutation. A pre-marker worker crash is
  reclaimable; a post-marker crash becomes `outcome_unknown` and is never retried automatically.
- The capability worker contains only reviewed typed adapters. It cannot reach `ToolExecutor`,
  generic shell, arbitrary URLs, or the broad internal GitHub tool.

## Deployment requirements

1. Use an async PostgreSQL `CAPABILITY_DATABASE_URL`; production rejects SQLite.
2. Apply Alembic through `0008_workflow_identity` before the application deployment.
3. Configure Clerk issuer/JWKS, audience, and authorized parties for the Vercel production origin.
4. Configure `CAPABILITY_CREDENTIAL_KEYS` as a JSON map of versioned urlsafe-base64 32-byte keys;
   keep the active key available through credential rotation and backup restoration.
5. Configure the least-privilege GitHub App and verify it can see only intended repositories.
6. Deploy with `CAPABILITY_ACTIONS_ENABLED=false`. Smoke the Hub, imports, installs, resolution,
   scopes, and pending inbox first. Enable actions only after the confirmed GitHub sandbox flow and
   worker-recovery gate pass.

The application must remain rollback-compatible with the expanded schema. Roll back by disabling
`CAPABILITY_HUB_ENABLED` or `CAPABILITY_ACTIONS_ENABLED` and promoting the previous immutable Vercel
deployment; do not drop the new tables during an application rollback. A database restore is only
for catastrophic recovery and requires an explicit audit-continuity/data-loss decision.

## Retention and deletion policy

| Data | Default policy | Deletion/minimization behavior |
|---|---|---|
| Rejected/approved raw quarantine archive | Immediate after terminal review | Encrypted archive deleted; immutable digests/evidence remain |
| Pending quarantine archive | Operational limit: 7 days | Expire candidate and delete encrypted archive unless a documented hold applies |
| Unpublished author draft | Until user deletes or account retention job runs | Delete when eligible; published immutable versions are archived instead |
| Installed passive assets/version | While installed plus rollback window | Archive/revoke; retain version/digests referenced by actions or audit |
| Pending/terminal action and checkpoint | 90 days minimum support window | Remove unnecessary payload text; retain digest, target classification, state, and receipt |
| Security audit | 365 days default incident window | Pseudonymize deleted-account subject after support/legal-hold evaluation |
| Backups | Deployment backup policy | Expire on schedule; test restore with key availability and audit continuity |

These durations are operational defaults to configure in the deployment’s retention job; legal or
contractual requirements may override them. Raw credentials and unnecessary user content are never
valid audit-retention fields.

## Release and incident checklist

- Record commit SHA, migration revision, preview URL, previous production deployment ID, enabled
  adapters, kill-switch values, and UTC deployment time.
- Require capability/auth/security tests, the full Python suite, frontend lint/build, tagged
  Playwright desktop/phone checks, and fresh-database migration to pass.
- Smoke `/`, `/#/chat`, `/#/projects`, and `/#/capabilities`; verify authenticated ownership,
  ordinary no-capability fallback, one pending-action reload, deny, approve, success receipt, and
  revoked-capability invalidation.
- Stop on cross-user access, raw secret exposure, action without consent, duplicate mutation,
  uncaught runtime errors, broken auth handoff, mobile overflow, or an ambiguous outcome presented
  as success.
- On incident: turn actions off first, revoke the affected version/connection, preserve evidence,
  promote the previous known-good deployment, and verify ordinary chat plus audit access before a
  forward fix.
