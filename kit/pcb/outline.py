"""Board outline (rounded rectangle) + round mounting-hole cut-outs from the config, then save. Safe to re-run.

usage: python outline.py [--board <name>]          (geometry: width_mm / height_mm / corner_mm / mount_holes_mm)
Run before placement and routing: changing the outline after copper exists can strand it.

outline-round replaces the outline, so re-running it is harmless. Holes are cut-outs (fills on the MULTI layer 12)
that pile up when created twice, so the existing cut-outs are read first and a hole is cut only where none is; after
cutting, the board is read again and each configured hole must have exactly one cut-out. Cut-outs that match no
configured hole (an antenna slot, a hole from an older config) are listed and left alone.
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
M, MM = K.MIL, K.MM
TOL = 0.2                                            # mm: centre match for an existing cut-out
TOL_D = 1.0                                          # mm: diameter match (the extent may include the fill's stroke)


def cutouts():
    """[(primitiveId, bbox in mm (x0, y0, x1, y1) or None)] of every MULTI-layer fill (board cut-out) on the PCB."""
    v = K.js("const out = []; for (const f of (await eda.pcb_PrimitiveFill.getAll(12)) ?? []) { "
             "const id = f.getState_PrimitiveId(); out.push({id, b: (await eda.pcb_Primitive.getPrimitivesBBox([id])) ?? null}); } "
             "return out;", tries=3)
    return [(c['id'], (c['b']['minX'] * MM, c['b']['minY'] * MM, c['b']['maxX'] * MM, c['b']['maxY'] * MM) if c['b'] else None)
            for c in v or []]


def matches(hole, cuts):
    x, y = hole
    return [i for i, b in cuts if b and abs((b[0] + b[2]) / 2 - x) < TOL and abs((b[1] + b[3]) / 2 - y) < TOL
            and abs(b[2] - b[0] - B.hole_d) < TOL_D and abs(b[3] - b[1] - B.hole_d) < TOL_D]


d = K.run(['pcb', 'outline-round', '--rect', f'0,0,{B.w * M:.3f},{B.h * M:.3f}', '--radius', f'{B.corner * M:.3f}'])
print('outline', B.w, 'x', B.h, 'mm, corner', B.corner, '->', d.get('ok'), d.get('error') or '')

cuts = cutouts() if B.holes else []
blind = [i for i, b in cuts if b is None]
if blind:
    raise SystemExit(f'cannot read the extent of cut-out(s) {blind}: not cutting holes, an existing one could be '
                     'doubled. Check them in EasyEDA (pcb fill list --layer 12), then re-run.')
r = B.hole_d / 2 * M
cut_now = set()
for x, y in B.holes:
    if matches((x, y), cuts):
        print(f'hole ({x}, {y}) d={B.hole_d} exists'); continue
    cx, cy = x * M, y * M
    pts = [[round(cx + r * math.cos(2 * math.pi * i / 48), 3), round(cy + r * math.sin(2 * math.pi * i / 48), 3)]
           for i in range(48)]
    d = K.run(['pcb', 'slot', '--points', json.dumps(pts), '--locked'])      # sent once (not retry-safe)
    print(f'hole ({x}, {y}) d={B.hole_d} cut', d.get('ok'), d.get('error') or '')
    cut_now.add((x, y))

if B.holes:                                          # read back: the replies above are not proof
    cuts = cutouts()
    bad = {}
    for h in B.holes:
        n = matches(h, cuts)
        if len(n) != 1: bad[h] = n
    for h, n in bad.items():
        if n: print(f'!! hole {h}: {len(n)} cut-outs {n} - delete the extra ones')
        elif h in cut_now: print(f'!! hole {h}: cut but not found on read-back - look in EasyEDA before re-running '
                                  '(pcb fill list --layer 12), a re-run could cut it twice')
        else: print(f'!! hole {h}: missing')
    mine = {i for h in B.holes for i in matches(h, cuts)}
    other = [(i, b and tuple(round(v, 2) for v in b)) for i, b in cuts if i not in mine]
    if other: print('other cut-outs (not touched):', other)
    print('holes read back:', 'OK' if not bad else 'PROBLEMS')
print('save', K.run(['pcb', 'save']).get('ok'))
if B.holes and bad:
    sys.exit(1)
