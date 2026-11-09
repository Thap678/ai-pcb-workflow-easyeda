"""Checks of a routed split-plane board that EasyEDA's DRC does not do (or gets wrong), read from the live PCB:

  connectivity  every net: pads + tracks + vias + the ACTUAL pour fills (pcb_PrimitivePour.getCopperRegion, plane
                and ground layers included) -> any pad in a separate copper island is reported. EasyEDA's DRC both
                missed islands (power pours that never reached their plane) and invented 239 (stale cache).
  routing style off-45 segments, 90-degree and sharper corners (same net, same layer, joined end to end)
  layer use     tracks on inner layers; other-net tracks inside a top-layer pour outline (signals through power)
  keepouts      fill under the keepout parts on any layer but the ground layer
  --netlist     every PCB pad net against `easyeda sch netlist` (needs a schematic page uuid: --page <uuid>)

usage: python plane_check.py [--dump d.json] [--fills f.json] [--netlist --page <sch page uuid>]
       (without --dump / --fills it reads the live board: kit/dump.py + the pour fills over debug exec)
Exit 1 when connectivity, inner-layer tracks, signals in pours, keepout fill, corners or off-45 are not clean.
"""
import argparse, collections, json, math, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
from shapely.geometry import LineString, Point, Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
import common as K  # noqa: E402
from plane_board import PlaneBoard, MM  # noqa: E402

U = 0.254          # getCopperRegion units: 10 mil


def ring(cp):
    pts, i = [(cp[0] * U, cp[1] * U)], 2
    while i < len(cp):
        if isinstance(cp[i], str):
            i += 2 if cp[i] == 'ARC' else 1          # an arc is taken as its chord
            continue
        pts.append((cp[i] * U, cp[i + 1] * U)); i += 2
    return pts


def fills(p):
    """Copper of one pour fill: filled polygons (outer ring + holes) and the thermal spokes."""
    polys, lines = [], []
    for f in (p.get('cr') or {}).get('pourFills', []):
        rings = [ring(c) for c in f['path']['complexPolygon']]
        if f['fill']:
            polys.append(Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3]).buffer(0))
        else:
            lines += [LineString(r).buffer(max(f['lineWidth'], 0.5) * U / 2) for r in rings if len(r) >= 2]
    return polys, lines


FETCH = """const ps = await eda.pcb_PrimitivePour.getAll(); const out = [];
for (const p of ps) { let cr = null; try { cr = await p.getCopperRegion(); } catch (e) {}
  out.push({name: p.getState_PourName(), net: p.getState_Net(), layer: p.getState_Layer(), cr: cr}); }
return out;"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dump'); ap.add_argument('--fills')
    ap.add_argument('--netlist', action='store_true'); ap.add_argument('--page')
    a = ap.parse_args()
    dump_path = a.dump or K.board().path('plane_check_dump.json')
    if not a.dump:
        K.dump(dump_path)
    V = json.load(open(a.fills, encoding='utf-8')) if a.fills else K.js(FETCH, timeout=300)
    B = PlaneBoard(dump_path)
    cu, problems = B.d['copper'], []
    lines = [l for l in cu['lines'] if l.get('layer') in (1, 2, 15, 16)]
    ALL = {1, 2, B.ground_layer, B.plane_layer}
    # ---- connectivity incl. pour fills
    items = collections.defaultdict(list)
    for pd in B.pads:
        if pd['net']: items[pd['net']].append((ALL if not pd['smd'] else set(pd['layers']), pd['geom'], pd['key']))
    for l in lines:
        items[l['net']].append(({l['layer']}, LineString([(l['startX'] * MM, l['startY'] * MM), (l['endX'] * MM, l['endY'] * MM)]).buffer(l['lineWidth'] * MM / 2, 8), 'T'))
    for v in cu['vias']:
        items[v['net']].append((ALL, Point(v['x'] * MM, v['y'] * MM).buffer(v.get('diameter', 23.6) * MM / 2, 12), 'V'))
    for p in V:
        ps, ls = fills(p)
        items[p['net']] += [({p['layer']}, g, 'P') for g in ps + ls]
    islands = {}
    for net, its in items.items():
        par = list(range(len(its)))
        def f(i):
            while par[i] != i: par[i] = par[par[i]]; i = par[i]
            return i
        tree = STRtree([g for _, g, _ in its])
        for i, (L, g, _) in enumerate(its):
            for j in tree.query(g):
                j = int(j)
                if j > i and L & its[j][0] and g.intersects(its[j][1]): par[f(i)] = f(j)
        groups = collections.defaultdict(list)
        for i in range(len(its)): groups[f(i)].append(its[i][2])
        pg = [sorted(x for x in g if x not in ('T', 'V', 'P')) for g in groups.values()]
        pg = [g for g in pg if g]
        if len(pg) > 1: islands[net] = sorted(pg, key=len)
    print(f'connectivity (pour fills included): {len(items)} nets, {len(islands)} with pads in more than one island')
    for net, g in islands.items():
        print(f'  {net}: ' + ' | '.join(','.join(x[:6]) for x in g[:4]))
    if islands: problems.append(f'{len(islands)} nets split into islands')
    # ---- routing style
    off45 = corner = 0
    ends = collections.defaultdict(list)
    for l in lines:
        a_, b_ = (l['startX'] * MM, l['startY'] * MM), (l['endX'] * MM, l['endY'] * MM)
        dx, dy = b_[0] - a_[0], b_[1] - a_[1]
        if abs(dx) > 1e-4 and abs(dy) > 1e-4 and abs(abs(dx) - abs(dy)) > 0.02: off45 += 1
        for p, q in ((a_, b_), (b_, a_)): ends[(l['net'], l['layer'], round(p[0], 3), round(p[1], 3))].append(q)
    spots = []
    for k, qs in ends.items():
        if len(qs) != 2: continue
        p = (k[2], k[3]); v1 = (qs[0][0] - p[0], qs[0][1] - p[1]); v2 = (qs[1][0] - p[0], qs[1][1] - p[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 > 1e-6 and n2 > 1e-6 and (v1[0] * v2[0] + v1[1] * v2[1]) / n1 / n2 > -0.1:     # <= ~95 deg
            corner += 1; spots.append(f'{k[0]} L{k[1]} ({p[0]:.2f}, {p[1]:.2f})')
    print(f'routing style: {len(lines)} segments, off-45 {off45}, 90-degree or sharper corners {corner}', spots[:6] or '')
    if off45: problems.append('off45')
    if corner: problems.append('corners')
    # ---- layer use
    inner = sorted({l['net'] for l in lines if l['layer'] in (B.ground_layer, B.plane_layer)})
    print('tracks on inner layers:', inner or 'none')
    if inner: problems.append('inner tracks')
    top_pours = [(p['net'], unary_union(sum(fills(p), []))) for p in V if p['layer'] == 1 and p['net'] != B.gnd]
    through = collections.Counter()
    for l in lines:
        if l['layer'] != 1: continue
        seg = LineString([(l['startX'] * MM, l['startY'] * MM), (l['endX'] * MM, l['endY'] * MM)])
        for net, g in top_pours:
            if net != l['net'] and seg.intersects(g): through[(l['net'], net)] += 1
    print('other-net tracks through top power pours:', dict(through) or 'none')
    if through: problems.append('signals in pours')
    # ---- keepouts
    under = []
    for ref, ko in B.keepout.items():
        for p in V:
            if p['layer'] == B.ground_layer: continue
            a_ = unary_union(sum(fills(p), [])).intersection(ko).area
            if a_ > 0.05: under.append(f'{ref}: {p["name"]} L{p["layer"]} {a_:.1f} mm2')
        for l in lines:
            if LineString([(l['startX'] * MM, l['startY'] * MM), (l['endX'] * MM, l['endY'] * MM)]).intersects(ko): under.append(f'{ref}: track {l["net"]}')
        for v in cu['vias']:
            if Point(v['x'] * MM, v['y'] * MM).intersects(ko): under.append(f'{ref}: via {v["net"]}')
    print('copper under keepout parts (ground layer excepted):', under or 'none')
    if under: problems.append('keepout')
    # ---- netlist
    if a.netlist:
        if not a.page: raise SystemExit('--netlist needs --page <schematic page uuid>')
        r = subprocess.run(['easyeda', '--project', B.cfg.project, '--doc', a.page, 'sch', 'netlist'], capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
        subprocess.run(['easyeda', '--project', B.cfg.project, 'doc', 'switch', B.cfg.pcb], capture_output=True)
        res = (K.parse(r.stdout) or {}).get('result') or {}
        if not res.get('artifactPath'): raise SystemExit('sch netlist export failed: ' + r.stdout[-300:])
        art = [res['artifactPath']]
        n = json.load(open(art[-1], encoding='utf-8'))
        sch = {(c['props']['Designator'], k): v['net'] for c in n['components'].values() for k, v in c['pinInfoMap'].items()}
        diff = [(c['designator'], p['padNumber'], sch[(c['designator'], p['padNumber'])], p['net']) for c in B.d['components']
                for p in c['pads'] if (c['designator'], p['padNumber']) in sch and (sch[(c['designator'], p['padNumber'])] or '') != (p['net'] or '')]
        print(f'netlist ({os.path.basename(art[-1])}): {len(diff)} pad nets differ from the schematic', diff[:10] or '')
        if diff: problems.append('netlist')
    print('RESULT:', 'clean' if not problems else 'NOT clean: ' + ', '.join(problems))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
