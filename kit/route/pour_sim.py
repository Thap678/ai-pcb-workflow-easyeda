"""Offline pour simulation: would two-layer ground pours join every ground pad of a routed SES?

usage: python pour_sim.py <prepared.dsn> <routed.ses> [--stitch <out.ses>]
Each layer's pour = board inset by [ground] pour_inset_mm, minus every other-net pad / track / via grown by
rules.pour_mm, minus necks narrower than 0.2 mm. Ground pads, tracks and vias join the pour piece they touch on
their layer; vias and through-hole pads join both layers. Prints the ground islands (should be 1).
--stitch: where a cut-off island's pour overlaps main ground on the other layer with room for a via, add a ground
via there (what stitch.py does on the real pours), repeat, and write the SES with those vias.
Approximate: no thermal spokes, EasyEDA's real pour may differ slightly - confirm with conn.py / DRC after pouring.
"""
import collections, math, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dsnses import Dsn, Ses
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
import common as K

B = K.try_board()
GND = B.gnd if B else 'GND'
POUR_CLR = B.rules.get('pour_mm', 0.25) if B else 0.25
INSET = B.ground.get('pour_inset_mm', 0.3) if B else 0.3
VIA_D = B.rules.get('via_d_mm', 0.45) if B else 0.45
H2H = (B.rules.get('hole_to_hole_mm', 0.4) + B.rules.get('via_hole_mm', 0.2)) if B else 0.6
LAYERS = ('TopLayer', 'BottomLayer')


def load(dsn_path, ses_path):
    dsn, ses = Dsn(dsn_path), Ses(ses_path)
    items = collections.defaultdict(list)          # layer -> [(geom, net, name)]
    for p in dsn.pads:
        g = Point(p['x'], p['y']).buffer(p['r'], 24) if p['kind'] == 'circle' else Polygon(p['pts']).buffer(0)
        items[p['layer']].append((g, p['net'], 'pad:' + p['pin']))
    for i, w in enumerate(ses.wires):
        items[w['layer']].append((LineString(w['pts']).buffer(w['width'] / 2, 8), w['net'], f'wire{i}'))
    vias = [(v['x'], v['y'], v['net']) for v in ses.vias]
    return dsn, ses, items, vias


def simulate(dsn, ses, items, vias):
    """-> (pad islands, pour pieces {layer: [polygon]}, island id of each piece {(layer, k): root})"""
    board = Polygon(dsn.outline).buffer(dsn.inset).buffer(-INSET)
    its = {L: list(items[L]) + [(Point(x, y).buffer(ses.via_diam / 2, 16), n, f'via{i}') for i, (x, y, n) in enumerate(vias)]
           for L in LAYERS}
    par = {}
    def f(a):
        par.setdefault(a, a)
        while par[a] != a: par[a] = par[par[a]]; a = par[a]
        return a
    def join(a, b): par[f(a)] = f(b)
    pieces = {}
    for L in LAYERS:
        other = unary_union([g.buffer(POUR_CLR) for g, n, _ in its[L] if n != GND])
        pour = board.difference(other).buffer(-0.1).buffer(0.1)
        pieces[L] = [p for p in getattr(pour, 'geoms', [pour]) if p.area > 0.05]
        for k, piece in enumerate(pieces[L]):
            f(f'{L}:pour{k}')
            for g, n, name in its[L]:
                if n == GND and g.intersects(piece): join(name, f'{L}:pour{k}')
        gi = [(g, name) for g, n, name in its[L] if n == GND]
        for a in range(len(gi)):
            for b in range(a + 1, len(gi)):
                if gi[a][0].intersects(gi[b][0]): join(gi[a][1], gi[b][1])
    gpads = sorted({name for L in LAYERS for g, n, name in its[L] if n == GND and name.startswith('pad:')})
    groups = collections.defaultdict(list)
    for name in gpads: groups[f(name)].append(name)
    islands = sorted(groups.values(), key=len, reverse=True)
    piece_root = {(L, k): f(f'{L}:pour{k}') for L in LAYERS for k in range(len(pieces[L]))}
    return islands, pieces, piece_root, f


def stitch(dsn, ses, items, vias, rounds=30):
    """Add ground vias joining cut-off islands to the main one: shortest chain of top/bottom pour overlaps (through
    pour pieces that hold no ground of their own, too), one via per hop. -> added [(x, y)]"""
    added = []
    for _ in range(rounds):
        islands, pieces, root, f = simulate(dsn, ses, items, vias)
        if len(islands) < 2: break
        main = f(islands[0][0])
        targets = {f(isl[0]) for isl in islands[1:]}
        top, bot = pieces[LAYERS[0]], pieces[LAYERS[1]]
        edges = collections.defaultdict(list)       # piece node -> [(node, via point)]
        for i, pa in enumerate(top):
            for j, pb in enumerate(bot):
                if not pa.intersects(pb): continue
                room = pa.intersection(pb).buffer(-(VIA_D / 2 + 0.05))
                if room.is_empty: continue
                pt = room.representative_point()
                if any(math.hypot(pt.x - x, pt.y - y) < H2H for x, y, _ in vias): continue
                a, b = (LAYERS[0], i), (LAYERS[1], j)
                edges[a].append((b, pt)); edges[b].append((a, pt))
        start = [n for n, r in root.items() if r == main]
        prev, seen, queue, hit = {}, set(start), collections.deque(start), None
        while queue and hit is None:
            n = queue.popleft()
            for m, pt in edges[n]:
                if m in seen: continue
                seen.add(m); prev[m] = (n, pt); queue.append(m)
                if root[m] in targets: hit = m; break
        if hit is None: break
        n = hit
        while n in prev:
            n, pt = prev[n]
            vias.append((pt.x, pt.y, GND)); added.append((pt.x, pt.y))
    return added


def write_vias(ses_path, out_path, pts):
    s = open(ses_path, encoding='utf-8').read()
    via_ps = re.search(r'\(padstack (\S+)', s).group(1)
    u = lambda mm: round(mm / 0.0254 * 1000)
    blk = ''.join(f'        (via {via_ps} {u(x)} {u(y)}\n        )\n' for x, y in pts)
    m = re.search(r'\(net ' + re.escape(GND) + r'\s*\n', s)
    if m:
        s = s[:m.end()] + blk + s[m.end():]
    else:
        j = s.index('\n', s.index('(network_out'))
        s = s[:j + 1] + f'      (net {GND}\n' + blk + '      )\n' + s[j + 1:]
    open(out_path, 'w', encoding='utf-8').write(s)


if __name__ == '__main__':
    dsn, ses, items, vias = load(sys.argv[1], sys.argv[2])
    if '--stitch' in sys.argv:
        out = sys.argv[sys.argv.index('--stitch') + 1]
        pts = stitch(dsn, ses, items, vias)
        write_vias(sys.argv[2], out, pts)
        print('stitch vias added:', len(pts), '->', out)
    islands, *_ = simulate(dsn, ses, items, vias)
    print('ground islands:', len(islands), '| pads per island:', [len(x) for x in islands])
    for x in islands[1:]: print('  cut off:', [n[4:] for n in x])
