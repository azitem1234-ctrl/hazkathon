"""EcoBiz Copilot API.

Endpoints:
    POST /api/analyze   - analyze an uploaded CSV/XLSX (falls back to the
                          bundled sample when no file is given)
    GET  /api/health    - liveness check
    GET  /              - serve the frontend
"""

import io
import os
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from calendar_utils import add_workday_column, load_off_periods_from_json
from core import (
    BASELINE_MULTIPLIER,
    CO2_KG_PER_KWH,
    DEFAULT_CALENDAR_PATH,
    TARIFF_KZT_PER_KWH,
    calculate_impact,
    detect_anomalies,
    get_baseline,
)
from backend.schemas import AnalyzeResponse, DayRecord, InsightRequest, InsightResponse, Summary
from backend.ai import AIUnavailable, ask_gemini

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "data" / "sample_data.csv"
FRONTEND_DIR = ROOT / "frontend"
# is_workday is optional: when the upload omits it, we derive it from weekends
# plus the Kazakhstan public-holiday/school-break calendar (holidays_kz.json).
REQUIRED_COLUMNS = {"date", "consumption_kwh"}
ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xls")

app = FastAPI(title="EcoBiz Copilot", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _to_frame(raw: bytes | Path, filename: str) -> pd.DataFrame:
    """Read raw upload bytes (or a path) into a DataFrame."""
    handle = raw if isinstance(raw, Path) else io.BytesIO(raw)
    try:
        if filename.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(handle)
        return pd.read_csv(handle)
    except Exception as exc:
        raise HTTPException(400, f"Could not read '{filename}': {exc}") from exc


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Validate required columns and coerce types with clear error messages."""
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise HTTPException(
            422, f"Missing required columns: {', '.join(sorted(missing))}"
        )
    if df.empty:
        raise HTTPException(422, "The file contains no data rows.")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["consumption_kwh"] = pd.to_numeric(out["consumption_kwh"], errors="coerce")
    bad_dates = int(out["date"].isna().sum())
    bad_kwh = int(out["consumption_kwh"].isna().sum())
    if bad_dates or bad_kwh:
        raise HTTPException(
            422, f"Invalid values: {bad_dates} bad date(s), {bad_kwh} bad kWh value(s)."
        )

    if "is_workday" in out.columns:
        workday = pd.to_numeric(out["is_workday"], errors="coerce")
        if workday.isna().any():
            raise HTTPException(422, "'is_workday' must be 0 or 1 on every row.")
        out["is_workday"] = workday.astype(int)
    else:
        calendar_periods = load_off_periods_from_json(DEFAULT_CALENDAR_PATH)
        out = add_workday_column(out, extra_off_periods=calendar_periods)
    return out


def _analyze(df: pd.DataFrame, tariff: float) -> AnalyzeResponse:
    """Run the full pipeline and shape a JSON-friendly response."""
    flagged = detect_anomalies(df)
    impact = calculate_impact(flagged, tariff=tariff)

    series = [
        DayRecord(
            date=row.date.strftime("%Y-%m-%d"),
            consumption_kwh=round(float(row.consumption_kwh), 2),
            is_workday=bool(row.is_workday),
            is_anomaly=bool(row.is_anomaly),
            excess_kwh=round(float(row.excess_kwh), 2),
        )
        for row in flagged.itertuples(index=False)
    ]
    anomalies = [r for r in series if r.is_anomaly]
    worst = max(anomalies, key=lambda r: r.excess_kwh) if anomalies else None

    return AnalyzeResponse(
        summary=Summary(
            **impact,
            baseline_kwh=round(get_baseline(flagged), 2),
            multiplier=BASELINE_MULTIPLIER,
            tariff_kzt_per_kwh=tariff,
            co2_factor=CO2_KG_PER_KWH,
            days_analyzed=int(len(flagged)),
            baseline_reliable=bool(flagged.attrs["baseline_reliable"]),
            off_day_samples=int(flagged.attrs["off_day_samples"]),
        ),
        series=series,
        source="",
        worst_day=worst and worst.date,
        first_anomaly=anomalies[0].date if anomalies else None,
        last_anomaly=anomalies[-1].date if anomalies else None,
    )


@app.post("/api/analyze")
async def analyze(file: UploadFile | None = None, tariff: float = TARIFF_KZT_PER_KWH):
    if tariff < 0:
        raise HTTPException(422, "Tariff cannot be negative.")

    if file is None or not file.filename:
        source: bytes | Path = SAMPLE_CSV
        label = SAMPLE_CSV.name
    else:
        if not file.filename.lower().endswith(ALLOWED_SUFFIXES):
            raise HTTPException(
                415, f"Unsupported file type; use {', '.join(ALLOWED_SUFFIXES)}"
            )
        source = await file.read()
        label = file.filename

    try:
        df = _clean(_to_frame(source, label))
        result = _analyze(df, tariff)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Analysis failed for '{label}': {exc}") from exc

    result.source = label
    return result


def _fallback_insight(req: InsightRequest) -> str:
    """Offline recommendation used when Gemini is unreachable or unconfigured.

    The demo must survive a venue with no Wi-Fi or an expired API key, so this
    reuses the same four-section structure as the Gemini prompt but fills it
    with a templated explanation instead of a model call. Every figure comes
    straight from the verified analysis, never invented.
    """
    s = req.summary
    dates = ", ".join(a.date for a in sorted(req.anomalies, key=lambda x: x.excess_kwh, reverse=True)[:5])
    reliability_note = (
        ""
        if s.baseline_reliable
        else (
            f"\n\n*Note: the baseline rests on only {s.off_day_samples} non-working day(s) "
            "in this dataset — collect a longer history before treating it as confirmed.*"
        )
    )
    return (
        f"## Summary\n"
        f"Over {s.days_analyzed} days, {s.anomaly_days} closed day(s) ran as if the building "
        f"were occupied, wasting {s.total_excess_kwh} kWh (~{s.savings_kzt:.0f} KZT at "
        f"{s.tariff_kzt_per_kwh} KZT/kWh, {s.co2_saved_kg} kg CO2)."
        + (f" Flagged dates: {dates}." if dates else "")
        + f"{reliability_note}\n\n"
        "## What likely happened\n"
        "1. **HVAC/heating left on a standard schedule** — the most common cause when "
        "consumption on a closed day stays close to the workday level instead of dropping "
        "toward the baseline.\n"
        "2. **Lighting or ventilation timers not updated for the closed period** — timers "
        "set for term time keep running unchanged through holidays and weekends.\n"
        "3. **Equipment/servers left in active mode** — plug loads that are never switched "
        "to standby will show up as a flat, unexplained excess.\n\n"
        "## Corrective actions\n"
        "1. Have the caretaker/BMS technician check the heating and ventilation schedule "
        "for the flagged dates and switch it to a low-power holiday mode.\n"
        "2. Walk the building on the next closed day to confirm which systems are still "
        "running at full power.\n"
        "3. If a BMS exists, add explicit calendar overrides for weekends and school "
        "breaks instead of relying on the default weekly schedule.\n\n"
        "## Prevention & monitoring\n"
        "Add the closed-day baseline as an alarm threshold, and review this report after "
        "every holiday period so a missed override is caught within days, not months.\n\n"
        "*(Offline fallback — Gemini was unavailable, so this recommendation was generated "
        "locally from the verified numbers above, not by the AI model.)*"
    )


@app.post("/api/insight", response_model=InsightResponse)
async def insight(req: InsightRequest):
    """Turn the current analysis into concrete recommendations via Gemini.

    Falls back to a locally templated recommendation (still built from the
    verified numbers, never invented) when Gemini has no key, no network, or
    errors out — so a live demo never breaks because of connectivity.
    """
    s = req.summary
    lines = [
        f"Dataset: {req.source} ({s.days_analyzed} days of daily electricity data for a building).",
        f"Closed-building baseline: {s.baseline_kwh} kWh; anomaly threshold: baseline x {s.multiplier}.",
        (
            f"Found {s.anomaly_days} closed day(s) running as if occupied, wasting "
            f"{s.total_excess_kwh} kWh total (~{s.savings_kzt:.0f} KZT at {s.tariff_kzt_per_kwh} KZT/kWh, "
            f"{s.co2_saved_kg} kg CO2)."
        ),
    ]
    if req.anomalies:
        top = ", ".join(
            f"{a.date} (+{a.excess_kwh} kWh over {a.consumption_kwh} kWh)"
            for a in sorted(req.anomalies, key=lambda x: x.excess_kwh, reverse=True)[:5]
        )
        lines.append(f"Flagged dates (worst first): {top}.")
    else:
        lines.append("No anomalies were detected in this dataset.")

    prompt = "\n".join(lines) + (
        "\n\nYou are a senior building energy-efficiency consultant writing directly for the "
        "facility manager. Write a DETAILED action plan in Markdown with exactly these sections:"
        "\n\n## Summary"
        "\nTwo or three sentences quantifying the problem using the exact kWh, KZT and CO₂ "
        "figures and the flagged dates above."
        "\n\n## What likely happened"
        "\nRank the 2-4 most probable root causes (HVAC schedules, lighting timers, plug loads / "
        "equipment left on, IT servers, security systems, holiday overrides missing). For each: "
        "explain WHY you suspect it, comparing the flagged consumption against the baseline and "
        "typical workday levels in the data."
        "\n\n## Corrective actions"
        "\nNumbered, concrete steps. For each step say what exactly to inspect or change, who "
        "should do it (BMS technician, caretaker, IT, management) and estimate the expected "
        "recovery in kWh/day and KZT/year where possible."
        "\n\n## Prevention & monitoring"
        "\nSpecific controls: BMS calendar overrides for holidays/breaks, sub-metering, alarm "
        "thresholds based on the closed-day baseline, and how often someone should review the data."
        "\n\nRules: use only the dates/figures provided; quantify every claim; no generic filler, "
        "no greetings, no closing pleasantries."
    )

    try:
        text = ask_gemini(prompt)
        model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    except AIUnavailable:
        text = _fallback_insight(req)
        model = "offline-fallback"
    return InsightResponse(insight=text, model=model)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
