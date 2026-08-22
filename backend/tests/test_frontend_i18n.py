"""Text in the interface reaches the user through a translation key.

Both checks here guard the same failure mode: markup or a branch that renders
a fixed string. Nothing breaks, no test fails, the wrong words simply appear.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

# Each fmtAge branch names both forms: ageUnit(n, 'age_month', 'age_months').
_AGE_UNIT_CALL = re.compile(r"ageUnit\([^,]+, '(\w+)', '(\w+)'\)")

# One per translation table, and every table carries this key.
_LANGUAGE_MARKER = re.compile(r"\bage_lt1day: '")

# A form label either carries data-i18n itself or wraps its text in a span that
# does. Anything left over between the tags is a string nobody can translate.
_FIELD_LABEL = re.compile(r'<label class="field-label"([^>]*)>(.*?)</label>', re.S)
_TRANSLATED_SPAN = re.compile(r"<span[^>]*data-i18n[^>]*>.*?</span>", re.S)
_ANY_TAG = re.compile(r"<[^>]+>")


def test_no_field_label_renders_a_hardcoded_string():
    src = FRONTEND.read_text(encoding="utf-8")

    labels = _FIELD_LABEL.findall(src)
    assert labels, "no field labels found, the markup must have changed"

    hardcoded = []
    for attrs, body in labels:
        if "data-i18n" in attrs:
            continue  # the whole label is translated
        leftover = _ANY_TAG.sub("", _TRANSLATED_SPAN.sub("", body)).strip()
        if leftover:
            hardcoded.append(leftover)

    assert not hardcoded, f"field labels that bypass the translation table: {hardcoded}"


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
