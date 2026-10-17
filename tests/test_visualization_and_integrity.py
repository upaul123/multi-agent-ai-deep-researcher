"""Numeric evidence, persistence and conservative claim validation."""
import pytest

from src.analysis.claim_validation import paraphrase_issues
from src.analysis.visualization import extract_charts
from src.ingestion.extract import extract_csv, ExtractionError
from src.models.evidence import Source, Origin, Passage, Claim, ClaimType
from src.models.research import ResearchRequest
from src.search.local import UploadedDocumentProvider
from src.search.registry import ProviderSet
from src.services.research_service import run_research
from tests.conftest import TODAY
from tests.test_multiuser import any_repo


def dataset(csv=b"year,revenue (USD)\n2024,10\n2025,15\n"):
    doc = extract_csv(csv, "revenue.csv")
    source = Source(id="src_csv", title=doc.title, origin=Origin.UPLOAD, retrieval_method="upload",
                    metadata={"kind": "csv", "table_columns": doc.metadata["columns"],
                              "table_records": doc.metadata["records"]})
    passages = [Passage(id=f"passage_{i}", source_id=source.id, text=text, location=loc)
                for i, (loc, text) in enumerate(doc.sections)]
    return source, passages, doc


def test_csv_values_are_exact_and_link_to_passages():
    source, passages, _ = dataset()
    charts, _ = extract_charts([source], passages, [])
    assert len(charts) == 1 and charts[0].kind == "line" and charts[0].unit == "usd"
    assert [(p.label, p.value) for p in charts[0].points] == [("2024", 10), ("2025", 15)]
    assert all(p.quote in next(x.text for x in passages if x.id == p.passage_id) for p in charts[0].points)


@pytest.mark.parametrize("csv", [b"year,value\n2024,5 USD\n2025,5 INR\n", b"year,value\n2024,5\n2024,6\n",
                                 b"year,value\n2024,Nan\n2025,Inf\n", b"year,share (%)\n2024,101\n2025,50\n"])
def test_ambiguous_values_are_not_charted(csv):
    source, passages, _ = dataset(csv)
    charts, notes = extract_charts([source], passages, [])
    assert not charts and notes


def test_missing_cell_is_omitted_not_zero_filled():
    source, passages, _ = dataset(b"year,value\n2023,\n2024,2\n2025,3\n")
    charts, _ = extract_charts([source], passages, [])
    assert [p.value for p in charts[0].points] == [2, 3]


def test_missing_provenance_rejects_chart():
    source, passages, _ = dataset()
    charts, _ = extract_charts([source], [], [])
    assert not charts


@pytest.mark.parametrize("csv", [b"year,year\n2024,5\n", b"year,value\n2024,5,6\n"])
def test_malformed_csv_rejected(csv):
    with pytest.raises(ExtractionError):
        extract_csv(csv, "bad.csv")


@pytest.mark.parametrize("quote,claim,issue", [
 ("Adoption could reach 40% by 2030.", "Adoption has reached 40%.", "forecast"),
 ("Adoption is 40% in 2024.", "Adoption is 70% in 2024.", "number"),
 ("Revenue is 20 million USD.", "Revenue is 20 billion USD.", "unit"),
 ("The study did not show an improvement.", "The study showed an improvement.", "negation"),
 ("Revenue may increase.", "Revenue increases.", "uncertainty"),
])
def test_concrete_claim_distortions_are_detected(quote, claim, issue):
    assert any(issue in x for x in paraphrase_issues(quote, claim))


def test_prose_statistic_keeps_context_and_synthetic_label():
    quote = "The survey reports that 38% of enterprise teams use agents in production."
    source = Source(id="src_demo", title="Survey", origin=Origin.DEMO, retrieval_method="demo")
    passage = Passage(id="p", source_id=source.id, text=quote, location="paragraph 1")
    claim = Claim(id="c", text=quote, quote=quote, claim_type=ClaimType.STATISTIC, source_id=source.id, passage_id="p")
    charts, _ = extract_charts([source], [passage], [claim])
    assert charts[0].kind == "indicator" and charts[0].synthetic
    assert charts[0].points[0].value == 38 and charts[0].metric == quote
    forecast = claim.model_copy(update={"claim_type": ClaimType.FORECAST})
    assert not extract_charts([source], [passage], [forecast])[0]


def test_csv_research_persists_charts_and_checks_owner(any_repo, settings):
    source, passages, doc = dataset()
    doc_id, _ = any_repo.save_document("revenue.csv", doc.title, doc.kind, b"revenue-test", doc.sections,
                                      doc.warnings, owner_id="alice", metadata=doc.metadata)
    documents = any_repo.get_documents([doc_id], "alice")
    request = ResearchRequest(question="How has revenue changed over the last two years?", mode="live", use_llm=False,
                              source_preferences=["uploaded"], document_ids=[doc_id])
    state = run_research(request, settings, repo=any_repo, owner_id="alice", today=TODAY,
                         providers=ProviderSet(providers=[UploadedDocumentProvider(documents)]))
    assert state["charts"] and state["statuses"]["data_visualizer"].status == "completed"
    stored = any_repo.load_run(state["run_id"], "alice")
    assert stored.charts == state["charts"] and stored.report.section("visualizations")
    assert any_repo.load_run(state["run_id"], "bob") is None
    assert any_repo.delete_run(state["run_id"], "alice")
    with any_repo.backend.session() as db:
        assert not db.rows("SELECT * FROM charts WHERE run_id = ?", (state["run_id"],))


def test_chart_view_renders_plot_values_and_download():
    from streamlit.testing.v1 import AppTest
    script = """
from datetime import datetime
from tests.test_visualization_and_integrity import dataset
from src.analysis.visualization import extract_charts
from src.models.run import RunBundle, RunSummary
from src.models.research import ResearchRequest
from src.ui.views.visualizations import render
source, passages, _ = dataset()
charts, notes = extract_charts([source], passages, [])
bundle = RunBundle(summary=RunSummary(id='test', question='Revenue trend', status='completed', mode='live', created_at=datetime.now()), request=ResearchRequest(question='How has revenue changed over time?', mode='live'), sources=[source], passages=passages, charts=charts, visualization_notes=notes)
render(bundle)
"""
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert len(at.get("plotly_chart")) == 1
    assert at.dataframe[0].value["Value"].tolist() == [10.0, 15.0]
    assert len(at.get("download_button")) == 1


def test_csv_download_escapes_formula_labels():
    import pandas as pd
    from src.ui.views.visualizations import safe_csv
    output = safe_csv(pd.DataFrame({"Label": ["=SUM(1)", " -command", "plain"], "Value": [-1, 2, 3]}))
    assert "'=SUM(1)" in output and "' -command" in output
    assert "plain,3" in output and ",-1" in output


def test_claim_validator_preserves_source_forecast_and_rejects_changed_value():
    from src.analysis.claims import ClaimDraft, ClaimDraftList, SubquestionMatcher, validate_llm_claims
    from src.agents.planner import PlannerAgent
    quote = "The survey estimates that adoption could reach 40% by 2030."
    source = Source(id="s", title="Survey", origin=Origin.UPLOAD, retrieval_method="upload")
    passage = Passage(id="p", source_id="s", text=quote, location="paragraph 1")
    matcher = SubquestionMatcher(PlannerAgent().heuristic_plan(ResearchRequest(question="How will adoption change?")))
    good = ClaimDraft(quote=quote.lower(), claim=quote, claim_type="fact", sceptical=True)
    bad = ClaimDraft(quote=quote, claim="Adoption has reached 70%.", claim_type="fact")
    claims, rejected = validate_llm_claims(ClaimDraftList(claims=[good, bad]), source, [passage], matcher)
    assert rejected == 1 and len(claims) == 1
    assert claims[0].text == quote and claims[0].quote == quote
    assert claims[0].claim_type == ClaimType.FORECAST
