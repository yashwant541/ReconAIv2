"""Web backend for FinanceReconAI.

Deploy into a Dataiku *Standard* web app's Python tab (Dataiku provides `app`),
or run locally via run_local.py which supplies `app`. The engine is
framework-free; this is the only web-aware layer. No disk persistence — parsed
documents and the last result live in an in-memory, TTL'd store.
"""
from __future__ import annotations

import io
import os
import threading
import time
import uuid
from typing import Any, Dict

from flask import jsonify, request, send_file

from financial_reconciliation import api, auth
from financial_reconciliation.config.settings import EngineConfig
from financial_reconciliation.exporters.processed_workbook import write_processed_workbook
from financial_reconciliation.matching.similarity import BACKEND
from financial_reconciliation.models.documents import UploadedDocument
from financial_reconciliation.models.results import ReconciliationSession
from financial_reconciliation.reference_library import ReferenceLibrary

SESSION_TTL_SECONDS = 60 * 60
MAX_UPLOAD_MB = 100
_LIBRARY = ReferenceLibrary.from_config()
_GLOB = {"sig": None, "onto": None}


def _global_ontology():
    fn, data = _LIBRARY.read_synonyms()
    if not data:
        _GLOB["sig"] = None
        _GLOB["onto"] = None
        return None
    sig = (fn, len(data))
    if _GLOB["sig"] != sig:
        _GLOB["onto"] = api.load_ontology(fn, data)
        _GLOB["sig"] = sig
    return _GLOB["onto"]

_SESSIONS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _evict() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    with _LOCK:
        for sid in [s for s, v in _SESSIONS.items() if v["ts"] < cutoff]:
            _SESSIONS.pop(sid, None)


def _get(sid: str) -> Dict[str, Any]:
    _evict()
    with _LOCK:
        s = _SESSIONS.get(sid)
        if s is None:
            raise KeyError("Unknown or expired session")
        s["ts"] = time.time()
        return s


def _tables_meta(extractions) -> list:
    out = []
    for e in extractions:
        for t in e.document.tables:
            out.append({"id": api.table_id(e.document.filename, t.name),
                        "file": e.document.filename, "table": t.name,
                        "columns": t.columns, "rows": t.row_count})
    return out


@app.route("/session", methods=["POST"])
def create_session():
    sid = uuid.uuid4().hex
    with _LOCK:
        _SESSIONS[sid] = {"ts": time.time(),
                          "session": ReconciliationSession(session_id=sid),
                          "result": None, "ontology": _global_ontology()}
    return jsonify({"session_id": sid, "fuzzy_backend": BACKEND})


@app.route("/upload", methods=["POST"])
def upload():
    sid = request.form.get("sid", "")
    side = request.form.get("side", "")
    if side not in ("left", "right"):
        return jsonify({"error": "side must be 'left' or 'right'"}), 400
    try:
        store = _get(sid)
    except KeyError as e:
        return jsonify({"error": str(e)}), 404

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files provided"}), 400

    docs = []
    for fs in files:
        data = fs.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            return jsonify({"error": f"{fs.filename} exceeds {MAX_UPLOAD_MB} MB"}), 400
        docs.append(UploadedDocument(fs.filename, data))

    try:
        extractions = api.extract_many(docs)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"parse failed: {e}"}), 400

    warnings = [{**w.as_dict(), "file": e.document.filename}
                for e in extractions for w in e.warnings]
    session = store["session"].with_extractions(side, extractions)
    store["session"] = session
    side_ext = session.left_extractions if side == "left" else session.right_extractions

    return jsonify({"side": side, "warnings": warnings,
                    "tables": _tables_meta(extractions),
                    "side_columns": session.columns(side),
                    "side_tables": _tables_meta(side_ext),
                    "side_file_count": len(side_ext)})


@app.route("/suggest_mapping", methods=["POST"])
def suggest_mapping():
    body = request.get_json(force=True)
    try:
        store = _get(body.get("sid", ""))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    s = store["session"]
    if not s.left_extractions or not s.right_extractions:
        return jsonify({"error": "upload files on both sides first"}), 400
    fields = api.suggest_mapping(list(s.left_extractions), list(s.right_extractions))
    left_profiles = [p.as_dict() for p in api.profile_side(list(s.left_extractions))]
    right_profiles = [p.as_dict() for p in api.profile_side(list(s.right_extractions))]
    return jsonify({"fields": fields, "left_profiles": left_profiles,
                    "right_profiles": right_profiles})


@app.route("/reset_side", methods=["POST"])
def reset_side():
    body = request.get_json(force=True)
    try:
        store = _get(body.get("sid", ""))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    side = body.get("side")
    if side in ("left", "right"):
        store["session"] = store["session"].cleared_side(side)
    return jsonify({"ok": True})


@app.route("/reconcile", methods=["POST"])
def reconcile():
    body = request.get_json(force=True)
    try:
        store = _get(body.get("sid", ""))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    s = store["session"]
    if not s.left_extractions or not s.right_extractions:
        return jsonify({"error": "upload files on both sides first"}), 400
    inc_l = set(body["included_left"]) if body.get("included_left") else None
    inc_r = set(body["included_right"]) if body.get("included_right") else None
    try:
        config = EngineConfig.from_dict(body)
        result = api.reconcile(list(s.left_extractions), list(s.right_extractions),
                               config, inc_l, inc_r, ontology=store.get("ontology"),
                               melt=bool(body.get("melt")))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"reconciliation failed: {e}"}), 400
    store["result"] = result
    return jsonify(api.result_to_dict(result))


@app.route("/export", methods=["GET"])
def export():
    try:
        store = _get(request.args.get("sid", ""))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    if store.get("result") is None:
        return jsonify({"error": "run a reconciliation first"}), 400
    data = api.export_workbook(store["result"])
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name="reconciliation.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/finish", methods=["POST"])
def finish():
    body = request.get_json(force=True)
    with _LOCK:
        _SESSIONS.pop(body.get("sid", ""), None)
    return jsonify({"ok": True})


@app.route("/upload_synonyms", methods=["POST"])
def upload_synonyms():
    sid = request.form.get("sid", "")
    try:
        store = _get(sid)
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    fs = request.files.get("file")
    if fs is None:
        return jsonify({"error": "no file"}), 400
    try:
        onto = api.load_ontology(fs.filename, fs.read())
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not read synonyms: {e}"}), 400
    g = _global_ontology()
    store["ontology"] = g.merge(onto) if g else onto
    total = store["ontology"]
    return jsonify({"ok": True, "aliases": total.size, "concepts": len(total.groups())})


@app.route("/reference/list", methods=["GET"])
def reference_list():
    return jsonify({"references": _LIBRARY.list()})


@app.route("/reference/use", methods=["POST"])
def reference_use():
    body = request.get_json(force=True)
    try:
        store = _get(body.get("sid", ""))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    side = body.get("side")
    if side not in ("left", "right"):
        return jsonify({"error": "side must be left/right"}), 400
    try:
        processed = _LIBRARY.get(body.get("ref_id", ""))
        ext = api.load_processed(processed)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"reference not found: {e}"}), 404
    store["session"] = store["session"].with_extractions(side, [ext])
    return jsonify({"ok": True, "side": side,
                    "tables": _tables_meta([ext]),
                    "side_columns": store["session"].columns(side)})


@app.route("/admin/login", methods=["POST"])
def admin_login():
    body = request.get_json(force=True)
    token = auth.login(body.get("username", ""), body.get("password", ""))
    if not token:
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"ok": True, "token": token})


@app.route("/admin/reference/upload", methods=["POST"])
def admin_reference_upload():
    """Admin-gated: upload a pre-processed document into the reference library.

    Accepts the processed xlsx/json directly (stored as-is), or a raw
    pdf/docx/csv with melt=true to pre-process it into the tidy schema.
    """
    if not auth.valid_token(request.form.get("token")):
        return jsonify({"error": "admin token required/invalid"}), 403
    fs = request.files.get("file")
    if fs is None:
        return jsonify({"error": "no file"}), 400
    name = (request.form.get("name") or fs.filename or "reference").strip()
    melt = request.form.get("melt", "false").lower() == "true"
    try:
        ext = api.extract_tables(fs.filename, fs.read())
        processed = api.export_processed([ext], name, long=melt)
        ref_id = _LIBRARY.save(request.form.get("ref_id") or name, processed)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not process reference: {e}"}), 400
    return jsonify({"ok": True, "ref_id": ref_id, "source": name,
                    "tables": len(processed["tables"])})


@app.route("/admin/reference/download", methods=["POST"])
def admin_reference_download():
    """Admin-gated: turn an uploaded file into a tidy processed workbook and
    return it for download (does not save to the library)."""
    if not auth.valid_token(request.form.get("token")):
        return jsonify({"error": "admin token required/invalid"}), 403
    fs = request.files.get("file")
    if fs is None:
        return jsonify({"error": "no file"}), 400
    name = (request.form.get("name") or fs.filename or "reference").strip()
    melt = request.form.get("melt", "false").lower() == "true"
    try:
        ext = api.extract_tables(fs.filename, fs.read())
        processed = api.export_processed([ext], name, long=melt)
        data = write_processed_workbook(processed)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not process: {e}"}), 400
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name="processed_tables.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/admin/reference/delete", methods=["POST"])
def admin_reference_delete():
    body = request.get_json(force=True)
    if not auth.valid_token(body.get("token")):
        return jsonify({"error": "admin token required/invalid"}), 403
    ok = _LIBRARY.delete(body.get("ref_id", ""))
    return jsonify({"ok": ok})


@app.route("/admin/synonyms/save", methods=["POST"])
def admin_synonyms_save():
    """Admin-gated: persist a global synonyms list applied to all sessions."""
    if not auth.valid_token(request.form.get("token")):
        return jsonify({"error": "admin token required/invalid"}), 403
    fs = request.files.get("file")
    if fs is None:
        return jsonify({"error": "no file"}), 400
    data = fs.read()
    try:
        onto = api.load_ontology(fs.filename, data)   # validate it parses
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not read synonyms: {e}"}), 400
    ext = (fs.filename.rsplit(".", 1)[-1].lower() if "." in fs.filename else "txt")
    _LIBRARY.write_synonyms(ext, data)
    _GLOB["sig"] = None  # force reload on next session
    return jsonify({"ok": True, "aliases": onto.size, "concepts": len(onto.groups())})
