"""Per-net connectivity of a routed SES against the pads of its DSN, before anything goes into EasyEDA.

usage: python ses_conn.py <prepared.dsn> <routed.ses>
A net is open when its pads fall into more than one copper island (pads + tracks + vias, touching = joined).
Run it on the router output and again after ses_polish.py: any net that is open only after polishing was broken
by the polish, not by the router.
"""
import collections, sys
from shapely.geometry import LineString, Point, Polygon
import dsnses as BS

dsn, ses = BS.Dsn(sys.argv[1]), BS.Ses(sys.argv[2])
items = collections.defaultdict(list)                  # net -> [(layer, geom, pin or None)]
for p in dsn.pads:
    if not p['net']: continue
    g = Point(p['x'], p['y']).buffer(p['r']) if p['kind'] == 'circle' else Polygon(p['pts']).buffer(0)
    items[p['net']].append((p['layer'], g, p['pin']))
for w in ses.wires:
    for a, b in zip(w['pts'], w['pts'][1:]):
        items[w['net']].append((w['layer'], LineString([a, b]).buffer(w['width'] / 2), None))
for v in ses.vias:
    for L in ('TopLayer', 'BottomLayer'):
        items[v['net']].append((L, Point(v['x'], v['y']).buffer(ses.via_diam / 2), ('via', v['x'], v['y'])))

open_nets = {}
for net, its in items.items():
    par = list(range(len(its)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    first = {}
    for i, (L, g, tag) in enumerate(its):              # the same pin / via on both layers is one conductor
        if tag is not None:
            if tag in first: par[f(i)] = f(first[tag])
            else: first[tag] = i
    for i in range(len(its)):
        for j in range(i + 1, len(its)):
            if its[i][0] == its[j][0] and its[i][1].distance(its[j][1]) < 0.005: par[f(i)] = f(j)
    groups = collections.defaultdict(set)
    for i, (L, g, tag) in enumerate(its):
        if isinstance(tag, str): groups[f(i)].add(tag)
    pin_groups = [sorted(s) for s in groups.values() if s]
    if len(pin_groups) > 1: open_nets[net] = pin_groups
print(f'nets {len(items)} | open nets: {len(open_nets)}')
for n, gs in open_nets.items(): print(' ', n, gs)
sys.exit(1 if open_nets else 0)
