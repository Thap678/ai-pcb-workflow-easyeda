"""Offline scorer for routed SES files and for a whole route.py batch, before anything goes into EasyEDA.

Metrics per SES: via count (total / non-GND / GND), signal length per layer, width histogram, and an
*approximate* ground-integrity score that follows the gmetrics.py formula without EasyEDA's real pour geometry:
    occupied(layer) = non-GND tracks (inflated by width/2 + pour clearance) + non-GND pads/vias
    free(layer)     = board outline inset by the pour inset - occupied
    S = 1.0*BOT + 0.5*TOP + 0.4*BOT_largest + 0.3*(BOT+TOP)/2 - 0.3*(BOT_pieces-1) - 2*dead
        - 0.005*BOT_sig_len_mm                 (all cover terms in % of the board area)
`free` stands in for "pour tied to main GND" (optimistic): compare variants with it, the real score is gmetrics.py
on the poured board. Ground net, pour clearance and pour inset come from the config ([ground] net, rules.pour_mm,
[ground] pour_inset_mm) when there is one.

usage:
  python batch_score.py <x.ses> --dsn <prepared.dsn> [--json out.json]
  python batch_score.py --batch route_runs/<board> [--baseline <x.ses>]   # rank route.py's variants, writes
                                                                           # ranking.md/json into that folder
Each variant folder needs out.ses + run.log; its in.dsn is the geometry (or pass --dsn). Then
`python render.py --dsn <prepared.dsn> --batch route_runs/<board>` draws the ranked variants and a contact sheet.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

from dsnses import Dsn, Ses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

_B = K.try_board()
GND = _B.gnd if _B else 'GND'
POUR_CLEAR = (_B.rules.get('pour_mm', 0.25) if _B else 0.25)          # pour-to-copper clearance, mm
POUR_INSET = (_B.ground.get('pour_inset_mm', 0.3) if _B else 0.3)     # pour-to-board-edge clearance, mm
CELL = 0.1                                                             # scoring raster, mm
WEIGHTS = dict(ground=0.60, vias=0.25, length=0.15)


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
        if (w['net'] == GND) != gnd:
            continue
        out.append(dict(kind='track', pts=w['pts'], r=w['width'] / 2 + POUR_CLEAR))
    return out


def _pads(dsn, layer, gnd, inflate=POUR_CLEAR):
    from shapely.geometry import Polygon
    out = []
    for p in dsn.pads:
        if p['layer'] != layer:
            continue
        if (p['net'] == GND) != gnd:
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
        if (v['net'] == GND) != gnd:
            continue
        out.append(dict(kind='circle', x=v['x'], y=v['y'], r=radius_pad + POUR_CLEAR))
    return out


# ----------------------------------------------------------------------------- metrics
def metrics(ses_path, dsn_path, want_layers=('TopLayer', 'BottomLayer')):
    from shapely.geometry import Polygon
    dsn = Dsn(dsn_path) if isinstance(dsn_path, str) else dsn_path
    ses = Ses(ses_path)
    board = dsn.board
    W, H = board
    edge = Polygon(dsn.outline).buffer(dsn.inset, join_style=2)      # the board edge (undo the routing inset)
    area = edge.area

    sig = [w for w in ses.wires if w['net'] != GND]
    vias_sig = [v for v in ses.vias if v['net'] != GND]
    vias_gnd = [v for v in ses.vias if v['net'] == GND]

    layer_len = {L: sum(w['length'] for w in sig if w['layer'] == L) for L in want_layers}
    total = sum(layer_len.values())
    widths = {}
    for w in sig:
        k = round(w['width'] * 1000, 1)
        d = widths.setdefault(k, dict(seg=0, mm=0.0))
        d['seg'] += 1
        d['mm'] += w['length']

    # ---- approximate pour / ground integrity -------------------------------------------------
    pour_area = rasterize([edge.buffer(-POUR_INSET)], board)
    free, gndcu = {}, {}
    for L in want_layers:
        obst = _tracks(ses, L, gnd=False) + _pads(dsn, L, gnd=False) + \
            _vias(ses, gnd=False, radius_pad=ses.via_diam / 2)
        free[L] = ~rasterize(obst, board) & pour_area
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
        return 100.0 * a / area

    to, bo = diag['TopLayer'], diag['BottomLayer']
    sig_bot = layer_len.get('BottomLayer', 0.0)
    # optimistic approximation: all free area ties to the main GND, one piece, no dead copper
    ground_opt = (1.0 * pct(bo['free_mm2']) + 0.5 * pct(to['free_mm2'])
                  + 0.4 * pct(bo['free_mm2']) + 0.3 * 100 * (bo['free_mm2'] + to['free_mm2']) / (2 * area)
                  - 0.005 * sig_bot)
    # stricter variant: largest piece term, piece-count and dead-pocket penalties as gmetrics
    main_t, main_b = to['free_mm2'], bo['free_mm2']
    ground_strict = (1.0 * pct(main_b) + 0.5 * pct(main_t)
                     + 0.4 * pct(bo['largest_mm2'])
                     + 0.3 * 100 * (main_b + main_t) / (2 * area)
                     - 0.3 * (bo['pieces'] - 1) - 2 * bo['dead'] - 0.005 * sig_bot)

    return dict(ses=os.path.basename(os.path.dirname(os.path.abspath(ses_path))) or ses_path,
                dsn=os.path.basename(dsn.path), board=[round(W, 2), round(H, 2)], board_mm2=round(area),
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
# v2.5.0 prints the settings it actually used near the end of the run:
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
def _rank_cell(v, best, low_is_better):
    if low_is_better:
        return 100.0 * best / v if v else 0.0
    return 100.0 * v / best if best else 0.0


def _eff_str(log):
    """Effective costs printed by Freerouting itself (=== Routing Parameters ===, 1-based layers)."""
    e = log.get('effective')
    if not e:
        return 'n/a'
    lay = e.get('layers') or {}
    s = [f"{t}{lay[k][0]:g}/{lay[k][1]:g}" for k, t in (('1', 'T'), ('2', 'B')) if k in lay]
    if e.get('via_costs') is not None:
        s.append(f"v{e['via_costs']:g}")
    return ' '.join(s) or 'n/a'


def batch(root, dsn=None, baseline=None, write=True):
    """Score and rank every <root>/<id>/out.ses (route.py's layout). Returns (ranked, rejected)."""
    notes = {}
    if _B:                                                  # variant notes from the config, when they match
        notes = {v['id']: v.get('note', '') for v in _B.route.get('variants', [])}
    items = []
    if baseline:
        bdsn = dsn or os.path.join(os.path.dirname(os.path.abspath(baseline)), 'in.dsn')
        items.append(dict(id='BASELINE', note=os.path.basename(baseline), ses=baseline, m=metrics(baseline, bdsn),
                          log=parse_log(os.path.join(os.path.dirname(os.path.abspath(baseline)), 'run.log'))))
        items[0]['log'].update(ok=True, reason='')          # the reference, whatever its log says
    for d in sorted(os.listdir(root)):
        vdir = os.path.join(root, d)
        if not os.path.isdir(vdir):
            continue
        ses, logp = os.path.join(vdir, 'out.ses'), os.path.join(vdir, 'run.log')
        if not os.path.exists(ses):
            if os.path.exists(logp):                         # aborted / still running: list it, don't drop it
                log = parse_log(logp)
                lp = log.get('last_pass')
                log.update(ok=False, reason=(f"aborted at pass {lp}: {log['unrouted']} unrouted, no SES"
                                             if lp else 'aborted: no SES written'))
                items.append(dict(id=d, note=notes.get(d, ''), ses=None, m=None, log=log))
            continue
        vdsn = dsn or os.path.join(vdir, 'in.dsn')
        try:
            m = metrics(ses, vdsn)
        except Exception as e:                               # noqa: BLE001 - a broken ses must not kill the table
            print(f'[warn] {d}: cannot score: {e}')
            continue
        log = parse_log(logp)
        # v2.5.0 prints "=== Routing Parameters ===" at DEBUG level only -> it lands in <vdir>/en/freerouting.log
        eff_src = os.path.join(vdir, 'en', 'freerouting.log')
        if os.path.exists(eff_src):
            log['effective'] = _parse_effective(open(eff_src, encoding='utf-8', errors='replace').read())
        items.append(dict(id=d, note=notes.get(d, ''), ses=ses, m=m, log=log))

    # some DSN settings are silently ignored by v2.5.0 (e.g. "(fanout off)"), which makes two variants produce
    # byte-identical SES files: flag them so the table does not over-count evidence
    seen = {}
    for it in items:
        if not it['ses'] or it['id'] == 'BASELINE':
            continue
        h = hashlib.md5(open(it['ses'], 'rb').read()).hexdigest()
        it['ses_md5'] = h[:12]
        if h in seen:
            it['dup_of'] = seen[h]
            it['note'] = (it['note'] + f' [identical SES to {seen[h]}: that setting had no effect]').strip()
        else:
            seen[h] = it['id']

    pool = [it for it in items if it['log']['ok'] and it['m']]
    if not pool:                                            # still rank the scored ones, flagged as unusable
        pool = [it for it in items if it['m']]
        print(f'!! {root}: no variant finished with 0 unrouted / 0 violations - ranking them anyway, none is usable')
        if not pool:
            raise SystemExit(f'{root}: nothing to score (no out.ses)')
    gmax = max(it['m']['ground']['approx'] for it in pool)
    vmin = min(it['m']['vias']['signal'] for it in pool)
    lmin = min(it['m']['len']['total'] for it in pool)
    base = items[0]['m'] if baseline else None
    for it in items:
        m = it['m']
        if m is None:
            it.update(norm=None, total=None, delta=None)
            continue
        g = _rank_cell(m['ground']['approx'], gmax, False)
        v = _rank_cell(m['vias']['signal'], vmin, True)
        L = _rank_cell(m['len']['total'], lmin, True)
        it['norm'] = dict(ground=round(g, 2), vias=round(v, 2), length=round(L, 2))
        it['total'] = round(WEIGHTS['ground'] * g + WEIGHTS['vias'] * v + WEIGHTS['length'] * L, 2)
        it['delta'] = base and dict(ground=round(m['ground']['approx'] - base['ground']['approx'], 2),
                                    vias=m['vias']['signal'] - base['vias']['signal'],
                                    length=round(m['len']['total'] - base['len']['total'], 1))

    ranked = sorted(pool, key=lambda it: -it['total'])
    in_pool = {id(it) for it in pool}
    rejected = [it for it in items if id(it) not in in_pool]
    if write:
        rows = [dict(id=it['id'], note=it['note'], ses=os.path.abspath(it['ses']) if it['ses'] else None,
                     total=it['total'], ground=it['m']['ground']['approx'] if it['m'] else None,
                     effective=_eff_str(it['log']), effective_raw=it['log'].get('effective'),
                     ses_md5=it.get('ses_md5'), dup_of=it.get('dup_of'), elapsed_s=it['log'].get('elapsed_s'),
                     norm=it['norm'], delta=it['delta'],
                     vias=it['m']['vias'] if it['m'] else None, len=it['m']['len'] if it['m'] else None,
                     widths=it['m']['widths'] if it['m'] else None,
                     ground_detail=it['m']['ground'] if it['m'] else None, log=it['log'], ok=id(it) in in_pool)
                for it in ranked + rejected]
        json.dump(dict(weights=WEIGHTS, gnd=GND, pour_clear_mm=POUR_CLEAR, pour_inset_mm=POUR_INSET,
                       ground_note='approximate gmetrics formula on SES copper - relative comparison only',
                       norm='each metric: best of the finished pool = 100', rows=rows,
                       rejected=[it['id'] for it in rejected]),
                  open(os.path.join(root, 'ranking.json'), 'w', encoding='utf-8'), indent=1)

        def sgn(x, f='{:+d}'):
            return '—' if abs(x) < 1e-9 else f.format(int(round(x)))

        lines = [
            f'# Freerouting batch `{os.path.basename(os.path.abspath(root))}` — offline score (no EasyEDA)',
            '',
            'weight: **ground 0.60 / signal-via 0.25 / signal-length 0.15**; each metric normalised to the best '
            'of the finished pool = 100.',
            f'ground = approximate gmetrics value from SES copper ({CELL} mm grid, non-{GND} copper + {POUR_CLEAR} mm '
            'pour clearance = occupied, rest = pour) — relative comparison only; the real score is gmetrics.py on '
            'the poured board.',
            f'vias = non-{GND} SES vias; length = all-layer signal length (mm). eff = costs Freerouting printed in '
            '`<variant>/en/freerouting.log` (T/B = Top/Bottom preferred/against, v = via_costs).',
            '',
            '| id | note | eff | total | ground | vias | len_mm |' + (' vs BASELINE (G/V/L) |' if base else '')
            + ' usable |',
            '|---|---|---|---|---|---|---|' + ('---|' if base else '') + '---|',
        ]
        for it in ranked:
            m, dl = it['m'], it['delta']
            lines.append(f"| {it['id']} | {it['note']} | {_eff_str(it['log'])} | {it['total']:.1f} | "
                         f"{m['ground']['approx']:.1f} | {m['vias']['signal']} | {m['len']['total']:.0f} |"
                         + (f" {sgn(dl['ground'], '{:+.1f}')} / {sgn(dl['vias'])} / {sgn(dl['length'])} |"
                            if base else '') + (' OK |' if it['log']['ok'] else f" no: {it['log']['reason']} |"))
        for it in rejected:
            lines.append(f"| {it['id']} | {it['note']} | {_eff_str(it['log'])} | — | — | — | — |"
                         + (' — |' if base else '') + f" no: {it['log']['reason']} |")
        open(os.path.join(root, 'ranking.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        print('wrote', os.path.join(root, 'ranking.md'), 'and ranking.json')
    return ranked, rejected


# ----------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ses', nargs='?')
    ap.add_argument('--dsn', default=None, help='prepared DSN (single SES: required; --batch: default <id>/in.dsn)')
    ap.add_argument('--json', default=None)
    ap.add_argument('--batch', default=None, help='route.py output folder with <id>/out.ses + run.log')
    ap.add_argument('--baseline', default=None, help='with --batch: a reference SES to report deltas against')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    if a.batch:
        ranked, rejected = batch(a.batch, a.dsn, a.baseline)
        for it in ranked:
            print(f"{it['id']:12s} total {it['total']:6.2f}  ground {it['m']['ground']['approx']:6.2f}  "
                  f"vias {it['m']['vias']['signal']:3d}  len {it['m']['len']['total']:6.0f}  "
                  f"{'ok' if it['log']['ok'] else it['log']['reason']}")
        for it in rejected:
            print(f"{it['id']:12s} ELIMINATED  {it['log']['reason']}")
        return
    if not a.ses or not a.dsn:
        ap.error('give <x.ses> --dsn <prepared.dsn>, or --batch <route_runs/board>')
    m = metrics(a.ses, a.dsn)
    if a.json:
        json.dump(m, open(a.json, 'w', encoding='utf-8'), indent=1)
    if not a.quiet:
        print(json.dumps(m, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
