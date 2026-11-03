"""GND stitching: (1) tie dead pour islands to the main GND, (2) 4 mm grid of vias where top and bottom main GND overlap.

A via is placed only where both pours have >= via radius + 0.2 mm of copper around it (pours are already 0.3 mm off
other nets) and >= 2.5 mm from any existing via. usage: python stitch2.py dump.json [grid_mm] -> stitch2_plan.json
"""
import json, sys
from shapely.geometry import Point
from shapely.ops import unary_union
import gndconn_lib as G

items, groups = G.islands(sys.argv[1])
grid = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
d = json.loads((lambda s: s[s.find('{'):])(open(sys.argv[1], encoding='utf-8').read()))
MM = G.MM; M = 0.3 + 0.2
existing = [Point(v['x'] * MM, v['y'] * MM) for v in d['copper']['vias']]
main = groups[0]
main_L = {L: unary_union([it[2] for it in main if it[0] == 'pour' and L in it[1]]) for L in (1, 2)}
plan = []
def free(p, gap=2.5): return all(p.distance(q) >= gap for q in existing + plan)

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
while x < 80:
    y = grid / 2
    while y < 87:
        p = Point(x, y)
        if both.contains(p) and free(p): plan.append(p); n_grid += 1
        y += grid
    x += grid
json.dump([(round(p.x / MM, 2), round(p.y / MM, 2)) for p in plan], open('stitch2_plan.json', 'w'))
print(f'dead islands tied: {tied} | grid vias: {n_grid} | total {len(plan)}')
