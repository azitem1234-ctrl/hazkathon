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
from starlette.types import Scope

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
# Real-world exports label the reading column differently; map the common
# aliases to our canonical name before validating — the numbers themselves
# are never touched, only the header.
CONSUMPTION_KWH_ALIASES = {"kwh", "consumption", "usage_kwh", "energy_kwh", "kwh_consumed"}

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
        raise HTTPException(400, f"Не удалось прочитать «{filename}»: {exc}") from exc


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Validate required columns and coerce types with clear error messages."""
    if "consumption_kwh" not in df.columns:
        for col in df.columns:
            if col.strip().lower() in CONSUMPTION_KWH_ALIASES:
                df = df.rename(columns={col: "consumption_kwh"})
                break

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise HTTPException(
            422, f"Отсутствуют обязательные колонки: {', '.join(sorted(missing))}"
        )
    if df.empty:
        raise HTTPException(422, "Файл не содержит строк с данными.")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["consumption_kwh"] = pd.to_numeric(out["consumption_kwh"], errors="coerce")
    bad_dates = int(out["date"].isna().sum())
    bad_kwh = int(out["consumption_kwh"].isna().sum())
    if bad_dates or bad_kwh:
        raise HTTPException(
            422,
            f"Некорректные значения: {bad_dates} неверных дат(ы), "
            f"{bad_kwh} неверных значений кВт·ч.",
        )

    if "is_workday" in out.columns:
        workday = pd.to_numeric(out["is_workday"], errors="coerce")
        if workday.isna().any():
            raise HTTPException(422, "Колонка 'is_workday' должна содержать только 0 или 1 в каждой строке.")
        out["is_workday"] = workday.astype(int)
    else:
        calendar_periods = load_off_periods_from_json(DEFAULT_CALENDAR_PATH)
        out = add_workday_column(out, extra_off_periods=calendar_periods)
    return out


def _analyze(df: pd.DataFrame, tariff: float, multiplier: float) -> AnalyzeResponse:
    """Run the full pipeline and shape a JSON-friendly response."""
    flagged = detect_anomalies(df, multiplier=multiplier)
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
            multiplier=multiplier,
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
async def analyze(
    file: UploadFile | None = None,
    tariff: float = TARIFF_KZT_PER_KWH,
    multiplier: float = BASELINE_MULTIPLIER,
):
    if tariff < 0:
        raise HTTPException(422, "Тариф не может быть отрицательным.")
    if multiplier <= 0:
        raise HTTPException(422, "Множитель порога аномалии должен быть положительным.")

    if file is None or not file.filename:
        source: bytes | Path = SAMPLE_CSV
        label = SAMPLE_CSV.name
    else:
        if not file.filename.lower().endswith(ALLOWED_SUFFIXES):
            raise HTTPException(
                415, f"Неподдерживаемый тип файла; используйте {', '.join(ALLOWED_SUFFIXES)}"
            )
        source = await file.read()
        label = file.filename

    try:
        df = _clean(_to_frame(source, label))
        result = _analyze(df, tariff, multiplier)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Не удалось проанализировать «{label}»: {exc}") from exc

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
            f"\n\n*Примечание: базовый уровень рассчитан всего по {s.off_day_samples} "
            "нерабочему(им) дню(дням) в этом наборе данных — соберите более длинную историю, "
            "прежде чем считать его подтверждённым.*"
        )
    )
    return (
        f"## Сводка\n"
        f"За {s.days_analyzed} дн. в {s.anomaly_days} нерабочий(их) день(дней) здание "
        f"потребляло энергию так, будто было занято людьми: перерасход составил "
        f"{s.total_excess_kwh} кВт·ч (~{s.savings_kzt:.0f} тенге при тарифе "
        f"{s.tariff_kzt_per_kwh} тенге/кВт·ч, {s.co2_saved_kg} кг CO₂)."
        + (f" Аномальные даты: {dates}." if dates else "")
        + f"{reliability_note}\n\n"
        "## Что вероятно произошло\n"
        "1. **Отопление или вентиляция остались в обычном рабочем режиме** — самая частая "
        "причина, когда потребление в нерабочий день остаётся близким к рабочему уровню "
        "вместо снижения до базового значения.\n"
        "2. **Таймеры освещения или вентиляции не обновлены под нерабочий период** — "
        "таймеры, настроенные на рабочий график, продолжают работать без изменений в "
        "выходные и каникулы.\n"
        "3. **Оборудование или серверы остались в активном режиме** — нагрузки, которые "
        "никогда не переводятся в режим ожидания, дают именно такой ровный необъяснимый "
        "перерасход.\n\n"
        "## Меры по исправлению\n"
        "1. Попросите завхоза или техника BMS проверить график отопления и вентиляции на "
        "отмеченные даты и переключить его в энергосберегающий режим на нерабочие дни.\n"
        "2. Пройдите по зданию в следующий нерабочий день, чтобы проверить, какие системы "
        "всё ещё работают на полную мощность.\n"
        "3. Если есть система BMS, добавьте явные календарные исключения для выходных и "
        "каникул вместо стандартного недельного расписания.\n\n"
        "## Профилактика и мониторинг\n"
        "Добавьте базовый уровень нерабочего дня как порог тревоги и проверяйте этот отчёт "
        "после каждого периода каникул, чтобы пропущенное исключение обнаруживалось за "
        "несколько дней, а не месяцев.\n\n"
        "*(Офлайн-режим — Gemini был недоступен, поэтому эта рекомендация сформирована "
        "локально на основе проверенных цифр выше, а не моделью ИИ.)*"
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
        f"Набор данных: {req.source} ({s.days_analyzed} дн. суточных данных электропотребления здания).",
        (
            f"Базовый уровень нерабочего дня: {s.baseline_kwh} кВт·ч; "
            f"порог аномалии: базовый уровень x {s.multiplier}."
        ),
        (
            f"Обнаружено {s.anomaly_days} нерабочих день(дней), когда здание потребляло "
            f"энергию как занятое: суммарный перерасход {s.total_excess_kwh} кВт·ч "
            f"(~{s.savings_kzt:.0f} тенге при тарифе {s.tariff_kzt_per_kwh} тенге/кВт·ч, "
            f"{s.co2_saved_kg} кг CO₂)."
        ),
    ]
    if req.anomalies:
        top = ", ".join(
            f"{a.date} (+{a.excess_kwh} кВт·ч сверх {a.consumption_kwh} кВт·ч)"
            for a in sorted(req.anomalies, key=lambda x: x.excess_kwh, reverse=True)[:5]
        )
        lines.append(f"Отмеченные даты (от самой затратной): {top}.")
    else:
        lines.append("В этом наборе данных аномалий не обнаружено.")

    prompt = "\n".join(lines) + (
        "\n\nТы — старший консультант по энергоэффективности зданий и пишешь напрямую для "
        "управляющего зданием. Напиши ПОДРОБНЫЙ план действий на русском языке в формате "
        "Markdown строго со следующими разделами (заголовки тоже должны быть на русском):"
        "\n\n## Сводка"
        "\nДва-три предложения, количественно описывающих проблему с использованием точных "
        "цифр в кВт·ч, тенге и CO₂ и отмеченных дат выше."
        "\n\n## Что вероятно произошло"
        "\nНазови 2-4 наиболее вероятные категории причин словами (например: график "
        "отопления/вентиляции, таймеры освещения, оборудование или розеточная нагрузка "
        "оставлены включёнными, IT-серверы, системы охраны, отсутствие календарных "
        "исключений на праздники). Для каждой категории объясни, ПОЧЕМУ ты её подозреваешь, "
        "сравнивая отмеченное потребление с базовым уровнем и типичным рабочим уровнем в "
        "данных — только словами и категориями, без числовых оценок по видам оборудования."
        "\n\n## Меры по исправлению"
        "\nПронумерованные, конкретные шаги. Для каждого шага укажи, что именно нужно "
        "проверить или изменить и кто должен это сделать (техник BMS, завхоз, IT, "
        "руководство) — не придумывай новых числовых показателей сверх переданных выше."
        "\n\n## Профилактика и мониторинг"
        "\nКонкретные меры контроля: календарные исключения BMS на праздники/каникулы, "
        "суб-счётчики, пороги тревоги на основе базового уровня нерабочего дня, и как часто "
        "нужно проверять данные."
        "\n\nПравила: используй только даты и цифры, приведённые выше; отвечай ТОЛЬКО на "
        "русском языке, включая заголовки разделов; не указывай числовые оценки "
        "энергопотребления по типам оборудования или отдельным системам — у тебя нет данных "
        "для этого, только переданные тебе агрегированные цифры и общие категории причин; "
        "никакого общего наполнителя, приветствий и прощаний."
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


class NoCacheStaticFiles(StaticFiles):
    """Force browsers to revalidate the frontend bundle on every load.

    Without this, a redeploy can silently leave users running a stale
    cached app.js — invisible bugs, "fixed" code that still misbehaves.
    Correctness beats the (negligible, for a dashboard this size) cost
    of an extra conditional request per file.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
