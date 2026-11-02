import calendar
import re
from datetime import date


_MONTH = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_PATTERNS = (
    re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"),
    re.compile(
        rf"\b(?P<month_name>{_MONTH})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
        rf",?\s+(?P<year>20\d{{2}})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month_name>{_MONTH})"
        rf",?\s+(?P<year>20\d{{2}})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?P<first>\d{1,2})/(?P<second>\d{1,2})/(?P<year>20\d{2})\b"),
)
_MONTH_NUMBERS = {
    name.lower(): number
    for number, names in enumerate(
        (
            ("January", "Jan"),
            ("February", "Feb"),
            ("March", "Mar"),
            ("April", "Apr"),
            ("May",),
            ("June", "Jun"),
            ("July", "Jul"),
            ("August", "Aug"),
            ("September", "Sep", "Sept"),
            ("October", "Oct"),
            ("November", "Nov"),
            ("December", "Dec"),
        ),
        start=1,
    )
    for name in names
}
_EXPIRY_CLAUSE = re.compile(
    r"\b(?:warranty|coverage)\b.{0,60}?"
    r"\b(?:expires?|expiry|expiration|ends?|end\s+date|valid\s+until)\b.{0,35}",
    re.IGNORECASE | re.DOTALL,
)
_DURATION_FROM_PURCHASE = (
    re.compile(
        r"\b(?:warranty|coverage)\b.{0,60}?\b(?P<value>\d{1,2})\s*[- ]?"
        r"(?P<unit>years?|months?)\b.{0,60}?\bfrom\s+(?:the\s+)?"
        r"(?:(?:date|day)\s+of\s+)?purchase\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?P<value>\d{1,2})\s*[- ]?(?P<unit>years?|months?)\s+"
        r"(?:warranty|coverage)\b.{0,60}?\bfrom\s+(?:the\s+)?"
        r"(?:(?:date|day)\s+of\s+)?purchase\b",
        re.IGNORECASE | re.DOTALL,
    ),
)


def _date_from_match(match: re.Match[str]) -> date | None:
    groups = match.groupdict()
    try:
        if groups.get("month_name"):
            month = _MONTH_NUMBERS[groups["month_name"].lower()]
            day = int(groups["day"])
        elif groups.get("first"):
            first = int(groups["first"])
            second = int(groups["second"])
            if first <= 12 and second <= 12:
                return None
            month, day = (first, second) if first <= 12 else (second, first)
        else:
            month = int(groups["month"])
            day = int(groups["day"])
        return date(int(groups["year"]), month, day)
    except (KeyError, ValueError):
        return None


def _explicit_expiry(text: str) -> date | None:
    for clause in _EXPIRY_CLAUSE.finditer(text):
        candidates = [
            (match.start(), _date_from_match(match))
            for pattern in _DATE_PATTERNS
            for match in pattern.finditer(clause.group())
        ]
        for _position, candidate in sorted(candidates, key=lambda item: item[0]):
            if candidate is not None:
                return candidate
    return None


def _duration_expiry(text: str, purchase_date: date | None) -> date | None:
    if purchase_date is None:
        return None
    for pattern in _DURATION_FROM_PURCHASE:
        match = pattern.search(text)
        if match is None:
            continue
        value = int(match.group("value"))
        unit = match.group("unit").lower()
        months = value * 12 if unit.startswith("year") else value
        if value == 0 or months > 360:
            return None
        month_index = purchase_date.month - 1 + months
        year = purchase_date.year + month_index // 12
        month = month_index % 12 + 1
        day = min(purchase_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    return None


def suggest_warranty_expiry(text: str, purchase_date: date | None) -> date | None:
    return _explicit_expiry(text) or _duration_expiry(text, purchase_date)
