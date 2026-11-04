"""Unblock a pin whose label never landed: delete the unnamed stub wire sitting on it, then reconnect the part.

usage: python fix_pin.py <spec.json> <REF> <PIN>
Symptom: verify says `REF.PIN expected NET got {''}` and `drv.py connect` reports `pin already connected to net
"$1N.."` / `stub touches an existing (foreign-net) wire`. The first connect drew the stub but not the label.
Reads the saved page (folder project .esch2, or cut out of an .epro2 export), deletes wires with an empty NET that
end on the pin, reconnects REF with drv.py, saves.
"""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import common as K

spec_path, ref, pin = sys.argv[1], sys.argv[2], sys.argv[3]
spec = json.load(open(spec_path, encoding='utf-8'))
B = K.board()
page = spec['page']
c = next(c for c in K.run(['sch', 'list', '--page', page, '--stay', '--include-pins'], doc=False)['result']['components']
         if c.get('designator') == ref)
pp = next(p for p in c['pins'] if p['pinNumber'] == pin)
px, py = round(pp['x']), round(pp['y'])

work = os.path.join(os.path.dirname(os.path.abspath(spec_path)), 'work'); os.makedirs(work, exist_ok=True)
arc = os.path.join(work, '_project.epro2')
if not K.export_project(arc).get('ok'): raise SystemExit('project export failed')
txt = K.page_text_from_epro2(arc, page)
merged = {}
for line in txt.splitlines():
    if not line.startswith('{'): continue
    hs, rest = line.split('||', 1); h = json.loads(hs); b = json.loads(rest.rstrip('|') or '{}')
    merged.setdefault((h['type'], h.get('id')), {}).update({k: v for k, v in b.items() if v is not None})
net = {b.get('parentId'): b.get('value') for (t, i), b in merged.items() if t == 'ATTR' and b.get('key') == 'NET'}
stubs = set()
for (t, i), b in merged.items():
    if t != 'LINE' or 'startX' not in b: continue
    ends = [(round(b['startX']), round(b['startY'])), (round(b['endX']), round(b['endY']))]
    if any(e in ((px, py), (px, -py)) for e in ends) and not net.get(b.get('lineGroup')):
        stubs.add(b.get('lineGroup'))
print(f'{ref}.{pin} at ({px},{py}): unnamed stub wires {sorted(stubs) or "none"}')
if stubs:
    print('delete', K.run(['sch', 'prim-delete', '--ids', ','.join(sorted(stubs))], doc=page).get('ok'))
p = subprocess.run([sys.executable, os.path.join(HERE, 'drv.py'), spec_path, 'connect', ref], capture_output=True,
                   text=True, encoding='utf-8', errors='replace')
print((p.stdout + p.stderr).strip()[:400])
print('save', K.run(['sch', 'save'], doc=page).get('ok'), '- now rerun build_pages.py on this spec to verify')
