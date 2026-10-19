# Deployment guide

How to deploy Deep Researcher AI to Streamlit Community Cloud, configure sign-in, storage and tracing, and verify
the result. Other hosts work the same way: run `streamlit run app.py` and supply the same secrets.

## Contents

1. [Before you start](#1-before-you-start)
2. [Identity providers](#2-identity-providers)
3. [Persistent storage (Turso)](#3-persistent-storage-turso)
4. [Configure Streamlit Cloud](#4-configure-streamlit-cloud)
5. [Sizing and limits](#5-sizing-and-limits)
6. [Verify the deployment](#6-verify-the-deployment)
7. [Operations](#7-operations)
8. [Troubleshooting sign-in](#troubleshooting-sign-in)

## 1. Before you start

You need:

- the repository on GitHub, and the branch you want to deploy;
- an **OpenRouter** API key (LLM features) and, for web and news search, a **Tavily** API key;
- at least one **identity provider**: Google, Microsoft or a hosted email provider (section 2);
- a **Turso** database, so history survives restarts (section 3);
- optionally, a **LangSmith** API key for tracing ([observability.md](observability.md)).

No service in this list requires a credit card on its free tier. Skip any "free trial" offers that ask for one.

Production refuses to start without sign-in (`APP_ENV` defaults to `production`). Never deploy with
`APP_ENV = "local"`: every visitor would become the administrator.

## 2. Identity providers

Every provider needs a client ID, a client secret and an OpenID discovery URL, and must allow this redirect URI:

```
https://<your-app>.streamlit.app/oauth2callback
```

Add `http://localhost:8501/oauth2callback` as well if you test sign-in locally.

### Google

1. In [Google Cloud Console](https://console.cloud.google.com), create or select a project.
2. Open **Google Auth Platform** (older menus: *APIs & Services → OAuth consent screen*) and click **Get started**:
   app name, support email, audience **External**, contact email.
3. **Audience → Test users:** add the Google accounts that may sign in while the app is in *Testing*.
4. **Clients → Create client → Web application:** add the redirect URIs above, then **Create**. Copy the client ID
   and secret straight away; the secret may not be shown again.
5. When you are ready for anyone to sign in: **Audience → Publish app → Confirm**. The app only requests name, email
   and profile picture, so Google does not need to review it.

Discovery URL: `https://accounts.google.com/.well-known/openid-configuration`

### Microsoft

1. In the [Microsoft Entra admin center](https://entra.microsoft.com): **App registrations → New registration**.
2. Choose who may sign in (personal and work accounts, or your organisation only) and add the redirect URI as a
   **Web** platform.
3. **Certificates & secrets → New client secret:** copy the *value*.

Discovery URL: `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration` (replace `common`
with your tenant id to restrict sign-in to one organisation).

Microsoft usually does not send an "email verified" claim, so Microsoft accounts cannot be admins. Use Google or
email sign-in for administrators.

### Email (hosted OpenID Connect, for example Auth0)

1. Create a **Regular Web Application** and enable a username-and-password database connection with sign-ups.
2. Add the redirect URI to **Allowed Callback URLs**.
3. Copy the client ID and secret.

Discovery URL: `https://<your-tenant>.<region>.auth0.com/.well-known/openid-configuration`

Passwords, verification emails and resets are handled by the provider; the app never sees a password.

## 3. Persistent storage (Turso)

Streamlit Cloud wipes local files when the app restarts, so use a hosted database:

```bash
turso db create deep-researcher
turso db show deep-researcher --url        # TURSO_DATABASE_URL (libsql://…)
turso db tokens create deep-researcher     # TURSO_AUTH_TOKEN
```

Choose a database region close to the app host to keep write latency low. The schema is created and migrated
automatically on first start.

Local development can share the same database. Heartbeats are stored in UTC, so a laptop and the cloud app can run
at the same time without marking each other's runs as failed. Local test runs will appear in the same history,
though; use a separate database if you want them kept apart.

## 4. Configure Streamlit Cloud

1. On [share.streamlit.io](https://share.streamlit.io), create the app from your repository and branch, with main
   file `app.py` and Python 3.13.
2. Open **App settings → Secrets** and paste the template below, filled in. Rules: quote every value, list each key
   once, and keep plain keys **above** the first `[section]`.
3. Save. The app restarts with the new settings.

```toml
APP_ENV = "production"

OPENROUTER_API_KEY = "sk-or-..."
TAVILY_API_KEY = "tvly-..."
CONTACT_EMAIL = "you@example.com"
ADMIN_EMAILS = "you@example.com"

TURSO_DATABASE_URL = "libsql://<database>-<org>.<region>.turso.io"
TURSO_AUTH_TOKEN = "..."

# Optional: model prices for cost estimates (USD per million tokens)
# LLM_INPUT_USD_PER_MILLION = "0.15"
# LLM_OUTPUT_USD_PER_MILLION = "0.60"

# Optional: LangSmith tracing (see observability.md)
# LANGSMITH_TRACING = "true"
# APP_API_TRACING = "true"
# LANGSMITH_API_KEY = "lsv2_..."
# LANGSMITH_PROJECT = "deep-researcher-prod"
# LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"
# LANGSMITH_HIDE_INPUTS = "true"
# LANGSMITH_HIDE_OUTPUTS = "true"

[auth]
redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
cookie_secret = "<long random string>"

[auth.google]
client_id = "<client id>.apps.googleusercontent.com"
client_secret = "<client secret>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

# Optional extra providers; remove the sections you do not use.
# [auth.microsoft]
# client_id = "<application (client) id>"
# client_secret = "<client secret value>"
# server_metadata_url = "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
#
# [auth.email]
# client_id = "<client id>"
# client_secret = "<client secret>"
# server_metadata_url = "https://<your-tenant>.<region>.auth0.com/.well-known/openid-configuration"
```

Generate the cookie secret with `python -c "import secrets; print(secrets.token_hex(32))"`. Use a different one from
your local `secrets.toml`.

**How secrets reach the app.** Streamlit stores them outside your repository and provides them at runtime: top-level
keys become environment variables (read by `src/config.py`), and the `[auth]` sections are read by Streamlit's
sign-in. The repository's `.env` and `.streamlit/secrets.toml` are git-ignored and never deployed.

**Keep these settings in the repository** (`.streamlit/config.toml`): `server.enableStaticServing = true` serves the
logo and hero image from `static/`, and `client.toolbarMode = "viewer"` keeps the theme switcher while hiding
developer options.

## 5. Sizing and limits

| Setting | Default | Guidance |
|---|---|---|
| `MAX_CONCURRENT_RUNS` | 2 | Investigations executing at once per process. Each uses a few threads and outbound connections; keep it low on small hosts. |
| `MAX_ACTIVE_RUNS_PER_USER` | 1 | Queued plus running investigations per user. |
| `MAX_QUEUED_RUNS` | 10 | New runs are refused once this many are active overall. |
| `RUN_HEARTBEAT_SECONDS` / `RUN_STALE_AFTER_SECONDS` | 15 / 120 | A run is marked failed after this long without a heartbeat (minimum three heartbeats). |

There are no per-user token or cost quotas; the per-run budget (by depth) and these limits bound usage.

## 6. Verify the deployment

**Health check** (does not test sign-in or research):

```bash
python scripts/check_deployment.py https://<your-app>.streamlit.app
```

**Hosted checklist.** Record the date, deployed commit, model, providers, outcome and any error for each step. Keep
personal data and secret values out of screenshots and logs.

- [ ] Fresh browser: the public home loads; guests can only run the three demo questions; a demo run completes.
- [ ] Sign in with a test account for each configured provider; sign out and sign in again.
- [ ] Run live research; check sources, quotations, claim types and citations.
- [ ] Close the tab mid-run, reopen the app and reattach from **In progress**; try **Stop & write report**.
- [ ] Upload a CSV with real sourced figures and units; check the Charts tab values and CSV download against the
      file. Try one with mixed units or missing values and confirm nothing is invented.
- [ ] Open every results tab and the progress, library, settings and profile pages, on desktop and phone width, in
      light and dark themes.
- [ ] Export Markdown, HTML and PDF; check citations and figures are preserved.
- [ ] Ask a follow-up question; check its citations refer to the run's evidence.
- [ ] With a second account, confirm the first account's runs and documents cannot be listed, opened, exported or
      deleted.
- [ ] Reboot the app and confirm history and charts are still there (Turso).
- [ ] If tracing is enabled, confirm a `research-investigation` trace appears in the production project.
- [ ] In a separate test deployment without `[auth]`, confirm the app stops before opening storage.

**Status:** the automated suite runs offline only. The hosted checklist above has not yet been completed for a
public deployment; do not treat offline tests or a health check as an end-to-end pass.

## 7. Operations

- **Change a setting:** edit Secrets and save; the app restarts automatically.
- **Rotate a key:** create the new key at the provider, update Secrets, confirm the app works, then revoke the old
  key. Removing a key from code does not invalidate it.
- **Stuck runs:** runs whose process stopped are marked failed automatically after `RUN_STALE_AFTER_SECONDS`.
- **App sleeping:** Community Cloud sleeps idle apps; runs in progress when it sleeps are marked failed on wake.
- **Fork pull requests:** CI waits for a maintainer to approve workflows on pull requests from forks
  (**Checks → Approve and run**). Fork workflows receive no repository secrets.

## Troubleshooting sign-in

| Message | Cause and fix |
|---|---|
| "Invalid format: please enter valid TOML" (Secrets editor) | Unquoted value, duplicate key, or a plain key placed after `[auth]`. |
| "Authentication is required in production" | `[auth]` or every provider section is missing, incomplete or still contains a `<placeholder>`. |
| Google "Error 400: redirect_uri_mismatch" | `redirect_uri` in Secrets differs from the URIs registered with Google (scheme, host, path `/oauth2callback`). |
| Google "Access blocked" | The app is in *Testing* and the account is not a test user; add it or publish the app. |
| "Your sign-in session comes from an identity provider this app does not accept" | The token's issuer does not match a configured provider's discovery URL host. |

**Diagnostics.** Set `AUTH_DIAGNOSTICS = "true"` (top level, before `[auth]`), reboot, and read the server logs
(**Manage app → logs**), not the browser console. Set it back to `"false"` afterwards. Only field names and
true/false results are logged, never values.

| Log line | Meaning |
|---|---|
| `AUTH_CONFIG` | Which required fields are present, which providers are complete, whether the callback is HTTPS, ends in `/oauth2callback` and is not localhost. "True" means a non-empty value without a placeholder, not that the provider accepts it. |
| `AUTH_BLOCKED` | Production stopped because sign-in configuration is missing, incomplete or unreadable. Fix Secrets; provider settings alone cannot fix this. |
| `AUTH_LOGIN_START` / `AUTH_LOGIN_FAILED` | Sign-in started for a provider / failed to start (exception class only). |
| `AUTH_SESSION_ACCEPTED` | A signed-in session from a configured provider reached the app. |
| `AUTH_SESSION_INVALID` | The session had no usable subject, or came from an unknown issuer. |

Diagnostics cannot see the redirect URIs registered at the provider or errors handled inside the provider's pages.
Do not enable HTTP wire logging or share complete secret files to diagnose them.
