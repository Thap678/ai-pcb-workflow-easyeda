"""Offline scorer for the Freerouting batch runs (round 1, 第 6.5 节批量布线调参).

Two artefacts are parsed (both are S-expression formats):

  DSN  route/boardR_prep.dsn       geometry only: pad shapes from `library/image/pin` + `padstack`, net members
                                   from `network`, board outline from `boundary`.  NOTE: EasyEDA writes DSN
                                   structure/library coordinates in MIL even though it declares
                                   `(resolution mil 1000)`; scale is auto-detected from the boundary bbox.
  SES  <run>/out.ses               routed copper: `(path <layer> <width> x1 y1 …)`, `(via <padstack> x y)`.
                                   Coordinates/widths are in resolution units (1/1000 mil -> /1000*0.0254 mm).

Metrics per run: via count (total / non-GND / GND), signal length per layer, width histogram,
unrouted/violations read from the run log, and an *approximate* ground-integrity score that follows the
gmetrics.py formula without EasyEDA's real pour geometry:
    occupied(layer) = non-GND tracks (inflated by width/2 + 0.25 mm pour clearance) + non-GND pads/vias
    free(layer)     = board inset by the 0.3 mm pour clearance - occupied
    S = 1.0*BOT + 0.5*TOP + 0.4*BOT_largest + 0.3*(BOT+TOP)/2 - 0.3*(BOT_pieces-1) - 2*dead
        - 0.005*BOT_sig_len_mm                 (all cover terms in % of the 80x87 mm board)
`free` is used as the "pour tied to main GND" area (optimistic), see ground_opt vs ground_strict below.

usage:
  python batch_score.py <x.ses> [--dsn boardR_prep.dsn] [--json out.json]
  python batch_score.py --round1 [--root batch_runs/round1]      # score a batch, writes ranking.md/json
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DSN_DEFAULT = os.path.join(HERE, 'boardR_prep.dsn')
BASELINE_SES = os.path.join(HERE, 'boardC_routed.ses')
BASELINE_LOG = os.path.join(HERE, 'boardC_route2.log')
CELL = 0.1                  # scoring raster, mm
POUR_CLEAR = 0.25           # pour-to-copper clearance, mm (design rule)
POUR_INSET = 0.3            # pour-to-board-edge clearance, mm
BOARD = (80.0, 87.0)        # fallback; normally derived from the DSN boundary + 0.15 mm inset


# ----------------------------------------------------------------------------- S-expression parser
def parse_sexpr(text):
    stack, i, n = [[]], 0, len(text)
    while i < n:
        c = text[i]
        if c == '(':
            stack.append([])
            i += 1
        elif c == ')':
            node = stack.pop()
            if stack:
                stack[-1].append(node)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            stack[-1].append(text[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return stack[0][0] if stack and stack[0] else []


def child(node, head):
    for c in node[1:] if node and isinstance(node[0], str) else node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def children(node, head):
    out = []
    for c in (node[1:] if node and isinstance(node[0], str) else node):
        if isinstance(c, list) and c and c[0] == head:
            out.append(c)
    return out


def fnum(x):
    return float(x)


# ----------------------------------------------------------------------------- DSN
class Dsn:
    """Pads (per layer) + net membership + board size from a Freerouting DSN."""

    def __init__(self, path):
        self.path = path
        text = open(path, encoding='utf-8', errors='replace').read()
        root = parse_sexpr(text)
        structure = child(root, 'structure')
        if structure is None:
            raise ValueError(f'{path}: no (structure ...)')
        # coordinate scale: EasyEDA writes DSN coords in mil regardless of (resolution mil 1000)
        bnd = child(structure, 'boundary')
        pts = [float(v) for v in child(bnd, 'path') if re.fullmatch(r'-?\d+(\.\d+)?', v)][2:]
        xs, ys = pts[0::2], pts[1::2]
        bbox = (min(xs), max(xs), min(ys), max(ys))
        self.unit_mm = 0.0254 if bbox[1] < 1e4 else 0.0254 / 1000       # mil vs 1/1000 mil
        inset = 0.15 / self.unit_mm
        self.board = ((bbox[1] - bbox[0]) * self.unit_mm + 2 * 0.15,
                      (bbox[3] - bbox[2]) * self.unit_mm + 2 * 0.15)

        lib = child(root, 'library')
        padstacks = {}
        for ps in children(lib, 'padstack'):
            shapes = []
            for sh in children(ps, 'shape'):
                if not (isinstance(sh[1], list) and sh[1]):
                    continue
                kind, layer = sh[1][0], sh[1][1]
                nums = [float(v) for v in sh[1][2:]]
                if kind == 'circle':
                    d = nums[0]
                    dx = nums[1] if len(nums) > 1 else 0.0
                    dy = nums[2] if len(nums) > 2 else 0.0
                    shapes.append((layer, 'circle', (dx, dy, d / 2)))
                elif kind == 'polygon':
                    xy = nums[1:]
                    shapes.append((layer, 'polygon', list(zip(xy[0::2], xy[1::2]))))
                elif kind == 'rect':
                    # (rect layer x1 y1 x2 y2)
                    x1, y1, x2, y2 = nums[:4]
                    shapes.append((layer, 'polygon', [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]))
            padstacks[ps[1]] = shapes

        net_of = {}
        net = child(root, 'network')
        self.nets = {}
        for nb in children(net, 'net'):
            name = nb[1]
            pins = child(nb, 'pins') or []
            refs = [p for p in pins[1:] if isinstance(p, str)]
            self.nets[name] = refs
            for r in refs:
                net_of[r.split('-', 1)[1] if '-' in r else r] = name
        self.pad_nets = net_of

        self.pads = []       # dict(x, y, layer, kind, geom, net)  coords already in mm
        image = child(lib, 'image')
        for pin in children(image, 'pin'):
            ps, name, x, y = pin[1], pin[2], float(pin[3]), float(pin[4])
            netname = net_of.get(name)
            for layer, kind, geom in padstacks.get(ps, []):
                if kind == 'circle':
                    dx, dy, r = geom
                    self.pads.append(dict(x=(x + dx) * self.unit_mm, y=(y + dy) * self.unit_mm,
                                          layer=layer, kind='circle', r=r * self.unit_mm,
                                          net=netname, pin=name))
                else:
                    self.pads.append(dict(x=x * self.unit_mm, y=y * self.unit_mm, layer=layer,
                                          kind='polygon',
                                          pts=[((x + px) * self.unit_mm, (y + py) * self.unit_mm)
                                               for px, py in geom],
                                          net=netname, pin=name))


# ----------------------------------------------------------------------------- SES
class Ses:
    def __init__(self, path):
        self.path = path
        root = parse_sexpr(open(path, encoding='utf-8', errors='replace').read())
        routes = child(root, 'routes')
        if routes is None:
            raise ValueError(f'{path}: no (routes ...)')
        res = child(routes, 'resolution')
        self.unit_mm = 0.0254 / int(res[2]) if res and res[1] == 'mil' else 0.0254

        lib = child(routes, 'library_out')
        self.via_diam = 0.6
        for ps in children(lib, 'padstack'):
            for sh in children(ps, 'shape'):
                if isinstance(sh[1], list) and sh[1][0] == 'circle' and sh[1][1] == 'TopLayer':
                    self.via_diam = float(sh[1][2]) * self.unit_mm
                    break
        self.wires = []      # dict(net, layer, width_mm, pts_mm, length_mm)
        self.vias = []       # dict(net, x, y, r_mm, padstack)
        self.degenerate = 0  # paths with 0/1 points (Freerouting occasionally emits these)
        netout = child(routes, 'network_out')
        for nb in children(netout, 'net'):
            netname = nb[1]
            for w in children(nb, 'wire'):
                p = child(w, 'path')
                if p is None:
                    continue
                layer, width = p[1], float(p[2])
                xy = [float(v) for v in p[3:]]
                pts = [(xy[k] * self.unit_mm, xy[k + 1] * self.unit_mm) for k in range(0, len(xy) - 1, 2)]
                if len(pts) < 2:
                    self.degenerate += 1
                    continue
                L = sum(math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
                        for k in range(len(pts) - 1))
                self.wires.append(dict(net=netname, layer=layer, width=width * self.unit_mm, pts=pts,
                                       length=L))
            for v in children(nb, 'via'):
                self.vias.append(dict(net=netname, x=float(v[2]) * self.unit_mm,
                                      y=float(v[3]) * self.unit_mm, padstack=v[1]))


# ----------------------------------------------------------------------------- raster helpers
def _geom_of(entry):
    from shapely.geometry import LineString, Point, Polygon
    kind = entry['kind']
    if kind == 'circle':
        return Point(entry['x'], entry['y']).buffer(entry['r'], quad_segs=16)
    if kind == 'polygon':
        return Polygon(entry['pts']).buffer(0)
    if kind == 'track':
        if len(entry['pts']) < 2:
            raise ValueError('degenerate track')
        return LineString(entry['pts']).buffer(entry['r'], cap_style=2, join_style=2, quad_segs=8)
    raise ValueError(kind)


def rasterize(geoms, board, cell=CELL):
    from shapely import contains_xy
    nx, ny = int(round(board[0] / cell)), int(round(board[1] / cell))
    mask = np.zeros((ny, nx), dtype=bool)
    xc = (np.arange(nx) + 0.5) * cell - 0.5 * cell        # cell centres, origin at board (0,0)
    yc = (np.arange(ny) + 0.5) * cell - 0.5 * cell
    for e in geoms:
        g = _geom_of(e) if isinstance(e, dict) else e
        x0, y0, x1, y1 = g.bounds
        i0 = max(0, int(math.floor((x0 - 0.5 * cell) / cell)))
        i1 = min(nx, int(math.ceil((x1 + 0.5 * cell) / cell)) + 1)
        j0 = max(0, int(math.floor((y0 - 0.5 * cell) / cell)))
        j1 = min(ny, int(math.ceil((y1 + 0.5 * cell) / cell)) + 1)
        if i0 >= i1 or j0 >= j1:
            continue
        X, Y = np.meshgrid(xc[i0:i1], yc[j0:j1])
        sub = contains_xy(g, X, Y)
        mask[j0:j1, i0:i1] |= sub
    return mask


def _tracks(ses, layer, gnd):
    out = []
    for w in ses.wires:
        if w['layer'] != layer:
            continue
        if (w['net'] == 'GND') != gnd:
            continue
        out.append(dict(kind='track', pts=w['pts'], r=w['width'] / 2 + POUR_CLEAR))
    return out


def _pads(dsn, layer, gnd, inflate=POUR_CLEAR):
    from shapely.geometry import Polygon
    out = []
    for p in dsn.pads:
        if p['layer'] != layer:
            continue
        if (p['net'] == 'GND') != gnd:
            continue
        if p['kind'] == 'circle':
            out.append(dict(kind='circle', x=p['x'], y=p['y'], r=p['r'] + inflate))
        else:
            poly = Polygon(p['pts']).buffer(0)
            if inflate:
                poly = poly.buffer(inflate, join_style=2)
            out.append(dict(kind='polygon', pts=list(poly.exterior.coords)))
    return out


def _vias(ses, gnd, radius_pad=0.0):
    out = []
    for v in ses.vias:
        if (v['net'] == 'GND') != gnd:
            continue
        out.append(dict(kind='circle', x=v['x'], y=v['y'], r=radius_pad + POUR_CLEAR))
    return out


# ----------------------------------------------------------------------------- metrics
def metrics(ses_path, dsn_path=DSN_DEFAULT, want_layers=('TopLayer', 'BottomLayer')):
    dsn = Dsn(dsn_path)
    ses = Ses(ses_path)
    board = dsn.board if abs(dsn.board[0] - BOARD[0]) < 2 else BOARD
    W, H = board

    sig = [w for w in ses.wires if w['net'] != 'GND']
    vias_sig = [v for v in ses.vias if v['net'] != 'GND']
    vias_gnd = [v for v in ses.vias if v['net'] == 'GND']

    layer_len = {L: sum(w['length'] for w in sig if w['layer'] == L) for L in want_layers}
    total = sum(layer_len.values())
    widths = {}
    for w in sig:
        k = round(w['width'] * 1000, 1)
        d = widths.setdefault(k, dict(seg=0, mm=0.0))
        d['seg'] += 1
        d['mm'] += w['length']

    # ---- approximate pour / ground integrity -------------------------------------------------
    nx, ny = int(round(W / CELL)), int(round(H / CELL))
    xc = (np.arange(nx) + 0.5) * CELL - 0.5 * CELL
    yc = (np.arange(ny) + 0.5) * CELL - 0.5 * CELL
    X, Y = np.meshgrid(xc, yc)
    free, gndcu = {}, {}
    for L in want_layers:
        obst = _tracks(ses, L, gnd=False) + _pads(dsn, L, gnd=False) + \
            _vias(ses, gnd=False, radius_pad=ses.via_diam / 2)
        free[L] = rasterize(obst, board)
        free[L] = ~free[L] & (X >= POUR_INSET) & (X <= W - POUR_INSET) & (Y >= POUR_INSET) & (Y <= H - POUR_INSET)
        gndcu[L] = rasterize(_tracks(ses, L, gnd=True) + _pads(dsn, L, gnd=True, inflate=0.0), board)

    from scipy import ndimage
    st = np.ones((3, 3), dtype=bool)
    cell_mm2 = CELL * CELL
    diag = {}
    for L in want_layers:
        lab, n = ndimage.label(free[L], structure=st)
        sizes = ndimage.sum(free[L], lab, range(1, n + 1)) * cell_mm2
        big = [k + 1 for k, s in enumerate(sizes) if s >= 2.0]
        dead = 0
        for k in big:
            if not (gndcu[L] & (lab == k)).any():
                dead += 1
        diag[L] = dict(free_mm2=float(free[L].sum() * cell_mm2),
                       pieces=len(big),
                       largest_mm2=float(max([sizes[k - 1] for k in big], default=0.0)),
                       dead=dead)

    def pct(a):
        return 100.0 * a / (W * H)

    to, bo = diag['TopLayer'], diag['BottomLayer']
    sig_bot = layer_len.get('BottomLayer', 0.0)
    # prescribed optimistic approximation: all free area ties to the main GND, one piece, no dead copper
    ground_opt = (1.0 * pct(bo['free_mm2']) + 0.5 * pct(to['free_mm2'])
                  + 0.4 * pct(bo['free_mm2']) + 0.3 * 100 * (bo['free_mm2'] + to['free_mm2']) / (2 * W * H)
                  - 0.005 * sig_bot)
    # stricter variant: free area minus dead pockets, largest piece term and piece-count penalty as gmetrics
    main_t, main_b = to['free_mm2'], bo['free_mm2'] - 0.0
    ground_strict = (1.0 * pct(main_b) + 0.5 * pct(main_t)
                     + 0.4 * pct(bo['largest_mm2'])
                     + 0.3 * 100 * (main_b + main_t) / (2 * W * H)
                     - 0.3 * (bo['pieces'] - 1) - 2 * bo['dead'] - 0.005 * sig_bot)

    return dict(ses=os.path.basename(os.path.dirname(os.path.abspath(ses_path))) or ses_path,
                dsn=os.path.basename(dsn_path), board=[W, H],
                nets=len(set([w['net'] for w in ses.wires] + [v['net'] for v in ses.vias])),
                vias=dict(total=len(ses.vias), signal=len(vias_sig), gnd=len(vias_gnd)),
                len=dict(top=round(layer_len.get('TopLayer', 0.0), 1),
                         bottom=round(layer_len.get('BottomLayer', 0.0), 1), total=round(total, 1)),
                widths={str(k): dict(paths=v['seg'], mm=round(v['mm'], 1)) for k, v in sorted(widths.items())},
                ground=dict(approx=round(ground_opt, 2), strict=round(ground_strict, 2),
                            top_free=round(to['free_mm2']), bottom_free=round(bo['free_mm2']),
                            top_pieces=to['pieces'], bottom_pieces=bo['pieces'],
                            bottom_largest=round(bo['largest_mm2']), dead=bo['dead'],
                            sig_bottom_mm=round(sig_bot)),
                degenerate_paths=ses.degenerate)


# ----------------------------------------------------------------------------- run log parsing
LOG_STAGE = re.compile(r'Auto-routing stage completed:.*?final score:\s*([\d.]+)\s*'
                       r'\((\d+) unrouted and (\d+) violations\)')
LOG_STATE = re.compile(r"Job .* finished with state:\s*(\w+)")
LOG_ELAPSED = re.compile(r"finished with state:\s*\w+ \(elapsed: (?:(\d+) minutes? )?([\d.]+) seconds")
LOG_PASS = re.compile(r'Auto-routing pass #(\d+).*?score\s*([\d.]+)\s*\((\d+) unrouted and (\d+) violations\)')
# v2.5.0 prints the settings it actually used near the end of the run (the lead verified this block):
#   === Routing Parameters ===
#     Via Costs: 25, Plane Via Costs: 5
#     Layer Costs (Preferred / Against):
#       Layer 1: 1.0 / 1.2     (1-based: Layer 1 = TopLayer, Layer 2 = BottomLayer)
LOG_PARAMS = re.compile(r'=+ Routing Parameters =+(.*?)(?:\n\s*=+|\Z)', re.S)


def _parse_effective(txt):
    m = LOG_PARAMS.search(txt)
    if not m:
        return None
    blk = m.group(1)
    out = dict(via_costs=None, plane_via_costs=None, layers={})
    mv = re.search(r'Via Costs:\s*([\d.]+)\s*,\s*Plane Via Costs:\s*([\d.]+)', blk)
    if mv:
        out['via_costs'] = float(mv.group(1))
        out['plane_via_costs'] = float(mv.group(2))
    for lay, pref, ag in re.findall(r'Layer\s+(\d+):\s*([\d.]+)\s*/\s*([\d.]+)', blk):
        out['layers'][lay] = [float(pref), float(ag)]
    return out


def parse_log(path):
    if not os.path.exists(path):
        return dict(ok=False, state='no-log', passes=0, score=None, unrouted=None, violations=None,
                    reason='run.log missing')
    txt = open(path, encoding='utf-8', errors='replace').read()
    out = dict(ok=False, state='running', passes=0, score=None, unrouted=None, violations=None, reason='',
               effective=None, elapsed_s=None)
    out['effective'] = _parse_effective(txt)
    me = LOG_ELAPSED.search(txt)
    if me:
        out['elapsed_s'] = round(int(me.group(1) or 0) * 60 + float(me.group(2)), 1)
    states = LOG_STATE.findall(txt)
    passes = LOG_PASS.findall(txt)
    out['passes'] = len(passes)
    out['last_pass'] = int(passes[-1][0]) if passes else None
    if passes:
        out['score'] = float(passes[-1][1])
        out['unrouted'] = int(passes[-1][2])
        out['violations'] = int(passes[-1][3])
    m = LOG_STAGE.search(txt)
    if m:
        out['score'] = float(m.group(1))
        out['unrouted'] = int(m.group(2))
        out['violations'] = int(m.group(3))
    if states:
        out['state'] = states[-1]
    if out['unrouted'] is not None:
        out['ok'] = (out['unrouted'] == 0 and out['violations'] == 0 and out['state'] == 'COMPLETED')
        if not out['ok']:
            out['reason'] = f'{out["unrouted"]} unrouted / {out["violations"]} violations / state {out["state"]}'
    else:
        out['reason'] = 'not finished (no pass summary)'
    return out


# ----------------------------------------------------------------------------- batch ranking
GROUND_BASELINE_REAL = 55.0          # gmetrics.py score of the baseline board (方案 C release)
WEIGHTS = dict(ground=0.60, vias=0.25, length=0.15)
ROOT1 = os.path.join(HERE, 'batch_runs', 'round1')


def batch_root(round_no):
    return os.path.join(HERE, 'batch_runs', f'round{round_no}')


def _rank_cell(v, best, low_is_better):
    if low_is_better:
        return 100.0 * best / v if v else 0.0
    return 100.0 * v / best if best else 0.0


def round1(root=ROOT1, round_no=1, write=True):
    base = metrics(BASELINE_SES)
    base_log = parse_log(BASELINE_LOG)
    base_ground = GROUND_BASELINE_REAL
    items = [dict(id='BASELINE', note='方案C boardC_routed.ses: pre-stitch/pre-pour SES, 11 GND vias',
                  ses=BASELINE_SES, m=base, log=base_log, ground=base_ground,
                  ground_approx=base['ground']['approx'])]
    for d in sorted(os.listdir(root)):
        vdir = os.path.join(root, d)
        if not os.path.isdir(vdir):
            continue
        ses = os.path.join(vdir, 'out.ses')
        meta = {}
        mp = os.path.join(vdir, 'meta.json')
        if os.path.exists(mp):
            meta = json.load(open(mp, encoding='utf-8'))
        if not os.path.exists(ses):
            # aborted / still running variant: list it as eliminated instead of silently dropping it
            logp = os.path.join(vdir, 'run.log')
            if os.path.exists(logp):
                log = parse_log(logp)
                lp = log.get('last_pass')
                log.update(ok=False, reason=(f"aborted at pass {lp}: {log['unrouted']} unrouted, no SES"
                                             if lp else 'aborted: no SES written'))
                items.append(dict(id=d, note=meta.get('param', {}).get('note', ''), ses=None, m=None,
                                  log=log, ground=None, ground_approx=None))
            continue
        try:
            m = metrics(ses)
        except Exception as e:                                  # noqa: BLE001 - a broken ses must not kill the table
            print(f'[warn] {d}: cannot score: {e}')
            continue
        log = parse_log(os.path.join(vdir, 'run.log'))
        # v2.5.0 prints "=== Routing Parameters ===" at DEBUG level only -> it lands in <vdir>/en/freerouting.log
        eff_src = os.path.join(vdir, 'en', 'freerouting.log')
        if os.path.exists(eff_src):
            try:
                log['effective'] = _parse_effective(open(eff_src, encoding='utf-8', errors='replace').read())
                log['effective_source'] = 'en/freerouting.log (DEBUG)'
            except OSError:
                pass
        ground = GROUND_BASELINE_REAL + (m['ground']['approx'] - base['ground']['approx'])
        items.append(dict(id=d, note=meta.get('param', {}).get('note', ''), ses=ses, m=m, log=log,
                          ground=round(ground, 2), ground_approx=m['ground']['approx']))

    # duplicate detection: some DSN settings are silently ignored by v2.5.0 (e.g. "(fanout off)"), which makes two
    # parameter variants produce byte-identical SES files.  Flag them so the table does not over-count evidence.
        seen = {}
    for it in items[1:]:
        if not it['ses']:
            continue
        h = hashlib.md5(open(it['ses'], 'rb').read()).hexdigest()
        it['ses_md5'] = h[:12]
        if h in seen:
            it['dup_of'] = seen[h]
            it['note'] = (it['note'] + f' [identical SES to {seen[h]}: that setting had no effect]').strip()
        else:
            seen[h] = it['id']

    pool = [it for it in items if it['log']['ok']]
    if not pool:
        raise SystemExit('no finished variant with 0 unrouted / 0 violations yet')
    gmax = max(it['ground'] for it in pool)
    vmin = min(it['m']['vias']['signal'] for it in pool)
    lmin = min(it['m']['len']['total'] for it in pool)
    for it in items:
        m = it['m']
        if m is None:
            it['norm'] = dict(ground=None, vias=None, length=None)
            it['total'] = None
            it['delta'] = None
            continue
        g = _rank_cell(it['ground'], gmax, False)
        v = _rank_cell(m['vias']['signal'], vmin, True)
        L = _rank_cell(m['len']['total'], lmin, True)
        it['norm'] = dict(ground=round(g, 2), vias=round(v, 2), length=round(L, 2))
        it['total'] = round(WEIGHTS['ground'] * g + WEIGHTS['vias'] * v + WEIGHTS['length'] * L, 2)
        it['delta'] = dict(ground=round(it['ground'] - base_ground, 2),
                           vias=m['vias']['signal'] - base['vias']['signal'],
                           length=round(m['len']['total'] - base['len']['total'], 1))

    ranked = sorted([it for it in items if it['log']['ok']], key=lambda it: -it['total'])
    rejected = [it for it in items if not it['log']['ok'] and it['id'] != 'BASELINE']
    base_item = items[0]

    def eff_str(it):
        """Effective costs printed by Freerouting itself (=== Routing Parameters ===, 1-based layers)."""
        e = it['log'].get('effective')
        if not e:
            return 'n/a (old log)'
        lay = e.get('layers') or {}
        s = []
        if '1' in lay:
            s.append(f"T{lay['1'][0]:g}/{lay['1'][1]:g}")
        if '2' in lay:
            s.append(f"B{lay['2'][0]:g}/{lay['2'][1]:g}")
        if e.get('via_costs') is not None:
            s.append(f"v{e['via_costs']:g}")
        return ' '.join(s) or 'n/a'

    # ---- ranking.json ---------------------------------------------------------------------
    if write:
        out = dict(round=round_no, weights=WEIGHTS, ground_baseline=GROUND_BASELINE_REAL,
                   ground_note='approx gmetrics formula on SES copper, anchored at the baseline 55.0 '
                               '(only valid for relative comparison)',
                   norm='each metric: best of pool (variants + baseline) = 100',
                   effective_note='effective = per-run `=== Routing Parameters ===` block printed by '
                                  'Freerouting v2.5.0 itself (proof the DSN autoroute_settings was applied)',
                   rows=[dict(id=it['id'], note=it['note'],
                              ses=os.path.abspath(it['ses']) if it['ses'] else None,
                              total=it['total'], ground=it['ground'], effective=eff_str(it),
                              effective_raw=it['log'].get('effective'),
                              ses_md5=it.get('ses_md5'), dup_of=it.get('dup_of'),
                              elapsed_s=it['log'].get('elapsed_s'),
                              norm=it['norm'], delta=it['delta'],
                              vias=it['m']['vias'] if it['m'] else None,
                              len=it['m']['len'] if it['m'] else None,
                              widths=it['m']['widths'] if it['m'] else None,
                              ground_detail=it['m']['ground'] if it['m'] else None,
                              log=it['log'], ok=it['log']['ok'])
                         for it in ranked + rejected],
                   rejected=[it['id'] for it in rejected])
        json.dump(out, open(os.path.join(root, 'ranking.json'), 'w', encoding='utf-8'), indent=1)

        # ---- ranking.md ---------------------------------------------------------------------
        def sgn(x, f='{:+d}'):
            return '—' if abs(x) < 1e-9 else f.format(int(round(x)))

        lines = [
            f'# Round {round_no} — Freerouting batch, offline score (no EasyEDA)',
            '',
            'weight: **ground 0.60 / signal-via 0.25 / signal-length 0.15**; each metric normalised to the '
            'best of the pool = 100.',
            'ground = approximate gmetrics value from SES copper (0.1 mm grid, non-GND copper + 0.25 mm pour '
            'clearance = occupied, rest = pour), **anchored so BASELINE = 55.0** — relative comparison only.',
            'vias = non-GND SES vias; length = all-layer signal length (mm). BASELINE has no stitching vias '
            '(75) and no pour in its SES.',
            'eff = costs Freerouting itself printed in the DEBUG log `<variant>/en/freerouting.log` '
            '(`=== Routing Parameters ===`: T/B = Top/Bottom preferred/against, v = via_costs) — proof the '
            'DSN autoroute_settings reached the router.',
            '',
            '| id | params | eff (T/B pref/against, via) | total | ground | vias | len_mm | vs BASELINE (G/V/L) | hard |',
            '|---|---|---|---|---|---|---|---|---|',
        ]
        b = base_item
        lines.append(f"| BASELINE | {b['note']} | n/a | {b['total']:.1f} | {b['ground']:.1f} | "
                     f"{b['m']['vias']['signal']} | {b['m']['len']['total']:.0f} | — | "
                     f"{'OK' if b['log']['ok'] else 'x'} |")
        for it in ranked:
            if it['id'] == 'BASELINE':
                continue
            d = it['delta']
            lines.append(f"| {it['id']} | {it['note']} | {eff_str(it)} | {it['total']:.1f} | "
                         f"{it['ground']:.1f} | {it['m']['vias']['signal']} | "
                         f"{it['m']['len']['total']:.0f} | "
                         f"{sgn(d['ground'], '{:+.1f}')} / {sgn(d['vias'])} / {sgn(d['length'])} | OK |")
        for it in rejected:
            lines.append(f"| {it['id']} | {it['note']} | {eff_str(it)} | — | — | — | — | eliminated: "
                         f"{it['log']['reason']} | x |")
        open(os.path.join(root, 'ranking.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        print('wrote', os.path.join(root, 'ranking.md'), 'and ranking.json')
    return items, ranked, rejected


# ----------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ses', nargs='?')
    ap.add_argument('--dsn', default=DSN_DEFAULT)
    ap.add_argument('--json', default=None)
    ap.add_argument('--round1', action='store_true', help='score a batch and write ranking.md/json')
    ap.add_argument('--round', type=int, default=1, help='batch round number (batch_runs/round<N>)')
    ap.add_argument('--root', default=None)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--render', action='store_true',
                    help='after ranking, also write fast Pillow previews + contact sheet (ms per frame)')
    ap.add_argument('--top', type=int, default=6, help='previews to render with --render')
    ap.add_argument('--pxmm', type=int, default=16, help='preview resolution, pixels per mm')
    a = ap.parse_args()
    if a.round1:
        root = a.root or batch_root(a.round)
        items, ranked, rejected = round1(root, a.round)
        for it in ranked:
            print(f"{it['id']:8s} total {it['total']:6.2f}  ground {it['ground']:6.2f}  "
                  f"vias {it['m']['vias']['signal']:3d}  len {it['m']['len']['total']:6.0f}  "
                  f"{it['log']['reason'] or 'ok'}")
        for it in rejected:
            print(f"{it['id']:8s} ELIMINATED  {it['log']['reason']}")
        if a.render:
            import fast_render
            files, times, t_base = fast_render.render_batch(a.dsn, root, None, a.top, a.pxmm)
            if files:
                t_all = sum(times)
                print(f'previews: {len(files)} frames in {t_all * 1000:.0f} ms '
                      f'({1000 * t_all / len(files):.1f} ms/frame, base {t_base * 1000:.1f} ms once)')
                print('contact sheet:', os.path.join(os.path.dirname(files[0]), 'contact_sheet.png'))
        return
    if not a.ses:
        ap.error('give a .ses file or use --round1 (batch ranking)')
    m = metrics(a.ses, a.dsn)
    if a.json:
        json.dump(m, open(a.json, 'w', encoding='utf-8'), indent=1)
    if not a.quiet:
        print(json.dumps(m, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
