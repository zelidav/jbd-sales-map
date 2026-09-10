#!/usr/bin/env python3
"""Fold a Pistil Next pull into index.html, keeping what the new pull can no longer see.

The subscription now covers Flower, Prerolls and Vapes only. That makes the new pull
CURRENT but NARROWER than the one baked into the map, so this is a merge, not a replace:

  refreshed  store rank, volume, momentum, quality, decile  (the 3 live categories)
             per-door category figures for flower / joint / vape
             brand carriage for brands whose presence really is in those categories
  carried    per-door figures for the 11 categories the subscription lost
             carriage for brands that mostly sell outside the new scope -- Wyld reads
             3 doors in a Flower/Preroll/Vape cut and 436 in the real world, and
             writing 3 over 436 would be a lie dressed as a refresh

Momentum now comes from the API as __delta_pct. The old pipeline differenced nested
windows to get it and that is what produced the +34% "market growth" artifact; nothing
here differences anything.

Usage: python tools/build_from_next.py [pullDir]
"""
import json, io, os, re, sys, statistics, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, 'index.html')
PULL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get('USERPROFILE', '.'), 'Downloads', 'pistil-next')

D_ = 'sales_estimates.'
L_ = 'listings.'
CATMAP = {'Flower': 'flower', 'Prerolls': 'joint', 'Vapes': 'vape'}
STOP = set('the llc inc corp co ltd dispensary dispensaries cannabis weed nyc ny rec med '
           'adult use store shop company group holdings of and a an'.split())


def load(name):
    p = os.path.join(PULL, name)
    return json.load(io.open(p, encoding='utf-8')) if os.path.exists(p) else None


def squash(s):
    s = re.sub(r'\(.*?\)', ' ', str(s or '').lower())
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return ' '.join(t for t in s.split() if t and t not in STOP)


def build_index(doors):
    """Match keys for the map's doors: squashed name, and squashed name + city."""
    by_name, by_name_city = {}, {}
    for d in doors:
        k = squash(d['n'])
        if k:
            by_name.setdefault(k, []).append(d)
            by_name_city.setdefault((k, squash(d.get('c'))), []).append(d)
    return by_name, by_name_city


def strip_city(key, city):
    """Pistil Next names carry the location: "Farmers Choice - Fishkill". The map stores
    the city separately, so those tokens have to come out or nothing lines up."""
    ct = set(city.split())
    toks = [t for t in key.split() if t not in ct]
    return ' '.join(toks) or key


def match(rows, doors):
    """New pull rows -> map doors, most confident rule first."""
    by_name, by_name_city = build_index(doors)
    used = set()
    out, missed = {}, []

    def take(d, r):
        out[id(d)] = r
        used.add(id(d))

    # Two passes: settle every exact name+city pair before letting the looser rules
    # compete for what is left, so a fuzzy hit cannot steal a door from an exact one.
    pending = []
    for r in rows:
        nm = r.get(D_ + 'store_name') or ''
        city = squash(r.get(D_ + 'city'))
        key = strip_city(squash(nm), city)
        c = [d for d in by_name_city.get((key, city), []) if id(d) not in used]
        if len(c) > 1:
            # The map has near-duplicates ("Lenox Hill" and "Lenox Hill Cannabis Co."
            # both in New York). Prefer the one whose name matches exactly.
            exact = [d for d in c if squash(d['n']) == key]
            if len(exact) == 1:
                c = exact
        if len(c) == 1:
            take(c[0], r)
        else:
            pending.append((r, nm, key, city))

    for r, nm, key, city in pending:
        cands = [d for d in by_name.get(key, []) if id(d) not in used]
        if len(cands) == 1:
            take(cands[0], r); continue
        # Same city, one name containing the other.
        cands = [d for d in doors if id(d) not in used and squash(d.get('c')) == city and key
                 and (key in strip_city(squash(d['n']), city) or strip_city(squash(d['n']), city) in key)]
        if len(cands) == 1:
            take(cands[0], r); continue
        # Token overlap, same city. Chains differ by city, so city equality is the guard
        # that stops one location absorbing another's numbers.
        kt = set(key.split())
        if kt:
            scored = []
            for d in doors:
                if id(d) in used or squash(d.get('c')) != city:
                    continue
                dt = set(strip_city(squash(d['n']), city).split())
                if not dt:
                    continue
                j = len(kt & dt) / len(kt | dt)
                if j >= 0.6:
                    scored.append((j, d))
            scored.sort(key=lambda x: -x[0])
            if len(scored) == 1 or (len(scored) > 1 and scored[0][0] > scored[1][0]):
                take(scored[0][1], r); continue
        missed.append(nm)
    return out, missed


def tiers(values):
    """Value / Mid / Prem by terciles of average price, the same shape the map expects."""
    v = sorted(x for x in values if x > 0)
    if len(v) < 3:
        return (0, 0)
    return (v[len(v) // 3], v[2 * len(v) // 3])


def main():
    src = io.open(HTML, encoding='utf-8').read()
    i = src.index('var DATA='); j = src.index('\n', i)
    doors = json.loads(src[i + len('var DATA='):j].rstrip(';'))

    stores = load('ny_stores_30d.json')
    if not stores:
        sys.exit(f'no ny_stores_30d.json in {PULL}')
    hit, missed = match(stores, doors)
    print(f'store rows {len(stores)} -> matched {len(hit)} map doors, {len(missed)} unmatched')

    # ---- store-level: rank, volume, momentum, quality, decile ----------------
    vols = [r.get(D_ + 'sum_sale_dollars') or 0 for r in stores]
    moms = [r.get(D_ + 'sum_sale_dollars__delta_pct') or 0 for r in stores]
    med_mom = statistics.median(moms) if moms else 0
    order = sorted(stores, key=lambda r: -(r.get(D_ + 'sum_sale_dollars') or 0))
    rank = {id(r): n + 1 for n, r in enumerate(order)}
    vsort = sorted(vols, reverse=True)

    def decile(v):
        if v <= 0:
            return None
        pos = next((k for k, x in enumerate(vsort) if x <= v), len(vsort) - 1)
        return max(1, min(10, int(pos / max(1, len(vsort)) * 10) + 1))

    refreshed = stale = 0
    for d in doors:
        r = hit.get(id(d))
        if not r:
            # The pull is the market as it stands. A door it does not mention has either
            # closed, stopped selling these categories, or changed name -- and in every
            # case last month's rank shown beside this month's is a wrong number, not an
            # old one. Clear the ranked fields; the door stays on the map, unranked.
            for f in ('psr', 'svol', 'svol30', 'svol90', 'svol180', 'sunits',
                      'mom', 'momr', 'dec', 'qs', 'qt', 'trend'):
                d.pop(f, None)
            stale += 1
            continue
        v = r.get(D_ + 'sum_sale_dollars') or 0
        d['psr'] = rank[id(r)]
        d['svol'] = int(v)
        d['svol30'] = int(v)
        d['sunits'] = int(r.get(D_ + 'sum_units_sold') or 0)
        d['mom'] = round(r.get(D_ + 'sum_sale_dollars__delta_pct') or 0, 1)
        d['momr'] = round(d['mom'] - med_mom, 1)
        d['dec'] = decile(v)
        refreshed += 1
    print(f'refreshed rank/volume/momentum on {refreshed} doors '
          f'(market median momentum {med_mom:+.1f}%)')
    print(f'cleared the ranked fields on {stale} doors the pull does not mention '
          f'(closed, out of these categories, or renamed) — they stay on the map, unranked')

    # ---- per-door category figures for the three live categories ------------
    for cat, key in CATMAP.items():
        rows = load(f'ny_stores_cat_{cat}.json')
        if not rows:
            print(f'  no per-store cut for {cat}; its old figures stay')
            continue
        chit, _ = match(rows, doors)
        prices = []
        for r in rows:
            u = r.get(D_ + 'sum_units_sold') or 0
            if u:
                prices.append((r.get(D_ + 'sum_sale_dollars') or 0) / u)
        lo, hi = tiers(prices)
        n = 0
        for d in doors:
            r = chit.get(id(d))
            if not r:
                # No row in this category means the door does not sell it NOW; drop a
                # stale entry rather than leave last month's figure looking current.
                if d.get('cat'):
                    d['cat'].pop(key, None)
                continue
            v = r.get(D_ + 'sum_sale_dollars') or 0
            u = r.get(D_ + 'sum_units_sold') or 0
            p = (v / u) if u else 0
            d.setdefault('cat', {})[key] = {
                'v': int(v), 'u': int(u), 'p': round(p, 2),
                't': 'Prem' if p >= hi else ('Value' if p <= lo else 'Mid'),
            }
            n += 1
        print(f'  {cat:9} -> {n} doors  (Value <= ${lo:.2f} < Mid < ${hi:.2f} <= Prem)')

    # ---- customer quality: volume 50%, price 30%, momentum 20% --------------
    scored = [d for d in doors if d.get('svol')]
    def pct_rank(vals):
        s = sorted(vals)
        return lambda x: (sum(1 for y in s if y < x) / max(1, len(s)))
    pv = pct_rank([d['svol'] for d in scored])
    pp = pct_rank([max((c.get('p', 0) for c in (d.get('cat') or {}).values()), default=0) for d in scored])
    pm = pct_rank([d.get('momr') or 0 for d in scored])
    for d in scored:
        price = max((c.get('p', 0) for c in (d.get('cat') or {}).values()), default=0)
        q = 50 * pv(d['svol']) + 30 * pp(price) + 20 * pm(d.get('momr') or 0)
        d['qs'] = int(round(q))
        d['qt'] = 'H' if q >= 66 else ('M' if q >= 33 else 'L')

    # ---- brand carriage ------------------------------------------------------
    car = load('ny_carriage_all.json') or {}
    i2 = src.index('var BRANDS='); j2 = src.index('\n', i2)
    old_brands = {b['n']: b for b in json.loads(src[i2 + len('var BRANDS='):j2].rstrip(';'))}

    kept, took = [], []
    for brand, rows in car.items():
        old_doors = old_brands.get(brand, {}).get('d', 0)
        # A brand that sells mostly outside the new scope reads tiny here. Keeping the
        # old figure is stale; writing the new one is wrong.
        if old_doors and len(rows) < 0.6 * old_doors:
            kept.append(brand)
            continue
        took.append(brand)
        bhit, _ = match(rows, doors)
        for d in doors:
            if d.get('br'):
                d['br'].pop(brand, None)
        for d in doors:
            r = bhit.get(id(d))
            if not r:
                continue
            v = r.get(D_ + 'sum_sale_dollars') or 0
            u = r.get(D_ + 'sum_units_sold') or 0
            d.setdefault('br', {})[brand] = [int(v), int(u), round((v / u) if u else 0, 2)]
        tot = sum((r.get(D_ + 'sum_sale_dollars') or 0) for r in rows)
        old_brands[brand] = {'n': brand, 'v': int(tot), 'd': len(rows)}
    print(f'carriage refreshed for {len(took)} brands; {len(kept)} kept from the previous '
          f'pull because the new cut only sees part of them: {", ".join(sorted(kept))}')

    brands = sorted(old_brands.values(), key=lambda b: -b['v'])

    # ---- write back ----------------------------------------------------------
    today = datetime.date.today().isoformat()
    src = src[:i] + 'var DATA=' + json.dumps(doors, ensure_ascii=False) + ';' + src[j:]
    i2 = src.index('var BRANDS='); j2 = src.index('\n', i2)
    src = src[:i2] + 'var BRANDS=' + json.dumps(brands, ensure_ascii=False) + ';' + src[j2:]

    prov = ('var DATASRC=' + json.dumps({
        'pulled': today,
        'live': ['flower', 'joint', 'vape'],
        'carried': ['baked', 'beverage', 'candy', 'chocolate', 'concentrate', 'cooking',
                    'edible', 'pill', 'tincture', 'topical', 'wellness'],
        'carriedBrands': sorted(kept),
        'note': 'Store rank, volume and momentum are current and cover Flower, Prerolls and '
                'Vapes. Figures for other categories are carried from the previous pull.',
    }, ensure_ascii=False) + ';')
    if 'var DATASRC=' in src:
        a = src.index('var DATASRC='); b = src.index('\n', a)
        src = src[:a] + prov + src[b:]
    else:
        anchor = 'var DATA='
        src = src.replace(anchor, prov + '\n' + anchor, 1)

    io.open(HTML, 'w', encoding='utf-8', newline='').write(src)
    print(f'\nwrote {HTML}')
    if missed[:8]:
        print('unmatched store names (sample):', '; '.join(missed[:8]))


if __name__ == '__main__':
    main()
