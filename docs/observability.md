# Observability with LangSmith

Deep Researcher AI can send traces of each investigation to [LangSmith](https://smith.langchain.com): one trace per
run, with a span for every agent, model call, provider search and HTTP operation. Tracing is optional and off by
default. It observes the app; it does not replace OpenRouter, the search providers or the app's own budget
tracking.

## Contents

1. [Switches](#switches)
2. [Set up locally](#set-up-locally)
3. [Set up a deployment](#set-up-a-deployment)
4. [Read a research trace](#read-a-research-trace)
5. [Token counts and cost](#token-counts-and-cost)
6. [Privacy](#privacy)
7. [Failure behaviour](#failure-behaviour)
8. [Troubleshooting](#troubleshooting)
9. [Verification status](#verification-status)

## Switches

| `LANGSMITH_TRACING` | `APP_API_TRACING` | Result |
|---|---|---|
| `false` or unset | any | No research tracing |
| `true` | `false` | Graph and agent spans only |
| `true` | `true` (default) | Graph and agent spans plus detailed request spans |

Restart Streamlit fully after changing either switch.

## Set up locally

1. Create an API key in LangSmith (**Settings → API keys**). Copy the full key when it is shown; the portal later
   shows only a shortened form, which cannot be used.
2. Add to `.env` (each key once):

   ```dotenv
   LANGSMITH_TRACING=true
   APP_API_TRACING=true
   LANGSMITH_API_KEY=lsv2_...
   LANGSMITH_PROJECT=deep-researcher-local
   LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com
   LANGSMITH_HIDE_INPUTS=true
   LANGSMITH_HIDE_OUTPUTS=true
   # LANGSMITH_WORKSPACE_ID=...   only if your key needs a workspace selected
   ```

3. Restart the app and run a **live** investigation with the LLM enabled. Demo runs make no model calls, so their
   traces contain agents but no model spans.

Pick the endpoint for your **workspace's region**, not your location: `https://api.smith.langchain.com` (US),
`https://eu.api.smith.langchain.com` (EU) or `https://apac.api.smith.langchain.com` (APAC). A workspace id is a
workspace id, not an organisation or project id.

Values already present in the process environment take precedence over `.env`.

## Set up a deployment

Add the same keys at the top of Streamlit Secrets, above `[auth]`, in TOML form (`LANGSMITH_TRACING = "true"`), and
use a separate project name such as `deep-researcher-prod` so production traces stay apart from local ones. Keep
input and output hiding on. See the template in [deployment.md](deployment.md#4-configure-streamlit-cloud).

## Read a research trace

1. In LangSmith, open **Tracing** and select your project.
2. Under **Traces**, open the latest **research-investigation** row. Its metadata holds the opaque run id, mode and
   depth.
3. Expand the tree: `planner`, `retriever`, `source_evaluator`, `critical_analyst`, `gap_analyst`, then
   `insight_generator` and `data_visualizer` (which run side by side) and `report_builder`. Follow-up rounds add
   another retrieval branch. Collapse a busy retriever branch to reach later agents.
4. Inside the agents, request spans show what happened:

| Span | Meaning |
|---|---|
| `openrouter.request` | One attempted model request; each retry is a separate span |
| `search.provider` | One provider search, including cache hits and budget outcomes |
| `http.get_json`, `http.post_json`, `http.get_text` | One logical provider HTTP operation |
| `http.request_attempt` | One attempt inside the HTTP retry loop |
| `http.safe_fetch`, `http.fetch_attempt` | A page fetch and each of its redirect hops |

The **Runs** tab lists every span (agents, routing and HTTP operations), so its count is much higher than the number
of investigations. Parent and child durations overlap, especially for parallel work, so do not add them up.

## Token counts and cost

Select an `openrouter.request` span and open **Attributes → metadata → request_metrics → usage_metadata** for
`input_tokens`, `output_tokens` and `total_tokens`. A badge such as `1.445K` means about 1,445 tokens for that call.
If the provider did not report usage, the fields are missing; that does not mean zero.

For per-investigation totals, use the app: open the run, then **Agents in action**. Its budget panel separates:

- **Observed tokens:** usage the provider reported.
- **Unreported allowance:** a conservative charge for requests whose usage is unknown.
- **In-flight tokens:** reservations for requests still running.

USD estimates need `LLM_INPUT_USD_PER_MILLION` and `LLM_OUTPUT_USD_PER_MILLION` for the active model. They are
estimates, not invoices.

## Privacy

- Custom spans never contain prompts, responses, search queries, URLs, headers, uploaded content or credentials.
  They contain operation names, safe outcomes and counts, attempt numbers, model ids, reported token usage and the
  opaque run id.
- Graph inputs and outputs are hidden by default (`LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS` default to
  `true`), but an explicit setting can turn hiding off.
- Hiding does not redact metadata or exception messages already present in graph errors. Review those paths before
  tracing sensitive workloads.
- Traces are stored by LangSmith in the region you selected.

## Failure behaviour

Tracing set-up and clean-up errors are isolated from research, so an unreachable LangSmith should not stop a run.
The SDK may still log export warnings, and tracing adds a small overhead. Trace context is copied explicitly into
each worker thread so background runs and parallel calls nest under the right investigation.

## Troubleshooting

| Problem | Check |
|---|---|
| No traces appear | Restart the app, run a fresh live investigation, check the project name and the portal's time filter, and look for export errors in the terminal. |
| HTTP 403 Forbidden | Regional endpoint, the full key (not the shortened portal form), workspace access, and conflicting values between the environment and Secrets. If the full key was not saved, create a new one and revoke the old one after testing. |
| Model spans missing | The run was in demo mode, or the LLM toggle was off. |
| Request spans missing | `APP_API_TRACING` is `false`. |

Never print or share API keys while diagnosing.

## Verification status

Confirmed locally (11 October 2026, APAC workspace): `research-investigation` traces with agent branches,
`openrouter.request` spans with token usage, provider searches and HTTP operations.

Not yet verified, and not covered by automated tests:

- token totals reconciled with provider billing;
- retries and exhausted budgets under controlled failures;
- no export when `LANGSMITH_TRACING=false`, and graph-only output when `APP_API_TRACING=false`;
- research completing when LangSmith is unreachable;
- tracing from the public deployment.
