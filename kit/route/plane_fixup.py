"""Fix-up pass after route_plane.py: add, into the space the signals left,
  1. stitching vias in every top power pour of a plane net, up to max(2, area / --per) capped at --max, inside the
     net's own plane region, clear of every other-net copper on both layers, own pads, keepouts, fine-pitch bodies,
     >= 1.2 mm apart within the pour and >= hole-to-hole from every other via and through-hole pad;
     nearest the pour's own pads first (where the current enters);
  2. a short 0.25 mm trace from every ground pad still without a via to a ground via within --reach mm (or to a
     ground pad of the same part that has one); adjacent via-less ground pads of one part are one tree.
Everything added passes the exact clearance check against everything already there (--clear, default 0.205 mm,
just above EasyEDA's 7.9 mil rule check). The router keeps these out of its own run on purpose: inside it they
made it stop converging (docs/05 section 8).

usage: python plane_fixup.py <dump.json> <routes.json> <out.json> [--max 8] [--per 6] [--reach 6]
"""
import argparse, json, math, os, sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import numpy as np  # noqa: E402
from shapely.geometry import Point, Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
import route_bundle as RB  # noqa: E402
from route_bundle import LAY  # noqa: E402
import route_plane as RP  # noqa: E402
from plane_board import PlaneBoard, MM  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
ap.add_argument('dump'); ap.add_argument('routes'); ap.add_argument('out')
ap.add_argument('--max', type=int, default=8)
ap.add_argument('--per', type=float, default=6.0)
ap.add_argument('--reach', type=float, default=6.0)
ap.add_argument('--clear', type=float, default=0.205)
a = ap.parse_args()

B = PlaneBoard(a.dump)
S = json.load(open(a.routes, encoding='utf-8'))
VR, GND = RP.VR, B.gnd
RB.SLACK = 0.02
RB.CLR = a.clear - RB.SLACK
RB.RULE = a.clear - 0.005
hole = B.cfg.rules.get('via_hole_mm', 0.3)
via_gap = hole + B.cfg.rules.get('hole_to_hole_mm', 0.5) + 0.1          # via centre to via centre
pours = [dict(name=p['name'], net=p['net'], geom=Polygon(p['poly'], p['holes'])) for p in S['pours']]
fp_vias = [dict(net=v['net'], x=v['x'] * MM, y=v['y'] * MM) for v in B.d['copper']['vias'] if len(v['primitiveId']) > 16]
args = SimpleNamespace(open=0.0, d=0.3, bot=1.5, via=25.0, pen=30.0, padpen=0.0, keep_gnd=True, padzone=0.6, maxw=10.0,
                       iters=1, polish=0, bot_hug=False, polish_mul=2.0, max_rips=12, window=a.reach + 2, width=0.25)
R = RP.PR(B, args, RP.fixed_from(B, S['wires'], S['vias'], pours, fp_vias), vias=S['vias'] + fp_vias, keep_new_vias=True)
add_w, add_v = [], []

# ---------------------------------------------------------------- 1. stitching vias
regions = {n: g.buffer(-(VR + 0.15)) for n, g in B.regions.items()}
ko = unary_union(list(B.keepout.values())).buffer(VR + 0.15)
fine = unary_union(list(RP.fine_pitch_parts(B).values())).buffer(VR + 0.1)
edge = B.outline.buffer(-(RB.EDGE + VR))
tht = unary_union([pd['geom'] for pd in B.pads if not pd['smd']]).buffer(RB.HOLE_KEEP + VR)
all_vias = [(v['net'], v['x'], v['y']) for v in S['vias']] + [(v['net'], v['x'], v['y']) for v in fp_vias]
foreign_of, report = {}, []
for po in pours:
    net = po['net']
    if net not in regions: continue
    g = po['geom']
    mine = all_vias + [(v['net'], v['x'], v['y']) for v in add_v]
    have = [(x, y) for n, x, y in mine if n == net and g.buffer(0.05).contains(Point(x, y))]
    want = int(min(a.max, max(2, g.area / a.per)))
    if len(have) >= want:
        report.append(f"{po['name']}: has {len(have)}"); continue
    if net not in foreign_of:
        foreign_of[net] = unary_union([gg for L in LAY for n, gg in R.fixed_geoms[L] if n != net]).buffer(a.clear + VR)
    pads_own = unary_union([pd['geom'] for pd in B.pads if pd['net'] == net]).buffer(VR + 0.1)
    ok = (g.buffer(-(VR + 0.1)).intersection(regions[net]).intersection(edge).difference(foreign_of[net])
          .difference(pads_own).difference(ko).difference(fine).difference(tht))
    if ok.is_empty:
        report.append(f"{po['name']}: has {len(have)}, no room"); continue
    src = [(pd['x'], pd['y']) for pd in B.pads if pd['net'] == net and pd['geom'].intersects(g)] or [(g.centroid.x, g.centroid.y)]
    x0, y0, x1, y1 = ok.bounds
    cand = [(x, y) for x in np.arange(round(x0, 1), x1, 0.1) for y in np.arange(round(y0, 1), y1, 0.1) if ok.contains(Point(x, y))]
    cand.sort(key=lambda p: min(math.dist(p, s) for s in src))
    got = 0
    for x, y in cand:
        if len(have) >= want: break
        if any(math.dist((x, y), h) < 1.2 for h in have): continue
        if any(math.dist((x, y), (vx, vy)) < via_gap for _, vx, vy in all_vias + [(v['net'], v['x'], v['y']) for v in add_v]): continue
        r = dict(wires=[], vias=[(round(float(x), 3), round(float(y), 3))])
        key = f'{net}#fs{len(add_v)}'
        R.pins[key] = []
        if R.check(key, r)[0]: continue
        R.commit(key, r)
        add_v.append(dict(net=net, x=round(float(x), 3), y=round(float(y), 3))); have.append((x, y)); got += 1
    report.append(f"{po['name']}: had {len(have) - got}, added {got} (want {want})")
print('stitching:'); [print('  ' + s) for s in report]

# ---------------------------------------------------------------- 2. ground pads without a via
gv = [(v['x'], v['y']) for v in S['vias'] + add_v if v['net'] == GND] + [(v['x'], v['y']) for v in fp_vias if v['net'] == GND]
pad_of = {pd['key']: pd for pd in B.pads}
todo = [k for k in S.get('gnd_fail', []) if not any(q['net'] == GND and q['key'] != k and q['geom'].intersects(pad_of[k]['geom']) for q in B.pads)]
groups = []
for k in todo:
    pd = pad_of[k]
    for gr in groups:
        if any(pad_of[q]['ref'] == pd['ref'] and math.dist((pad_of[q]['x'], pad_of[q]['y']), (pd['x'], pd['y'])) < 1.5 for q in gr):
            gr.append(k); break
    else:
        groups.append([k])
done = set()
for reach in (a.reach, a.reach + 4):
    for gr in groups:
        if all(k in done for k in gr): continue
        cx = sum(pad_of[k]['x'] for k in gr) / len(gr); cy = sum(pad_of[k]['y'] for k in gr) / len(gr)
        near = [p for p in gv if math.dist(p, (cx, cy)) < reach]
        mates = [q for q in B.pads if q['net'] == GND and q['ref'] == pad_of[gr[0]]['ref'] and q['key'] not in todo
                 and math.dist((q['x'], q['y']), (cx, cy)) < 6.0]
        terms = [Point(x, y).buffer(VR, 16) for x, y in near] + [q['geom'] for q in mates]
        if not terms: continue
        key = f'{GND}#fg' + '+'.join(gr)
        R.pins[key] = list(gr)
        R.pseudo_pin(key, key + '.t', GND, unary_union(terms))
        r = R.route_checked(key)
        if r is None: continue
        R.commit(key, r); done.update(gr)
        for li, pts in r['wires']:
            add_w.append(dict(net=GND, layer=int(li) + 1, width=0.25, pts=[(round(float(x), 3), round(float(y), 3)) for x, y in pts]))
        for x, y in r['vias']:
            add_v.append(dict(net=GND, x=round(float(x), 3), y=round(float(y), 3)))
print('ground pads connected:', sorted(done), '| still without a via:', sorted(set(todo) - done) or 'none')

out = dict(S)
out['wires'] = S['wires'] + add_w
out['vias'] = S['vias'] + add_v
out['gnd_fail'] = sorted(set(todo) - done)
out['fixup'] = dict(stitch=report, gnd_done=sorted(done), added_vias=len(add_v), added_wires=len(add_w))
json.dump(out, open(a.out, 'w'), indent=0)
print('wrote', a.out, '+%d vias, +%d wires' % (len(add_v), len(add_w)))
