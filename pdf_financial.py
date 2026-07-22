"""Reconstruct financial tables from a PDF page using word geometry.

Generic table detection fails on bank/statement PDFs: the tables are ruled by
alignment, not borders, headers span multiple lines, the first column is an
unlabelled row-label ("key"), and footnote superscripts sit between columns.

This extractor instead:
  * clusters words into lines by their vertical position;
  * finds the value columns by clustering the right edges of numeric words that
    repeat down the page (so a stray "1" in "Common Equity Tier 1" is ignored);
  * treats everything left of the first value column as the row label (key);
  * merges multi-line period headers into clean column names;
  * drops footnote superscripts by font size.

Output is a clean grid: [label, col1, col2, ...] with one row per line item —
exactly the key/value shape reconciliation needs.
"""
from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

# a token that is a financial value: 1,234 / (1,234.5) / 12.3 / 45% / (7)bps / nm / -
_NUM = re.compile(r"^\(?-?[\d,]*\.?\d+\)?(%|bps)?$")
_PLACEHOLDER = {"-", "–", "—", "nm", "n/a"}
_UNIT = re.compile(r"^\$?m?illion$|^\$m$|^%$|^\$million$", re.I)


def _is_value(text: str) -> bool:
    t = text.strip()
    return t.lower() in _PLACEHOLDER or bool(_NUM.match(t))


def _cluster_lines(words: List[dict], tol: float = 3.0) -> List[List[dict]]:
    lines: List[List[dict]] = []
    for w in sorted(words, key=lambda x: (round(x["top"] / tol), x["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda x: x["x0"])
    return lines


def _cluster_columns(words: List[dict], page_width: float, n_rows: int
                     ) -> List[Tuple[float, float]]:
    """Cluster right-edges (x1) of numeric words into value columns."""
    nums = sorted((w for w in words if _is_value(w["text"])), key=lambda x: x["x1"])
    if not nums:
        return []
    clusters: List[List[dict]] = [[nums[0]]]
    for w in nums[1:]:
        if w["x1"] - clusters[-1][-1]["x1"] <= 16:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    cols: List[Tuple[float, float]] = []
    min_members = max(2, int(0.35 * n_rows))
    for c in clusters:
        mean_x1 = sum(w["x1"] for w in c) / len(c)
        if len(c) >= min_members and mean_x1 > 0.25 * page_width:
            x0 = min(w["x0"] for w in c)
            x1 = max(w["x1"] for w in c)
            cols.append((x0, x1))
    return cols


def _assign(line: List[dict], cols: List[Tuple[float, float]], median_size: float
            ) -> Tuple[str, List[str], bool]:
    """Split a line into (label, [values per column], has_any_value)."""
    first_col_left = cols[0][0]
    label_words, col_words = [], {i: [] for i in range(len(cols))}
    for w in line:
        cx = (w["x0"] + w["x1"]) / 2
        placed = False
        for i, (x0, x1) in enumerate(cols):
            if x0 - 12 <= cx <= x1 + 12:
                col_words[i].append(w)
                placed = True
                break
        if not placed and w["x1"] < first_col_left - 4:
            # drop footnote superscripts: tiny numeric words in the label gap
            if _is_value(w["text"]) and w.get("size", median_size) < median_size - 1.2:
                continue
            label_words.append(w)
    values, any_val = [], False
    for i in range(len(cols)):
        cell = " ".join(w["text"] for w in sorted(col_words[i], key=lambda x: x["x0"]))
        cell = cell.strip()
        if cell:
            any_val = True
        values.append(cell)
    label = " ".join(w["text"] for w in label_words).strip()
    label = re.sub(r"\s+", " ", label)
    return label, values, any_val


def _clean_header(s: str) -> str:
    s = re.sub(r"[\u00b9\u00b2\u00b3\u2070-\u2079]", "", s)   # superscripts
    s = re.sub(r"\s+\d{1,2}$", "", s)                         # trailing footnote marker
    return re.sub(r"\s+", " ", s).strip()


def _mostly_text(cells: List[str]) -> bool:
    filled = [c for c in cells if c]
    if not filled:
        return False
    texty = sum(1 for c in filled if not _is_value(c.split()[0])) if filled else 0
    return texty >= max(1, len(filled) / 2)


_YEARISH = re.compile(r"^((19|20)\d{2}[A-Za-z]?|[Qq][1-4]'?\d{0,4}|FY'?\d{2,4}|H[12]'?\d{0,4})$")


def _is_period_row(label: str, values: List[str]) -> bool:
    """A sub-header row: no row label, and every cell is a year/period token."""
    filled = [v for v in values if v]
    if len(filled) < 2 or not all(_YEARISH.match(v.replace(" ", "")) for v in filled):
        return False
    # the label cell may hold a unit caption ("USDm", "ZAR millions", "$m")
    return (not label) or (len(label.split()) <= 3 and not re.search(r"\d", label))


def _header_names_from(header_lines: List[List[dict]],
                       cols: List[Tuple[float, float]]) -> List[str]:
    per_col: List[List[str]] = [[] for _ in cols]
    for ln in header_lines:
        words = [w for w in sorted(ln, key=lambda x: x["x0"])
                 if not _UNIT.match(w["text"])
                 and (not _is_value(w["text"]) or _YEARISH.match(w["text"].strip()))]
        if not words:
            continue
        # direct hits: a word whose centre sits inside the column band
        hits: List[List[str]] = [[] for _ in cols]
        for w in words:
            cx = (w["x0"] + w["x1"]) / 2
            for i, (x0, x1) in enumerate(cols):
                if x0 - 16 <= cx <= x1 + 16:
                    hits[i].append(w["text"])
        # spanning tier (e.g. "Actual" over 2 cols, "Baseline" over 4): spread
        # only words that already claimed a column, so left-margin headings and
        # section titles never leak into column names.
        claimed = [w for w in words
                   if any(x0 - 16 <= (w["x0"] + w["x1"]) / 2 <= x1 + 16
                          for x0, x1 in cols)]
        if claimed and len(claimed) < len(cols):
            for i, (x0, x1) in enumerate(cols):
                if hits[i]:
                    continue
                mid = (x0 + x1) / 2
                nearest = min(claimed, key=lambda w: abs((w["x0"] + w["x1"]) / 2 - mid))
                hits[i].append(nearest["text"])
        for i, texts in enumerate(hits):
            if texts:
                per_col[i].append(" ".join(texts))

    names = [_clean_header(" ".join(parts)) for parts in per_col]
    seen: Dict[str, int] = {}
    out = []
    for n in names:
        n = n or "value"
        if n in seen:
            seen[n] += 1; out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 0; out.append(n)
    return out


def _find_title(lines: List[List[dict]], block_top: float, page_width: float,
                median_size: float, skip_tops=None) -> Optional[str]:
    """Nearest heading above a table block (short, larger/bold, non-tabular)."""
    skip = skip_tops or set()
    best, best_score = None, 0.0
    for ln in lines:
        top = ln[0]["top"]
        if round(top) in skip:
            continue
        if top >= block_top - 1 or block_top - top > 95:
            continue
        if any(_is_value(w["text"]) and (w["x0"] + w["x1"]) / 2 > 0.4 * page_width
               for w in ln):
            continue
        text = " ".join(w["text"] for w in sorted(ln, key=lambda x: x["x0"])).strip()
        if not text or len(text.split()) > 9:
            continue
        size = max(w.get("size", median_size) for w in ln)
        bold = any("bold" in str(w.get("fontname", "")).lower() for w in ln)
        if size <= median_size * 1.05 and not bold:
            continue
        score = size + (2 if bold else 0) - (block_top - top) * 0.02
        if score > best_score:
            best, best_score = text, score
    return re.sub(r"\s+continued$", "", best).strip() if best else None


def _is_wrap_of(prev_row: Dict[str, str], col_names: List[str],
                values: List[str]) -> bool:
    """True if `values` are the tail of numbers that wrapped from `prev_row`.

    A PDF may break "-15,280" across two lines, leaving "-" on the first and
    "15,280" on the second. The continuation has no row label and only fills
    columns whose previous cell is a bare sign or empty.
    """
    filled = [(n, v) for n, v in zip(col_names, values) if v]
    if not filled:
        return False
    for name, _v in filled:
        prev = (prev_row.get(name) or "").strip()
        if prev not in ("-", "\u2013", "\u2014", "+", ""):
            return False
    return True


def extract_financial_tables(page, label_header: str = "Line item"
                             ) -> List[Dict[str, Any]]:
    """Return a list of clean tables: {name, title, columns, rows(list[dict])}."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False,
                               extra_attrs=["size", "fontname"])
    if not words:
        return []
    median_size = median([w.get("size", 10) for w in words]) or 10
    lines = _cluster_lines(words)

    def is_tabular(ln):
        return any(_is_value(w["text"]) and (w["x0"] + w["x1"]) / 2 > 0.25 * page.width
                   for w in ln)

    tops = sorted({round(l[0]["top"]) for l in lines})
    diffs = [b - a for a, b in zip(tops, tops[1:]) if b > a]
    line_gap = median(diffs) if diffs else 14.0

    def is_label_wrap(ln, prev_top):
        """A wrapped row label: body text in the left label region, set at the
        normal line spacing directly under the row it belongs to.

        The spacing test is what separates a wrapped cell from a section
        heading (weight and size are unreliable — many statements set their
        row labels in bold at body size).
        """
        if any((w["x0"] + w["x1"]) / 2 > 0.25 * page.width for w in ln):
            return False
        size = max(w.get("size", median_size) for w in ln)
        if size > median_size * 1.05:
            return False          # a heading
        if size < median_size * 0.95:
            return False          # footnote / small print, not a table cell
        return (ln[0]["top"] - prev_top) <= line_gap * 1.6

    blocks: List[List[List[dict]]] = []
    cur: List[List[dict]] = []
    gap = 0
    prev_top = None
    for ln in lines:
        if is_tabular(ln):
            cur.append(ln); gap = 0; prev_top = ln[0]["top"]
        elif cur and prev_top is not None and is_label_wrap(ln, prev_top):
            cur.append(ln); gap = 0; prev_top = ln[0]["top"]
        else:
            gap += 1
            if cur and gap > 1:
                blocks.append(cur); cur = []; prev_top = None
    if cur:
        blocks.append(cur)


    tables = []
    for b_idx, block in enumerate(blocks):
        block_words = [w for ln in block for w in ln]
        cols = _cluster_columns(block_words, page.width, len(block))
        if not cols:
            continue

        # split leading header-like lines (period labels) from data rows
        assigned = [(ln, _assign(ln, cols, median_size)) for ln in block]
        data_start = 0
        for ln, (label, values, _any) in assigned:
            if (_mostly_text(values) and not label) or _is_period_row(label, values):
                data_start += 1
            else:
                break
        header_in_block = [ln for ln, _ in assigned[:data_start]]
        # climb upward for header lines; stop at a big gap or a prose line
        btop = block[0][0]["top"]
        above: List[List[dict]] = []
        for ln in reversed([l for l in lines if l[0]["top"] < btop - 1]):
            if btop - ln[0]["top"] > 62:
                break
            aligned = sum(1 for w in ln if any(x0 - 16 <= (w["x0"] + w["x1"]) / 2 <= x1 + 16
                                               for x0, x1 in cols))
            if len(ln) > 8 and aligned < len(ln) / 2:
                break  # reached a paragraph / section title
            above.append(ln)
        above = [] if header_in_block else list(reversed(above))[-4:]
        header_lines = above + header_in_block
        col_names = _header_names_from(header_lines, cols)
        columns = [label_header] + col_names
        # a line only counts as "header" (and so is barred from being the title)
        # if it actually sits over the value columns
        header_tops = {round(l[0]["top"]) for l in header_lines
                       if any(x0 - 16 <= (w["x0"] + w["x1"]) / 2 <= x1 + 16
                              for w in l for x0, x1 in cols)}

        rows: List[Dict[str, str]] = []
        for ln, (label, values, any_val) in assigned[data_start:]:
            if not label and not any_val:
                continue
            if re.fullmatch(r"\d{1,3}", label) and not any_val:
                continue  # page-number / stray footer
            if rows and not label and any_val and _is_wrap_of(rows[-1], col_names, values):
                prev = rows[-1]
                for name, val in zip(col_names, values):
                    if val:
                        prev[name] = (prev.get(name) or "") + val
                continue
            if not any_val and rows:
                # wrapped continuation of the previous row's label
                prev = rows[-1]
                prev[label_header] = f"{prev[label_header]} {label}".strip()
                continue
            row = {label_header: label}
            for name, val in zip(col_names, values):
                row[name] = val
            rows.append(row)

        if not rows:
            continue
        # reject prose blocks (sentences with inline numbers) vs real tables
        avg_label_words = sum(len(r[label_header].split()) for r in rows) / len(rows)
        cells = [v for r in rows for k, v in r.items() if k != label_header]
        density = (sum(1 for c in cells if c) / len(cells)) if cells else 0
        if avg_label_words > 7:
            continue
        if len(cols) >= 2 and density < 0.45:
            continue
        if len(cols) == 1 and (len(rows) < 4 or avg_label_words > 5):
            continue
        title = _find_title(lines, block[0][0]["top"], page.width, median_size,
                            skip_tops=header_tops)
        tables.append({"name": f"table_{b_idx + 1}", "title": title,
                       "columns": columns, "rows": rows})
    return tables
