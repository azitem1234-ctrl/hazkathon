"""LLM-слой: переводит результат анализа в короткое действие для завхоза."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)
# Явный путь не зависит от папки, из которой запустили uvicorn. Ключи читаются
# из .env до первого обращения к os.getenv ниже; они не кэшируются в config.py.
DOTENV_PATH = Path(__file__).with_name(".env")
DOTENV_LOADED = load_dotenv(dotenv_path=DOTENV_PATH, override=False)

SYSTEM_PROMPT = """Ты — ИИ-энергоаудитор для школ и малого бизнеса Казахстана.
Тебе дают данные об аномальном расходе электроэнергии в нерабочие периоды.
Сформулируй короткую (3–5 предложений), понятную нетехническому человеку
(завхозу школы) рекомендацию: что происходит, вероятная причина, что сделать.
Обязательно укажи точную сумму экономии в тенге из переданных данных. Не выдумывай
цифр, которых нет во входных данных.

В приложении точные даты, кВт·ч, тенге и CO₂ безопасно добавляются программой из
проверенного результата. Поэтому верни только 1–3 поясняющих предложения без чисел,
дат, температур и сроков. Вместе с фактами от приложения итоговая рекомендация будет
состоять из 3–5 предложений."""


def _format_number(value: object) -> str:
    """Единый формат чисел для интерфейса и рекомендации."""
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")


def _fallback_recommendation(impact: dict, anomaly_days: list[str]) -> str:
    """Автономная рекомендация: демо остаётся рабочим без интернета и ключа."""
    dates = ", ".join(anomaly_days) if anomaly_days else "не указаны"
    facts = (
        f"Найдено аномальных нерабочих дней: {impact['anomaly_days']}; даты: {dates}. "
        f"Избыточный расход составил {_format_number(impact['total_excess_kwh'])} кВт·ч, "
        f"потенциальная экономия — {_format_number(impact['savings_kzt'])} ₸, "
        f"а снижение выбросов — {_format_number(impact['co2_saved_kg'])} кг CO₂."
    )
    return (
        f"{facts} Вероятно, в периоды простоя часть оборудования оставалась в обычном режиме. "
        "Проверьте отопление, вентиляцию, освещение и другие постоянные нагрузки; "
        "на нерабочее время переведите их в согласованный дежурный режим."
    )


def _build_user_message(impact: dict, anomaly_days: list[str]) -> str:
    return (
        "Проверенные данные анализа (используй только их):\n"
        f"- избыточный расход: {impact['total_excess_kwh']} кВт·ч;\n"
        f"- потенциальная экономия: {impact['savings_kzt']} тенге;\n"
        f"- снижение CO₂: {impact['co2_saved_kg']} кг;\n"
        f"- число аномальных дней: {impact['anomaly_days']};\n"
        f"- даты аномалий: {', '.join(anomaly_days) if anomaly_days else 'нет'}.\n"
        "Дай только поясняющую часть рекомендации по системной инструкции."
    )


def _clean_llm_narrative(text: str) -> str:
    """Убирает ответ с цифрами: все измеримые факты выводит только код."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    safe_sentences = [sentence.strip() for sentence in sentences if sentence.strip() and not re.search(r"\d", sentence)]
    return " ".join(safe_sentences[:3])


def _get_api_key(api_key: str | None) -> tuple[str | None, str]:
    """Вернуть ключ и его источник, не записывая секрет в лог."""
    if api_key:
        return api_key, "аргумент функции"
    if os.getenv("LLM_API_KEY"):
        return os.environ["LLM_API_KEY"], "LLM_API_KEY из окружения/.env"
    if os.getenv("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"], "GEMINI_API_KEY из окружения/.env"
    return None, "не найден"


def generate_recommendation_with_source(
    impact: dict, anomaly_days: list[str], api_key: str | None = None
) -> tuple[str, Literal["gemini", "fallback"]]:
    """Получить совет и источник: Gemini либо автономный fallback.

    ``LLM_PROVIDER`` вынесен в окружение, поэтому в будущем провайдер можно
    заменить без изменения API-контракта. В текущем MVP намеренно включён только
    Gemini: незнакомый провайдер не ломает ответ и переводит его на fallback.
    """
    fallback = _fallback_recommendation(impact, anomaly_days)
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    key, key_source = _get_api_key(api_key)
    LOGGER.info(
        "LLM configuration: dotenv_path=%s, dotenv_found=%s, provider=%s, key_source=%s.",
        DOTENV_PATH,
        DOTENV_LOADED,
        provider,
        key_source,
    )

    if not key:
        LOGGER.info("LLM-ключ не задан: использована локальная рекомендация.")
        return fallback, "fallback"
    if provider != "gemini":
        LOGGER.warning("Провайдер '%s' не подключён: использован fallback.", provider)
        return fallback, "fallback"

    # Gemini API сообщает, что предыдущая 2.5 Flash-Lite недоступна новым
    # пользователям; используем рекомендованную замену, но оставляем настройку .env.
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    try:
        # Импорт здесь нужен, чтобы отсутствие необязательной библиотеки тоже не
        # останавливало демонстрацию: команда всё равно увидит fallback.
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=12_000),
        )
        LOGGER.info(
            "Gemini API call started: model=%s, timeout_ms=%s, key_source=%s.",
            model,
            12_000,
            key_source,
        )
        response = client.models.generate_content(
            model=model,
            contents=_build_user_message(impact, anomaly_days),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=180,
            ),
        )
        LOGGER.info(
            "Gemini API call succeeded: model=%s, response_characters=%s.",
            model,
            len(response.text or ""),
        )
        narrative = _clean_llm_narrative(response.text or "")
        if not narrative:
            raise ValueError("Gemini вернул пустой или неподходящий ответ.")

        # Цифры не доверяются модели: первая часть всегда создаётся из impact кодом.
        facts = _fallback_recommendation(impact, anomaly_days).split(" Вероятно,")[0]
        return f"{facts} {narrative}", "gemini"
    except Exception as error:  # Ошибки сети, лимиты и SDK не должны срывать демо.
        # exception добавляет traceback, а str(error) — сообщение Gemini (401, 429,
        # таймаут, неверная модель и т. п.). API-ключ намеренно не логируется.
        LOGGER.exception(
            "Gemini API call failed: model=%s, error_type=%s, error=%s. "
            "Used fallback recommendation.",
            model,
            type(error).__name__,
            error,
        )
        return fallback, "fallback"


def generate_recommendation(
    impact: dict, anomaly_days: list[str], api_key: str | None = None
) -> str:
    """Совместимый текстовый интерфейс для простого использования и старых тестов."""
    recommendation, _ = generate_recommendation_with_source(
        impact,
        anomaly_days,
        api_key=api_key,
    )
    return recommendation
