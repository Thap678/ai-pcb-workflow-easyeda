"""GND connectivity through poured copper: which GND pads/vias/tracks share copper, which pour islands float.

usage: python gndconn.py dump.json  -> islands with their GND pads, and pads not touching any GND copper
"""
import json, sys, collections
from shapely.geometry import Polygon, Point, LineString
from shapely.ops import unary_union

MM = 0.0254
def islands(path):
    global d, cu
    d = json.loads((lambda s: s[s.find('{'):])(open(path, encoding='utf-8').read()))
    cu = d['copper']


    def ring(src):
        pts, i = [(src[0], src[1])], 2
        while i < len(src):
            t = src[i]
            if t == 'L':
                i += 1
                while i + 1 < len(src) and not isinstance(src[i], str):
                    pts.append((src[i], src[i + 1])); i += 2
            elif t == 'ARC':
                pts.append((src[i + 2], src[i + 3])); i += 4       # chord approximation is fine for connectivity
            else: i += 1
        return [(x * MM, y * MM) for x, y in pts]


    items = []        # (kind, layer set, geometry, label)
    for pd in cu['poured']:
        lay = pd.get('layer') or pd.get('layerId')
        for f in pd['fills']:
            rings = [ring(r) for r in f['source']]
            if len(rings[0]) < 3: continue                         # degenerate sliver
            poly = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3]).buffer(0)
            if poly.area < 0.05: continue
            items.append(('pour', {lay}, poly, f"pour L{lay} {f['id'][:6]} {poly.area:.0f}mm2"))
    for c in d['components']:
        for p in c['pads']:
            if p.get('net') != 'GND': continue
            lay = {1, 2} if p['layer'] == 12 else {p['layer']}
            g = Point(p['x'] * MM, p['y'] * MM).buffer(max(p['width'], p['height']) * MM / 2)
            items.append(('pad', lay, g, f"{c['designator']}.{p['padNumber']}"))
    for l in cu['lines']:
        if l.get('net') == 'GND':
            g = LineString([(l['startX'] * MM, l['startY'] * MM), (l['endX'] * MM, l['endY'] * MM)]).buffer(l['lineWidth'] * MM / 2)
            items.append(('track', {l['layer']}, g, l['primitiveId']))
    for v in cu['vias']:
        if v.get('net') == 'GND':
            items.append(('via', {1, 2}, Point(v['x'] * MM, v['y'] * MM).buffer(v['diameter'] * MM / 2), v['primitiveId']))

    par = list(range(len(items)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a[1] & b[1] and a[2].distance(b[2]) < 0.02: par[f(i)] = f(j)
    groups = collections.defaultdict(list)
    for i, it in enumerate(items): groups[f(i)].append(it)
    gs = sorted(groups.values(), key=lambda g: -sum(x[2].area for x in g))

    return items, gs
