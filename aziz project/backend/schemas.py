"""Typed API contract for EcoBiz Copilot (shared with the OpenAPI docs)."""

from pydantic import BaseModel, Field


class DayRecord(BaseModel):
    """One day of consumption as returned to the frontend."""

    date: str
    consumption_kwh: float
    is_workday: bool
    is_anomaly: bool
    excess_kwh: float


class Summary(BaseModel):
    total_excess_kwh: float
    savings_kzt: float
    co2_saved_kg: float
    anomaly_days: int = Field(ge=0)
    baseline_kwh: float = Field(ge=0)
    multiplier: float
    tariff_kzt_per_kwh: float = Field(ge=0)
    co2_factor: float
    days_analyzed: int = Field(ge=1)


class AnalyzeResponse(BaseModel):
    summary: Summary
    series: list[DayRecord]
    source: str
    worst_day: str | None = None
    first_anomaly: str | None = None
    last_anomaly: str | None = None


class InsightRequest(BaseModel):
    """The frontend posts back the analysis it received from /api/analyze."""

    source: str
    summary: Summary
    anomalies: list[DayRecord] = Field(default_factory=list)


class InsightResponse(BaseModel):
    insight: str
    model: str
