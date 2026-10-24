"""Render DataFrames/Series as small HTML tables for Anki cards (inline styles only, readable in light and dark mode).

Highlights: new columns (blue tint), cells changed vs. the input table (amber tint), missing values (red tint).
"""
import html

import pandas as pd

CELL = "border:1px solid rgba(128,128,128,.55); padding:3px 9px; text-align:right"
HEAD = CELL + "; text-align:center; background:rgba(128,128,128,.16); font-weight:bold"
IDX = CELL + "; text-align:left; font-weight:bold; opacity:.7"
NEW = "; background:rgba(59,130,246,.22)"
CHG = "; background:rgba(245,158,11,.28)"
NA = "; background:rgba(239,68,68,.20)"
TABLE = "border-collapse:collapse; font-family:Menlo,Consolas,monospace; font-size:14px; margin:2px auto"
WRAP = "display:inline-block; vertical-align:top; margin:6px 10px; text-align:center"
CAP = "font-family:Menlo,Consolas,monospace; font-size:13px; opacity:.75; margin-bottom:2px"


def _fmt_col(s):
    """Format a column roughly the way pandas prints it (floats share one number of decimals)."""
    if pd.api.types.is_float_dtype(s):
        vals = s.dropna()
        dec = 1
        for v in vals:
            t = f"{v:.4f}".rstrip("0")
            dec = max(dec, len(t.split(".")[1]) if "." in t else 0)
        return [("NaN" if pd.isna(v) else f"{v:.{dec}f}") for v in s]
    return [("NaN" if (not isinstance(v, str) and pd.isna(v)) else str(v)) for v in s]


def _as_frame(obj):
    if isinstance(obj, pd.Series):
        return obj.to_frame(name=obj.name if obj.name is not None else "")
    return obj


def table(obj, caption=None, base=None, show_index=True):
    """base: the input DataFrame to compare against (new columns and changed cells get highlighted)."""
    df = _as_frame(obj)
    base = _as_frame(base) if base is not None else None
    nlev = df.index.nlevels
    idx_names = [n for n in df.index.names]
    rows = []

    # header row(s): pivot-style frames have a columns name and an index name, shown on two rows like pandas
    named_idx = any(n is not None for n in idx_names)
    two_rows = named_idx and df.columns.name is not None
    # pivot-style frames name both axes: pandas prints the columns name in the corner and the index name below
    corner = df.columns.name if df.columns.name is not None else " / ".join(str(n or "") for n in idx_names)
    head = "<tr>"
    if show_index:
        head += f'<th style="{HEAD}" colspan="{nlev}">{html.escape(str(corner))}</th>'
    for c in df.columns:
        new = base is not None and c not in base.columns
        head += f'<th style="{HEAD}{NEW if new else ""}">{html.escape(str(c))}</th>'
    rows.append(head + "</tr>")
    if show_index and two_rows:
        r = "<tr>" + "".join(f'<th style="{HEAD}">{html.escape(str(n or ""))}</th>' for n in idx_names)
        r += f'<th style="{HEAD}" colspan="{len(df.columns)}"></th></tr>'
        rows.append(r)

    cols = {c: _fmt_col(df[c]) for c in df.columns}
    for i, ix in enumerate(df.index):
        r = "<tr>"
        if show_index:
            for part in (ix if nlev > 1 else (ix,)):
                r += f'<td style="{IDX}">{html.escape(str(part))}</td>'
        for c in df.columns:
            text = cols[c][i]
            style = CELL
            if text == "NaN":
                style += NA
            elif base is not None and c not in base.columns:
                style += NEW
            elif base is not None and ix in base.index and c in base.columns:
                old = base.at[ix, c]
                if not (pd.isna(old) and text == "NaN") and str(old) != str(df.at[ix, c]) and \
                        not (isinstance(old, float) and old == df.at[ix, c]):
                    style += CHG
            r += f'<td style="{style}">{html.escape(text)}</td>'
        rows.append(r + "</tr>")
    cap = f'<div style="{CAP}">{html.escape(caption)}</div>' if caption else ""
    return f'<div style="{WRAP}">{cap}<table style="{TABLE}">{"".join(rows)}</table></div>'


def scalar(val, caption=None):
    cap = f'<div style="{CAP}">{html.escape(caption)}</div>' if caption else ""
    return f'<div style="{WRAP}">{cap}<code style="font-size:15px">{html.escape(str(val))}</code></div>'


LEGEND = ('<div style="font-size:12px; opacity:.8; margin-top:6px">'
          '<span style="padding:0 6px; background:rgba(59,130,246,.22)">new column</span> '
          '<span style="padding:0 6px; background:rgba(245,158,11,.28)">changed cell</span> '
          '<span style="padding:0 6px; background:rgba(239,68,68,.20)">NaN</span></div>')
