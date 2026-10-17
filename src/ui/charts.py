"""Plotly charts for the dashboard."""
from __future__ import annotations

from collections import Counter

import plotly.graph_objects as go

AGREEMENT_ORDER = ["strong", "moderate", "single_source", "mixed", "contested"]
# Wireframe palette (violet accent, green, blue, gold, pink, teal, neutral grey).
AGREEMENT_HEX = {"strong": "#27876e", "moderate": "#7159dc", "single_source": "#a2a4b1", "mixed": "#b78127",
                 "contested": "#c0392b"}
QUALITY_HEX = {"high": "#27876e", "medium": "#7159dc", "low": "#b78127", "unknown": "#a2a4b1"}
PALETTE = ["#7159dc", "#27876e", "#4181bf", "#b78127", "#c0568c", "#389592", "#a2a4b1"]


def _layout(fig: go.Figure, height: int = 300) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", legend=dict(orientation="h", y=-0.2),
                      font=dict(family="DM Sans, sans-serif", size=12, color="#1d2038"), colorway=PALETTE, bargap=0.45)
    fig.update_xaxes(gridcolor="rgba(127,127,127,.15)", zeroline=False, linecolor="rgba(127,127,127,.25)")
    fig.update_yaxes(gridcolor="rgba(127,127,127,.15)", zeroline=False, linecolor="rgba(127,127,127,.25)")
    return fig


def agreement_bar(rows) -> go.Figure:
    counts = Counter(r.agreement.value for r in rows)
    labels = [a for a in AGREEMENT_ORDER if counts.get(a)]
    fig = go.Figure(go.Bar(x=[l.replace("_", " ") for l in labels], y=[counts[l] for l in labels],
                           marker_color=[AGREEMENT_HEX[l] for l in labels]))
    fig.update_yaxes(title="evidence rows")
    return _layout(fig)


def source_types_pie(sources) -> go.Figure:
    counts = Counter(s.source_type.value.replace("_", " ") for s in sources)
    fig = go.Figure(go.Pie(labels=list(counts), values=list(counts.values()), hole=0.62, textinfo="value",
                           marker=dict(colors=PALETTE, line=dict(width=0)), sort=False))
    return _layout(fig)


def quality_relevance_scatter(sources, assessments) -> go.Figure:
    fig = go.Figure()
    for quality, colour in QUALITY_HEX.items():
        pts = [(assessments[s.id].relevance, (s.published_date.year if s.published_date else None), s.title)
               for s in sources if s.id in assessments and assessments[s.id].evidence_quality.value == quality]
        if pts:
            fig.add_scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers", name=f"{quality} quality",
                            marker=dict(size=10, color=colour, symbol="square", opacity=.85), text=[p[2] for p in pts],
                            hovertemplate="%{text}<br>relevance %{x:.2f}<br>%{y}<extra></extra>")
    fig.update_xaxes(title="relevance (heuristic)", range=[-0.05, 1.05])
    fig.update_yaxes(title="publication year")
    return _layout(fig, 340)


def flags_bar(assessments) -> go.Figure:
    counts = Counter(f.replace("_", " ") for a in assessments.values() for f in a.flags)
    fig = go.Figure(go.Bar(y=list(counts), x=list(counts.values()), orientation="h", marker_color="#dc2626"))
    fig.update_xaxes(title="sources")
    return _layout(fig, 260)
