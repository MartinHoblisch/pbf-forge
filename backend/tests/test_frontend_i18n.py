"""The age label beside an outdated download has to agree in number.

`fmtAge` picks between a singular and a plural key per unit. A branch added
without its singular form does not fail anywhere: it silently renders
"1 months outdated".
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

# Each fmtAge branch names both forms: ageUnit(n, 'age_month', 'age_months').
_AGE_UNIT_CALL = re.compile(r"ageUnit\([^,]+, '(\w+)', '(\w+)'\)")

# One per translation table, and every table carries this key.
_LANGUAGE_MARKER = re.compile(r"\bage_lt1day: '")


def test_every_age_unit_has_both_forms_in_every_language():
    src = FRONTEND.read_text(encoding="utf-8")

    calls = _AGE_UNIT_CALL.findall(src)
    assert calls, "fmtAge no longer routes its units through ageUnit"

    languages = len(_LANGUAGE_MARKER.findall(src))
    assert languages >= 2, "expected at least the German and English tables"

    for singular, plural in calls:
        assert singular != plural, f"{singular} is used for both forms"
        for key in (singular, plural):
            defined = len(re.findall(rf"\b{key}: '", src))
            assert defined == languages, (
                f"{key} is defined {defined}x, once per language would be {languages}"
            )
