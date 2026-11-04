"""Place components from a placement table (bbox CENTRE in mm, origin = board lower-left, +y up), check, lock.

usage: python place_table.py <placement.csv> [--check-only]
CSV columns: ref,x_mm,y_mm,rot,side,lock      side = TOP | BOTTOM, lock = 1 to lock after placing
Rows may be partial: place the big / mechanically fixed parts first, lock them, then the small parts.

After placing it reads the board back and reports, for every part on the board:
  - centre off by > 0.05 mm from the table, parts outside the outline, parts on a mounting-hole keep-out,
  - pairs of parts on the same side whose rendered extents overlap.
"""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
M, MM = K.MIL, K.MM
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8-sig')))
plan = {r['ref']: r for r in rows}
comps = {c['designator']: c for c in K.run(['pcb', 'list', '--include-bbox'])['result']['components']}
missing = sorted(set(plan) - set(comps))
if missing: print('not on the board (import the schematic first?):', missing)

if '--check-only' not in sys.argv:
    locked = [comps[r]['primitiveId'] for r in plan if r in comps and comps[r].get('locked')]
    if locked: K.run(['pcb', 'lock', '--ids', ','.join(locked), '--unlock'])
    for ref, r in plan.items():
        c = comps.get(ref)
        if not c: continue
        side = 2 if r.get('side', 'TOP').strip().upper().startswith('B') else 1
        if c['layer'] != side:                     # flip on its own: layer + x/y in one patch does not land
            K.run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps({'layer': side})])
        K.run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps({'rotation': float(r.get('rot') or 0)})])
        K.run(['pcb', 'modify', '--id', c['primitiveId'], '--center',
               '--x', f"{float(r['x_mm']) * M:.3f}", '--y', f"{float(r['y_mm']) * M:.3f}"])
    comps = {c['designator']: c for c in K.run(['pcb', 'list', '--include-bbox'])['result']['components']}
    ids = [comps[r]['primitiveId'] for r, v in plan.items() if r in comps and str(v.get('lock', '')).strip() in ('1', 'true', 'yes')]
    if ids: print('lock', len(ids), K.run(['pcb', 'lock', '--ids', ','.join(ids)]).get('ok'))
    print('save', K.run(['pcb', 'save']).get('ok'))

# ---- readback checks (all parts on the board, not only the table) ----
ext = {}
for ref, c in comps.items():
    b = c.get('bbox') or {}
    if b: ext[ref] = (b['minX'] * MM, b['minY'] * MM, b['maxX'] * MM, b['maxY'] * MM, c['layer'])
off = []
for ref, r in plan.items():
    if ref in ext:
        x0, y0, x1, y1, _ = ext[ref]
        if abs((x0 + x1) / 2 - float(r['x_mm'])) > 0.05 or abs((y0 + y1) / 2 - float(r['y_mm'])) > 0.05:
            off.append((ref, round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2)))
outside = [r for r, (x0, y0, x1, y1, _) in ext.items() if x0 < 0 or y0 < 0 or x1 > B.w or y1 > B.h]
keep = B.hole_d / 2 + 1.0                        # hole radius + 1 mm keep-out
on_hole = [r for r, (x0, y0, x1, y1, _) in ext.items()
           if any(x0 - keep < hx < x1 + keep and y0 - keep < hy < y1 + keep for hx, hy in B.holes)]
names = sorted(ext)
overlap = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
           if ext[a][4] == ext[b][4] and ext[a][0] < ext[b][2] and ext[b][0] < ext[a][2] and ext[a][1] < ext[b][3] and ext[b][1] < ext[a][3]]
print('centre off table:', off)
print('outside outline:', outside, '| near a mounting hole:', on_hole)
print('same-side overlaps (rendered extents incl. silkscreen, review by eye):', overlap)
