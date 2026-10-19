"""Results tab: Ask follow-up questions answered from this run's evidence."""
import streamlit as st

from src.agents.qa import answer_question
from src.models.run import RunBundle
from src.retrieval.vector import open_run_vectors
from src.services.llm import LLMClient


def render(bundle: RunBundle, repo, settings, user) -> None:
    st.caption("Answers come from this run's evidence (claims, evidence matrix and source passages), not just the report.")
    history = repo.list_qa(bundle.summary.id, user.id)
    for item in history:
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant", avatar=":material/science:"):
            answer = item["answer"]
            st.markdown(answer["answer"])
            st.caption(f"Basis: {answer['basis']} · {answer['method']}")
    llm = LLMClient(settings) if settings.llm_configured else None
    use_llm = st.toggle("Use LLM for answers", value=bool(llm) and bundle.summary.mode == "live",
                        disabled=llm is None or user.is_guest,
                        help="Answers are always grounded in this run's evidence; citations are validated.")
    question = st.chat_input("e.g. Which sources disagree about adoption rates, and why?")
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/science:"):
        with st.spinner("Retrieving evidence…"):
            vectors = open_run_vectors(settings, bundle.summary.mode, bundle.summary.id, [])  # None: keyword search
            answer = answer_question(bundle, question, llm=llm, use_llm=use_llm and not user.is_guest, vectors=vectors)
        st.markdown(answer.answer)
        if answer.conflicts:
            st.markdown("**Conflicting evidence:**\n" + "\n".join(f"- {c}" for c in answer.conflicts))
        if answer.gaps:
            st.markdown("**Missing evidence:**\n" + "\n".join(f"- {g}" for g in answer.gaps))
        if answer.general_knowledge_note:
            st.info(f"General knowledge (not from the research corpus): {answer.general_knowledge_note}")
        with st.expander(f"Sources ({len(answer.citations)}) and how this answer was reached"):
            st.caption(answer.how_reached)
            for c in answer.citations:
                st.markdown(f"**[{c['n']}]** {c['label']} · {c['origin']}" + (f" · {c['location']}" if c["location"] else "")
                            + (f"\n\n> {c['passage']}" if c["passage"] else ""))
        st.caption(f"Basis: {answer.basis} · {answer.method}")
    repo.save_qa(bundle.summary.id, question, answer.model_dump(), user.id)
    repo.log_activity(user.id, "qa", bundle.summary.id, {"question": question[:200]})
