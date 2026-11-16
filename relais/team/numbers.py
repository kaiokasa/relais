"""Deterministic check of the reviewer: every number of an answer must come from a result.

A number is accepted when a value of a result (SQL table, statistics result, chart data) or of the question is
within half a unit of its last written digit: "94,3 %" matches 94.27, "45 196 996" must match exactly. Rates are
also accepted in the other scale (0.8216 written as "82,2 %"). Numbers glued to letters ("4G", "2e trimestre",
"T2") are labels, not quantities, and are not checked.
"""
import math
import re

import numpy as np
import pandas as pd

SCALES = {"million": 1e6, "millions": 1e6, "milliard": 1e9, "milliards": 1e9}
NUMBER = re.compile(
    r"(?<![\w.,−-])([-−])?"              # sign (a hyphen glued to a word is not a sign)
    r"(\d{1,3}(?:[   ]\d{3})+|\d+)"     # integer part, with French thousands spaces
    r"(?:[,.](\d+))?"                              # decimals, French comma or point
    r"(?![\w])"                                    # not glued to letters: 4G, 2e, T2 are labels
    r"(?:\s*(millions?|milliards?)\b)?", re.IGNORECASE)
PLAIN_DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
FREE_INTEGERS = set(range(0, 11))  # "3 classes", "2 minutes", "7 niveaux": small counts are not checked
MAX_ALLOWED_VALUES = 3_000_000


def numbers_in_text(text):
    """(written form, value, tolerance) for every quantity of a French text."""
    found = []
    for match in NUMBER.finditer(text or ""):
        sign, integer, decimals, scale_word = match.groups()
        value = float(re.sub(r"[   ]", "", integer) + ("." + decimals if decimals else ""))
        scale = SCALES.get((scale_word or "").lower(), 1.0)
        value *= scale * (-1 if sign else 1)
        tolerance = 0.5 * 10 ** -len(decimals or "") * scale
        found.append((match.group(0).strip(), value, tolerance))
    return found


def _values_of_object(value, out):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, np.integer, np.floating)):
        if not (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            out.append(float(value))
    elif isinstance(value, str):
        out.extend(v for _, v, _ in numbers_in_text(value))
        out.extend(float(d.replace(",", ".")) for d in PLAIN_DIGITS.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _values_of_object(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _values_of_object(item, out)


def values_of_frame(frame, max_rows=200_000):
    """Every number a table can justify: numeric cells, the parts of dates, the digits of labels."""
    out = []
    frame = frame.head(max_rows)
    for column in frame.columns:
        series = frame[column]
        if pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_numeric_dtype(series):
            out.extend(series.dropna().astype(float).tolist())
        elif pd.api.types.is_datetime64_any_dtype(series):
            dates = series.dropna()
            out.extend(dates.dt.year.astype(float).tolist() + dates.dt.month.astype(float).tolist()
                       + dates.dt.day.astype(float).tolist())
        else:
            for text in series.dropna().astype(str).unique()[:5000]:
                _values_of_object(text, out)
    return out


class AllowedNumbers:
    """The values an answer may quote, with both scales of a rate (0.82 and 82)."""

    def __init__(self, values):
        base = np.asarray([v for v in values if math.isfinite(v)], dtype=float)[:MAX_ALLOWED_VALUES]
        both = np.concatenate([base, base * 100, base / 100, np.abs(base), np.abs(base) * 100])
        self.values = np.unique(both)

    @classmethod
    def from_sources(cls, question="", frames=(), objects=()):
        values = [v for _, v, _ in numbers_in_text(question)]
        for frame in frames:
            values += values_of_frame(frame)
        for item in objects:
            _values_of_object(item, values)
        return cls(values)

    def accepts(self, value, tolerance):
        if value in FREE_INTEGERS:
            return True
        if self.values.size == 0:
            return False
        i = np.searchsorted(self.values, value)
        neighbours = self.values[max(0, i - 1):i + 1]
        return bool(np.any(np.abs(neighbours - value) <= tolerance + 1e-9 * max(1.0, abs(value))))


def unsupported_numbers(text, allowed):
    """The written numbers of `text` that no result justifies."""
    return [raw for raw, value, tolerance in numbers_in_text(text) if not allowed.accepts(value, tolerance)]
