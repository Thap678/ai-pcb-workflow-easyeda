"""Ratsnest report of a placement, to improve the layout BEFORE routing: airwire length, crossings, the parts and
cells that cause them. Lower crossings / length = fewer vias and a board that routes at all.

usage: python ratsnest.py [dump.json] [--png out.png] [--top 12] [--skip NET ...]
       without a dump it snapshots the live board (kit/dump.py). GND and the split-plane supply nets
       ([boards.<name>.planes] regions) are skipped: they go to pours / planes, not to tracks.

Airwires are a per-net minimum spanning tree over the pad centres. A crossing is two airwires of different nets
that intersect. Compare the numbers before and after moving / rotating a part or swapping equivalent pins; keep a
change only when crossings (first) or length (second) go down.
"""
import argparse, collections, json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import LineString  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
import common as K  # noqa: E402

MM = 0.0254


def airwires(d, skip):
    nets = collections.defaultdict(list)
    for c in d['components']:
        for p in c['pads']:
            if p.get('net') and p['net'] not in skip:
                nets[p['net']].append((p['x'] * MM, p['y'] * MM, c['designator']))
    out = []
    for net, pts in nets.items():
        pts = list(dict.fromkeys(pts))
        used, rest = [pts[0]], pts[1:]
        while rest:
            dd, i, j = min((math.dist(u[:2], w[:2]), i, j) for i, u in enumerate(used) for j, w in enumerate(rest))
            if dd > 1e-6: out.append((net, used[i], rest[j], dd))
            used.append(rest.pop(j))
    return out


def report(d, skip, top=12):
    aw = airwires(d, skip)
    segs = [LineString([a[:2], b[:2]]) for _, a, b, _ in aw]
    tree, cross = STRtree(segs), collections.Counter()
    pairs = 0
    for i, s in enumerate(segs):
        for j in tree.query(s):
            j = int(j)
            if j > i and aw[i][0] != aw[j][0] and s.crosses(segs[j]):
                pairs += 1; cross[i] += 1; cross[j] += 1
    part, netx, netl = collections.Counter(), collections.Counter(), collections.Counter()
    for i, (net, a, b, dd) in enumerate(aw):
        netl[net] += dd; netx[net] += cross[i]
        for r in {a[2], b[2]}: part[r] += cross[i]
    cell = collections.Counter()
    for i, (net, a, b, dd) in enumerate(aw):
        k = max(1, int(dd / 0.5))
        for c in {(int((a[0] + (b[0] - a[0]) * t / k) // 2), int((a[1] + (b[1] - a[1]) * t / k) // 2)) for t in range(k + 1)}:
            cell[c] += 1
    print(f'airwires {len(aw)}  length {sum(a[3] for a in aw):.0f} mm  crossings {pairs}')
    print('parts on the most crossing airwires:', ', '.join(f'{r} {n}' for r, n in part.most_common(top) if n))
    print('nets with the most crossings:', ', '.join(f'{n} {x} ({netl[n]:.0f} mm)' for n, x in netx.most_common(top) if x))
    print('longest nets:', ', '.join(f'{n} {l:.0f} mm' for n, l in netl.most_common(top)))
    print('densest 2 mm cells (x, y mm: airwires):', ', '.join(f'({c[0] * 2}, {c[1] * 2}): {n}' for c, n in cell.most_common(6)))
    return aw, cross


def draw(d, aw, cross, out, s=12):
    from PIL import Image, ImageDraw
    xs = [x * MM for x, _ in d['outline']['points']]; ys = [y * MM for _, y in d['outline']['points']]
    x0, y0, x1, y1 = min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2
    P = lambda x, y: ((x - x0) * s, (y1 - y) * s)
    im = Image.new('RGB', (int((x1 - x0) * s), int((y1 - y0) * s)), 'white'); g = ImageDraw.Draw(im)
    g.line([P(x * MM, y * MM) for x, y in d['outline']['points']] + [P(xs[0], ys[0])], fill='black', width=2)
    for c in d['components']:
        px = [p['x'] * MM for p in c['pads']] or [c['x'] * MM]; py = [p['y'] * MM for p in c['pads']] or [c['y'] * MM]
        a, b = P(min(px) - .4, max(py) + .4), P(max(px) + .4, min(py) - .4)
        g.rectangle([a, b], outline=(120, 120, 120) if c.get('layer', 1) == 1 else (90, 90, 220))
        g.text((a[0] + 2, a[1] + 1), c['designator'], fill=(60, 60, 60))
    for i, (_, a, b, _) in enumerate(aw):
        n = cross[i]
        g.line([P(*a[:2]), P(*b[:2])], fill=(0, 150, 0) if n == 0 else (230, 140, 0) if n < 3 else (220, 0, 0), width=1 if n == 0 else 2)
    im.save(out); print('->', out, '(green: no crossing, orange: 1-2, red: 3 or more)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dump', nargs='?'); ap.add_argument('--png'); ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--skip', nargs='*', default=[])
    a = ap.parse_args()
    B = K.try_board()
    if a.dump:
        d = K.load_dump(a.dump)
    else:
        d = K.dump(B.path('ratsnest_dump.json'))
    skip = set(a.skip) | {B.gnd if B else 'GND'} | ({r['net'] for r in B.planes.get('regions', [])} if B else set())
    aw, cross = report(d, skip, a.top)
    if a.png: draw(d, aw, cross, a.png)


if __name__ == '__main__':
    main()
