"""Data Visualization Agent: extracts verified numbers and chooses safe deterministic charts."""
from src.agents.base import Agent, RunContext
from src.analysis.visualization import extract_charts
from src.orchestration.state import ResearchState


class DataVisualizationAgent(Agent):
    name = "data_visualizer"
    title = "Data Visualization"
    responsibility = "Charts extracted numeric evidence with source and passage provenance; refuses ambiguous data."

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        charts, notes = extract_charts(state.get("sources", []), state.get("passages", []), state.get("claims", []))
        ctx.event(self.name, "completed", f"Created {len(charts)} evidence-backed chart(s).", charts=len(charts))
        return {"charts": charts, "visualization_notes": notes}
