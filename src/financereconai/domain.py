from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Mapping


class Side(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    PARTIAL = "partial"
    UNMATCHED = "unmatched"
    EXCEPTION = "exception"


@dataclass(frozen=True, slots=True)
class Provenance:
    document_id: str
    filename: str
    page: int | None = None
    table: str | None = None
    row: int | None = None


@dataclass(frozen=True, slots=True)
class FinancialCell:
    value: str | None
    column: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class FinancialRow:
    cells: tuple[FinancialCell, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class FinancialTable:
    name: str
    columns: tuple[str, ...]
    rows: tuple[FinancialRow, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class FinancialSection:
    name: str
    tables: tuple[FinancialTable, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class FinancialDocument:
    id: str
    filename: str
    kind: str
    metadata: Mapping[str, str]
    sections: tuple[FinancialSection, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FinancialRecord:
    id: str
    side: Side
    description: str
    amount: Decimal | None
    currency: str | None
    transaction_date: date | None
    account: str | None
    counterparty: str | None
    reference: str | None
    fields: Mapping[str, str]
    provenance: Provenance
    concept: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    strategy: str
    score: Decimal
    detail: str


@dataclass(frozen=True, slots=True)
class Match:
    left_id: str
    right_id: str
    confidence: Decimal
    evidence: tuple[Evidence, ...]
    variance: Decimal | None


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    status: MatchStatus
    left: FinancialRecord | None
    right: FinancialRecord | None
    match: Match | None
    reason: str


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    code: str
    record_id: str | None
    message: str
