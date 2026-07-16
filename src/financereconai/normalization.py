from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import re

CURRENCY = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}

def text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())

def decimal(value: object) -> Decimal | None:
    original, raw = text(value), text(value)
    if not raw or raw.lower() in {"na", "n/a", "null", "none", "-"}: return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = re.sub(r"[₹$€£,%]", "", raw.strip("()"))
    try: result = Decimal(raw)
    except InvalidOperation: return None
    result = -result if negative else result
    return result / Decimal("100") if "%" in original else result

def currency(value: object) -> str | None:
    raw = text(value).upper()
    for symbol, code in CURRENCY.items():
        if symbol in str(value): return code
    found = re.search(r"\b(INR|USD|EUR|GBP|AUD|CAD|JPY)\b", raw)
    return found.group(1) if found else None

def date_value(value: object) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d-%m-%Y"):
        try: return datetime.strptime(text(value), fmt).date()
        except ValueError: pass
    return None
