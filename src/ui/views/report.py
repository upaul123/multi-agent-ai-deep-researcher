"""Results tab: Full report, shown without citation markers, with the sources it drew on."""
from html import escape

import streamlit as st

from src.models.run import RunBundle
from src.reporting.render import section_text, section_title
from src.ui.components import card, html


def render(bundle: RunBundle) -> None:
    report = bundle.report
    with card("report"):
        html(f'<div class="ds-label">Full research report</div><div style="font:700 21px/1.4 Manrope,sans-serif;'
             f'margin:10px 0 4px">{escape(report.title)}</div>')
        st.caption(f"Generated {report.created_at:%d %b %Y %H:%M} · {report.generated_by} · "
                   f"{len(report.references)} sources")
        with st.expander("Contents"):
            st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(map(section_title, report.sections), start=1)))
        for section in report.sections:
            st.markdown(f"#### {section_title(section)}")
            if section.key == "references":
                for r in report.references:
                    line = f"- {r['label']}"
                    if r.get("url"):
                        line += f" · [{'link (synthetic)' if r['synthetic'] else 'link'}]({r['url']})"
                    tags = [r["origin_label"]] + ([] if r["full_text_reviewed"] else ["abstract/snippet only"])
                    st.markdown(line + f"  \n<span class='dr-muted'>{' · '.join(tags)}</span>", unsafe_allow_html=True)
            else:
                st.markdown(section_text(section))
