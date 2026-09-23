#!/usr/bin/env python3
"""Flip a demo org between empty and fully loaded, so the before/after can be shown live.

    python tools/demo_reset.py empty    # clear the sales -> market layer only, all Prospect
    python tools/demo_reset.py full     # reload 17 months of Dragonfly -> 214 doors

An upload REPLACES a company's sales, so never point this at a real tenant.
"""
import io, json, os, sys, time, urllib.request

ORG_EMAIL = 'david+bdsatest@canismajorpartners.com'
ORG_CODE = 'DFWDP6R8'
API = 'https://jbd-sales-bot-ltqqg7poja-uc.a.run.app'
CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'data', 'dragonfly_demo.csv')


def call(method, body):
    req = urllib.request.Request(API + '/org/sales', data=json.dumps(body).encode(),
                                 method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode())


mode = (sys.argv[1] if len(sys.argv) > 1 else 'full').lower()
creds = {'email': ORG_EMAIL, 'code': ORG_CODE}
if mode == 'empty':
    print('cleared ->', call('DELETE', creds))
else:
    body = dict(creds, filename='dragonfly_full_17mo.csv',
                csv=io.open(CSV, encoding='utf-8').read())
    t = time.time()
    j = call('POST', body)
    print('loaded %s rows -> %s doors in %.0fs' % (j['rows'], j['matched'], time.time() - t))
