"""Diagnose PDF extraction differences between environments.

Run this INSIDE Dataiku (notebook, or a Python recipe in the same code env as
the webapp) and send me the whole output.

    python diagnose_env.py /path/to/testexcel2.pdf

In a notebook you can instead do:

    import diagnose_env; diagnose_env.run("/path/to/testexcel2.pdf")
"""
from __future__ import annotations

import sys


def run(pdf_path: str | None = None) -> None:
    print("=" * 68)
    print("1. WHICH BUILD IS LOADED")
    print("=" * 68)
    try:
        import financial_reconciliation.extraction.pdf_financial as m
        print("module file :", m.__file__)
        markers = {
            "_is_wrap_of (split-negative fix)": hasattr(m, "_is_wrap_of"),
            "_is_period_row (year-row header)": hasattr(m, "_is_period_row"),
            "_YEARISH (period token regex)": hasattr(m, "_YEARISH"),
        }
        for name, present in markers.items():
            print(f"  {'OK ' if present else 'MISSING'} {name}")
        print("BUILD:", "NEW" if all(markers.values()) else "OLD  <-- library not updated")
        # column threshold actually in use
        import inspect
        src = inspect.getsource(m)
        print("column threshold in source:",
              "0.25 (new)" if "0.25 * page_width" in src else "0.40 (old)")
    except Exception as exc:  # noqa: BLE001
        print("could not import engine:", exc)
        return

    print()
    print("=" * 68)
    print("2. LIBRARY VERSIONS")
    print("=" * 68)
    for mod in ("pdfplumber", "pdfminer", "openpyxl", "pandas", "docx"):
        try:
            lib = __import__(mod)
            print(f"  {mod:12s}", getattr(lib, "__version__", "(no __version__)"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {mod:12s} MISSING ({exc})")

    if not pdf_path:
        print("\n(no pdf given — pass a path to also dump word geometry)")
        return

    print()
    print("=" * 68)
    print("3. RAW WORD GEOMETRY (first 10 lines of page 1)")
    print("=" * 68)
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        print("page width:", round(page.width), " height:", round(page.height))
        words = page.extract_words(extra_attrs=["size", "fontname"])
        print("word count:", len(words))
        if words:
            w0 = words[0]
            print("attrs on first word:", sorted(w0.keys()))
            print("  has size:", "size" in w0, " has fontname:", "fontname" in w0)
        rows: dict = {}
        for w in words:
            rows.setdefault(round(w["top"]), []).append(
                (round(w["x0"]), round(w["x1"]), w["text"]))
        for t in sorted(rows)[:10]:
            line = " ".join(f"[{a}-{b}]{x}" for a, b, x in sorted(rows[t]))
            print(f"  top={t:4d} {line[:150]}")

    print()
    print("=" * 68)
    print("4. WHAT THE ENGINE EXTRACTS")
    print("=" * 68)
    from financial_reconciliation import api
    with open(pdf_path, "rb") as f:
        ext = api.extract_tables("diag.pdf", f.read())
    print("tables:", len(ext.document.tables))
    for t in ext.document.tables[:3]:
        print(f"\n  name: {t.name}  rows: {t.row_count}")
        print("  columns:", t.columns)
        for r in t.records[:4]:
            print("   ", [r.values.get(c) for c in t.columns])
    for w in ext.warnings[:5]:
        print("  warning:", w.code.value, "-", w.message[:70])


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
