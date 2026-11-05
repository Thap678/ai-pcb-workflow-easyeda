"""Offline SES polish: make the router's output look like a hand-routed board.

Freerouting optimises connectivity, not looks.  This script rewrites the SES geometry directly, applying the
user's routing-quality rules with a geometric clearance check before every edit (an edit is reverted when it
would break the 0.20 mm router clearance, the 0.35 mm board-edge rule or the 0.5 mm slot rule):

  R1 right angles      : chamfer corners sharper than 135 deg (0.25 mm 45-deg cut)
  R2 uniform width     : widen the router's neck-down segments back to the net's class width
  R3 board edge        : push interior vertices away from the board edge (target 0.7 mm copper-to-edge)
  R4 mounting holes    : push interior vertices away from the 4 x M3 cut-outs (target 1.2 mm edge-to-edge)
  R5 tidy vias         : snap vias onto a 0.5 mm lattice (max 0.2 mm move), dragging their track endpoints
  R6 even bundles      : align straight via-to-via runs that form a parallel bundle to the median pitch

usage:
  python ses_polish.py <in.ses> --dsn <prepared.dsn> [--out out.ses] [--report r.json]
         [--chamfer 0.25] [--edge-target 0.7] [--hole-target 1.2] [--via-grid 0.5] [--no-bundle]

R6 never moves a vertex that is a wire terminal (it would detach an end-to-end or T joint; an earlier version
opened EC11_B that way). Still run conn.py on the imported copper.
"""
import argparse
import collections
import json
import math
import os

from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

import sys
import dsnses as BS
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

_B = K.board()
CLR = _B.route.get('router_clearance_mm', 0.20)                       # router clearance the DSN was prepared with (mm)
EDGE_HARD = _B.route.get('boundary_inset_mm', 0.15) + CLR             # copper-to-board-edge minimum (mm)
SLOT_HARD = _B.rules.get('slot_pour_mm', 0.50)                         # copper-to-mounting-hole minimum (mm)
EDGE_TARGET = 0.70      # style target copper-to-edge
HOLE_TARGET = 1.20      # style target copper-to-hole-edge
M3 = [tuple(map(float, h)) for h in _B.holes]                          # mounting holes (mm)
M3_R = _B.hole_d / 2
LAY = {'TopLayer': 1, 'BottomLayer': 2}


def load_dsn(dsn_path):
    dsn = BS.Dsn(dsn_path)
    board = Polygon(dsn.outline).buffer(0)
    pads = {1: [], 2: []}
    for p in dsn.pads:
        if p['kind'] == 'circle':
            g = Point(p['x'], p['y']).buffer(p['r'])
        else:
            g = Polygon(p['pts']).buffer(0)
        for L in {1: [1], 2: [2], 12: [1, 2]}.get(p['layer'], [1, 2]):
            pads[L].append((p['net'], g))
    return dsn, board, pads


class Polish:
    def __init__(self, ses_path, dsn_path):
        self.ses_path = ses_path
        self.ses = BS.Ses(ses_path)
        self.unit = self.ses.unit_mm
        self.via_diam = self.ses.via_diam
        self.dsn, self.board, self.pads = load_dsn(dsn_path)
        self.edge = self.board.exterior
        self.holes = [Point(x, y) for x, y in M3]
        self.wires = [dict(net=w['net'], layer=LAY[w['layer']], width=w['width'],
                           pts=[(round(x, 4), round(y, 4)) for x, y in w['pts']]) for w in self.ses.wires]
        self.vias = [dict(net=v['net'], x=round(v['x'], 4), y=round(v['y'], 4), padstack=v['padstack'])
                     for v in self.ses.vias]
        self.stats = collections.Counter()
        self.rebuild()

    # ---------------------------------------------------------------- geometry index
    def rebuild(self):
        self.obst = {1: [], 2: []}
        for L in (1, 2):
            self.obst[L] += [(n, g) for n, g in self.pads[L]]
        for w in self.wires:
            half = w['width'] / 2
            for a, b in zip(w['pts'], w['pts'][1:]):
                self.obst[w['layer']].append(
                    (w['net'], LineString([a, b]).buffer(half, cap_style=2, join_style=2)))
        for v in self.vias:
            g = Point(v['x'], v['y']).buffer(self.via_diam / 2)
            for L in (1, 2):
                self.obst[L].append((v['net'], g))
        self.tree = {L: STRtree([g for _, g in self.obst[L]]) for L in (1, 2)}
        self.nets = {L: [n for n, _ in self.obst[L]] for L in (1, 2)}

    def clear(self, layer, net, centerline, width):
        """True when this centerline keeps CLR to other nets, EDGE_HARD to the edge, SLOT_HARD to the holes."""
        cand = centerline.buffer(width / 2, cap_style=2, join_style=2)
        if cand.distance(self.edge) < EDGE_HARD:
            return False
        for h in self.holes:
            if cand.distance(h) < M3_R + SLOT_HARD:
                return False
        for k in self.tree[layer].query(cand.buffer(CLR + 1.0)):
            if self.nets[layer][k] == net:
                continue
            if cand.distance(self.obst[layer][k][1]) < CLR:
                return False
        return True

    # ---------------------------------------------------------------- R2 width
    def pass_width(self):
        target = {}
        for w in self.wires:
            target[w['net']] = max(target.get(w['net'], 0.0), w['width'])
        for w in self.wires:
            if w['width'] < target[w['net']] - 0.005:
                ok = all(self.clear(w['layer'], w['net'], LineString([a, b]), target[w['net']])
                         for a, b in zip(w['pts'], w['pts'][1:]))
                if ok:
                    w['width'] = target[w['net']]
                    self.stats['width_widened'] += 1
                else:
                    self.stats['width_kept'] += 1
        self.rebuild()

    # ---------------------------------------------------------------- R1 chamfer
    def pass_chamfer(self, cut):
        for w in self.wires:
            if len(w['pts']) < 3 or cut <= 0:
                continue
            L, net, width = w['layer'], w['net'], w['width']
            pts, new = w['pts'], [w['pts'][0]]
            for i in range(1, len(pts) - 1):
                p0, p1, p2 = new[-1], pts[i], pts[i + 1]
                va = (p1[0] - p0[0], p1[1] - p0[1])
                vb = (p2[0] - p1[0], p2[1] - p1[1])
                la, lb = math.hypot(*va), math.hypot(*vb)
                if la < 1e-6 or lb < 1e-6:
                    new.append(p1)
                    continue
                ang = math.degrees(math.acos(max(-1, min(1, (va[0] * vb[0] + va[1] * vb[1]) / (la * lb)))))
                if ang >= 135.0 or la < 3 * cut or lb < 3 * cut:
                    new.append(p1)
                    continue
                q1 = (p1[0] - va[0] / la * cut, p1[1] - va[1] / la * cut)
                q2 = (p1[0] + vb[0] / lb * cut, p1[1] + vb[1] / lb * cut)
                if (self.clear(L, net, LineString([p0, q1]), width)
                        and self.clear(L, net, LineString([q1, q2]), width)
                        and self.clear(L, net, LineString([q2, p2]), width)):
                    new += [q1, q2]
                    self.stats['chamfered'] += 1
                else:
                    new.append(p1)
                    self.stats['chamfer_kept'] += 1
            new.append(pts[-1])
            w['pts'] = new
        self.rebuild()

    # ---------------------------------------------------------------- R3 + R4 edge/holes
    def pass_edge_holes(self, edge_target, hole_target, rounds=2):
        for _ in range(rounds):
            moved = 0
            for w in self.wires:
                pts, L, net, width = w['pts'], w['layer'], w['net'], w['width']
                for i in range(1, len(pts) - 1):          # interior vertices only: endpoints stay on pads/vias
                    p = pts[i]
                    dx = dy = 0.0
                    d_edge = Point(p).distance(self.edge)
                    need = (edge_target + width / 2) - d_edge
                    if need > 0:
                        near = self.edge.interpolate(self.edge.project(Point(p)))
                        vx, vy = p[0] - near.x, p[1] - near.y
                        n = math.hypot(vx, vy) or 1.0
                        dx += vx / n * min(need, 0.30)
                        dy += vy / n * min(need, 0.30)
                    for h in self.holes:
                        d = math.hypot(p[0] - h.x, p[1] - h.y)
                        need = (M3_R + hole_target + width / 2) - d
                        if need > 0:
                            vx, vy = p[0] - h.x, p[1] - h.y
                            n = math.hypot(vx, vy) or 1.0
                            dx += vx / n * min(need, 0.35)
                            dy += vy / n * min(need, 0.35)
                    if dx == 0 and dy == 0:
                        continue
                    q = (round(p[0] + dx, 4), round(p[1] + dy, 4))
                    old = pts[i]
                    pts[i] = q
                    ok = (self.clear(L, net, LineString([pts[i - 1], q]), width)
                          and self.clear(L, net, LineString([q, pts[i + 1]]), width))
                    if ok:
                        moved += 1
                    else:
                        pts[i] = old
                w['pts'] = pts
            self.stats['edge_hole_vertices_moved'] += moved
            self.rebuild()
            if moved == 0:
                break

    # ---------------------------------------------------------------- R6 bundles
    def pass_bundle(self, max_shift=0.35):
        # vias indexed by rounded position
        vmap = collections.defaultdict(list)
        for vi, v in enumerate(self.vias):
            vmap[(round(v['x'], 2), round(v['y'], 2))].append(vi)

        def via_at(p):
            return vmap.get((round(p[0], 2), round(p[1], 2)))

        runs = []
        for wi, w in enumerate(self.wires):
            if len(w['pts']) != 2:
                continue
            a, b = w['pts']
            va, vb = via_at(a), via_at(b)
            if not va or not vb:
                continue
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 1.5:
                continue
            ang = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            px, py = -uy, ux
            off = a[0] * px + a[1] * py
            runs.append(dict(wi=wi, layer=w['layer'], net=w['net'], ang=ang, u=(ux, uy), p=(px, py),
                             off=off, a=a, b=b, vias=set(va + vb), L=L))
        # group by layer + direction bucket
        groups = collections.defaultdict(list)
        for r in runs:
            groups[(r['layer'], round(r['ang'] / math.radians(5)))].append(r)
        bundles = 0
        for _, rs in groups.items():
            rs.sort(key=lambda r: r['off'])
            chunk = []
            def flush(chunk):
                nonlocal bundles
                if len(chunk) < 3:
                    return
                offs = [r['off'] for r in chunk]
                diffs = [offs[i + 1] - offs[i] for i in range(len(offs) - 1)]
                pitch = sorted(diffs)[len(diffs) // 2]
                if not (0.20 <= pitch <= 1.0):
                    return
                mid = (offs[0] + offs[-1]) / 2
                bundles += 1
                for i, r in enumerate(chunk):
                    want = mid + (i - (len(chunk) - 1) / 2) * pitch
                    shift = want - r['off']
                    if abs(shift) < 0.02 or abs(shift) > max_shift:
                        continue
                    dx, dy = r['p'][0] * shift, r['p'][1] * shift
                    wa = self.wires[r['wi']]
                    newpts = [(round(wa['pts'][0][0] + dx, 4), round(wa['pts'][0][1] + dy, 4)),
                              (round(wa['pts'][1][0] + dx, 4), round(wa['pts'][1][1] + dy, 4))]
                    if not self.clear(r['layer'], r['net'], LineString(newpts), wa['width']):
                        continue
                    # move the two vias + every wire endpoint attached to them
                    move_ok = True
                    for vi in r['vias']:
                        v = self.vias[vi]
                        nx, ny = v['x'] + dx, v['y'] + dy
                        cand = Point(nx, ny).buffer(self.via_diam / 2)
                        if cand.distance(self.edge) < EDGE_HARD + 0.02:
                            move_ok = False
                            break
                        for h in self.holes:
                            if cand.distance(h) < M3_R + SLOT_HARD:
                                move_ok = False
                                break
                        if not move_ok:
                            break
                        for k in self.tree[1].query(cand.buffer(CLR + 1.0)):
                            if self.nets[1][k] == v['net']:
                                continue
                            if cand.distance(self.obst[1][k][1]) < CLR:
                                move_ok = False
                                break
                        if not move_ok:
                            break
                    if not move_ok:
                        continue
                    for vi in r['vias']:
                        v = self.vias[vi]
                        ox, oy = v['x'], v['y']
                        v['x'], v['y'] = round(ox + dx, 4), round(oy + dy, 4)
                        for wj, w in enumerate(self.wires):
                            for pi, p in enumerate(w['pts']):
                                if abs(p[0] - ox) < 2e-3 and abs(p[1] - oy) < 2e-3:
                                    w['pts'][pi] = (v['x'], v['y'])
                    self.wires[r['wi']]['pts'] = newpts
                    self.stats['bundled_runs'] += 1
            for r in rs:
                if chunk and r['off'] - chunk[-1]['off'] > 1.2:
                    flush(chunk)
                    chunk = []
                chunk.append(r)
            flush(chunk)
            self.rebuild()
        self.stats['bundles'] = bundles

    # ---------------------------------------------------------------- R6 bundles (segment level)
    def pass_bundle2(self, min_len=2.0, max_shift=0.20, rounds=2):
        via_pos = {(round(v['x'], 3), round(v['y'], 3)) for v in self.vias}
        pad_pos = {1: set(), 2: set()}
        for p in self.dsn.pads:
            for L in {1: [1], 2: [2], 12: [1, 2]}.get(p['layer'], [1, 2]):
                pad_pos[L].add((round(p['x'], 2), round(p['y'], 2)))

        for _ in range(rounds):
            # wire terminals are joints (end-to-end or T into another wire): a vertex there must not move
            joints = {1: set(), 2: set()}
            for w in self.wires:
                for p in (w['pts'][0], w['pts'][-1]):
                    joints[w['layer']].add((round(p[0], 3), round(p[1], 3)))
            segs = []
            for wi, w in enumerate(self.wires):
                for k, (a, b) in enumerate(zip(w['pts'], w['pts'][1:])):
                    L = math.hypot(b[0] - a[0], b[1] - a[1])
                    if L < min_len:
                        continue
                    ang = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
                    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
                    px, py = -uy, ux
                    off = (a[0] * px + a[1] * py + b[0] * px + b[1] * py) / 2
                    segs.append(dict(wi=wi, k=k, layer=w['layer'], net=w['net'], width=w['width'],
                                     a=a, b=b, ang=ang, p=(px, py), off=off, L=L))
            groups = collections.defaultdict(list)
            for s in segs:
                groups[(s['layer'], round(s['ang'] / math.radians(5)))].append(s)

            moved = 0
            for _, rs in groups.items():
                rs.sort(key=lambda s: s['off'])
                chunk = []

                def flush(chunk):
                    nonlocal moved
                    if len(chunk) < 3:
                        return
                    offs = [s['off'] for s in chunk]
                    diffs = [offs[i + 1] - offs[i] for i in range(len(offs) - 1)]
                    pitch = sorted(diffs)[len(diffs) // 2]
                    if not (0.20 <= pitch <= 1.0):
                        return
                    mid = (offs[0] + offs[-1]) / 2
                    for i, s in enumerate(chunk):
                        shift = mid + (i - (len(chunk) - 1) / 2) * pitch - s['off']
                        if abs(shift) < 0.02 or abs(shift) > max_shift:
                            continue
                        w = self.wires[s['wi']]
                        p0, p1 = s['a'], s['b']
                        if any((round(p[0], 3), round(p[1], 3)) in via_pos for p in (p0, p1)):
                            continue
                        if any((round(p[0], 2), round(p[1], 2)) in pad_pos[s['layer']] for p in (p0, p1)):
                            continue
                        if any((round(p[0], 3), round(p[1], 3)) in joints[s['layer']] for p in (p0, p1)):
                            continue
                        dx, dy = s['p'][0] * shift, s['p'][1] * shift
                        na = (round(p0[0] + dx, 4), round(p0[1] + dy, 4))
                        nb = (round(p1[0] + dx, 4), round(p1[1] + dy, 4))
                        old_a, old_b = w['pts'][s['k']], w['pts'][s['k'] + 1]
                        w['pts'][s['k']], w['pts'][s['k'] + 1] = na, nb
                        ok = True
                        for j in (s['k'], s['k'] + 1):
                            for nb_i in (j - 1, j):
                                if 0 <= nb_i < len(w['pts']) - 1:
                                    if not self.clear(s['layer'], s['net'],
                                                      LineString([w['pts'][nb_i], w['pts'][nb_i + 1]]), s['width']):
                                        ok = False
                        if ok:
                            moved += 1
                        else:
                            w['pts'][s['k']], w['pts'][s['k'] + 1] = old_a, old_b

                for s in rs:
                    if chunk and s['off'] - chunk[-1]['off'] > 1.0:
                        flush(chunk)
                        chunk = []
                    chunk.append(s)
                flush(chunk)
            self.stats['bundle_segments_moved'] += moved
            self.rebuild()
            if moved == 0:
                break

    # ---------------------------------------------------------------- R5 via lattice
    def pass_via_grid(self, grid=0.5, max_shift=0.2):
        for vi, v in enumerate(self.vias):
            tx = round(v['x'] / grid) * grid
            ty = round(v['y'] / grid) * grid
            dx, dy = tx - v['x'], ty - v['y']
            if abs(dx) < 2e-3 and abs(dy) < 2e-3:
                self.stats['vias_on_grid'] += 1
                continue
            if abs(dx) > max_shift or abs(dy) > max_shift:
                continue
            cand = Point(tx, ty).buffer(self.via_diam / 2)
            if cand.distance(self.edge) < EDGE_HARD:
                continue
            ok = True
            for h in self.holes:
                if cand.distance(h) < M3_R + SLOT_HARD:
                    ok = False
                    break
            if not ok:
                continue
            # drill-to-drill >= 0.8 mm centre spacing to every other via (EDA reports Hole to Hole otherwise)
            if any(oi != vi and math.hypot(o['x'] - tx, o['y'] - ty) < 0.8 for oi, o in enumerate(self.vias)):
                continue
            for L in (1, 2):
                for k in self.tree[L].query(cand.buffer(CLR + 1.0)):
                    if self.nets[L][k] == v['net']:
                        continue
                    if cand.distance(self.obst[L][k][1]) < CLR:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            # tentative: move via + attached endpoints, then validate the touched segments
            ox, oy = v['x'], v['y']
            touched = []
            for w in self.wires:
                for pi, p in enumerate(w['pts']):
                    if abs(p[0] - ox) < 2e-3 and abs(p[1] - oy) < 2e-3:
                        touched.append((w, pi))
            good = True
            for w, pi in touched:
                old = w['pts'][pi]
                w['pts'][pi] = (round(tx, 4), round(ty, 4))
                nbrs = [p for j, p in enumerate(w['pts']) if abs(j - pi) == 1]
                for n in nbrs:
                    if not self.clear(w['layer'], w['net'], LineString([w['pts'][pi], n]), w['width']):
                        good = False
                w['pts'][pi] = old
                if not good:
                    break
            if not good:
                continue
            v['x'], v['y'] = round(tx, 4), round(ty, 4)
            for w, pi in touched:
                w['pts'][pi] = (v['x'], v['y'])
            self.stats['vias_snapped'] += 1
        self.rebuild()

    # ---------------------------------------------------------------- audit + write
    def audit(self):
        bad = 0
        for w in self.wires:
            for a, b in zip(w['pts'], w['pts'][1:]):
                if not self.clear(w['layer'], w['net'], LineString([a, b]) if False else LineString([a, b]), w['width']):
                    bad += 1
        return bad

    def write(self, out):
        src = open(self.ses_path, encoding='utf-8', errors='replace').read()
        prefix = src[:src.index('(network_out')]
        nets = {}
        for w in self.wires:
            d = nets.setdefault(w['net'], dict(wires=[], vias=[]))
            d['wires'].append(dict(layer='TopLayer' if w['layer'] == 1 else 'BottomLayer',
                                   width=w['width'], pts=w['pts']))
        for v in self.vias:
            nets.setdefault(v['net'], dict(wires=[], vias=[]))['vias'].append(v)
        u = self.unit
        lines = [prefix, '      (network_out']
        for net, d in nets.items():
            lines.append(f'      (net {net}')
            for w in d['wires']:
                lines.append(f'        (wire\n          (path {w["layer"]} {int(round(w["width"] / u))}\n'
                             + ''.join(f'            {int(round(x / u))} {int(round(y / u))}\n' for x, y in w['pts'])
                             + '          )\n        )')
            for v in d['vias']:
                lines.append(f'        (via {v["padstack"]} {int(round(v["x"] / u))} {int(round(v["y"] / u))})')
            lines.append('      )')
        lines.append('    )\n  )\n)\n')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        open(out, 'w', encoding='utf-8').write('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ses')
    ap.add_argument('--dsn', required=True, help='the prepared DSN the SES was routed from')
    ap.add_argument('--out', default=None)
    ap.add_argument('--report', default=None)
    ap.add_argument('--chamfer', type=float, default=0.25)
    ap.add_argument('--edge-target', type=float, default=EDGE_TARGET)
    ap.add_argument('--hole-target', type=float, default=HOLE_TARGET)
    ap.add_argument('--via-grid', type=float, default=0.5)
    ap.add_argument('--no-bundle', action='store_true')
    ap.add_argument('--no-via-grid', action='store_true')
    a = ap.parse_args()

    p = Polish(a.ses, a.dsn)
    bad0 = p.audit()
    p.pass_width()
    p.pass_chamfer(a.chamfer)
    p.pass_edge_holes(a.edge_target, a.hole_target)
    if not a.no_bundle:
        p.pass_bundle2()
    if not a.no_via_grid:
        p.pass_via_grid(a.via_grid)
    bad = p.audit()
    out = a.out or (os.path.splitext(a.ses)[0] + '_polished.ses')
    p.write(out)
    rep = dict(input=a.ses, output=out, clearance_violations_before=bad0, clearance_violations_after=bad,
               **dict(p.stats))
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        json.dump(rep, open(a.report, 'w', encoding='utf-8'), indent=1)
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
