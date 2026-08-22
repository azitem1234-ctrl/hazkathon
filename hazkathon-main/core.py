"""Статистическое ядро EcoBiz Copilot.

Пайплайн прост для защиты: загрузка данных -> поиск аномалий -> расчёт эффекта.
Мы не предсказываем расход «магией»: нормой служит нижний квартиль расхода
самого здания в нерабочие дни.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from calendar_utils import add_workday_column, load_off_periods_from_json
from config import DEFAULT_CO2_KG_PER_KWH, DEFAULT_DEMO_TARIFF_KZT_PER_KWH

LOGGER = logging.getLogger(__name__)

# Значение оставлено как понятный псевдоним для совместимости с ранней версией MVP.
# Это демонстрационный тариф для физлиц, а не подтверждённый тариф школы.
TARIFF_KZT_PER_KWH = DEFAULT_DEMO_TARIFF_KZT_PER_KWH
CO2_KG_PER_KWH = DEFAULT_CO2_KG_PER_KWH
BASELINE_MULTIPLIER = 1.5
MIN_OFF_DAY_SAMPLES = 5
DEFAULT_CALENDAR_PATH = Path(__file__).with_name("holidays_kz.json")


def load_data(
    file_path_or_buffer: str | Path | BinaryIO,
    *,
    filename: str | None = None,
    extra_off_periods: list | None = None,
) -> pd.DataFrame:
    """Загрузить CSV/XLSX и добавить ``is_workday``, если его нет в файле.

    Реальная выгрузка может содержать только ``date`` и ``consumption_kwh``.
    В таком случае используются выходные и календарь РК из ``holidays_kz.json``.
    Переданные пользователем периоды имеют приоритет как дополнительная информация.
    """
    source_name = filename or getattr(file_path_or_buffer, "name", None) or str(file_path_or_buffer)
    suffix = Path(str(source_name)).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(file_path_or_buffer)
    elif suffix == ".csv":
        df = pd.read_csv(file_path_or_buffer)
    else:
        raise ValueError("Поддерживаются только файлы CSV, XLSX и XLS.")

    if "date" not in df.columns:
        raise ValueError("В файле отсутствует обязательная колонка 'date'.")

    # Ошибочные даты не скрываем: detector позже отбросит их с предупреждением.
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "is_workday" in df.columns:
        return df

    calendar_periods = load_off_periods_from_json(DEFAULT_CALENDAR_PATH)
    if extra_off_periods:
        calendar_periods.extend(extra_off_periods)
    return add_workday_column(df, extra_off_periods=calendar_periods)


def _require_columns(df: pd.DataFrame, required_columns: set[str]) -> None:
    missing = required_columns.difference(df.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"В данных отсутствуют обязательные колонки: {missing_text}.")


def _clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Аккуратно очистить типичные ошибки выгрузки и записать предупреждения."""
    result = df.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")

    invalid_dates = result["date"].isna()
    if invalid_dates.any():
        LOGGER.warning("Удалено строк с некорректной датой: %s", int(invalid_dates.sum()))
        result = result.loc[~invalid_dates].copy()

    duplicate_dates = result["date"].duplicated(keep="last")
    if duplicate_dates.any():
        LOGGER.warning(
            "Найдены дубликаты дат; оставлены последние записи: %s",
            int(duplicate_dates.sum()),
        )
        result = result.loc[~duplicate_dates].copy()

    result["consumption_kwh"] = pd.to_numeric(result["consumption_kwh"], errors="coerce")
    invalid_consumption = result["consumption_kwh"].isna() | (result["consumption_kwh"] < 0)
    if invalid_consumption.any():
        LOGGER.warning(
            "Удалено строк с пустым, текстовым или отрицательным расходом: %s",
            int(invalid_consumption.sum()),
        )
        result = result.loc[~invalid_consumption].copy()

    if result.empty:
        raise ValueError("После очистки не осталось строк с корректными данными.")
    return result.sort_values("date").reset_index(drop=True)


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Найти расход в нерабочие дни, значительно выше базового уровня здания.

    База — 25-й перцентиль нерабочих дней: устойчивый ориентир для дня, когда
    оборудование действительно перевели в дежурный режим. При менее чем пяти
    нерабочих днях расчёт сохраняется для MVP, но ``baseline_reliable`` явно
    помечается как ``False`` в ``DataFrame.attrs`` и ответе API.
    """
    _require_columns(df, {"date", "consumption_kwh"})
    result = _clean_data(df)
    if "is_workday" not in result.columns:
        result = add_workday_column(
            result,
            extra_off_periods=load_off_periods_from_json(DEFAULT_CALENDAR_PATH),
        )

    result["is_workday"] = pd.to_numeric(result["is_workday"], errors="coerce")
    invalid_workday = ~result["is_workday"].isin([0, 1])
    if invalid_workday.any():
        raise ValueError("Колонка 'is_workday' должна содержать только 0 (выходной) или 1 (рабочий).")

    off_days = result.loc[result["is_workday"] == 0, "consumption_kwh"]
    if off_days.empty:
        raise ValueError("Нет нерабочих дней: невозможно построить базовый уровень расхода.")

    baseline_reliable = len(off_days) >= MIN_OFF_DAY_SAMPLES
    if not baseline_reliable:
        LOGGER.warning(
            "Для надёжной базы нужно минимум %s нерабочих дней; найдено: %s.",
            MIN_OFF_DAY_SAMPLES,
            len(off_days),
        )

    baseline = float(off_days.quantile(0.25))
    over_baseline = (result["is_workday"] == 0) & (
        result["consumption_kwh"] > baseline * BASELINE_MULTIPLIER
    )
    result["is_anomaly"] = over_baseline
    result["excess_kwh"] = (
        result["consumption_kwh"] - baseline
    ).where(over_baseline, 0.0)

    # attrs не меняют табличные данные, зато честно передают качество расчёта API.
    result.attrs["baseline_kwh"] = round(baseline, 2)
    result.attrs["baseline_reliable"] = baseline_reliable
    result.attrs["off_day_samples"] = int(len(off_days))
    return result


def calculate_impact(
    df: pd.DataFrame,
    tariff: float = TARIFF_KZT_PER_KWH,
    co2_kg_per_kwh: float = CO2_KG_PER_KWH,
) -> dict[str, float | int]:
    """Посчитать потенциальную экономию и снижение CO2 после детекции аномалий."""
    _require_columns(df, {"excess_kwh", "is_anomaly"})
    if tariff < 0:
        raise ValueError("Тариф не может быть отрицательным.")
    if co2_kg_per_kwh < 0:
        raise ValueError("Коэффициент CO2 не может быть отрицательным.")

    total_excess = float(df["excess_kwh"].sum())
    return {
        "total_excess_kwh": round(total_excess, 2),
        "savings_kzt": round(total_excess * tariff, 2),
        "co2_saved_kg": round(total_excess * co2_kg_per_kwh, 2),
        "anomaly_days": int(df["is_anomaly"].sum()),
        "tariff_kzt_per_kwh": round(float(tariff), 2),
        "co2_kg_per_kwh": round(float(co2_kg_per_kwh), 3),
    }
