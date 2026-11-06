"""Per-net copper connectivity from a `pcb dump --include-copper` snapshot (units mil).

Pads, track segments, arcs and vias of one net are joined when they touch (end inside a pad, endpoints within the
half-widths, via on a track end). A net with N islands has N-1 unrouted connections. Pours are ignored.
usage: python conn.py dump.json
Library use: `islands(dump_dict) -> {net: [[pad names of one island], ...]}` for every net with more than one.
"""
import json, sys, math, collections


def _items(d):
    cu = d.get('copper') or {}
    items = collections.defaultdict(list)          # net -> [(kind, geometry, layers, name)]
    for c in d['components']:
        for p in c.get('pads', []):
            if p.get('net'):
                layers = {1, 2} if p.get('layer') in (12, None) or c['designator'].startswith(('J', 'SW', 'BZ', 'H')) and p.get('layer') != 1 else {p.get('layer')}
                r = max(p.get('width', 0), p.get('height', 0)) / 2
                items[p['net']].append(('pad', (p['x'], p['y'], r), layers, f"{c['designator']}.{p['padNumber']}"))
    # an arc only joins things at its two ends, so its chord stands in for it
    for l in (cu.get('lines') or []) + (cu.get('arcs') or []):
        if l.get('net') and 'startX' in l:
            items[l['net']].append(('line', (l['startX'], l['startY'], l['endX'], l['endY'], l.get('lineWidth', 6) / 2), {l['layer']}, l['primitiveId']))
    for v in cu.get('vias') or []:
        if v.get('net'):
            items[v['net']].append(('via', (v['x'], v['y'], v.get('diameter', 24) / 2), {1, 2}, v['primitiveId']))
    return items


def _pts(it):
    g = it[1]
    if it[0] == 'line': return [(g[0], g[1], g[4]), (g[2], g[3], g[4])]
    return [(g[0], g[1], g[2])]


def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _touch(a, b):
    if not (a[2] & b[2]): return False
    for (x, y, r) in _pts(a):
        if b[0] == 'line':
            g = b[1]
            if seg_dist(x, y, g[0], g[1], g[2], g[3]) <= r + g[4] + 0.5: return True
        else:
            for (u, v, q) in _pts(b):
                if math.hypot(x - u, y - v) <= r + q + 0.5: return True
    for (x, y, r) in _pts(b):
        if a[0] == 'line':
            g = a[1]
            if seg_dist(x, y, g[0], g[1], g[2], g[3]) <= r + g[4] + 0.5: return True
    return False


def islands(d, nets=None):
    """{net: [[pad names], ...]} for every net (or every net in `nets`) whose pads sit on more than one island."""
    bad = {}
    for net, its in _items(d).items():
        if nets is not None and net not in nets: continue
        par = list(range(len(its)))
        def f(i):
            while par[i] != i: par[i] = par[par[i]]; i = par[i]
            return i
        for i in range(len(its)):
            for j in range(i + 1, len(its)):
                if f(i) != f(j) and _touch(its[i], its[j]): par[f(i)] = f(j)
        groups = collections.defaultdict(list)
        for i, it in enumerate(its): groups[f(i)].append(it)
        pad_groups = [g for g in ([it[3] for it in g if it[0] == 'pad'] for g in groups.values()) if g]
        if len(pad_groups) > 1: bad[net] = pad_groups
    return bad


if __name__ == '__main__':
    s = open(sys.argv[1], encoding='utf-8').read(); d = json.loads(s[s.find('{'):])
    d = d.get('result', d) if 'components' not in d else d
    bad = islands(d)
    print('nets', len(_items(d)), '| nets with unrouted connections:', len(bad))
    for n, g in bad.items(): print(' ', n, [sorted(x)[:6] for x in g])
