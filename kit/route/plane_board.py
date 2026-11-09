"""Board geometry for the split-plane router (4 layers: signals top + bottom, a solid ground layer, a power layer split
into one region per supply). Everything in mm, y-up, the frame of a `kit/dump.py` snapshot.

The plane layout comes from the config, [boards.<name>.planes] (see board.example.toml):
  plane_layer / ground_layer   EasyEDA layer ids of the split power layer and the solid ground layer (16 / 15)
  gap_mm                       copper gap between two power regions on the plane layer
  keepout_refs                 parts whose body gets no copper on top, bottom and the plane layer (inductors);
                               the ground layer stays as a shield
  wide_nets                    {net: width_mm}: nets routed first as wide top tracks without a pour (motor outputs)
  [[...planes.regions]]        net, boxes = [[x0, y0, x1, y1], ...], cut = [...]: the power regions, in order;
                               an earlier region wins where two overlap
  [[...planes.pours]]          name, net, pads = ["REF.pin", ...]: top-layer power pours; the router joins these
                               pads with a 1 mm skeleton and grows the pour around it (no pads = every top pad of net)
"""
import json, os, sys
from shapely.affinity import rotate, scale, translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K  # noqa: E402

MM = 0.0254


def pad_geom(p):
    x, y = p['x'] * MM, p['y'] * MM
    kind, w, h = p['shape'][0], p['shape'][1] * MM, p['shape'][2] * MM
    if kind == 'ELLIPSE':
        g = scale(Point(0, 0).buffer(0.5, 32), w, h, origin=(0, 0))
    elif kind == 'OVAL':
        r = min(w, h) / 2
        L = max(w, h) / 2 - r
        g = (LineString([(-L, 0), (L, 0)]) if w >= h else LineString([(0, -L), (0, L)])).buffer(r, 16)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    return translate(rotate(g, p.get('rotation', 0) or 0, origin=(0, 0)), x, y)


def regions_from(spec):
    """{net: polygon} from the ordered [[planes.regions]] list: an earlier region wins where two overlap."""
    out, taken = {}, None
    for r in spec.get('regions', []):
        g = unary_union([box(*b) for b in r['boxes']])
        if r.get('cut'):
            g = g.difference(unary_union([box(*b) for b in r['cut']]))
        if taken is not None:
            g = g.difference(taken)
        out[r['net']] = g if r['net'] not in out else out[r['net']].union(g)
        taken = g if taken is None else taken.union(g)
    return out


class PlaneBoard:
    def __init__(self, dump_path, board=None):
        self.cfg = board or K.board()
        spec = self.cfg.planes
        if not spec:
            raise SystemExit(f'no [boards.{self.cfg.name}.planes] in the config - the split-plane router needs it (docs/05 section 8)')
        self.spec = spec
        self.d = d = json.load(open(dump_path, encoding='utf-8'))
        self.outline = Polygon([(x * MM, y * MM) for x, y in d['outline']['points']]).buffer(0)
        b = self.outline.bounds
        self.board = (b[2] + 0.5, b[3] + 0.5)
        self.plane_layer = spec.get('plane_layer', 16)
        self.ground_layer = spec.get('ground_layer', 15)
        self.gap = spec.get('gap_mm', 0.6)
        self.gnd = self.cfg.gnd
        self.pads, self.by_key, self.comp = [], {}, {c['designator']: c for c in d['components']}
        for c in d['components']:
            seen = {}
            for p in c['pads']:
                k = f"{c['designator']}.{p['padNumber']}"
                seen[k] = seen.get(k, 0) + 1
                key = k if seen[k] == 1 else f'{k}~{seen[k]}'
                pd = dict(key=key, ref=c['designator'], num=p['padNumber'], net=p['net'] or '',
                          layers=(1, 2) if p['layer'] == 12 else (p['layer'],), geom=pad_geom(p),
                          smd=p['layer'] != 12, x=p['x'] * MM, y=p['y'] * MM)
                self.pads.append(pd)
                self.by_key.setdefault(k, []).append(pd)
        self.region_raw = regions_from(spec)
        self.regions = {n: g.buffer(-self.gap / 2, join_style=2) for n, g in self.region_raw.items()}
        self.keepout = {}
        for ref in spec.get('keepout_refs', []):
            c = self.comp[ref]; bb = c['bbox']
            body = box(bb['minX'] * MM, bb['minY'] * MM, bb['maxX'] * MM, bb['maxY'] * MM)
            own = unary_union([pd['geom'] for pd in self.pads if pd['ref'] == ref]).buffer(0.3)
            self.keepout[ref] = body.difference(own)
        self.pour_groups = {p['name']: (p['net'], p.get('pads')) for p in spec.get('pours', [])}
        self.wide_nets = dict(spec.get('wide_nets', {}))

    def pads_of(self, label):
        return self.by_key[label]


if __name__ == '__main__':
    B = PlaneBoard(sys.argv[1])
    print('pads', len(B.pads), 'outline', B.board)
    for n, g in B.regions.items():
        parts = len(getattr(g, 'geoms', [g]))
        print(f'  region {n:10s} {g.area:7.1f} mm2  {parts} piece(s)' + ('  <- NOT one piece' if parts > 1 else ''))
    off = [pd['key'] for pd in B.pads if pd['net'] in B.regions and pd['smd'] is False
           and not B.region_raw[pd['net']].contains(pd['geom'].centroid)]
    print('  through-hole power pads outside their region:', off or 'none')
