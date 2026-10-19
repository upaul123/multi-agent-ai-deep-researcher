"""Brand visuals rendered as HTML: the hero, the agents team tiles and the LangGraph workflow graph.

Images are served by Streamlit static serving (`static/` → `app/static/`, enabled in .streamlit/config.toml).
All colours come from the CSS tokens in `components.CSS`, so every visual follows the active light or dark theme.
Animations stop for visitors who ask their system for reduced motion.
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

HERO_IMAGE = "app/static/hero.jpg"
LOGO_IMAGE = "app/static/logo.png"
BRAND_NAME = "DAMRU"  # shown next to the agent logo
BRAND_EXPANSION = "Deep Agent for Multimodal Research and Understanding"  # its hover text
HERO_ALT = "The Deep Research Agent robot in a lab coat, surrounded by small helper robots in a research lab"


def _icon(name: str, size: int = 20) -> str:
    """An icon drawn as a CSS mask (see ICON_CSS): st.html strips inline <svg>, and a mask keeps theme colours."""
    return f'<i class="dr-i dr-i-{name}" aria-hidden="true" style="width:{size}px;height:{size}px"></i>'


ICONS = {
    "planner": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "retriever": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "source_evaluator": '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "critical_analyst": '<path d="M12 3v18M7 21h10M5 7h14M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/>',
    "gap_analyst": '<path d="M16 3h5v5M8 21H3v-5M21 3l-7 7M3 21l7-7"/>',
    "insight_generator": '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.7 1 1.5 1 2.5h6c0-1 .2-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
    "data_visualizer": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "report_builder": '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    "team": '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M15 14.5c3 .2 6 2.2 6 5.5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "observability": '<path d="M22 12h-4l-3 8-6-16-3 8H2"/>',
    "cost": ('<circle cx="12" cy="12" r="9"/><path d="M14.8 9.2c-.6-.9-1.6-1.4-2.8-1.4-1.7 0-3 .9-3 2.1s1.3 1.8 3 2.1 '
             '3 .9 3 2.1-1.3 2.1-3 2.1c-1.2 0-2.3-.5-2.9-1.4M12 6v1.8M12 16.2V18"/>'),
}



def _icon_css() -> str:
    rules = [".dr-i { display: inline-block; flex: none; background: currentColor; }"]
    for name, paths in ICONS.items():
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' "
               f"stroke-linecap='round' stroke-linejoin='round'>{paths}</svg>").replace('"', "'")
        url = 'url("data:image/svg+xml,' + quote(svg, safe=" '=:/,.-") + '")'
        rules.append(f".dr-i-{name} {{ -webkit-mask: {url} center / contain no-repeat; mask: {url} center / contain no-repeat; }}")
    return "\n".join(rules)
ICON_CSS = _icon_css()

# (agent, name, a couple of words) in pipeline order
TEAM = [
    ("planner", "Research Planner", "Breaks the question down"),
    ("retriever", "Contextual Retriever", "Finds the sources"),
    ("source_evaluator", "Source Evaluator", "Weighs credibility"),
    ("critical_analyst", "Critical Analyst", "Tests every claim"),
    ("gap_analyst", "Gap & Follow-up", "Chases what's missing"),
    ("insight_generator", "Insight Generator", "Connects the dots"),
    ("data_visualizer", "Data Visualizer", "Charts the evidence"),
    ("report_builder", "Report Builder", "Writes the cited report"),
]
# Cross-cutting agents watch every step instead of taking a turn in the pipeline. They present capabilities the app
# already has: LangSmith tracing, the live event log and token accounting (services/observability.py,
# services/run_events.py, orchestration/budget.py), and the run budget guard, 24 h search cache and cost estimate.
CROSS_CUTTING = [
    ("observability", "Observability & Telemetry", "Traces every step, live",
     ["LangSmith traces", "Live events", "Token usage"], "traces · events · tokens"),
    ("cost", "Cost Optimizer", "Keeps spend in check",
     ["Budget guard", "Search cache", "Cost estimate"], "budget · cache · cost estimate"),
]


def public_hero_html() -> str:
    """The lab-scene image in a glass frame with two floating facts."""
    return (f'<div class="dr-hero"><div class="dr-hero-frame"><img src="{HERO_IMAGE}" alt="{HERO_ALT}"></div>'
            f'<div class="dr-float a">{_icon("team", 18)}10 AI agents</div>'
            f'<div class="dr-float b">{_icon("check", 18)}Every claim linked to its source</div></div>')


def landing_figure_html() -> str:
    return (f'<figure class="dr-figure"><img src="{HERO_IMAGE}" alt="{HERO_ALT}"><figcaption>'
            "<b>DR-01 and the team</b><span>10 agents · every claim cited</span></figcaption></figure>")


def brand_mark_html() -> str:
    """The DAMRU name; <abbr> gives the expansion as hover text and to screen readers."""
    return f'<abbr class="dr-wordmark" title="{escape(BRAND_EXPANSION)}">{escape(BRAND_NAME)}</abbr>'


def signin_brand_html(heading: str, subheading: str) -> str:
    return (f'<div class="dr-signin-brand"><div class="dr-brand-row"><img src="{LOGO_IMAGE}" alt="Deep Research Agent">'
            f"{brand_mark_html()}</div><h1>{escape(heading)}</h1><p>{escape(subheading)}</p></div>")


def _brand_css() -> str:
    """DAMRU beside the sidebar logo. st.logo cannot carry text, so the name and a hover tooltip are drawn with CSS
    on the logo's container (the first child of the sidebar header)."""
    logo = '[data-testid="stSidebarHeader"] > :first-child'  # a div, or the stLogoLink button when the logo links home
    # Logo and name are centred in the pane; the collapse button is taken out of the row so it does not offset them.
    return ('[data-testid="stSidebarHeader"] { position: relative; z-index: 1000; justify-content: center; }\n'
            '[data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"] { position: absolute; right: 0.5rem; '
            "top: 50%; transform: translateY(-50%); }\n"
            f'{logo} {{ position: relative; display: flex; align-items: center; justify-content: center; gap: 10px; '
            "margin: 0 auto; opacity: 1; }\n"
            f'{logo}::after {{ content: "{BRAND_NAME}"; font: 700 14px Sora, sans-serif; letter-spacing: .14em; '
            "color: var(--dr-ink); opacity: 1; }\n"
            f'{logo}:hover::before {{ content: "{BRAND_EXPANSION}"; position: absolute; left: 50%; top: calc(100% + 8px); '
            "transform: translateX(-50%); "
            "z-index: 1000; width: max-content; max-width: 230px; padding: 8px 11px; border-radius: 10px; "
            "background: var(--dr-node); border: 1px solid var(--dr-line); box-shadow: 0 10px 30px var(--dr-shadow); "
            "font: 500 12.5px/1.4 'DM Sans', sans-serif; letter-spacing: 0; color: var(--dr-ink); white-space: normal; }")


BRAND_CSS = _brand_css()


def agents_team_html() -> str:
    """Eight pipeline tiles (a highlight passes from tile to tile in order) and two always-on cross-cutting tiles
    (an amber light sweeps across them)."""
    tiles = []
    for i, (agent, name, words) in enumerate(TEAM):
        violet = " violet" if i % 2 else ""
        tiles.append(
            f'<article class="dr-tile{violet}"><div class="relay" aria-hidden="true" style="animation-delay:{i * 0.8:.1f}s"></div>'
            f'<div class="dr-tile-top"><span class="dr-badge-icon"><span class="ring" aria-hidden="true" '
            f'style="animation-duration:{7 + (i * 5) % 6}s"></span><span class="core">{_icon(agent, 18)}</span></span>'
            f'<span class="dr-tile-num">{i + 1:02d}</span></div><div><h3>{escape(name)}</h3><p>{escape(words)}</p></div></article>')
    cross = []
    for n, (agent, name, words, tags, _outputs) in enumerate(CROSS_CUTTING):
        chips = "".join(f'<span class="dr-ctag">{escape(tag)}</span>' for tag in tags)
        cross.append(
            f'<article class="dr-ctile"><div class="sweep" aria-hidden="true" style="animation-delay:{n * 1.6:.1f}s"></div>'
            f'<span class="dr-badge-icon"><span class="ring" aria-hidden="true"></span><span class="dr-on" aria-hidden="true">'
            f'</span><span class="core">{_icon(agent, 19)}</span></span><div class="dr-ctile-body"><div class="dr-ctile-name">'
            f'<h3>{escape(name)}</h3><span>always on</span></div><p>{escape(words)}</p><div class="dr-ctags">{chips}</div>'
            "</div></article>")
    return ('<section aria-labelledby="team-title"><div class="dr-section-head"><h2 id="team-title">Get to know your Agents '
            'team</h2><p>Eight specialists hand the work along, in parallel where they can; two more watch over every '
            f'step.</p></div><div class="dr-team">{"".join(tiles)}</div>'
            '<div class="dr-cross-head"><span>Cross-cutting · watching every step</span><i></i></div>'
            f'<div class="dr-cross">{"".join(cross)}</div></section>')


# ------------------------------------------------------------------ workflow graph
# Seven columns: planner, retriever, source evaluator, critical analyst, gap & follow-up, the parallel synthesis
# column (insight generator on the rail, data visualizer below it), report builder. Column centres are at
# (i + 0.5) / 7 of the width; the parallel branch splits at 5/7 and joins at 6/7.
_GRAPH_NODES = [
    ("planner", "Research<br>Planner", "subquestions", ""),
    ("retriever", "Contextual<br>Retriever", "sources", ""),
    ("source_evaluator", "Source<br>Evaluator", "credibility", ""),
    ("critical_analyst", "Critical<br>Analyst", "claims", ""),
    ("gap_analyst", "Gap &amp;<br>Follow-up", "route", "decision"),
    ("insight_generator", "Insight<br>Generator", "insights", ""),
    ("report_builder", "Report<br>Builder", "cited report", "final"),
]


def _node(agent: str, label: str, output: str, kind: str, delay: float) -> str:
    out_class = {"decision": " v", "final": " final"}.get(kind, "")
    return (f'<li class="dr-node"><span class="dr-node-icon {kind}"><span class="relay" aria-hidden="true" '
            f'style="animation-delay:{delay:.1f}s"></span>{_icon(agent)}</span><b>{label}</b>'
            f'<span class="dr-out{out_class}">{output}</span></li>')


def _lanes(title: str, labels: list[str]) -> str:
    lanes = "".join(f'<span class="dr-lane"><i aria-hidden="true" style="animation-delay:-{n * 0.3:.1f}s"></i>'
                    f"<span>{escape(label)}</span></span>" for n, label in enumerate(labels))
    return f'<span>{escape(title)}</span>{lanes}'


def _vrow(agent: str, name: str, note: str, note_class: str = "", kind: str = "") -> str:
    return (f'<div class="dr-vrow"><span class="dr-node-icon {kind}">{_icon(agent, 18)}</span><span><b>{name}</b>'
            f'<small class="{note_class}">{note}</small></span></div>')


def _cross_band() -> str:
    """The always-on band under the stages: a tap from every step and one flowing lane per cross-cutting agent."""
    taps = "".join(f'<div class="dr-abs dr-tap flow-yd" aria-hidden="true" style="left:calc({(i + 0.5) / 7 * 100:.2f}% - 1px)">'
                   "</div>" for i in range(7))
    lanes = "".join(
        f'<div class="dr-abs dr-clane" style="top:{388 + n * 34}px"><span class="dr-clane-name"><span class="dr-ccore">'
        f'{_icon(agent, 15)}</span>{escape(name)}</span><i class="flow-x" aria-hidden="true"></i><span class="dr-clane-out">'
        f"{escape(outputs)}</span></div>"
        for n, (agent, name, _words, _tags, outputs) in enumerate(CROSS_CUTTING))
    return (f'{taps}<div class="dr-abs dr-cband" aria-hidden="true"></div><div class="dr-abs dr-cband-title">'
            f"4 · Cross-cutting · always on</div>{lanes}")


def workflow_graph_html() -> str:
    """The LangGraph workflow: stages, a flowing evidence rail, in-step fan-out, the parallel synthesis branch and
    the bounded follow-up loop. A desktop layout and a stacked phone layout (switched by CSS)."""
    nodes = "".join(_node(agent, label, output, kind, i * 0.9)
                    for i, (agent, label, output, kind) in enumerate(_GRAPH_NODES))
    dots = 'class="dr-abs dr-dots {cls}" aria-hidden="true"'
    horizontal = f"""<div class="dr-wf-h">
<div class="dr-abs dr-grid7" aria-hidden="true" style="left:0;right:0;top:0;height:338px">
<div class="dr-band" style="grid-column:1/span 1;margin-right:5px"></div>
<div class="dr-band" style="grid-column:2/span 4;margin:0 5px"></div>
<div class="dr-band" style="grid-column:6/span 2;margin-left:5px"></div></div>
<div class="dr-abs dr-grid7" style="left:0;right:0;top:12px">
<span class="dr-band-title" style="grid-column:1/span 1">1 · Plan</span>
<span class="dr-band-title" style="grid-column:2/span 4">2 · Gather &amp; verify</span>
<span class="dr-band-title" style="grid-column:6/span 2">3 · Synthesise</span></div>
<div class="dr-abs" aria-hidden="true" style="left:7.14%;right:7.14%;top:64px;height:4px;border-radius:4px;background:var(--dr-line)"></div>
<div {dots.format(cls="flow-x")} style="left:7.14%;right:7.14%;top:64px;height:4px"></div>
<div class="dr-abs dr-glow" aria-hidden="true" style="top:58px;width:9%;height:16px"></div>
<div class="dr-abs" style="left:64.29%;width:14.29%;top:36px;display:flex;justify-content:center"><span class="dr-pill">synthesise →</span></div>
<div {dots.format(cls="col flow-yd")} style="left:calc(71.43% - 2px);top:66px;width:4px;height:132px"></div>
<div {dots.format(cls="flow-x")} style="left:71.43%;width:14.29%;top:194px;height:4px"></div>
<div {dots.format(cls="col flow-yu")} style="left:calc(85.71% - 2px);top:66px;width:4px;height:132px"></div>
<span class="dr-abs dr-joint" aria-hidden="true" style="left:calc(71.43% - 7px);top:59px"></span>
<span class="dr-abs dr-joint" aria-hidden="true" style="left:calc(85.71% - 7px);top:59px"></span>
<div class="dr-abs" style="left:71.43%;width:14.29%;top:300px;display:flex;justify-content:center"><span class="dr-pill a">∥ run side by side</span></div>
<div class="dr-abs dr-lanes" style="left:14.29%;width:14.29%;top:164px">{_lanes("∥ parallel searches", ["OpenAlex", "arXiv", "Tavily web"])}</div>
<div class="dr-abs dr-lanes" style="left:42.86%;width:14.29%;top:164px">{_lanes("∥ one per source", ["source 1", "source 2", "source n"])}</div>
<div {dots.format(cls="v col flow-yd")} style="left:calc(64.29% - 2px);top:154px;width:4px;height:166px"></div>
<div {dots.format(cls="v flow-xr")} style="left:7.5%;width:56.79%;top:316px;height:4px"></div>
<div {dots.format(cls="v col flow-yu")} style="left:7.5%;top:272px;width:4px;height:48px"></div>
<div {dots.format(cls="v flow-x")} style="left:7.5%;width:7.5%;top:272px;height:4px"></div>
<div class="dr-abs" aria-hidden="true" style="left:calc(15% - 2px);top:267px;width:0;height:0;border-top:7px solid transparent;border-bottom:7px solid transparent;border-left:10px solid var(--dr-violet)"></div>
<div class="dr-abs" style="left:21.43%;width:35.71%;top:306px;display:flex;justify-content:center"><span class="dr-pill v">↺ follow-up round · bounded by budget</span></div>
{_cross_band()}
<ol class="dr-abs dr-nodes dr-grid7" style="left:0;right:0;top:42px">{nodes}</ol>
<div class="dr-abs" style="left:71.43%;width:14.29%;top:172px">{_node("data_visualizer", "Data<br>Visualizer", "charts", "", 4.5).replace("<li", "<div").replace("</li>", "</div>")}</div>
</div>"""
    vertical = ('<div class="dr-wf-v"><div class="dr-abs dr-dots col flow-yd" aria-hidden="true" '
                'style="left:21px;top:40px;bottom:30px;width:4px"></div>'
                '<span class="dr-vband">1 · Plan</span>'
                + _vrow("planner", "Research Planner", "subquestions")
                + '<span class="dr-vband">2 · Gather &amp; verify</span>'
                + _vrow("retriever", "Contextual Retriever", "∥ searches OpenAlex, arXiv, Crossref and Tavily at once", "acc")
                + _vrow("source_evaluator", "Source Evaluator", "credibility")
                + _vrow("critical_analyst", "Critical Analyst", "∥ extracts claims from each source in parallel", "acc")
                + _vrow("gap_analyst", "Gap &amp; Follow-up", "↺ back to the Retriever while a gap is worth it", "vio",
                        "decision")
                + '<span class="dr-vband">3 · Synthesise</span><div class="dr-vpair">'
                + f'<div><span class="dr-node-icon">{_icon("insight_generator", 16)}</span>Insight Generator</div>'
                + f'<div><span class="dr-node-icon">{_icon("data_visualizer", 16)}</span>Data Visualizer</div>'
                + "<em>∥ run side by side</em></div>"
                + _vrow("report_builder", "Report Builder", "waits for both, then writes the cited report", "acc", "final")
                + '<span class="dr-vband amber">4 · Cross-cutting · always on</span><div class="dr-vcross">'
                + "".join(f'<div><span class="dr-ccore">{_icon(agent, 15)}</span><span><b>{escape(name)}</b>'
                          f"<small>{escape(words.lower())}</small></span></div>"
                          for agent, name, words, _tags, _outputs in CROSS_CUTTING)
                + "</div></div>")
    legend = ('<div class="dr-legend">'
              '<span><span class="dr-dots flow-x" style="width:26px;height:4px;background-size:9px 4px"></span>Evidence flow</span>'
              '<span><span style="display:flex;flex-direction:column;gap:3px"><span class="dr-dots flow-x" '
              'style="width:22px;height:3px;background-size:8px 3px"></span><span class="dr-dots flow-x" '
              'style="width:22px;height:3px;background-size:8px 3px"></span></span>Runs in parallel</span>'
              '<span><span class="dr-dots v flow-xr" style="width:26px;height:4px;background-size:9px 4px"></span>Follow-up loop</span>'
              '<span><span class="dr-diamond"></span>Decision</span>'
              '<span><span class="dr-cross-key"></span>Cross-cutting</span></div>')
    return (f'<section class="dr-wf" aria-labelledby="wf-title"><div class="dr-wf-head"><div><h2 id="wf-title">How an '
            "investigation flows</h2><p>The LangGraph workflow behind every run. Work fans out where it can, and "
            f"follow-up rounds repeat only while a gap is worth pursuing.</p></div>{legend}</div>{horizontal}{vertical}</section>")
