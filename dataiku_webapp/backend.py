"""Paste into the Python backend of a Dataiku Standard webapp.

Requires the `financereconai` project library (or installed package) and the
same code environment dependencies listed in ../requirements.txt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request

from financereconai.config import MatchingConfig
from financereconai.domain import FinancialRecord, Side
from financereconai.export import export, rows_data
from financereconai.ontology import Ontology
from financereconai.parsers import ParserFactory
from financereconai.pipeline import ReconciliationPipeline

# Dataiku injects `app` into Standard webapp Python backends.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_SIDE = 20
SESSION_TTL = timedelta(minutes=30)
MAX_SESSIONS = 100
ENGINE = ReconciliationPipeline(Ontology({
    "Revenue": ["sales", "turnover", "operating revenue", "income"],
    "Accounts Receivable": ["trade receivable", "debtors", "customer receivable"],
    "Accounts Payable": ["trade payable", "creditors", "vendor payable"],
    "Cash": ["bank balance", "cash and bank"],
    "Expense": ["cost", "operating expense", "expenditure"],
}), MatchingConfig())

@dataclass(slots=True)
class SessionState:
    updated_at: datetime
    left: tuple[FinancialRecord, ...] = ()
    right: tuple[FinancialRecord, ...] = ()
    results: tuple[Any, ...] = ()

_sessions: dict[str, SessionState] = {}
_lock = RLock()

def _purge() -> None:
    now = datetime.now(UTC)
    expired = [key for key, value in _sessions.items() if now - value.updated_at > SESSION_TTL]
    for key in expired:
        del _sessions[key]
    while len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions, key=lambda key: _sessions[key].updated_at)
        del _sessions[oldest]

def _state() -> SessionState:
    token = request.headers.get("X-FinanceRecon-Session", "")
    try:
        UUID(token)
    except ValueError:
        raise ValueError("Invalid browser session")
    with _lock:
        _purge()
        return _sessions.setdefault(token, SessionState(datetime.now(UTC)))

def _record_payload(records: tuple[FinancialRecord, ...]) -> list[dict[str, str | None]]:
    return [{"id": r.id, "description": r.description, "amount": str(r.amount or ""),
             "currency": r.currency, "date": str(r.transaction_date or ""),
             "concept": r.concept, "source": r.provenance.filename} for r in records]

@app.errorhandler(ValueError)
def invalid_request(error: ValueError) -> tuple[Response, int]:
    return jsonify({"error": str(error)}), 400

@app.post("/upload/<side>")
def upload(side: str) -> Response:
    if side not in {Side.LEFT.value, Side.RIGHT.value}:
        raise ValueError("side must be left or right")
    state = _state()
    files = request.files.getlist("files")
    if not files or len(files) > MAX_FILES_PER_SIDE:
        raise ValueError(f"Upload between 1 and {MAX_FILES_PER_SIDE} files")
    documents = []
    for item in files:
        name = item.filename or ""
        if not name or "." not in name:
            raise ValueError("Every file needs a supported extension")
        content = item.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError(f"{name}: maximum file size is 25 MB")
        documents.append(ParserFactory.parse(content, name))
        # `content` goes out of scope after parsing; it is never written to DSS storage.
    parsed = ENGINE.records(tuple(documents), Side(side))
    with _lock:
        setattr(state, side, getattr(state, side) + parsed)
        state.updated_at = datetime.now(UTC)
        state.results = ()
    return jsonify({"count": len(parsed), "records": _record_payload(getattr(state, side))})

@app.get("/state")
def state() -> Response:
    current = _state()
    return jsonify({"left": _record_payload(current.left), "right": _record_payload(current.right),
                    "results": rows_data(current.results)})

@app.post("/reconcile")
def reconcile() -> Response:
    current = _state()
    with _lock:
        current.results = ENGINE.reconcile(current.left, current.right)
        current.updated_at = datetime.now(UTC)
    return jsonify({"results": rows_data(current.results)})

@app.post("/clear")
def clear() -> Response:
    token = request.headers.get("X-FinanceRecon-Session", "")
    with _lock:
        _sessions.pop(token, None)
    return jsonify({"cleared": True})

@app.get("/export/<fmt>")
def download(fmt: str) -> Response:
    if fmt not in {"csv", "json", "xlsx"}:
        raise ValueError("format must be csv, json, or xlsx")
    current = _state()
    mime = {"csv": "text/csv", "json": "application/json", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[fmt]
    return Response(export(current.results, fmt), mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="reconciliation.{fmt}"', "Cache-Control": "no-store"})
