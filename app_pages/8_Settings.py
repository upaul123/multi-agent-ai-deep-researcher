"""Page 8: Settings.

- Personal preferences (any signed-in user, or the local user): default depth, mode, rounds, export banner.
- My documents: the user's own uploads.
- App-wide settings (admins only): models, search providers, cache, usage per user. Secrets are never stored or shown.
"""
import pandas as pd
import streamlit as st

from src.config import get_settings, mask_secret
from src.models.research import DEPTH_PRESETS, Depth
from src.ui.components import card, html, page_header
from src.ui.state import auth_enabled, configured_providers, current_user, effective_settings, get_repo, preference, provider_settings

repo, user = get_repo(), current_user()
env = get_settings()
settings = effective_settings()
page_header("Configuration", "Settings", "Your preferences and documents" + (", plus app-wide models, providers and "
            "usage (admin)." if user.is_admin else "."))

with card("caps"):
    html('<div class="ds-label">Capabilities</div>')

    def cap(label: str, on: bool, value: str) -> str:
        dot = "#41bb94" if on else "#c9cad6"
        return (f'<div class="ds-stat"><span><span style="color:{dot}">●</span> {label}</span><b>{value}</b></div>')
    html(cap("LLM", settings.llm_configured, settings.openrouter_model if settings.llm_configured else "not configured")
         + cap("Web search", settings.web_search_configured,
               "Tavily" if settings.web_search_configured else "not configured")
         + cap("Academic", True, "OpenAlex · Crossref · arXiv") + cap("Demo dataset", True, "offline · synthetic")
         + cap("Storage", True, repo.storage_description))
st.write("")

if user.is_guest:
    st.info("Sign in to save personal preferences and upload documents.", icon=":material/info:")
    st.stop()

# ------------------------------------------------------------------ personal
st.subheader("My research defaults")
with st.form("defaults"):
    depth = st.selectbox("Default depth", [d.value for d in Depth], index=[d.value for d in Depth].index(preference("depth")))
    iterations = st.slider("Default maximum follow-up rounds", 1, 6, int(preference("max_iterations")))
    mode = st.radio("Default mode", ["demo", "live"], index=0 if preference("mode") == "demo" else 1, horizontal=True)
    banner = st.checkbox("Add a 'synthetic demo' banner to exported demo reports", value=bool(preference("include_demo_banner")))
    if st.form_submit_button("Save my defaults"):
        for key, value in {"depth": depth, "max_iterations": iterations, "mode": mode, "include_demo_banner": banner}.items():
            repo.set_user_setting(user.id, f"pref_{key}", value)
        st.success("Saved.")
st.dataframe(pd.DataFrame([{
    "Depth": d.value, "Subquestions": p["subquestions"], "Queries / subquestion": p["queries_per_subquestion"],
    "Results / search": p["results_per_search"], "Max searches": p["budget"].max_searches,
    "Max sources": p["budget"].max_sources, "Max seconds": p["budget"].max_seconds,
    "Max LLM calls": p["budget"].max_llm_calls, "Max LLM tokens": p["budget"].max_llm_tokens}
    for d, p in DEPTH_PRESETS.items()]), hide_index=True, width="stretch")
st.caption("Hard limits per depth. The orchestrator also stops early when evidence is sufficient or new rounds stop "
           "finding new sources.")

st.subheader("My documents")
docs = repo.list_documents(user.id)
if not docs:
    st.caption("No uploaded documents. Add them in the Research Workspace.")
for doc in docs:
    cols = st.columns([4, 1])
    cols[0].caption(f"{doc['title'][:60]} ({doc['filename']}, {doc['sections']} sections)")
    if cols[1].button("Delete", key=f"doc_{doc['id']}"):
        repo.delete_document(doc["id"], user.id)
        st.rerun()

if not user.is_admin:
    st.caption("App-wide settings (models, providers, cache) are managed by the app's administrators.")
    st.stop()

# ------------------------------------------------------------------ admin: app-wide
st.divider()
st.subheader("App-wide settings (admin)")
st.dataframe(pd.DataFrame([
    {"Setting": "OPENROUTER_API_KEY", "Status": mask_secret(env.openrouter_api_key), "Used for": "LLM planning, extraction, synthesis, Q&A"},
    {"Setting": "TAVILY_API_KEY", "Status": mask_secret(env.tavily_api_key), "Used for": "Live web and news search"},
    {"Setting": "PINECONE_API_KEY", "Status": mask_secret(env.pinecone_api_key), "Used for": "Vector search and embeddings for live research"},
    {"Setting": "TURSO_AUTH_TOKEN", "Status": mask_secret(env.turso_auth_token), "Used for": "Persistent shared storage"},
    {"Setting": "Sign-in ([auth])", "Status": (", ".join(configured_providers()) or "configured") if auth_enabled()
     else "not configured",
     "Used for": "Per-user accounts"},
    {"Setting": "CONTACT_EMAIL", "Status": "set" if env.contact_email else "not set", "Used for": "Polite use of OpenAlex/Crossref APIs"},
]), hide_index=True, width="stretch")
st.caption(f"Storage: {repo.storage_description}. Keys come from `.env` locally or Streamlit Secrets in the cloud, and "
           "are never written to the database or shown in full.")

with st.form("llm"):
    st.markdown("**LLM model**")
    st.text_input("Provider endpoint", value=env.openrouter_base_url, disabled=True,
                  help="Set OPENROUTER_BASE_URL to change it.")
    model = st.text_input("Model (OpenRouter id)", value=settings.openrouter_model,
                          help="Any OpenRouter model that supports JSON output, e.g. openai/gpt-4o-mini.")
    if st.form_submit_button("Save model"):
        repo.set_setting("llm_model", model.strip() or env.openrouter_model)
        st.success("Saved.")

current = provider_settings()
with st.form("providers"):
    st.markdown("**Search providers (live mode)**")
    tavily = st.checkbox("Tavily web & news search", value=current.tavily, disabled=not env.web_search_configured,
                         help=None if env.web_search_configured else "Requires TAVILY_API_KEY")
    openalex = st.checkbox("OpenAlex (scholarly works)", value=current.openalex)
    crossref = st.checkbox("Crossref (DOI metadata, lead resolution)", value=current.crossref)
    arxiv = st.checkbox("arXiv (preprints)", value=current.arxiv)
    fetch = st.checkbox("Fetch full text of web results (bounded, public pages only)", value=current.fetch_full_text)
    if st.form_submit_button("Save providers"):
        repo.set_setting("providers", {"tavily": tavily, "openalex": openalex, "crossref": crossref, "arxiv": arxiv,
                                       "fetch_full_text": fetch})
        st.success("Saved.")

c1, c2 = st.columns(2)
with c1:
    st.markdown(f"Cached search results: **{repo.cache_size()}** (shared, valid for 24 h)")
    if st.button("Clear search cache"):
        st.success(f"Removed {repo.clear_cache()} cached result(s).")

st.subheader("Usage by user (admin)")
st.caption("Counts only: administrators cannot see other users' questions, reports or documents here.")
stats = repo.user_stats()
if stats:
    st.dataframe(pd.DataFrame([{"User": s["name"] or s["email"] or s["id"], "Email": s["email"] or "",
                                "Last login": (s["last_login"] or "").replace("T", " ")[:16], "Runs": s["runs"],
                                "Live runs": s["live_runs"], "Documents": s["documents"]} for s in stats]),
                 hide_index=True, width="stretch")
else:
    st.caption("No signed-in users yet." if auth_enabled() else "Sign-in is not configured (single local user).")
