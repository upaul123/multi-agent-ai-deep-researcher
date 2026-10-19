"""Persistable, provenance-linked chart data; no generated executable plotting code."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class DataPoint(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    label: str
    value: float
    source_id: str
    passage_id: str
    location: str
    quote: str


class EvidenceChart(BaseModel):
    id: str
    title: str
    metric: str
    unit: str
    kind: Literal["bar", "line", "indicator"] = "bar"
    points: list[DataPoint] = Field(min_length=1, max_length=2000)
    synthetic: bool = False
    limitation: str = "Values are reported by the cited source; extraction does not verify source accuracy."

    @model_validator(mode="after")
    def comparable(self):
        if len({p.source_id for p in self.points}) != 1:
            raise ValueError("Charts must keep each source dataset separate.")
        if self.kind == "line" and len(self.points) < 2:
            raise ValueError("A trend requires at least two observations.")
        return self
