"""Evidence-backed visualizations, numeric tables and provenance drill-down."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.models.evidence import ORIGIN_LABELS


def safe_csv(data):
    return data.map(lambda x: "\'" + x if isinstance(x, str) and x.lstrip().startswith(("=", "+", "-", "@")) else x).to_csv(index=False)


def render(bundle):
    st.subheader("Charts from extracted evidence")
    st.caption("Every plotted value links to a stored source passage. Different sources and units are kept separate.")
    if not bundle.charts:
        st.info("No unambiguous numeric evidence was available for charts. Try including a CSV with a year or category column and numeric metrics.")
    for chart in bundle.charts:
        with st.container(border=True):
            st.markdown(f"**{chart.title}**")
            if chart.synthetic:
                st.warning("SYNTHETIC DEMO DATA — these values are fictional.")
            if chart.kind == "indicator":
                st.metric("Reported value", f"{chart.points[0].value:g}{chart.unit}")
                st.write(chart.metric)
            else:
                labels, values = [p.label for p in chart.points], [p.value for p in chart.points]
                fig = go.Figure(go.Scatter(x=labels, y=values, mode="lines+markers") if chart.kind == "line"
                                else go.Bar(x=labels, y=values))
                fig.update_layout(height=340, margin=dict(l=20, r=20, t=20, b=40), yaxis_title=chart.unit,
                                  xaxis_type="category", colorway=["#7159dc"])
                st.plotly_chart(fig, width="stretch", key=chart.id)
            st.caption(chart.limitation)
            data = pd.DataFrame([{"Label": p.label, "Value": p.value, "Unit": chart.unit,
                "Source ID": p.source_id, "Passage ID": p.passage_id, "Location": p.location,
                "Quotation": p.quote} for p in chart.points])
            with st.expander("Underlying data and citations"):
                st.dataframe(data, hide_index=True, width="stretch")
                for sid in dict.fromkeys(p.source_id for p in chart.points):
                    source = bundle.source(sid)
                    if source:
                        st.write(f"Source: {source.title} · {ORIGIN_LABELS[source.origin]}")
                        if source.url and source.url.startswith(("https://", "http://")):
                            st.link_button("Open original source", source.url)
                st.download_button("Download chart data (CSV)", safe_csv(data),
                    file_name=f"{chart.id}.csv", mime="text/csv", key=f"data_{chart.id}")
    if bundle.visualization_notes:
        with st.expander("Extraction notes"):
            for note in bundle.visualization_notes:
                st.write(note)
