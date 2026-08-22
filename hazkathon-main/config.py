"""Настройки MVP, которые команда должна проверить перед финальным демо."""

# ВАЖНО: 28.12 — среднеотпускной тариф АО «Астана-РЭК» для физических лиц
# с НДС с 01.07.2026. Он удобен только для демонстрационного датасета.
# Для школы/бюджетной организации тариф необходимо уточнить по договору или на
# astanaenergosbyt.kz перед финальным демо и передать в POST /analyze как tariff.
DEFAULT_DEMO_TARIFF_KZT_PER_KWH = 28.12

# Это исходное демонстрационное допущение MVP, а не подтверждённый коэффициент
# для конкретного периода, региона или школы. Перед финалом нужно выбрать
# методологию (market-based/location-based) и подтвердить актуальный источник.
DEFAULT_CO2_KG_PER_KWH = 0.85
CO2_FACTOR_TODO = (
    "Подтвердить коэффициент выбросов CO₂e для электроэнергии Казахстана "
    "и выбранной методологии расчёта перед финальным демо."
)

# Это справочник для питча и интерфейса, а не «автоматический выбор тарифа».
# У каждой записи сохранён контекст, чтобы не выдать бытовый тариф за школьный.
REGION_TARIFFS = {
    "astana_household_average_2026": {
        "tariff_kzt_per_kwh": 28.12,
        "includes_vat": True,
        "consumer_type": "физические лица",
        "valid_from": "2026-07-01",
        "source": "https://astrec.kz/abonentam/tarify-dlia-fizicheskih-lits",
        "use_for_school": False,
    },
    "astana_legal_entity_ceiling_2026": {
        "tariff_kzt_per_kwh": 32.74,
        "includes_vat": False,
        "consumer_type": "юридические лица: предельная цена энергоснабжения",
        "valid_from": "2026-07-01",
        "source": "https://astrec.kz/abonentam/tarify",
        "use_for_school": False,
    },
    "astana_electricity_transmission_2026": {
        "tariff_kzt_per_kwh": 10.53,
        "includes_vat": False,
        "consumer_type": "передача электроэнергии; не является конечным тарифом школы",
        "valid_from": "2026-05-01",
        "source": "https://astrec.kz/abonentam/tarify",
        "use_for_school": False,
    },
}

SCHOOL_TARIFF_TODO = (
    "Уточнить тариф бюджетной организации в Астане по договору или на "
    "astanaenergosbyt.kz перед финальным демо."
)
