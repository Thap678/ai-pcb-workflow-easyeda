"""Ground fanout: give every SMD ground pad its own short stub + via, so ground needs no tracks between pads.

Route the signals without the ground net first (it is removed from the netlist, its pads stay obstacles), then:
    python gnd_fanout.py <prepared.dsn> <signals.ses> <out.ses> [--via-in-pad-mm 1.0]
The ground pours on both layers (pour_gnd.py) join the vias afterwards; stitch.py adds plane-to-plane vias.

Per SMD ground pad it tries 16 directions and steps outward from the pad edge, and keeps the shortest stub whose
via and track keep the router clearance from every other-net pad, track and via (both layers for the via, the
pad's layer for the stub), stay inside the board, and keep hole-to-hole from every via. Pads at least
--via-in-pad-mm on both sides (module / QFN exposed pads) may take the via in the pad instead. Through-hole ground
pads are skipped: they already reach both layers. Units: mm inside, SES resolution units in the file.
"""
import math, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dsnses import Dsn, Ses
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
import common as K

args = [a for a in sys.argv[1:] if not a.startswith('--')]
VIP = float(sys.argv[sys.argv.index('--via-in-pad-mm') + 1]) if '--via-in-pad-mm' in sys.argv else 1.0
if len(args) < 3: raise SystemExit(__doc__)
dsn_path, ses_path, out_path = args[:3]
B = K.try_board()
R = B.rules if B else {}
GND = B.gnd if B else 'GND'
CLR = (B.route.get('router_clearance_mm', 0.2) if B else 0.2)
VIA_D, HOLE = R.get('via_d_mm', 0.45), R.get('via_hole_mm', 0.2)
H2H = R.get('hole_to_hole_mm', 0.4)
EDGE = R.get('edge_mm', 0.25) + 0.05
STUB_W = next((tc['default_mm'] for tc in (B.track_classes if B else []) if GND in tc.get('nets', [])), 0.25)
VR = VIA_D / 2

dsn, ses = Dsn(dsn_path), Ses(ses_path)
board = Polygon(dsn.outline).buffer(0).buffer(-(EDGE - dsn.inset))
layers_of = {}
for p in dsn.pads: layers_of.setdefault(p['pin'], set()).add(p['layer'])


def pad_geom(p):
    return Point(p['x'], p['y']).buffer(p['r'], 24) if p['kind'] == 'circle' else Polygon(p['pts']).buffer(0)


obs = {'TopLayer': [], 'BottomLayer': []}            # (geometry, net)
for p in dsn.pads:
    obs.setdefault(p['layer'], []).append((pad_geom(p), p['net']))
for w in ses.wires:
    obs.setdefault(w['layer'], []).append((LineString(w['pts']).buffer(w['width'] / 2, 8), w['net']))
vias = [(v['x'], v['y']) for v in ses.vias]
for x, y in vias:
    for L in obs: obs[L].append((Point(x, y).buffer(ses.via_diam / 2, 16), None))
trees = {L: STRtree([g for g, _ in o]) for L, o in obs.items()}


def clear(geom, layer, own_pin_geom=None):
    """True when `geom` keeps CLR from every non-ground item on `layer` (ground items may touch)."""
    for k in trees[layer].query(geom.buffer(CLR)):
        g, net = obs[layer][k]
        if net == GND: continue
        if geom.distance(g) < CLR - 1e-6: return False
    return True


PADS = [(pad_geom(p), p['pin']) for p in dsn.pads]
PAD_TREE = STRtree([g for g, _ in PADS])


def via_ok(x, y, placed, own_pin):
    d = Point(x, y).buffer(VR, 16)
    if not board.contains(d): return False
    if any(math.hypot(x - u, y - v) < HOLE + H2H for u, v in vias + placed): return False
    for L in ('TopLayer', 'BottomLayer'):
        if not clear(d, L): return False
    # no via on (or too close to) any other pad, ground pads included: only its own pad, when in-pad
    for k in PAD_TREE.query(d.buffer(CLR)):
        g, pin = PADS[k]
        if pin != own_pin and d.distance(g) < CLR - 1e-6: return False
    return True


gpads = {}
for p in dsn.pads:
    if p['net'] == GND and layers_of[p['pin']] != {'TopLayer', 'BottomLayer'}:
        gpads[p['pin']] = p
placed, stubs, fails = [], [], []
for pin, p in sorted(gpads.items(), key=lambda t: (t[1]['y'], t[1]['x'])):
    L, g = p['layer'], pad_geom(p)
    minx, miny, maxx, maxy = g.bounds
    best = None
    if min(maxx - minx, maxy - miny) >= VIP and via_ok(p['x'], p['y'], placed, pin):
        best = (0.0, p['x'], p['y'])
    for k in range(16) if best is None else []:
        a = 2 * math.pi * k / 16
        ux, uy = math.cos(a), math.sin(a)
        edge = max(t for t in [i * 0.01 for i in range(400)] if g.buffer(1e-6).contains(Point(p['x'] + ux * t, p['y'] + uy * t)))
        t = edge + VR + 0.05
        while t < edge + 1.6:
            x, y = p['x'] + ux * t, p['y'] + uy * t
            if via_ok(x, y, placed, pin):
                stub = LineString([(p['x'], p['y']), (x, y)]).buffer(STUB_W / 2, 8)
                if clear(stub, L):
                    if best is None or t < best[0]: best = (t, x, y)
                    break
            t += 0.05
    if best is None:
        fails.append(pin); continue
    _, x, y = best
    placed.append((x, y))
    if best[0] > 0: stubs.append((L, (p['x'], p['y']), (x, y)))
n_in_pad = len(placed) - len(stubs)
# no room for a via (a fine-pitch QFN ground pin, say): a stub to the nearest ground pad / fanout via on the same
# layer instead (the exposed pad next to it), when that stub clears everything else
joined = []
for pin in list(fails):
    p = gpads[pin]; L = p['layer']; c = Point(p['x'], p['y'])
    targets = [pad_geom(q) for q2, q in gpads.items() if q2 != pin and q['layer'] == L and q2 not in fails]
    targets += [Point(x, y) for x, y in placed]
    best = None
    for tg in targets:
        if tg.distance(c) > 2.0: continue
        q = tg.centroid if tg.geom_type == 'Point' else tg.exterior.interpolate(tg.exterior.project(c))
        stub = LineString([(p['x'], p['y']), (q.x, q.y)])
        if clear(stub.buffer(STUB_W / 2, 8), L) and (best is None or stub.length < best[0]):
            best = (stub.length, (q.x, q.y))
    if best:
        stubs.append((L, (p['x'], p['y']), best[1])); fails.remove(pin); joined.append(pin)
print(f'ground SMD pads {len(gpads)} | vias placed {len(placed)} ({n_in_pad} in pad) | joined to a neighbour by stub: '
      f'{len(joined)} | no room: {len(fails)}')
if fails: print('  no room for (leave to the top pour, check conn after pouring):', fails)

# write: append a (net GND ...) block to network_out
u = lambda mm: round(mm / 0.0254 * 1000)
via_ps = re.search(r'\(padstack (\S+)', open(ses_path, encoding='utf-8').read()).group(1)
blk = [f'      (net {GND}']
for L, a, b in stubs:
    blk += ['        (wire', f'          (path {L} {u(STUB_W)}', f'            {u(a[0])} {u(a[1])}',
            f'            {u(b[0])} {u(b[1])}', '          )', '        )']
for x, y in placed:
    blk += [f'        (via {via_ps} {u(x)} {u(y)}', '        )']
blk.append('      )')
s = open(ses_path, encoding='utf-8').read()
if re.search(r'\(net ' + re.escape(GND) + r'\s*\n', s):
    raise SystemExit(f'{ses_path} already routes {GND}: route without it first')
i = s.index('(network_out')
j = s.index('\n', i)
s = s[:j + 1] + '\n'.join(blk) + '\n' + s[j + 1:]
open(out_path, 'w', encoding='utf-8').write(s)
print('wrote', out_path, f'| stub {STUB_W} mm, via {VIA_D}/{HOLE} mm ({via_ps}), clearance {CLR} mm')
