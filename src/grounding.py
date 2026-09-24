"""Check that every number in an answer comes from the tool results behind it.

The natural-language layer must never invent numbers. This module makes that
testable: it extracts each number from an answer and looks for it among the
numbers in the tool envelopes (data, assumptions and limitations) and in the
question itself, allowing only the rounding the answer's own precision
implies. Percentages may match either the fraction (0.693 -> 69.3%) or the
value itself. Dates are checked as whole tokens.

Anything left over is reported as ``unsupported`` -- the deterministic
renderer is tested to produce none; an LLM answer with unsupported numbers is
flagged (and retried once) by ``src/nl_agent.py``.
"""

import json
import math
import re

DATE_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SEASON_PATTERN = re.compile(r"\b(\d{4})-(\d{2})\b(?!-)")
NUMBER_PATTERN = re.compile(r"(?<![\w.])[-+−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])[-+−]?\d+(?:\.\d+)?%?")
# Integers this small are ordinals/counts in prose ("top 6", "best-of-7",
# "2 of 3 checks"); requiring a source for each would only add noise.
SMALL_INTEGER_LIMIT = 10
# Definitional labels, not data: the names of interval conventions.
LABEL_PHRASES = re.compile(
    r"\b5(?:th)?\s*(?:-|–|to)\s*95(?:th)?(?:\s*percentile|%)?|\b(?:10th|90th|95th|5th)\s*percentile|"
    r"\b80%\s*(?:range|interval)s?|\b90%\s*(?:range|interval|of outcomes)",
    re.IGNORECASE,
)


def _walk(value, numbers, strings):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            numbers.append(float(value))
        return
    if isinstance(value, str):
        strings.append(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            strings.append(str(key))
            _walk(item, numbers, strings)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, numbers, strings)


def source_values(sources):
    """All numbers and date tokens available in the sources."""
    numbers, strings = [], []
    for source in sources:
        _walk(source, numbers, strings)
    dates = set()
    for text in strings:
        dates.update(match.group(0) for match in DATE_PATTERN.finditer(text))
        for token in NUMBER_PATTERN.findall(DATE_PATTERN.sub(" ", text)):
            parsed = _parse_number(token)
            if parsed is not None:
                numbers.append(parsed[0])
    return numbers, dates


def _parse_number(token):
    token = token.replace("−", "-").replace(",", "")
    is_percent = token.endswith("%")
    token = token.rstrip("%")
    try:
        value = float(token)
    except ValueError:
        return None
    decimals = len(token.split(".")[1]) if "." in token else 0
    return value, decimals, is_percent


def _matches(value, decimals, is_percent, candidates):
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    for candidate in candidates:
        if abs(abs(value) - abs(candidate)) <= tolerance:
            return True
        if is_percent and abs(abs(value) - abs(candidate) * 100) <= tolerance:
            return True
        # A percentage written without the % sign ("69.3 percent") or a
        # probability quoted as a percentage-point figure.
        if not is_percent and decimals <= 1 and abs(abs(value) - abs(candidate) * 100) <= tolerance \
                and abs(candidate) <= 1:
            return True
    return False


def check_grounding(answer, sources, question=""):
    """Return which numbers in ``answer`` are supported by ``sources``.

    ``sources`` is a list of JSON-like objects (tool envelopes, registry
    entries). Returns ``{"numbers_checked", "supported", "unsupported",
    "grounded"}``.
    """
    numbers, dates = source_values(list(sources) + [question])
    question_dates = set(match.group(0) for match in DATE_PATTERN.finditer(question or ""))
    dates |= question_dates
    supported, unsupported = [], []
    text = LABEL_PHRASES.sub(" ", answer or "")
    for match in DATE_PATTERN.finditer(text):
        (supported if match.group(0) in dates else unsupported).append(match.group(0))
    # Season labels like "2025-26" -> check the start year only.
    text_without_dates = DATE_PATTERN.sub(" ", text)
    for match in SEASON_PATTERN.finditer(text_without_dates):
        start = float(match.group(1))
        (supported if _matches(start, 0, False, numbers) else unsupported).append(match.group(0))
    text_without_dates = SEASON_PATTERN.sub(" ", text_without_dates)
    for token in NUMBER_PATTERN.findall(text_without_dates):
        parsed = _parse_number(token)
        if parsed is None:
            continue
        value, decimals, is_percent = parsed
        if not is_percent and decimals == 0 and abs(value) <= SMALL_INTEGER_LIMIT:
            continue
        if _matches(value, decimals, is_percent, numbers):
            supported.append(token)
        else:
            unsupported.append(token)
    return {
        "numbers_checked": len(supported) + len(unsupported),
        "supported": supported,
        "unsupported": unsupported,
        "grounded": not unsupported,
    }


def compact_json(value, limit=30000):
    """JSON for a tool result, truncated (and said so) past ``limit`` chars."""
    text = json.dumps(value, default=str, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[:limit] + '..."[truncated]"'
