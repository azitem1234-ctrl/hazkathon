"""Независимый HTTP API для будущего React, HTML/JS или другого фронтенда."""

from __future__ import annotations

import io
import json
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from advisor import generate_recommendation_with_source
from config import (
    CO2_FACTOR_TODO,
    DEFAULT_CO2_KG_PER_KWH,
    DEFAULT_DEMO_TARIFF_KZT_PER_KWH,
    SCHOOL_TARIFF_TODO,
)
from core import calculate_impact, detect_anomalies, load_data

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

app = FastAPI(
    title="EcoBiz Copilot API",
    version="1.0.0",
    description="Анализирует расход электроэнергии в нерабочие дни здания.",
)

# Для локального MVP фронтенд может запускаться на любом порту.
# Перед публичным деплоем список origin нужно ограничить доменом приложения.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _parse_extra_off_periods(extra_off_periods: str | None) -> list | None:
    if not extra_off_periods:
        return None
    try:
        periods = json.loads(extra_off_periods)
    except json.JSONDecodeError as error:
        raise ValueError("extra_off_periods должен быть JSON-массивом пар дат.") from error
    if not isinstance(periods, list):
        raise ValueError("extra_off_periods должен быть JSON-массивом.")
    return periods


@app.get("/health")
def health() -> dict[str, str]:
    """Быстрая проверка, что API запущен перед видеодемо."""
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    tariff: float | None = Form(default=None),
    co2_kg_per_kwh: float | None = Form(default=None),
    extra_off_periods: str | None = Form(default=None),
) -> dict:
    """Принять CSV/XLSX и вернуть готовый для фронтенда JSON-результат."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Не удалось определить имя загруженного файла.")
    if tariff is not None and tariff < 0:
        raise HTTPException(status_code=400, detail="Тариф не может быть отрицательным.")
    if co2_kg_per_kwh is not None and co2_kg_per_kwh < 0:
        raise HTTPException(status_code=400, detail="Коэффициент CO2 не может быть отрицательным.")

    try:
        content = await file.read()
        periods = _parse_extra_off_periods(extra_off_periods)
        dataframe = load_data(
            io.BytesIO(content),
            filename=file.filename,
            extra_off_periods=periods,
        )
        analysed = detect_anomalies(dataframe)
        used_tariff = tariff if tariff is not None else DEFAULT_DEMO_TARIFF_KZT_PER_KWH
        used_co2_factor = (
            co2_kg_per_kwh if co2_kg_per_kwh is not None else DEFAULT_CO2_KG_PER_KWH
        )
        impact = calculate_impact(
            analysed,
            tariff=used_tariff,
            co2_kg_per_kwh=used_co2_factor,
        )
        anomaly_dates = analysed.loc[analysed["is_anomaly"], "date"].dt.strftime("%Y-%m-%d").tolist()
        recommendation, recommendation_source = generate_recommendation_with_source(
            impact,
            anomaly_dates,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        LOGGER.exception("Неожиданная ошибка при анализе файла.")
        raise HTTPException(status_code=500, detail="Не удалось обработать файл. Проверьте формат данных.") from error

    return {
        "impact": impact,
        "anomaly_dates": anomaly_dates,
        "recommendation": recommendation,
        "recommendation_source": recommendation_source,
        "baseline_reliable": bool(analysed.attrs["baseline_reliable"]),
        "baseline_kwh": analysed.attrs["baseline_kwh"],
        "off_day_samples": analysed.attrs["off_day_samples"],
        "tariff_note": (
            "Использован тариф из запроса."
            if tariff is not None
            else f"Использован демо-тариф {DEFAULT_DEMO_TARIFF_KZT_PER_KWH} ₸/кВт·ч. {SCHOOL_TARIFF_TODO}"
        ),
        "co2_note": (
            "Использован коэффициент CO₂ из запроса."
            if co2_kg_per_kwh is not None
            else f"Использовано демонстрационное допущение {DEFAULT_CO2_KG_PER_KWH} кг CO₂/кВт·ч. {CO2_FACTOR_TODO}"
        ),
    }
