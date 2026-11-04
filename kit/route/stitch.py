"""Ground stitching: plan the vias from a poured-board dump, then place them (idempotent), then save.

usage: python stitch.py <poured dump.json> [grid_mm] [--plan-only]
Plan: (1) one via per dead pour island that overlaps main ground on the other layer, (2) a grid (default
[ground] stitch_grid_mm, 4 mm) where both layers' main ground overlap. A via needs >= via radius + 0.2 mm of pour
around it, >= 2.5 mm to other vias (1.0 mm for island ties), >= 0.55 mm (edge to edge) from any through-hole pad
and >= 0.3 mm from SMD pads of other nets - without the pad check vias landed next to header pins and DRC failed.
Place: skips plan points that already have a ground via within 1 mil, so it can be re-run after a connector drop.
"""
import json, os, sys
from shapely.geometry import Point
from shapely.ops import unary_union
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
import gndconn_lib as G

B = K.board()
NET = B.ground.get('net', 'GND')
path = sys.argv[1]
grid = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else B.ground.get('stitch_grid_mm', 4.0)
items, groups = G.islands(path, NET)
d = K.load_dump(path)
MM = K.MM
VIA_R = B.rules.get('via_d_mm', 0.6) / 2
M = 0.3 + 0.2
existing = [Point(v['x'] * MM, v['y'] * MM) for v in d['copper']['vias']]
pads = []                                              # (geometry, min edge gap)
for c in d['components']:
    for p in c['pads']:
        th = p['layer'] == 12
        if not th and p.get('net') == NET: continue
        g = Point(p['x'] * MM, p['y'] * MM).buffer(max(p['width'], p['height']) * MM / 2)
        pads.append((g, 0.55 if th else 0.3))
main = groups[0]
main_L = {L: unary_union([it[2] for it in main if it[0] == 'pour' and L in it[1]]) for L in (1, 2)}
plan = []


def free(p, gap=2.5):
    if not all(p.distance(q) >= gap for q in existing + plan): return False
    via = p.buffer(VIA_R)
    return all(via.distance(g) >= m for g, m in pads)


tied = 0
for g in groups[1:]:
    if any(it[0] == 'pad' for it in g): continue
    for it in sorted(g, key=lambda x: -x[2].area):
        L = next(iter(it[1]))
        zone = it[2].buffer(-M).intersection(main_L[3 - L].buffer(-M))
        if zone.is_empty: continue
        p = zone.representative_point()
        if free(p, 1.0): plan.append(p); tied += 1; break
both = main_L[1].buffer(-M).intersection(main_L[2].buffer(-M))
n_grid = 0
x = grid / 2
while x < B.w:
    y = grid / 2
    while y < B.h:
        p = Point(x, y)
        if both.contains(p) and free(p): plan.append(p); n_grid += 1
        y += grid
    x += grid
print(f'dead islands tied: {tied} | grid vias: {n_grid} | total {len(plan)}')
json.dump([(round(p.x, 3), round(p.y, 3)) for p in plan], open(path + '.stitch_plan.json', 'w'))   # mm, for review
if '--plan-only' in sys.argv:
    raise SystemExit

have = [(v['x'], v['y']) for v in d['copper']['vias'] if v.get('net') == NET]
ok = skipped = bad = 0
for p in plan:
    x_mil, y_mil = p.x * K.MIL, p.y * K.MIL
    if any(abs(x_mil - ex) < 1.0 and abs(y_mil - ey) < 1.0 for ex, ey in have):
        skipped += 1; continue
    r = K.run(['pcb', 'via', '--x', f'{x_mil:.2f}', '--y', f'{y_mil:.2f}', '--net', NET], tries=1)
    if r.get('ok'): ok += 1; have.append((x_mil, y_mil))
    else: bad += 1; print('FAIL', round(p.x, 2), round(p.y, 2), json.dumps(r.get('error'), ensure_ascii=False)[:160])
print(f'vias placed ok={ok} skipped={skipped} failed={bad} (re-run to retry; a failed reply may still have landed)')
print('pour-rebuild', K.run(['pcb', 'pour-rebuild']).get('ok'), '| save', K.run(['pcb', 'save']).get('ok'))
