"""Tariff and CO2 sourcing notes the team must double-check before the final demo.

This file intentionally holds no logic — only documented numbers. `core.py` keeps
using its own defaults (so existing tests keep passing), but every default here is
traceable to a real source instead of an "eyeballed" number.
"""

# Real household tariff for the Abai region (bytovoy/household tariff), cited in
# the project brief. This is the default used by core.py. It is NOT verified for
# any specific school or business — confirm the actual contracted tariff for the
# region being analyzed before presenting numbers as final.
ABAI_HOUSEHOLD_TARIFF_KZT_PER_KWH = 17.447

# CO2 factor used across the MVP. This is a demonstration assumption, not a
# confirmed emissions factor for Kazakhstan's grid mix in a specific period.
DEFAULT_CO2_KG_PER_KWH = 0.85
CO2_FACTOR_TODO = (
    "Confirm the CO2e emissions factor for Kazakhstan's electricity grid and the "
    "calculation methodology (market-based vs. location-based) before the final demo."
)

# Reference tariffs for the team to pick from when adapting to a different school
# or business region. None of these are auto-selected by the app — each entry
# keeps its context so a household tariff is never silently presented as a school
# tariff.
REGION_TARIFFS = {
    "abai_household_2024": {
        "tariff_kzt_per_kwh": 17.447,
        "consumer_type": "households (bytovoy tariff)",
        "region": "Abai region",
        "use_for_school": False,
    },
    "astana_household_average_2026": {
        "tariff_kzt_per_kwh": 28.12,
        "includes_vat": True,
        "consumer_type": "households",
        "valid_from": "2026-07-01",
        "source": "https://astrec.kz/abonentam/tarify-dlia-fizicheskih-lits",
        "use_for_school": False,
    },
    "astana_legal_entity_ceiling_2026": {
        "tariff_kzt_per_kwh": 32.74,
        "includes_vat": False,
        "consumer_type": "legal entities: price ceiling",
        "valid_from": "2026-07-01",
        "source": "https://astrec.kz/abonentam/tarify",
        "use_for_school": False,
    },
}

SCHOOL_TARIFF_TODO = (
    "Confirm the budget organization's actual contracted electricity tariff "
    "before the final demo — none of the tariffs above are a verified school rate."
)
