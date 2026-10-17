"""Reusable UI pieces for the Deep Research Agent glass skin: cards, tags, headers, provenance badges and helpers.

Layout notes: Streamlit gives every keyed container the CSS class `st-key-<key>`, so cards are containers whose key
starts with "card" and are styled by `[class*="st-key-card"]`. Widgets that need a special look (demo question
cards, depth cards, suggestion pills) are targeted the same way through their key.
"""
from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from src.evidence.citations import strip_citations  # noqa: F401  (re-exported for the pages)
from src.ui.brand import BRAND_CSS, ICON_CSS
from src.models.evidence import ORIGIN_LABELS, Origin, Source, SourceAssessment

CSS = """
<style>
/* Theme tokens. Streamlit sets color-scheme (light|dark) on the app and sidebar, so light-dark() follows the active
   theme instantly, including a switch from the app menu. Values mirror .streamlit/config.toml. */
.stApp, [data-testid="stSidebar"] {
  --dr-ink: light-dark(#0B1220, #EEF2FF); --dr-soft: light-dark(#2C3550, #CDD4EA); --dr-muted: light-dark(#4A5470, #AAB4D0);
  --dr-glass: light-dark(rgba(255,255,255,.66), rgba(255,255,255,.055));
  --dr-glass-strong: light-dark(rgba(255,255,255,.88), rgba(255,255,255,.09));
  --dr-field: light-dark(rgba(255,255,255,.85), rgba(255,255,255,.05));
  --dr-line: light-dark(rgba(15,23,42,.10), rgba(255,255,255,.12));
  --dr-panel: light-dark(rgba(255,255,255,.55), rgba(255,255,255,.03));
  --dr-node: light-dark(#FFFFFF, #111A33); --dr-solid: light-dark(#F4F7FC, #0B1124);
  --dr-accent: light-dark(#0A7EA4, #3BDCFF); --dr-accent-ink: light-dark(#FFFFFF, #04111A);
  --dr-accent-text: light-dark(#086A8A, #7BE8FF); --dr-accent-soft: light-dark(rgba(10,126,164,.10), rgba(59,220,255,.13));
  --dr-violet: light-dark(#5A49D6, #9A8CFF); --dr-violet-text: light-dark(#4B3BC4, #BDB3FF);
  --dr-violet-soft: light-dark(rgba(90,73,214,.10), rgba(154,140,255,.15));
  --dr-ok: light-dark(#047857, #6EE7B7); --dr-ok-soft: light-dark(rgba(5,150,105,.12), rgba(52,211,153,.14));
  --dr-warn: light-dark(#92400E, #FCD34D); --dr-warn-soft: light-dark(rgba(217,119,6,.13), rgba(251,191,36,.14));
  --dr-bad: light-dark(#B91C1C, #FCA5A5); --dr-bad-soft: light-dark(rgba(220,38,38,.10), rgba(248,113,113,.14));
  --dr-amber: light-dark(#B45309, #FBBF24);
  --dr-pink: light-dark(#BE185D, #F9A8D4); --dr-pink-soft: light-dark(rgba(190,24,93,.10), rgba(244,114,182,.14));
  --dr-glow-a: light-dark(rgba(10,126,164,.16), rgba(59,220,255,.18));
  --dr-glow-b: light-dark(rgba(90,73,214,.14), rgba(150,132,255,.16));
  --dr-shadow: light-dark(rgba(15,23,42,.08), rgba(0,0,0,.35));
  --dr-blur: blur(20px) saturate(140%);
}
.stApp { background-image: radial-gradient(900px 640px at 92% -8%, var(--dr-glow-a), transparent 62%),
                           radial-gradient(760px 620px at 6% 108%, var(--dr-glow-b), transparent 62%);
         background-attachment: fixed; }
.block-container { padding-top: 4.5rem; max-width: 1240px; }
h1, h2, h3 { letter-spacing: -0.02em; }
h1 em, h2 em, .ds-title em, .dr-h1 em { font-style: normal; color: var(--dr-accent); }
[data-testid="stCaptionContainer"], .ds-muted { color: var(--dr-muted); }
[data-testid="stHeader"] { background: transparent; backdrop-filter: var(--dr-blur); }
/* glass surfaces: keyed card containers and HTML cards */
[class*="st-key-card"], .ds-card { background: var(--dr-glass); border: 1px solid var(--dr-line); border-radius: 20px;
  backdrop-filter: var(--dr-blur); box-shadow: 0 18px 50px var(--dr-shadow); padding: 18px 22px; }
[class*="st-key-card-flat"] { box-shadow: none; }
/* eyebrow + page heading */
.ds-eyebrow { font: 500 11px 'JetBrains Mono', monospace; letter-spacing: .1em; color: var(--dr-accent-text);
  text-transform: uppercase; display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ds-eyebrow i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--dr-accent); }
.ds-heading { margin: 0 0 1.3rem 0; }
.ds-title { font-family: Sora, sans-serif; font-weight: 700; font-size: 2rem; letter-spacing: -.03em; line-height: 1.22;
  margin: .25rem 0 .55rem 0; color: var(--dr-ink); }
.ds-report-head { margin-bottom: 1.6rem; }
.ds-report-head .ds-eyebrow, .ds-report-head .ds-title { margin-bottom: 16px; }
.ds-report-head .ds-meta { row-gap: 10px; }
.ds-report-head .ds-title { font-size: 1.65rem; font-weight: 600; line-height: 1.35; max-width: 900px; }
[data-baseweb="tab-list"] { gap: 24px; flex-wrap: wrap; }
.ds-sub { color: var(--dr-muted); font-size: .98rem; margin: 0; max-width: 760px; line-height: 1.55; }
.ds-label { font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .1em; color: var(--dr-muted);
  text-transform: uppercase; margin: 2px 0 6px 0; }
/* tags & badges */
.ds-tag { display: inline-block; border-radius: 999px; padding: 3px 10px; font-size: 11px; font-weight: 600;
  vertical-align: middle; }
.ds-complete { background: var(--dr-ok-soft); color: var(--dr-ok); }
.ds-running { background: var(--dr-accent-soft); color: var(--dr-accent-text); }
.ds-failed { background: var(--dr-bad-soft); color: var(--dr-bad); }
.ds-draft { background: var(--dr-glass-strong); color: var(--dr-soft); border: 1px solid var(--dr-line); }
.ds-demo-tag { background: var(--dr-warn-soft); color: var(--dr-warn); }
.ds-live-tag { background: var(--dr-violet-soft); color: var(--dr-violet-text); }
.dr-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600;
  letter-spacing: .03em; text-transform: uppercase; margin-right: .3rem; vertical-align: middle;
  background: var(--dr-glass-strong); border: 1px solid var(--dr-line); }
.dr-demo { color: var(--dr-warn); background: var(--dr-warn-soft); } .dr-live { color: var(--dr-ok); background: var(--dr-ok-soft); }
.dr-cache { color: var(--dr-violet-text); background: var(--dr-violet-soft); }
.dr-upload { color: var(--dr-pink); background: var(--dr-pink-soft); }
.dr-observed { color: var(--dr-ok); background: var(--dr-ok-soft); }
.dr-inferred { color: var(--dr-violet-text); background: var(--dr-violet-soft); }
.dr-speculative { color: var(--dr-pink); background: var(--dr-pink-soft); }
.dr-flag { color: var(--dr-bad); background: var(--dr-bad-soft); }
.dr-muted { color: var(--dr-muted); font-size: .86rem; }
/* demo banner */
.ds-banner { display: flex; align-items: center; gap: 11px; margin: 0 0 18px; padding: 10px 14px; border-radius: 14px;
  border: 1px solid var(--dr-line); background: var(--dr-warn-soft); color: var(--dr-warn); font-size: 11px;
  font-weight: 700; letter-spacing: .06em; backdrop-filter: var(--dr-blur); }
.ds-banner span { font-weight: 400; letter-spacing: 0; color: var(--dr-soft); }
/* buttons */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] { border: 0; background: var(--dr-accent); color: var(--dr-accent-ink);
  box-shadow: 0 10px 28px var(--dr-glow-a); font-weight: 700; }
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primaryFormSubmit"] p { color: var(--dr-accent-ink); }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { filter: brightness(1.06); transform: translateY(-1px);
  background: var(--dr-accent); color: var(--dr-accent-ink); }
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"] { font-weight: 600;
  background: var(--dr-glass-strong); border-color: var(--dr-line); backdrop-filter: var(--dr-blur); }
[data-testid="stBaseButton-secondary"]:hover { border-color: var(--dr-accent); color: var(--dr-ink); }
.stButton button, .stDownloadButton button, .stLinkButton a { font-weight: 600; }
/* inputs */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div { background: var(--dr-field);
  backdrop-filter: var(--dr-blur); }
/* tabs */
[data-testid="stTab"] p { color: var(--dr-muted); }
[data-testid="stTab"][aria-selected="true"] p { color: var(--dr-accent-text); font-weight: 700; }
/* sidebar: glass panel with the navigation menu */
[data-testid="stSidebar"] { border-right: 1px solid var(--dr-line); backdrop-filter: var(--dr-blur); }
[data-testid="stSidebarNavLink"] { border-radius: 12px; }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: var(--dr-glass-strong); box-shadow: inset 0 0 0 1px var(--dr-line); }
[data-testid="stSidebarNavLink"][aria-current="page"] span { color: var(--dr-ink); font-weight: 700; }
[data-testid="stSidebarNavLink"][aria-current="page"] [data-testid="stIconMaterial"] { color: var(--dr-accent); }
[data-testid="stNavSectionHeader"] { font: 500 10.5px 'JetBrains Mono', monospace !important; letter-spacing: .1em;
  color: var(--dr-muted); text-transform: uppercase; }
.st-key-side_new button { background: var(--dr-accent); color: var(--dr-accent-ink); border: 0; font-weight: 700;
  box-shadow: 0 8px 24px var(--dr-glow-a); }
.st-key-side_new button p { color: var(--dr-accent-ink); }
.st-key-side_new button:hover { background: var(--dr-accent); color: var(--dr-accent-ink); filter: brightness(1.06); }
[class*="st-key-recent_"], [class*="st-key-active_"] { margin-bottom: -0.75rem; }
[class*="st-key-recent_"] button, [class*="st-key-active_"] button { border: 0; background: transparent;
  justify-content: flex-start; color: var(--dr-soft); font-weight: 500; padding: 6px 9px; min-height: 0; }
[class*="st-key-recent_"] button:hover, [class*="st-key-active_"] button:hover { background: var(--dr-glass-strong);
  color: var(--dr-ink); }
[class*="st-key-recent_"] button p, [class*="st-key-active_"] button p { white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-size: 13px; text-align: left; }
/* the hover-hint wrapper right-aligns and the label is centred: stretch every level and align left */
[class*="st-key-recent_"] .stTooltipIcon, [class*="st-key-recent_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-active_"] .stTooltipIcon, [class*="st-key-active_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-recent_"] button, [class*="st-key-active_"] button { width: 100%; justify-content: flex-start; }
[class*="st-key-recent_"] button > div, [class*="st-key-recent_"] button > div > span,
[class*="st-key-active_"] button > div, [class*="st-key-active_"] button > div > span {
  width: 100%; justify-content: flex-start; text-align: left; min-width: 0; }
[class*="st-key-recent_"] [data-testid="stMarkdownContainer"],
[class*="st-key-active_"] [data-testid="stMarkdownContainer"] { flex: 1 1 auto; min-width: 0; text-align: left; }
[class*="st-key-active_"] [data-testid="stIconMaterial"] { color: var(--dr-accent); animation: dr-spin 2.4s linear infinite; }
.ds-profile { display: flex; align-items: center; gap: 10px; padding: 6px 2px; }
.ds-avatar { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 50%; background: var(--dr-accent);
  color: var(--dr-accent-ink); font: 600 13px Sora, sans-serif; flex: 0 0 38px; overflow: hidden; }
.ds-avatar img { width: 100%; height: 100%; object-fit: cover; }
.ds-profile b { display: block; font-size: 13.5px; } .ds-profile small { display: block; color: var(--dr-muted); font-size: 11.5px; }
/* selectable cards built from radios (demo questions, depth) */
[class*="st-key-pick_"] [data-testid="stElementContainer"], [class*="st-key-pick_"] .stRadio { width: 100%; }
[class*="st-key-pick_"] [role="radiogroup"] { gap: 10px; width: 100%; }
[class*="st-key-pick_"] [role="radiogroup"] > div { flex: 1 1 0; background: var(--dr-glass); border: 1px solid var(--dr-line);
  border-radius: 16px; padding: 12px 15px; margin: 0; cursor: pointer; transition: .15s; backdrop-filter: var(--dr-blur); }
[class*="st-key-pick_"] [role="radiogroup"] > div:hover { border-color: var(--dr-accent); }
[class*="st-key-pick_"] [role="radiogroup"] > div[data-selected="true"] { border-color: var(--dr-accent);
  background: var(--dr-accent-soft); box-shadow: inset 0 0 0 1px var(--dr-accent); }
[class*="st-key-pick_"] [data-testid="stRadioOption"] { width: 100%; margin: 0; }
[class*="st-key-pick_"] [data-testid="stRadioOption"] > div > div:first-child { display: none; }
[class*="st-key-pick_"] [data-testid="stRadioOption"] p { font-weight: 600; font-size: .94rem; }
[class*="st-key-pick_"] [data-testid="stRadioCaption"] { padding-left: 0; margin-left: 0; }
[class*="st-key-pick_"] [data-testid="stRadioCaption"] p { font-size: 12px; }
.st-key-pick_demo [role="radiogroup"] { flex-direction: column; align-items: stretch; }
.st-key-pick_demo [role="radiogroup"] > div { width: 100%; }
.st-key-pick_demo [role="radiogroup"] > div[data-selected="true"] [data-testid="stRadioOption"] p::before {
  content: "✓  "; color: var(--dr-accent); }
/* suggestion pills */
[class*="st-key-sugg_"] button { border: 1px solid var(--dr-line); background: var(--dr-glass); border-radius: 999px;
  padding: 4px 12px; color: var(--dr-soft); font-weight: 500; min-height: 0; }
[class*="st-key-sugg_"] button:hover { background: var(--dr-accent-soft); border-color: var(--dr-accent); color: var(--dr-ink); }
[class*="st-key-sugg_"] button p { font-size: 12.5px; }
/* ---------- public home and sign-in ---------- */
.dr-chip-row { display: flex; }
.dr-eyebrow-chip { display: inline-flex; align-items: center; gap: 10px; padding: 8px 14px; border-radius: 999px;
  border: 1px solid var(--dr-line); background: var(--dr-glass); backdrop-filter: var(--dr-blur);
  font: 500 12px 'JetBrains Mono', monospace; letter-spacing: .06em; text-transform: uppercase; color: var(--dr-muted); }
.dr-live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dr-accent); animation: dr-pulse 2.2s infinite; }
.dr-h1 { margin: 18px 0 16px; font: 700 clamp(34px, 3.6vw, 52px)/1.08 Sora, sans-serif; letter-spacing: -.035em; color: var(--dr-ink); }
.dr-lead { margin: 0 0 22px; max-width: 520px; font-size: 18px; line-height: 1.6; color: var(--dr-muted); }
.dr-hero { position: relative; margin: 6px 4px 10px; }
.dr-hero-frame { border-radius: 28px; padding: 10px; border: 1px solid var(--dr-line); background: var(--dr-glass);
  backdrop-filter: var(--dr-blur); box-shadow: 0 30px 80px var(--dr-shadow); }
.dr-hero-frame img { display: block; width: 100%; aspect-ratio: 3 / 2; object-fit: cover; border-radius: 20px; }
.dr-float { position: absolute; display: flex; align-items: center; gap: 10px; padding: 11px 15px; border-radius: 16px;
  border: 1px solid var(--dr-line); background: var(--dr-glass-strong); backdrop-filter: var(--dr-blur);
  font-weight: 600; font-size: 14px; color: var(--dr-ink); animation: dr-float 6s ease-in-out infinite; }
.dr-float .dr-i { color: var(--dr-accent); }
.dr-float.a { left: -14px; bottom: 34px; } .dr-float.b { right: -10px; top: 28px; animation-delay: -3s; }
.st-key-hero_demo button, .st-key-hero_signin button { min-height: 50px; padding: 0 26px; font-size: 15px; }
.st-key-hero_demo button p, .st-key-hero_signin button p { font-size: 15px; font-weight: 700; }
.dr-signin-brand { display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center; margin-bottom: 6px; }
.dr-brand-row { display: flex; align-items: center; justify-content: center; gap: 14px; }
.dr-wordmark { font: 700 26px Sora, sans-serif; letter-spacing: .14em; color: var(--dr-ink); text-decoration: none; cursor: help; }
.dr-signin-brand img { width: 102px; height: 102px; border-radius: 50%; border: 1px solid var(--dr-line);
  box-shadow: 0 0 30px var(--dr-glow-a); }
/* sidebar/header logo (st.logo, size="large" = 2rem): 50% larger */
img.stLogo { height: 3rem; max-height: 3rem; width: auto; }  /* sidebar (stSidebarLogo) and collapsed header */
.dr-signin-brand h1 { margin: 0; padding: 0; font: 600 24px Sora, sans-serif; letter-spacing: -.02em; }
.dr-signin-brand p { margin: 0; color: var(--dr-muted); font-size: 15px; }
.st-key-card-signin { max-width: 440px; margin: 2vh auto 0; padding: 30px 28px; border-radius: 28px; }
.st-key-card-signin [data-testid="stBaseButton-secondary"] { min-height: 48px; }
/* ---------- signed-in landing ---------- */
.dr-greet h1 { margin: 0; padding: 0; font: 700 clamp(28px, 3.2vw, 38px) Sora, sans-serif; letter-spacing: -.025em; }
.dr-greet p { margin: 6px 0 0; color: var(--dr-muted); font-size: 16px; }
.dr-figure { position: relative; margin: 0; height: 100%; min-height: 260px; border-radius: 24px; overflow: hidden;
  border: 1px solid var(--dr-line); }
.dr-figure img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 35%; }
.dr-figure figcaption { position: absolute; left: 14px; right: 14px; bottom: 14px; padding: 12px 14px; border-radius: 16px;
  border: 1px solid rgba(255,255,255,.18); background: rgba(7,11,24,.55); backdrop-filter: blur(16px); color: #EEF2FF;
  display: flex; flex-direction: column; gap: 2px; }
.dr-figure figcaption b { font: 600 15px Sora, sans-serif; } .dr-figure figcaption span { font-size: 13px; color: #C6CEE6; }
.dr-section-head h2 { margin: 0; padding: 0; font: 600 24px Sora, sans-serif; letter-spacing: -.02em; }
.dr-section-head p { margin: 4px 0 14px; color: var(--dr-muted); }
.dr-team { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.dr-tile { position: relative; overflow: hidden; padding: 18px; border-radius: 20px; border: 1px solid var(--dr-line);
  background: var(--dr-glass); backdrop-filter: var(--dr-blur); display: flex; flex-direction: column; gap: 12px;
  transition: transform .2s; }
.dr-tile:hover { transform: translateY(-3px); }
.dr-tile .relay { position: absolute; inset: 0; border-radius: 20px; pointer-events: none; opacity: 0;
  box-shadow: inset 0 0 0 1.5px var(--dr-accent), 0 0 34px var(--dr-glow-a);
  background: linear-gradient(180deg, var(--dr-accent-soft), transparent 70%); animation: dr-relay 6.4s ease-in-out infinite; }
.dr-tile-top { display: flex; align-items: center; justify-content: space-between; }
.dr-badge-icon { position: relative; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; }
.dr-badge-icon .ring { position: absolute; inset: 0; border-radius: 50%; border: 1.5px dashed var(--dr-accent);
  opacity: .6; animation: dr-spin 9s linear infinite; }
.dr-badge-icon .core { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--dr-accent-soft); color: var(--dr-accent-text); }
.dr-tile.violet .ring { border-color: var(--dr-violet); }
.dr-tile.violet .core { background: var(--dr-violet-soft); color: var(--dr-violet-text); }
.dr-tile-num { font: 500 12px 'JetBrains Mono', monospace; color: var(--dr-muted); }
.dr-tile h3 { margin: 0 0 4px; padding: 0; font: 600 15px Sora, sans-serif; }
.dr-tile p { margin: 0; font-size: 13px; color: var(--dr-muted); }
/* cross-cutting agents: amber, dashed, always on */
.dr-cross-head { display: flex; align-items: center; gap: 12px; margin: 20px 0 12px; }
.dr-cross-head span { font: 500 11px 'JetBrains Mono', monospace; letter-spacing: .1em; text-transform: uppercase;
  color: var(--dr-warn); }
.dr-cross-head i { flex: 1; height: 1px; background: var(--dr-line); }
.dr-cross { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.dr-ctile { position: relative; overflow: hidden; padding: 18px; border-radius: 20px; border: 1.5px dashed var(--dr-amber);
  background: var(--dr-glass); backdrop-filter: var(--dr-blur); display: flex; gap: 16px; align-items: flex-start; }
.dr-ctile .sweep { position: absolute; top: 0; bottom: 0; width: 34%; pointer-events: none;
  background: linear-gradient(90deg, transparent, var(--dr-warn-soft), transparent); animation: dr-sweep 3.2s ease-in-out infinite; }
.dr-ctile .dr-badge-icon { width: 52px; height: 52px; flex: none; }
.dr-ctile .ring { border-color: var(--dr-amber); animation-duration: 6s; }
.dr-ctile .core { width: 40px; height: 40px; background: var(--dr-warn-soft); color: var(--dr-warn); }
.dr-on { position: absolute; right: 2px; top: 2px; width: 9px; height: 9px; border-radius: 50%; background: var(--dr-amber);
  animation: dr-pulse 2s infinite; }
.dr-ctile-body { position: relative; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.dr-ctile-name { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dr-ctile-name h3 { margin: 0; padding: 0; font: 600 15px Sora, sans-serif; }
.dr-ctile-name span { font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .06em; text-transform: uppercase; color: var(--dr-warn); }
.dr-ctile p { margin: 0; font-size: 13px; color: var(--dr-muted); }
.dr-ctags { display: flex; flex-wrap: wrap; gap: 6px; }
.dr-ctag { padding: 3px 9px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--dr-line); background: var(--dr-node);
  color: var(--dr-soft); }
.dr-cross-key { width: 18px; height: 10px; border-radius: 4px; border: 1.5px dashed var(--dr-amber); }
.dr-tap { top: 340px; width: 2px; height: 22px; background-image: radial-gradient(circle, var(--dr-amber) 1.1px, transparent 1.5px);
  background-size: 2px 7px; }
.dr-cband { left: 0; right: 0; top: 362px; height: 98px; border-radius: 18px; border: 1.5px dashed var(--dr-amber); background: var(--dr-panel); }
.dr-cband-title { left: 0; right: 0; top: 370px; text-align: center; font: 500 10.5px 'JetBrains Mono', monospace;
  letter-spacing: .1em; text-transform: uppercase; color: var(--dr-warn); }
.dr-clane { left: 14px; right: 14px; height: 30px; display: flex; align-items: center; gap: 12px; }
.dr-clane-name { display: flex; align-items: center; gap: 8px; flex: none; width: 220px; font-size: 12.5px; font-weight: 700; }
.dr-clane i { flex: 1; height: 4px; background-image: radial-gradient(circle, var(--dr-amber) 1.5px, transparent 2px); background-size: 14px 4px; }
.dr-clane-out { flex: none; font: 500 10.5px 'JetBrains Mono', monospace; color: var(--dr-warn); }
.dr-ccore { width: 28px; height: 28px; flex: none; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--dr-warn-soft); color: var(--dr-warn); }
.dr-vband.amber { color: var(--dr-warn); }
.dr-vcross { position: relative; margin-left: 60px; padding: 10px 12px; border-radius: 14px; border: 1.5px dashed var(--dr-amber);
  background: var(--dr-panel); display: flex; flex-direction: column; gap: 10px; }
.dr-vcross > div { display: flex; align-items: center; gap: 10px; } .dr-vcross b { display: block; font-size: 13.5px; }
.dr-vcross small { display: block; font-size: 12px; color: var(--dr-muted); }
/* workflow graph */
.dr-wf { box-sizing: border-box; padding: 26px 28px 28px; border-radius: 24px; border: 1px solid var(--dr-line);
  background: var(--dr-glass); backdrop-filter: var(--dr-blur); }
.dr-wf-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 12px; margin-bottom: 20px; }
.dr-wf-head h2 { margin: 0; padding: 0; font: 600 22px Sora, sans-serif; letter-spacing: -.02em; }
.dr-wf-head p { margin: 4px 0 0; color: var(--dr-muted); }
.dr-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; font-size: 12px; color: var(--dr-muted); }
.dr-legend > span { display: flex; align-items: center; gap: 7px; }
.dr-diamond { width: 10px; height: 10px; transform: rotate(45deg); border-radius: 2px; border: 1.5px solid var(--dr-violet); }
.dr-dots { background-image: radial-gradient(circle, var(--dr-accent) 1.6px, transparent 2px); background-size: 14px 4px; }
.dr-dots.v { background-image: radial-gradient(circle, var(--dr-violet) 1.6px, transparent 2px); }
.dr-dots.col { background-size: 4px 14px; }
.flow-x { animation: dr-flowx .9s linear infinite; } .flow-xr { animation: dr-flowxr .9s linear infinite; }
.flow-yd { animation: dr-flowyd .9s linear infinite; } .flow-yu { animation: dr-flowyu .9s linear infinite; }
.dr-wf-h { position: relative; height: 462px; }
.dr-wf-v { display: none; }
.dr-abs { position: absolute; }
.dr-band { border-radius: 18px; border: 1px solid var(--dr-line); background: var(--dr-panel); }
.dr-grid7 { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.dr-band-title { font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .1em; text-transform: uppercase;
  color: var(--dr-muted); text-align: center; }
.dr-pill { padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; background: var(--dr-solid);
  border: 1px solid var(--dr-line); color: var(--dr-accent-text); white-space: nowrap; }
.dr-pill.v { border-color: var(--dr-violet); color: var(--dr-violet-text); font-size: 11.5px; padding: 3px 12px; }
.dr-pill.a { border-color: var(--dr-accent); font-size: 11.5px; padding: 3px 10px; }
.dr-nodes { list-style: none; margin: 0; padding: 0; }
.dr-node { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; margin: 0; }
.dr-node b { font-size: 12.5px; line-height: 1.25; }
.dr-node-icon { position: relative; width: 48px; height: 48px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; background: var(--dr-node); border: 1px solid var(--dr-line);
  box-shadow: 0 0 0 5px var(--dr-solid); color: var(--dr-accent-text); }
.dr-node-icon .relay { position: absolute; inset: -5px; border-radius: 50%; opacity: 0;
  box-shadow: 0 0 0 2px var(--dr-accent), 0 0 24px var(--dr-glow-a); animation: dr-relay 6.3s ease-in-out infinite; }
.dr-node-icon.final { background: var(--dr-accent); color: var(--dr-accent-ink); border-color: var(--dr-accent);
  box-shadow: 0 0 0 5px var(--dr-solid), 0 0 28px var(--dr-glow-a); }
.dr-node-icon.decision { background: transparent; border: 0; box-shadow: none; color: var(--dr-violet-text); }
.dr-node-icon.decision::before { content: ""; position: absolute; inset: 7px; transform: rotate(45deg); border-radius: 9px;
  background: var(--dr-node); border: 1.5px solid var(--dr-violet); box-shadow: 0 0 0 5px var(--dr-solid), 0 0 22px var(--dr-violet-soft); }
.dr-node-icon.decision .relay { inset: 2px; border-radius: 11px; transform: rotate(45deg);
  box-shadow: 0 0 0 2px var(--dr-violet), 0 0 24px var(--dr-violet-soft); }
.dr-node-icon .dr-i { position: relative; }
.dr-out { padding: 2px 8px; border-radius: 999px; font: 500 10.5px 'JetBrains Mono', monospace;
  background: var(--dr-accent-soft); color: var(--dr-accent-text); }
.dr-out.v { background: var(--dr-violet-soft); color: var(--dr-violet-text); }
.dr-out.final { background: var(--dr-accent); color: var(--dr-accent-ink); }
.dr-lanes { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.dr-lanes > span:first-child { font: 500 10px 'JetBrains Mono', monospace; letter-spacing: .06em; color: var(--dr-muted); }
.dr-lane { display: flex; align-items: center; gap: 6px; }
.dr-lane i { width: 26px; height: 3px; background-image: radial-gradient(circle, var(--dr-accent) 1.4px, transparent 1.8px);
  background-size: 8px 3px; animation: dr-flowx .9s linear infinite; }
.dr-lane span { padding: 1px 7px; border-radius: 6px; font-size: 10.5px; border: 1px solid var(--dr-line); background: var(--dr-node); }
.dr-joint { width: 14px; height: 14px; border-radius: 50%; background: var(--dr-solid); border: 2px solid var(--dr-accent);
  box-sizing: border-box; }
.dr-glow { border-radius: 16px; background: radial-gradient(closest-side, var(--dr-glow-a), transparent); pointer-events: none;
  animation: dr-travel 4.8s cubic-bezier(.55,.1,.45,.9) infinite; }
.dr-vrow { position: relative; display: flex; align-items: center; gap: 14px; }
.dr-vrow .dr-node-icon { width: 46px; height: 46px; flex: none; box-shadow: 0 0 0 4px var(--dr-solid); }
.dr-vrow small { display: block; font-size: 12px; color: var(--dr-muted); } .dr-vrow small.acc { color: var(--dr-accent-text); }
.dr-vrow small.vio { color: var(--dr-violet-text); }
.dr-vband { padding: 8px 0 0 60px; font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dr-muted); }
.dr-vpair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-left: 60px; }
.dr-vpair > div { padding: 10px; border-radius: 14px; border: 1px solid var(--dr-line); background: var(--dr-panel);
  display: flex; flex-direction: column; gap: 6px; font-weight: 700; font-size: 13px; }
.dr-vpair .dr-node-icon { width: 36px; height: 36px; box-shadow: none; }
.dr-vpair > em { grid-column: 1 / span 2; text-align: center; font-style: normal; font-size: 12px; font-weight: 600;
  color: var(--dr-accent-text); }
@keyframes dr-spin { to { transform: rotate(360deg); } }
@keyframes dr-sweep { 0% { left: -40%; } 100% { left: 110%; } }
@keyframes dr-relay { 0% { opacity: 0; } 4% { opacity: 1; } 14% { opacity: 1; } 22% { opacity: 0; } 100% { opacity: 0; } }
@keyframes dr-travel { 0% { left: 0%; } 100% { left: 92%; } }
@keyframes dr-pulse { 0% { box-shadow: 0 0 0 0 var(--dr-glow-a); } 70% { box-shadow: 0 0 0 10px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
@keyframes dr-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
@keyframes dr-flowx { to { background-position: 14px 0; } } @keyframes dr-flowxr { to { background-position: -14px 0; } }
@keyframes dr-flowyd { to { background-position: 0 14px; } } @keyframes dr-flowyu { to { background-position: 0 -14px; } }
@media (max-width: 860px) {
  .dr-team { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dr-cross { grid-template-columns: minmax(0, 1fr); }
  .dr-wf-h { display: none; } .dr-wf-v { display: flex; flex-direction: column; gap: 10px; position: relative; }
  .dr-wf { padding: 20px 16px; } .dr-float.a { left: 6px; } .dr-float.b { right: 6px; }
}
@media (prefers-reduced-motion: reduce) {
  .dr-ctile .sweep, .dr-on, .dr-tap, .dr-clane i, .dr-tile .relay, .dr-node-icon .relay, .dr-badge-icon .ring, .flow-x, .flow-xr, .flow-yd, .flow-yu, .dr-lane i,
  .dr-glow, .dr-live-dot, .dr-float, [class*="st-key-active_"] [data-testid="stIconMaterial"] { animation: none; }
}
/* ---------- progress, results and library (existing class names) ---------- */
.ds-icon { width: 30px; height: 30px; border-radius: 10px; display: grid; place-items: center; font-size: 13px; flex: 0 0 30px; }
.ds-purple, .ds-indigo { background: var(--dr-violet-soft); color: var(--dr-violet-text); }
.ds-blue, .ds-teal { background: var(--dr-accent-soft); color: var(--dr-accent-text); }
.ds-green { background: var(--dr-ok-soft); color: var(--dr-ok); }
.ds-gold { background: var(--dr-warn-soft); color: var(--dr-warn); } .ds-pink { background: var(--dr-pink-soft); color: var(--dr-pink); }
.ds-agent { display: flex; align-items: center; gap: 11px; border-top: 1px solid var(--dr-line); padding: 11px 0; }
.ds-agent:first-of-type { border-top: 0; }
.ds-agent > div { min-width: 0; } .ds-agent b { display: block; font-size: 13.5px; }
.ds-agent small { display: block; font-size: 12px; color: var(--dr-muted); margin-top: 3px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.ds-state { margin-left: auto; font-size: 11.5px; color: var(--dr-muted); white-space: nowrap; }
.ds-agent.done .ds-state { color: var(--dr-ok); } .ds-agent.working .ds-state { color: var(--dr-accent-text); font-weight: 700; }
.ds-agent.working .ds-icon { box-shadow: 0 0 0 2px var(--dr-accent), 0 0 18px var(--dr-glow-a); }
.ds-agent.failed .ds-state { color: var(--dr-bad); }
.ds-act { position: relative; display: flex; gap: 11px; padding: 12px 0 6px 4px; }
.ds-act:not(:last-child)::before { content: ''; position: absolute; top: 24px; left: 7px; bottom: -8px; width: 1px; background: var(--dr-line); }
.ds-act > i { position: relative; z-index: 1; width: 8px; height: 8px; flex: 0 0 8px; background: var(--dr-violet);
  border-radius: 50%; margin-top: 4px; }
.ds-act.current > i { background: var(--dr-accent); animation: dr-pulse 2s infinite; }
.ds-act b { display: block; font-size: 12.5px; } .ds-act small { display: block; font-size: 11.5px; color: var(--dr-muted); margin: 3px 0; }
.ds-act time { font-size: 10.5px; color: var(--dr-muted); }
.ds-progress { height: 6px; background: var(--dr-line); border-radius: 6px; margin-top: 16px; overflow: hidden; }
.ds-progress i { display: block; height: 100%; background: linear-gradient(90deg, var(--dr-accent), var(--dr-violet)); border-radius: 6px; }
.ds-progress-cap { display: flex; justify-content: space-between; margin-top: 7px; font-size: 12px; color: var(--dr-muted); }
.ds-meta { display: flex; flex-wrap: wrap; align-items: center; row-gap: 4px; color: var(--dr-muted); font-size: 13px; line-height: 1.6; }
.ds-meta span + span::before { content: "·"; margin: 0 10px; color: var(--dr-muted); }
.ds-live { font-size: 11px; color: var(--dr-accent-text); background: var(--dr-accent-soft); border-radius: 999px;
  padding: 7px 12px; font-weight: 700; display: inline-flex; align-items: center; gap: 7px; }
.ds-live i { width: 7px; height: 7px; border-radius: 50%; background: var(--dr-accent); display: inline-block; animation: dr-pulse 2s infinite; }
.ds-finding { display: flex; gap: 12px; border-top: 1px solid var(--dr-line); padding: 12px 0; }
.ds-finding > span { font: 600 12px Sora, sans-serif; color: var(--dr-accent-text); }
.ds-finding p { font-size: 14px; line-height: 1.65; color: var(--dr-soft); margin: 0; } .ds-finding b { color: var(--dr-ink); }
.ds-stat { display: flex; justify-content: space-between; font-size: 13.5px; color: var(--dr-muted); padding: 6px 0; }
.ds-stat b { color: var(--dr-ink); } .ds-stat .ok { color: var(--dr-ok); } .ds-stat .warn { color: var(--dr-warn); }
.ds-pills { display: flex; flex-wrap: wrap; gap: 6px; padding-bottom: 4px; }
.ds-pills span { background: var(--dr-glass-strong); border: 1px solid var(--dr-line); color: var(--dr-soft); padding: 5px 10px;
  border-radius: 999px; font-size: 12px; }
.ds-meter { height: 6px; background: var(--dr-line); border-radius: 6px; margin: 16px 0 7px; }
.ds-meter i { display: block; height: 100%; background: var(--dr-ok); border-radius: 6px; }
.ds-num { display: block; font: 700 26px Sora, sans-serif; color: var(--dr-line); margin-bottom: 8px; }
.ds-row-title b { display: block; font-size: 14px; } .ds-row-title small { display: block; font-size: 12px; color: var(--dr-muted); margin-top: 3px; }
.ds-cell { font-size: 13px; color: var(--dr-soft); padding-top: 6px; }
.ds-colhead { font: 500 10.5px 'JetBrains Mono', monospace; letter-spacing: .08em; color: var(--dr-muted); padding: 4px 0 0 0; }
[data-testid="stMetricLabel"] p { font-size: 11px !important; letter-spacing: .06em; text-transform: uppercase; color: var(--dr-muted); }
[data-testid="stExpander"] details { border-radius: 16px; background: var(--dr-glass); backdrop-filter: var(--dr-blur); }
[data-testid="stExpander"] summary p { font-weight: 600; }
[data-testid="stPopoverBody"] { backdrop-filter: var(--dr-blur); }
hr { border-color: var(--dr-line) !important; }
</style>
"""

ORIGIN_CLASS = {Origin.DEMO: "dr-demo", Origin.LIVE: "dr-live", Origin.CACHE: "dr-cache", Origin.UPLOAD: "dr-upload"}
AGREEMENT_COLORS = {"strong": "green", "moderate": "blue", "single_source": "gray", "mixed": "orange", "contested": "red"}
QUALITY_COLORS = {"high": "green", "medium": "blue", "low": "orange", "unknown": "gray"}
STATUS_ICON = {"pending": "○", "running": "◐", "completed": "●", "failed": "✕", "skipped": "–"}
AGENT_TITLES = {"planner": "Research Planner", "retriever": "Contextual Retriever", "source_evaluator": "Source Evaluation",
                "critical_analyst": "Critical Analysis", "gap_analyst": "Gap & Follow-up",
                "insight_generator": "Insight Generation", "data_visualizer": "Data Visualization", "report_builder": "Report Builder",
                "orchestrator": "Orchestrator"}
# icon, colour class, what the agent does (shown while it is queued)
AGENT_INFO = {
    "planner": ("⌕", "ds-purple", "Splits the question into research threads and queries"),
    "retriever": ("◎", "ds-blue", "Searches web, academic and document sources"),
    "source_evaluator": ("▤", "ds-indigo", "Rates relevance, credibility and freshness"),
    "critical_analyst": ("⌁", "ds-green", "Links claims to passages and finds contradictions"),
    "gap_analyst": ("◈", "ds-gold", "Finds gaps and decides whether to dig deeper"),
    "insight_generator": ("✧", "ds-pink", "Finds patterns, implications and hypotheses"),
    "data_visualizer": ("▥", "ds-blue", "Charts extracted values and links every point to its source"),
    "report_builder": ("✓", "ds-teal", "Writes the cited report and validates citations"),
}
STATUS_TAG = {"completed": ("Complete", "ds-complete"), "running": ("In progress", "ds-running"),
              "queued": ("Queued", "ds-running"), "failed": ("Failed", "ds-failed"),
              "cancelled": ("Stopped", "ds-draft")}
DEPTH_LABELS = {"quick": "Quick", "standard": "Standard", "deep": "Deep"}


def inject_css() -> None:
    """The glass skin (CSS was requested for the glassmorphic design; colours themselves come from config.toml)."""
    st.html(CSS.replace("</style>", ICON_CSS + "\n" + BRAND_CSS + "\n</style>"))


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def eyebrow(text: str, center: bool = False) -> str:
    style = ' style="justify-content:center"' if center else ""
    return f'<div class="ds-eyebrow"{style}><i></i>{escape(text)}</div>'


def page_header(eyebrow_text: str, title: str, subtitle: str = "", accent: str = "") -> None:
    """Wireframe page heading: dotted eyebrow, bold title with an optional violet accent, muted lead."""
    accent_html = f" <em>{escape(accent)}</em>" if accent else ""
    lead = f'<p class="ds-sub">{escape(subtitle)}</p>' if subtitle else ""
    html(f'<div class="ds-heading">{eyebrow(eyebrow_text)}<div class="ds-title">{escape(title)}{accent_html}</div>{lead}</div>')


def card(key: str, flat: bool = False):
    """A white rounded card (a keyed container styled via CSS)."""
    return st.container(key=f"card-{'flat-' if flat else ''}{key}")


def tag(text: str, css: str) -> str:
    return f'<span class="ds-tag {css}">{escape(text)}</span>'


def status_tag(status: str) -> str:
    label, css = STATUS_TAG.get(status, (status, "ds-draft"))
    return tag(label, css)


def mode_tag(mode: str) -> str:
    return tag("Demo", "ds-demo-tag") if mode == "demo" else tag("Live", "ds-live-tag")


def ago(when: datetime | None) -> str:
    if when is None:
        return ""
    seconds = (datetime.now() - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if seconds < 172800:
        return "yesterday"
    return f"{when:%b %d, %Y}"


def badge(text: str, css: str) -> str:
    return f'<span class="dr-badge {css}">{escape(text)}</span>'


def origin_badge(origin: Origin) -> str:
    return badge(ORIGIN_LABELS[origin].upper(), ORIGIN_CLASS[origin])


def label_badge(label: str) -> str:
    return badge(label.upper(), f"dr-{label}")


def demo_banner(mode: str) -> None:
    if mode == "demo":
        html('<div class="ds-banner">DEMO PREVIEW <span>Synthetic sample dataset: every source is fictional. Use it to '
             'explore the workflow, not as evidence. Sign in and switch to Live research for real sources.</span></div>')


def source_header(source: Source, assessment: SourceAssessment | None, number: int | None = None) -> str:
    who = source.publisher or ", ".join(source.authors[:2]) or "Unknown publisher"
    when = source.published_date.strftime("%d %b %Y") if source.published_date else "date unknown"
    prefix = f"[{number}] " if number else ""
    quality = assessment.evidence_quality.value if assessment else "unknown"
    return f"{prefix}{source.title} · {who} · {when} · quality {quality}"


def link_for(source: Source):
    if not source.url:
        return None
    label = "Open source (synthetic placeholder URL)" if source.is_synthetic else "Open original source"
    return label, source.url


def flags_html(assessment: SourceAssessment | None) -> str:
    if not assessment or not assessment.flags:
        return ""
    return " ".join(badge(f.replace("_", " "), "dr-flag") for f in assessment.flags)
