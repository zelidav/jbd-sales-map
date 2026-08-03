"""Verify a Pistil export: exact window length, rows, total, top store."""
import sys, os, glob, openpyxl


def window_days(vals):
    """Avg-per-day columns reveal the denominator = number of days in the window."""
    frac = [v for v in vals if isinstance(v, (int, float)) and v != int(v)]
    if not frac:
        return None
    for d in range(1, 200):
        ok = sum(1 for v in frac if abs(v * d - round(v * d)) < 1e-3)
        if ok / len(frac) > 0.85:
            return d
    return None


def info(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h) if h is not None else '' for h in rows[0]]
    body = [r for r in rows[1:] if r and r[0] is not None]
    vi = next((i for i, h in enumerate(hdr) if 'Sales Volume' in h), None)
    ai = next((i for i, h in enumerate(hdr) if h.startswith('Avg #')), None)
    tot = sum((r[vi] or 0) for r in body) if vi is not None else 0
    days = window_days([r[ai] for r in body[:80]]) if ai is not None else None
    return {
        'file': os.path.basename(path), 'sheet': wb.sheetnames[0], 'rows': len(body),
        'days': days, 'total': tot, 'top': body[0][1] if body else None,
        'perday': (tot / days) if days else None,
    }


if __name__ == '__main__':
    args = sys.argv[1:] or sorted(glob.glob(os.path.expanduser('~/Downloads/PULL_*.xlsx')))
    for p in args:
        try:
            d = info(p)
            pd = f"${d['perday']:>11,.0f}/day" if d['perday'] else 'perday=?'
            print(f"{d['file']:44} {str(d['days']):>4}d rows={d['rows']:>4} "
                  f"total=${d['total']:>14,.0f} {pd}  top={d['top']}")
        except Exception as e:
            print(f'{os.path.basename(p):44} ERR {e}')
