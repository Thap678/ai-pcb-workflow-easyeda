"""Write a route_plane.py / plane_fixup.py result into the PCB (split-plane boards).

usage: python plane_apply.py <live dump.json> <routes.json> [--dry-run] [--no-backup]
  1. backs the project up (kit/backup.py) unless --no-backup
  2. deletes the board's tracks, board vias (footprint vias stay: their ids are longer than 16), pours and regions
  3. draws tracks (collinear grid points merged first: ~13k -> ~2k segments) and vias in JS batches
  4. pours: ground on top (priority 99, so the power pours win), on the ground layer and on the bottom; one pour
     per plane region on the plane layer; the router's top power pours (priority 5)
  5. keepout regions (no wires / pours / fills) under the keepout parts on top, bottom and the plane layer
  6. saves. It does NOT rebuild the pours: one pour-rebuild of all pours times out on the connector - run
     plane_drc.py next (reload, rebuild net by net, DRC).
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import common as K  # noqa: E402
from plane_board import PlaneBoard  # noqa: E402

MIL = 1 / 0.0254
dump_path, routes_path = sys.argv[1], sys.argv[2]
dry, no_backup = '--dry-run' in sys.argv, '--no-backup' in sys.argv
B = PlaneBoard(dump_path)
R = json.load(open(routes_path, encoding='utf-8'))
d, run, GND = B.d, K.run, B.gnd
via_d, via_h = B.cfg.rules.get('via_d_mm', 0.6) * MIL, B.cfg.rules.get('via_hole_mm', 0.3) * MIL


def js(code):
    r = run(['debug', 'exec', '--code', code])
    if not r.get('ok'):
        raise SystemExit('JS failed: ' + str(r)[:300])
    return (r.get('result') or {}).get('value')


def pts_mil(coords):
    out = []
    for x, y in coords:
        p = [round(x * MIL, 2), round(y * MIL, 2)]
        if not out or p != out[-1]: out.append(p)
    if out[0] == out[-1]: out.pop()
    return out


def simplify(pts):
    """Drop points where the direction does not change (the router writes one point per 0.05 mm grid step)."""
    out = [pts[0]]
    for p in pts[1:]:
        if p == out[-1]: continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs((b[0] - a[0]) * (p[1] - b[1]) - (b[1] - a[1]) * (p[0] - b[0])) < 1e-6
                    and (b[0] - a[0]) * (p[0] - b[0]) + (b[1] - a[1]) * (p[1] - b[1]) > 0):
                out[-1] = p; continue
        out.append(p)
    return out


segs = []
for w in R['wires']:
    pts = simplify([tuple(p) for p in w['pts']])
    for a, b in zip(pts, pts[1:]):
        segs.append([w['net'], w['layer'], round(a[0] * MIL, 2), round(a[1] * MIL, 2), round(b[0] * MIL, 2), round(b[1] * MIL, 2), round(w['width'] * MIL, 3)])
vias = [[v['net'], round(v['x'] * MIL, 2), round(v['y'] * MIL, 2)] for v in R['vias']]
print(f'{len(segs)} segments, {len(vias)} vias, {len(R["pours"])} top power pours, {len(B.regions)} plane regions, '
      f'{len(B.keepout)} keepout parts; missing {R.get("missing")}, ground pads without via {R.get("gnd_fail")}')
if dry: raise SystemExit
if not no_backup:
    subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'backup.py'), 'before-plane-apply'], check=True)
cu = d['copper']
old_l = [l['primitiveId'] for l in cu['lines']]
old_v = [v['primitiveId'] for v in cu['vias'] if len(v['primitiveId']) == 16]
old_p = [p['primitiveId'] for p in cu['pours']]
for i in range(0, len(old_l), 400):
    js(f"return await eda.pcb_PrimitiveLine.delete({json.dumps(old_l[i:i + 400])});")
if old_v: js(f"return await eda.pcb_PrimitiveVia.delete({json.dumps(old_v)});")
if old_p: js(f"return await eda.pcb_PrimitivePour.delete({json.dumps(old_p)});")
rid = [x.get('primitiveId') for x in ((run(['pcb', 'region', 'list']).get('result') or {}).get('regions') or [])]
if rid: run(['pcb', 'region', 'delete', '--ids', ','.join(rid)])
print('cleared', len(old_l), 'tracks', len(old_v), 'vias', len(old_p), 'pours', len(rid), 'regions')
n = sum(js(f"const S={json.dumps(segs[i:i + 250])}; let k=0; for(const s of S){{ if(await eda.pcb_PrimitiveLine.create(s[0],s[1],s[2],s[3],s[4],s[5],s[6])) k++; }} return k;")
        for i in range(0, len(segs), 250))
print('tracks created', n, 'of', len(segs))
m = sum(js(f"const S={json.dumps(vias[i:i + 200])}; let k=0; for(const s of S){{ if(await eda.pcb_PrimitiveVia.create(s[0],s[1],s[2],{via_h:.3f},{via_d:.3f})) k++; }} return k;")
        for i in range(0, len(vias), 200))
print('vias created', m, 'of', len(vias))
outline = B.outline.buffer(-0.4, join_style=2)
full = pts_mil(outline.exterior.coords)
ok = []
for L, pri, name in [(1, 99, 'GND_TOP'), (B.ground_layer, 1, 'GND_INNER'), (2, 1, 'GND_BOT')]:
    ok.append(run(['pcb', 'pour', '--points', json.dumps(full), '--net', GND, '--layer', str(L), '--priority', str(pri), '--name', name], tries=1).get('ok'))
for net, poly in B.regions.items():
    for k, g in enumerate(getattr(poly.intersection(outline), 'geoms', [poly.intersection(outline)])):
        ok.append(run(['pcb', 'pour', '--points', json.dumps(pts_mil(g.exterior.coords)), '--net', net, '--layer', str(B.plane_layer),
                       '--priority', '1', '--name', f'{net}_PLANE_{k}'], tries=1).get('ok'))
for k, po in enumerate(R['pours']):
    ok.append(run(['pcb', 'pour', '--points', json.dumps(pts_mil(po['poly'])), '--net', po['net'], '--layer', '1',
                   '--priority', '5', '--name', f"P{k}_{po['name'].split('#')[1]}"], tries=1).get('ok'))
for ref, ko in B.keepout.items():
    for L in (1, 2, B.plane_layer):
        ok.append(run(['pcb', 'region', 'create', '--points', json.dumps(pts_mil(ko.exterior.coords)), '--layer', str(L),
                       '--rule', 'no-wires', '--rule', 'no-pours', '--rule', 'no-fills'], tries=1).get('ok'))
for pid, name, pri in js("return (await eda.pcb_PrimitivePour.getAll()).map(p=>[p.getState_PrimitiveId(),p.getState_PourName(),p.getState_PourPriority()]);"):
    want = 99 if name == 'GND_TOP' else (5 if name.startswith('P') else 1)
    if pri != want:
        js(f"await eda.pcb_PrimitivePour.modify('{pid}', {{pourPriority: {want}}}); return 1;")
print(f'pours / regions created {sum(1 for x in ok if x)} of {len(ok)}; save', run(['pcb', 'save']).get('ok'))
print('next: python plane_drc.py   (reload, rebuild the pours net by net, DRC)')
