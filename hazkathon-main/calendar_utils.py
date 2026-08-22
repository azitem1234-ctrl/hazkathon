"""Календарная логика для определения нерабочих дней здания."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)


def load_off_periods_from_json(json_path: str | Path) -> list[tuple[str, str]]:
    """Прочитать периоды простоя из JSON-календаря проекта.

    JSON хранит даты отдельно от кода, поэтому команда может исправить календарь
    конкретной школы без изменения алгоритма.
    """
    path = Path(json_path)
    try:
        calendar_data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Не найден файл календаря: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Некорректный JSON-календарь: {path}") from error

    periods = calendar_data.get("off_periods", [])
    parsed_periods: list[tuple[str, str]] = []
    for index, period in enumerate(periods):
        try:
            parsed_periods.append((period["start_date"], period["end_date"]))
        except (KeyError, TypeError) as error:
            raise ValueError(
                f"Период №{index + 1} в {path.name} должен иметь start_date и end_date."
            ) from error
    return parsed_periods


def _parse_periods(extra_off_periods: list | None) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    parsed_periods: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for index, period in enumerate(extra_off_periods or []):
        if isinstance(period, dict):
            start, end = period.get("start_date"), period.get("end_date")
        else:
            try:
                start, end = period
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Нерабочий период №{index + 1} должен быть парой дат (start, end)."
                ) from error

        start_date = pd.to_datetime(start, errors="coerce")
        end_date = pd.to_datetime(end, errors="coerce")
        if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
            raise ValueError(
                f"Некорректные границы нерабочего периода №{index + 1}: {start} — {end}."
            )
        parsed_periods.append((start_date.normalize(), end_date.normalize()))
    return parsed_periods


def add_workday_column(
    df: pd.DataFrame, extra_off_periods: list | None = None
) -> pd.DataFrame:
    """Добавить ``is_workday`` по выходным и известным периодам простоя.

    Если колонка уже передана школой вручную, она не меняется: локальный график
    всегда точнее типового государственного календаря.
    """
    if "date" not in df.columns:
        raise ValueError("Нельзя определить рабочий день: отсутствует колонка 'date'.")

    result = df.copy()
    if "is_workday" in result.columns:
        return result

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    invalid_dates = result["date"].isna()
    if invalid_dates.any():
        LOGGER.warning(
            "Для %s строк не удалось определить рабочий день из-за некорректной даты.",
            int(invalid_dates.sum()),
        )

    is_workday = (result["date"].dt.dayofweek < 5) & ~invalid_dates
    for start_date, end_date in _parse_periods(extra_off_periods):
        is_workday &= ~result["date"].between(start_date, end_date)

    result["is_workday"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result.loc[~invalid_dates, "is_workday"] = is_workday.loc[~invalid_dates].astype(int)
    return result
