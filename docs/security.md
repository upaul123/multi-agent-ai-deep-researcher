# Security model

How Deep Researcher AI protects accounts, user data, secrets and its LLM pipeline, and the risks that remain.
Operational steps (Secrets, providers, diagnostics) are in [deployment.md](deployment.md).

## Contents

1. [Assets and threats](#1-assets-and-threats)
2. [Identity and authentication](#2-identity-and-authentication)
3. [Authorisation and data isolation](#3-authorisation-and-data-isolation)
4. [LLM and prompt-injection controls](#4-llm-and-prompt-injection-controls)
5. [Network and content handling](#5-network-and-content-handling)
6. [Secrets management](#6-secrets-management)
7. [Telemetry and logging](#7-telemetry-and-logging)
8. [Verification](#8-verification)
9. [Residual risks](#9-residual-risks)

## 1. Assets and threats

| Asset | Threats considered |
|---|---|
| User research data (runs, uploads, Q&A, profile) | Access by another user; id guessing; data surviving deletion |
| Accounts and admin rights | Unauthenticated access to a public deployment; identity confusion across providers; claiming an admin's email |
| API keys and tokens | Commits to Git; exposure in logs, traces or the UI |
| The research pipeline | Prompt injection in retrieved pages or uploads; the LLM inventing citations or numbers |
| The host and its network | SSRF through fetched URLs; oversized or hostile uploads; resource exhaustion |

## 2. Identity and authentication

- **Fail closed.** `APP_ENV` defaults to `production`. If `[auth]` (redirect URI, cookie secret) or every provider
  section is missing, unreadable or contains a placeholder, the UI stops *before* opening the shared database.
  `APP_ENV=local` is the only way to run without sign-in, and it creates a single local administrator: never use it
  on a public server.
- **Standards-based sign-in.** Google, Microsoft and hosted email providers use OpenID Connect through Streamlit's
  `st.login` (Authlib). Passwords for email accounts are entered only on the provider's pages.
- **Issuer binding.** A session's issuer must match the host of a configured provider's discovery URL; any other
  issuer is rejected. Sessions without a subject are rejected.
- **Namespaced identities.** Account ids are the provider subject, prefixed by provider (`microsoft:…`, `email:…`;
  Google subjects stay unprefixed for compatibility), so the same subject from two issuers can never share an
  account.
- **Admin rights** require the address to be in `ADMIN_EMAILS` **and** marked verified by the provider; otherwise
  open email sign-up would let anyone register an administrator's address.

## 3. Authorisation and data isolation

| Capability | Guest | Signed-in user | Admin |
|---|---|---|---|
| Demo research (three preselected questions) | ✅ | ✅ | ✅ |
| Live research, uploads, LLM answers | — | ✅ | ✅ |
| Own history, profile, defaults | Session only | ✅ | ✅ |
| App-wide models, providers, cache | — | — | ✅ |
| Other users' content | — | — | — (usage counts only) |

- **Enforced on the server.** The research service rejects live research for guests and any non-preselected demo
  question before a run is queued; hiding UI controls is not the only barrier.
- **Owner scoping in the repository.** Every method touching user data takes an owner id and filters by it. Reading
  another user's run returns nothing (not "forbidden"), so ids cannot be probed. Writes check ownership too.
- **Guests** receive a random per-session id; their data is purged after 24 hours.
- **Deletion.** "Delete my data" stops active runs, then removes runs and all per-run tables, documents, activity,
  preferences, profile and the account record. A run deleted mid-execution is not re-created by its final save.
- **Shared data** is limited to the search-result cache (24-hour lifetime) and admin settings.

## 4. LLM and prompt-injection controls

- Retrieved pages, uploads, evidence matrices and report drafts are wrapped in `<untrusted_source>` boundaries. The
  system policy forbids following instructions inside them; injected boundary tags are stripped
  (case-insensitively) and source ids are HTML-escaped.
- Instruction-like text in a source is detected, excluded from claims, and the source is flagged and down-weighted.
- Structured outputs are schema-validated. Contradiction verdicts use a fixed set of values; insights referring to
  unknown claim ids are rejected; claims must quote a passage verbatim and may not add numbers or flip negation.
- Report rewrites must keep the deterministic report's citation markers and may not introduce new numbers;
  otherwise the deterministic report is used.
- Agents have no general tools: they can call only the configured search APIs and fetch pages from search results.

These controls reduce injection risk and keep outputs traceable; they do not prove factual accuracy or guarantee
complete prevention.

## 5. Network and content handling

- **SSRF protection.** `safe_fetch` allows only public `http(s)` addresses, blocking private, loopback,
  link-local, non-global and cloud-metadata ranges. Each hop resolves once, rejects mixed public/private DNS
  answers, and connects to a validated literal IP. The original hostname remains the HTTP Host and TLS
  certificate/SNI identity. A fresh client per hop prevents cross-host connection reuse; environment proxies
  are disabled for page fetching so they cannot bypass IP pinning. Redirects are resolved against the original
  URL and validated independently. Size, timeout and content-type limits remain in place.
  Offline regression tests exercise the real HTTPX/httpcore path with simulated DNS and TCP, including rebinding,
  redirects, TLS identity and proxy settings. Hosted end-to-end fetching still needs deployment verification.
- **Uploads** are limited to PDF, text, Markdown and CSV, 15 MB, 300 PDF pages, 400,000 characters and 2,000 CSV
  rows.
- **Rendering.** HTML export escapes source markup. User-controlled text placed into page HTML (display names,
  questions, titles) is escaped.
- **Resource limits.** Per-run budgets (rounds, searches, sources, time, LLM calls, tokens) and per-user and
  per-process run limits bound the work any one user can cause.

## 6. Secrets management

- Secrets come from `.env` locally or Streamlit Secrets when deployed. `.env`, `.streamlit/secrets.toml` and the
  databases are git-ignored; only `*.example` templates are committed.
- The UI shows only masked status ("configured (…abcd)"), and the settings table refuses keys that look like
  secrets.
- `scripts/scan_secrets.py` scans tracked and new files plus all reachable Git history for known key formats,
  private keys, credential URLs and credential assignments. It prints the candidate type, path, line and commit,
  never the value, and exits non-zero on a finding. CI runs it on every push and pull request.
- If a real credential is exposed: revoke or rotate it at the provider, then update Secrets. Deleting it from the
  code does not invalidate it or remove it from history.

## 7. Telemetry and logging

- **LangSmith tracing** is off unless `LANGSMITH_TRACING=true`. Graph inputs and outputs are hidden by default
  (`LANGSMITH_HIDE_INPUTS`, `LANGSMITH_HIDE_OUTPUTS`); custom spans carry operation names, attempt numbers, model
  ids, safe counts and opaque run ids, never prompts, responses, queries, URLs, headers, uploads or credentials.
  Hiding does not redact metadata or exception text already present in graph errors, so review those before
  tracing private workloads. See [observability.md](observability.md#privacy).
- **Sign-in diagnostics** (`AUTH_DIAGNOSTICS=true`) log field names and true/false results only. Turn them off
  after use.

## 8. Verification

The offline suite covers: missing, partial, unreadable and placeholder sign-in settings; missing identities and
unknown issuers; provider namespacing; verified-email admin rules; guest restrictions; owner isolation on SQLite and
libSQL; deletion; boundary-escape attempts; invalid verdicts; untrusted evidence in downstream prompts; citation
tampering; SSRF blocking; and escaping of a hostile display name. Tests use fake models and offline data; they are
not live-model attack evaluations.

## 9. Residual risks

- Prompt boundaries lower but do not eliminate injection risk with live models.
- Admission limits are best effort across instances; there are no per-user token or cost quotas.
- Live OAuth flows, the deployed application and live-model injection resistance still need hosted verification
  ([deployment.md](deployment.md#6-verify-the-deployment)).
- The secret scanner matches known patterns; unknown formats, deleted refs, LFS payloads and external logs are out
  of scope.


### Research question checks

Both research composers validate requests through `ResearchRequest` before submission. Questions are normalized,
limited to 12–600 characters and must contain at least three words of three or more English letters. Obvious
single-word repetition and greeting-only input are rejected, as are placeholder text, questions made only of
filler words, and questions containing any word that looks like keyboard mashing. A word looks like mashing when it
contains a keyboard run (`asdf`), a tripled letter, a short repeated unit (`jkjkjk`), no vowel (5+ letters) or two
letter pairs that almost never occur in English (`ajdkf`); all-caps acronyms are skipped.

Questions made of real words can still be meaningless ("why does the colour seven taste louder than Tuesday"). For
live runs with the LLM enabled, `RunManager.submit` therefore asks the LLM for a short sense check
(`services/question_check.py`) before a run is created. The check is permissive (broad, speculative or controversial
questions pass), treats the question as untrusted data, and fails open: without an LLM, or if the call fails, only the
rules above apply.

Every rejection starts with "This research question seems to be invalid! Please check input." followed by the reason.
No run is created and nothing is researched, so no report can be generated from a meaningless question. Both
composers show rule-based problems as a warning under the question as soon as it is entered, keep the text so it can
be corrected, and report that the research was not started. The service revalidates requests before queueing or
execution, including models modified by programmatic callers. Control characters are removed as before.

Broad phrases such as “tell me everything” receive an advisory scope hint; they are not blocked or rewritten.
The composers remind users to specify scope and verify evidence, and that forecasts remain uncertain. These
checks do not establish factual correctness, detect all meaningless input or prevent prompt injection/hallucinations.
Existing evidence and output validation remain necessary. Interactive clarification and premise validation (whether
the question's assumptions are true) are not implemented.
