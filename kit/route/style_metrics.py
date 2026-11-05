"""Style / manufacturability metrics for a routed SES, using the user's six routing-quality rules.

Rules -> measurable proxies (all signal nets only; GND is a pour helper and is excluded):

  R1 right-angle bends      interior corner angle < 135 deg          -> sharp_corners per 100 mm
  R2 uniform width          distinct signal widths / per-net spread  -> n_widths, nets_with_mixed_width
  R3 keep off board edge    signal copper closer than 0.6 mm to edge -> edge_hits, edge_len_mm
  R4 keep off mounting holes signal copper within 1.0 mm of M3 edge  -> hole_hits, hole_len_mm
  R5 tidy vias              vias on a 0.5 mm lattice                 -> via_grid_pct, via_spacing_cv
  R6 even bundles           parallel neighbours at 0.2-0.8 mm pitch  -> bundle_pairs, bundle_spacing_cv

usage:
  python style_metrics.py <x.ses> --dsn <prepared.dsn> [--json out.json]
  python style_metrics.py --compare route_runs/<board> [<more route.py folders> ...]
                          # rank every <id>/out.ses (geometry: <id>/in.dsn); with a ranking.json from
                          # batch_score.py --batch only its usable variants
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys

from shapely.geometry import LineString, Point, Polygon

import dsnses as BS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
_B = K.board()
GND = _B.gnd                                             # excluded: the ground net is poured, not styled
M3 = [tuple(map(float, h)) for h in _B.holes]            # mounting holes (mm)
M3_R = _B.hole_d / 2                                     # cut-out radius (mm)


def board_polygon(dsn_path):
    return Polygon(BS.Dsn(dsn_path).outline).buffer(0)


def _ang(a, b, c):
    """interior angle at b for polyline a-b-c, degrees (180 = straight)."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 180.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def metrics(ses_path, dsn_path, board=None):
    board = board or board_polygon(dsn_path)
    edge = board.exterior
    ses = BS.Ses(ses_path)
    sig = [w for w in ses.wires if w['net'] != GND]
    total_len = sum(w['length'] for w in sig)

    # R1 sharp corners
    corners = 0
    for w in sig:
        pts = w['pts']
        for k in range(1, len(pts) - 1):
            if _ang(pts[k - 1], pts[k], pts[k + 1]) < 135.0:
                corners += 1
    per100 = 100.0 * corners / total_len if total_len else 0.0

    # R2 width uniformity
    widths = collections.Counter(round(w['width'], 3) for w in sig)
    by_net = collections.defaultdict(set)
    for w in sig:
        by_net[w['net']].add(round(w['width'], 3))
    mixed = sum(1 for s in by_net.values() if len(s) > 1)

    # R3 / R4 edge + mounting-hole proximity (geometric, per segment)
    edge_hits = hole_hits = 0
    edge_len = hole_len = 0.0
    hole_pts = [Point(x, y) for x, y in M3]
    for w in sig:
        pts = w['pts']
        half = w['width'] / 2
        for a, b in zip(pts, pts[1:]):
            seg = LineString([a, b])
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg.distance(edge) - half < 0.6:
                edge_hits += 1
                edge_len += L
            for hp in hole_pts:
                if seg.distance(hp) - half - M3_R < 1.0:
                    hole_hits += 1
                    hole_len += L
                    break

    # R5 via lattice / spacing
    vx = [v['x'] for v in ses.vias]
    vy = [v['y'] for v in ses.vias]
    on_grid = 0
    for x, y in zip(vx, vy):
        dx = abs(x - round(x / 0.5) * 0.5)
        dy = abs(y - round(y / 0.5) * 0.5)
        if dx <= 0.15 and dy <= 0.15:
            on_grid += 1
    grid_pct = 100.0 * on_grid / len(vx) if vx else 0.0

    # via nearest-neighbour spacing regularity
    spacing = []
    order = sorted(zip(vx, vy))
    for i, (x, y) in enumerate(order):
        best = min((math.hypot(x - u, y - v) for j, (u, v) in enumerate(order) if j != i), default=None)
        if best is not None and best < 3.0:
            spacing.append(best)
    spacing_cv = (statistics.pstdev(spacing) / statistics.mean(spacing) * 100.0) if len(spacing) > 2 else 0.0

    # R6 parallel neighbours -> bundle pitch regularity (different nets only)
    segs = []
    for w in sig:
        pts = w['pts']
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L >= 0.8:
                segs.append((w['net'], a, b, math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi, L))
    gaps = []
    for i in range(len(segs)):
        n1, a1, b1, t1, l1 = segs[i]
        l1s = LineString([a1, b1])
        for j in range(i + 1, len(segs)):
            n2, a2, b2, t2, l2 = segs[j]
            if n1 == n2:
                continue
            dt = abs(t1 - t2)
            dt = min(dt, math.pi - dt)
            if dt > math.radians(3):
                continue
            l2s = LineString([a2, b2])
            if l1s.distance(l2s) > 0.8 or l1s.distance(l2s) < 0.18:
                continue
            # overlapping projection along the first segment
            dx, dy = b1[0] - a1[0], b1[1] - a1[1]
            ux, uy = dx / l1, dy / l1
            proj = sorted((((p[0] - a1[0]) * ux + (p[1] - a1[1]) * uy) for p in (a2, b2)))
            share = min(l1, proj[1]) - max(0.0, proj[0])
            if share > 0.8:
                gaps.append(l1s.distance(l2s))
    gap_cv = (statistics.pstdev(gaps) / statistics.mean(gaps) * 100.0) if len(gaps) > 5 else 0.0

    m = dict(ses=os.path.basename(os.path.dirname(os.path.abspath(ses_path))),
             signal_mm=round(total_len, 1),
             sharp_corners=corners, sharp_per100=round(per100, 2),
             n_widths=len(widths), nets_mixed_width=mixed,
             edge_hits=edge_hits, edge_len=round(edge_len, 1),
             hole_hits=hole_hits, hole_len=round(hole_len, 1),
             vias=len(ses.vias), via_grid_pct=round(grid_pct, 1), via_spacing_cv=round(spacing_cv, 1),
             bundle_pairs=len(gaps), bundle_spacing_cv=round(gap_cv, 1))
    # 0-100 style score: each rule mapped so lower-is-better metrics fall and "more bundling" rises
    s1 = 100.0 / (1.0 + per100 / 2.0)                      # 0 corners/100mm -> 100
    s2 = 100.0 * (1.0 - min(1.0, (len(widths) - 1) / 5.0)) * (1.0 - min(1.0, mixed / 10.0))
    s3 = max(0.0, 100.0 - edge_len * 4.0)                  # every mm of edge-hugging copper costs
    s4 = max(0.0, 100.0 - hole_len * 8.0)
    s5 = 0.6 * grid_pct + 0.4 * max(0.0, 100.0 - spacing_cv)
    s6 = min(100.0, len(gaps) * 2.0) * (1.0 - min(1.0, gap_cv / 60.0))
    m['style'] = round(0.20 * s1 + 0.15 * s2 + 0.20 * s3 + 0.15 * s4 + 0.15 * s5 + 0.15 * s6, 1)
    return m


def compare(roots):
    rows = []
    for root in roots:
        rj = os.path.join(root, 'ranking.json')
        ok = set()
        if os.path.exists(rj):
            try:
                ok = {x['id'] for x in json.load(open(rj, encoding='utf-8'))['rows'] if x.get('ok')}
            except (KeyError, ValueError):
                ok = set()
        for name in sorted(os.listdir(root)):
            ses = os.path.join(root, name, 'out.ses')
            if not os.path.exists(ses) or (ok and name not in ok):
                continue
            try:
                rows.append(metrics(ses, os.path.join(root, name, 'in.dsn')))
            except Exception as e:                          # noqa: BLE001
                print('skip', name, e)
    rows.sort(key=lambda m: -m['style'])
    hdr = ('ses', 'style', 'sharp/100', 'widths', 'mixed', 'edge_hits', 'hole_hits',
           'via_grid%', 'via_sp_cv', 'bundle_pairs', 'bundle_cv', 'vias')
    print(' | '.join(hdr))
    for m in rows:
        print(' | '.join(str(m[k]) for k in ('ses', 'style', 'sharp_per100', 'n_widths', 'nets_mixed_width',
                                             'edge_hits', 'hole_hits', 'via_grid_pct', 'via_spacing_cv',
                                             'bundle_pairs', 'bundle_spacing_cv', 'vias')))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ses', nargs='?')
    ap.add_argument('--dsn', default=None, help='the prepared DSN the SES was routed from')
    ap.add_argument('--json', default=None)
    ap.add_argument('--compare', nargs='+', default=None, help='route.py output folders')
    a = ap.parse_args()
    if a.compare:
        compare(a.compare)
        return
    if not a.ses or not a.dsn:
        ap.error('give <x.ses> --dsn <prepared.dsn>, or --compare route_runs/<board>')
    m = metrics(a.ses, a.dsn)
    if a.json:
        json.dump(m, open(a.json, 'w', encoding='utf-8'), indent=1)
    print(json.dumps(m, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
