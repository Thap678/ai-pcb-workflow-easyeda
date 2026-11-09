"""Split-plane router for 4-layer boards (signals top + bottom, a solid ground layer, a power layer split into one
region per supply), built on route_bundle.py (0.05 mm A*, 45-degree only, tracks hug each other, own vias, rip-up).

Use it instead of route_bundle.py when power is distributed on an inner plane split by supply (docs/05 section 8).
The plane layout comes from [boards.<name>.planes] in the config (plane_board.py).

Stages, each a Router whose fixed copper is everything decided before it:
  1  power skeleton   1 mm top tracks joining the pads of every top power pour group ([[planes.pours]]) and the
                      wide nets (planes.wide_nets); the pour of a group = its skeleton + pads, grown, minus foreign
                      pads (connected by construction, never swallows another net's pad)
  2  fanout           every SMD pad of a plane net gets a stub + via inside its own region; every SMD ground pad a via
                      to the ground layer; a few stitching vias per pour (plane_fixup.py adds the rest later).
                      A pad a 0.5 mm stub cannot leave (0.5 / 0.65 mm pitch) is tried again with 0.25 mm, straight out
                      of its own pin (the part's other pins keep their escape lanes); a ground pad that still has no
                      via is tied pad-to-pad to a ground pad of the same part that has one
  3  signals          every other net on top / bottom (0.25 mm); plane-net pads outside their region ("exceptions")
                      are routed as one tree per cluster to a nearby via of their own net
The plane and ground layers carry no tracks. Keepout parts (inductors) get no copper on top / bottom.

Defaults are the settings that converged on the nuedc-car-2026 lower board (87 nets, 80 x 87 mm, 33 min):
grid clearance = route.router_clearance_mm, exact check 0.04 mm below it (equal values make a path that lands a
few um short against fixed copper fail forever), no extra rip-up margin, 1 stitching via per pour. Putting the
stitching vias, ground fallbacks or a wider clearance into the router itself made it stop converging - those go
into plane_fixup.py after the signals.

usage: python route_plane.py <dump.json> <out.json> [--stage 1|2|3] [--iters 800] [--layer-dir] ...
       then plane_fixup.py, plane_render.py, plane_apply.py, plane_drc.py, plane_check.py (docs/05 section 8)
Run several variants at once with plane_par.sh (the search is single-threaded; ~1.5 GB RAM per run).
"""
import argparse, collections, json, math, os, sys, time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import numpy as np  # noqa: E402
from shapely.geometry import Point, LineString, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
import route_bundle as RB  # noqa: E402
from route_bundle import Router, LAY, RES, T45, T90, add, paint  # noqa: E402
from plane_board import PlaneBoard, MM  # noqa: E402

RB.Ses = lambda x: x          # no SES: fixed copper is passed in directly
VR = 0.3
POUR_GROW = 0.6               # a pour reaches this far beyond its 1.0 mm skeleton
POUR_ESC = 0.6                # and stays this far from foreign pads (room for their escape)
EXC_LINK = 19.0               # mm: exception pads closer than this share one tree
VICTIM_MARGIN = 0.0
STITCH_MAX, STITCH_PER = 1, 1000.0


class Empty:
    wires, vias, via_diam = [], [], 2 * VR


def mk_dsn(B):
    pads = [dict(pin=pd['key'], net=pd['net'], layer=LAY[L - 1], kind='poly', pts=list(pd['geom'].exterior.coords))
            for pd in B.pads for L in pd['layers']]
    return SimpleNamespace(board=B.board, pads=pads, outline=list(B.outline.exterior.coords))


class PR(Router):
    """route_bundle.Router with fixed copper passed in, pseudo pins (a via, a pour, a set of vias as one terminal),
    a search window, an optional layer direction, a crash-safe exact check and stub fanouts to a target area."""

    def __init__(self, B, args, fixed, vias=(), keep_new_vias=False):
        super().__init__(mk_dsn(B), Empty(), args)
        self.B = B
        for pd in B.pads:           # hole-to-hole keep for vias around through-hole pads (mk_dsn passes polygons)
            if not pd['smd']:
                paint(self.tht_keep, self.g.geom(pd['geom'].buffer(RB.HOLE_KEEP + self.vr, 16)))
        # ... and around every via: same-net vias are not kept apart by the copper clearance at all, other-net ones
        # by ~0.8 mm that the 0.05 mm grid can shave below the hole-to-hole rule (two VSERVO vias 0.776 mm apart)
        hole = B.cfg.rules.get('via_hole_mm', 0.3)
        self.via_keep_r = hole + B.cfg.rules.get('hole_to_hole_mm', 0.5) + 0.05
        self.keep_new_vias = keep_new_vias
        for v in vias:
            self.keep_via(v['x'], v['y'])
        for net, li, geo, trk in fixed:
            self.add_fixed(net, li, geo, trk)

    def keep_via(self, x, y):
        paint(self.tht_keep, self.g.geom(Point(x, y).buffer(self.via_keep_r, 16)))

    def commit(self, key, r):
        super().commit(key, r)
        if self.keep_new_vias:        # stages without rip-up: a placed via keeps the next ones away
            for x, y in r['vias']:
                self.keep_via(x, y)

    def add_fixed(self, net, li, geo, trk=False):
        self._stamp_fixed(li, geo)
        self.fixed_geoms[LAY[li]].append((net, geo))
        if trk:
            add(self.trk[li], self.g.geom(geo), 1)

    def route_net(self, net, soft=False, forbid=None):
        """A* first inside the pins' bounding box + a.window mm (the board-wide search is the slow part); the whole
        board if that fails. a.layer_dir: a net spread mostly east-west gets a cheaper bottom (a.bot_ew), mostly
        north-south a dearer one (a.bot_ns)."""
        W = getattr(self.a, 'window', 0)
        bs = [g.bounds for pin in self.pins[net] for g, _ in self.pad_geo[pin].values()] if self.pins[net] else []
        bot0 = self.a.bot
        if getattr(self.a, 'layer_dir', False) and bs:
            dx = max(b[2] for b in bs) - min(b[0] for b in bs); dy = max(b[3] for b in bs) - min(b[1] for b in bs)
            if dx > 10 and dx > 2 * dy: self.a.bot = self.a.bot_ew
            elif dy > 10 and dy > 2 * dx: self.a.bot = self.a.bot_ns
        try:
            if W > 0 and len(self.pins[net]) >= 2:
                x0, y0 = min(b[0] for b in bs) - W, min(b[1] for b in bs) - W
                x1, y1 = max(b[2] for b in bs) + W, max(b[3] for b in bs) + W
                out = np.ones((self.ny, self.nx), bool)
                out[max(0, int(y0 / RES)):int(y1 / RES) + 1, max(0, int(x0 / RES)):int(x1 / RES) + 1] = False
                r = super().route_net(net, soft, out if forbid is None else (forbid | out))
                if r is not None:
                    return r
            return super().route_net(net, soft, forbid)
        finally:
            self.a.bot = bot0

    def check(self, key, r):
        """route_bundle's exact check; near the tolerance edge the overlap can be empty (GEOS getX on an empty
        point), then the track's own centroid marks the spot."""
        bad, crossed = [], set()
        gs = self._geoms(r)
        base = key.split('#')[0]
        rule = RB.RULE
        for L in LAY:
            others = [(nn, g) for nn, g in self.fixed_geoms[L] if nn != base]
            others += [(k, g) for k, rr in self.routes.items() if k.split('#')[0] != base for g in rr['geoms'][L]]
            if not others or not gs[L]:
                continue
            tree = STRtree([g for _, g in others])
            for g in gs[L]:
                for j in tree.query(g.buffer(rule)):
                    nn, og = others[j]
                    if g.distance(og) < rule - 1e-6:
                        p = og.centroid if og.area < g.area else g.intersection(og.buffer(rule)).centroid
                        if p.is_empty: p = g.centroid
                        bad.append((LAY.index(L), p.x, p.y))
                        crossed.add(nn)
                if g.distance(self.outline.exterior) < RB.EDGE - 0.05:
                    bad.append((LAY.index(L), g.centroid.x, g.centroid.y))
        return bad, crossed

    def pseudo_pin(self, key, name, net, geo, layers=(0, 1)):
        self.pad_geo[name] = {li: (geo, net) for li in layers}
        self.pins[key].append(name)

    def mask(self, geo):
        m = np.zeros((self.ny, self.nx), bool)
        if geo is not None and not geo.is_empty:
            paint(m, self.g.geom(geo))
        return m

    def fanout_any(self, key, pin, li, spot_mask, max_len=4.0, block=None):
        """Stub on layer li from pad `pin` (a pin of route key `key`) to the nearest legal via spot in spot_mask;
        `block` keeps the stub and the via out of an area (the escape lanes of the part's other pins)."""
        n, nx, ny = self.n, self.nx, self.ny
        hw, vr = self.hw, self.vr
        own_plain, own_inf, pin_cells = self.own_masks(key)
        fixed_t = (self.fix_inf[hw] - own_inf[hw]) > 0
        fixed_v = (self.fix_inf[vr] - own_inf[vr]) > 0
        sig_t, sig_v = self.sig_inf[hw] > 0, self.sig_inf[vr] > 0
        legal = np.zeros((2, ny, nx), bool)
        legal[li] = (~fixed_t[li] & ~sig_t[li] & self.edge[hw]) | own_plain[li]
        if block is not None:
            legal[li] &= ~block | own_plain[li]
            spot_mask = spot_mask & ~block
        spot = (~fixed_v[0] & ~fixed_v[1] & ~sig_v[0] & ~sig_v[1] & self.edge[vr] & ~own_plain[0] & ~own_plain[1]
                & ~self.tht_keep & spot_mask)
        tgt = np.zeros(2 * n, np.int32)
        tgt[li * n:(li + 1) * n][spot.reshape(-1)] = 1
        pad = np.zeros((ny, nx), bool)
        for l2, m in pin_cells[pin]:
            if l2 == li:
                pad |= m
        srcs = (np.flatnonzero(pad.reshape(-1) & legal[li].reshape(-1)) + li * n).astype(np.int64)
        if not len(srcs):
            return None
        self.cur += 1
        st = RB.astar(np.ones(2 * n, np.float32), legal.reshape(-1), np.zeros(n, np.bool_), srcs, tgt,
                      np.zeros(n, np.float32), nx, n, T45, T90, RES, 1.0, self.dist, self.par, self.stamp, self.cur)
        if st < 0:
            return None
        nodes = self._path(st)
        if len(nodes) * RES > max_len * 1.5:
            return None
        c = nodes[-1] % n
        r = dict(wires=[self._poly(nodes)] if len(nodes) > 1 else [], vias=[((c % nx) * RES, (c // nx) * RES)])
        return None if self.check(key, r)[0] else r


def run_queue(R, keys, a, label):
    """Route, else search through routed copper, rip up what is in the way, retry; stop when a net has been ripped
    a.max_rips times (oscillation: fix the placement there) and report its pins."""
    queue = collections.deque(keys)
    t0, it = time.time(), 0
    while queue and it < a.iters:
        it += 1
        if it % 25 == 0: print(f'  [{label} {it}] {len(R.routes)} routed, queue {len(queue)}, {time.time() - t0:.0f}s', flush=True)
        hot = [k for k in keys if R.hist[k] >= a.max_rips]
        if hot:
            print(f'  [{label} {it}] STALL: ripped >= {a.max_rips} times: {hot}', flush=True)
            for k in hot:
                print(f'      {k}: pins {[p for p in R.pins.get(k, []) if isinstance(p, str)][:8]}', flush=True)
            break
        net = queue.popleft()
        r = R.route_checked(net)
        if r is not None:
            R.commit(net, r); continue
        soft = R.route_net(net, soft=True)
        if soft is None:
            print(f'  [{label} {it}] {net}: no path even through other nets', flush=True); continue
        rule0 = RB.RULE; RB.RULE = rule0 + VICTIM_MARGIN
        _, crossed = R.check(net, soft)
        RB.RULE = rule0
        victims = [c for c in crossed if c in R.routes and c in keys]
        for v in victims:
            R.rip(v); R.hist[v] += 1; queue.append(v)
        r = R.route_checked(net)
        if r is not None:
            R.commit(net, r)
            print(f'  [{label} {it}] {net}: ok after ripping {victims}', flush=True)
        else:
            print(f'  [{label} {it}] {net}: still blocked after ripping {victims}', flush=True)
            queue.append(net)
    for ps in range(getattr(a, 'polish', 0)):
        bot0, via0 = a.bot, a.via
        a.bot, a.via = bot0 * a.polish_mul, via0 * a.polish_mul
        better = 0
        for net in sorted([k for k in R.routes if k in keys], key=lambda k: -len(R.routes[k]['vias'])):
            old = R.routes[net]; R.rip(net)
            r = R.route_checked(net)
            if r is not None and len(r['vias']) <= len(old['vias']):
                better += len(r['vias']) < len(old['vias']); R.commit(net, r)
            else:
                R.commit(net, {k: old[k] for k in ('wires', 'vias')})
        a.bot, a.via = bot0, via0
        print(f'  [{label}] polish {ps + 1}: fewer vias on {better} nets ({time.time() - t0:.0f}s)', flush=True)
    missing = [k for k in keys if k not in R.routes]
    print(f'[{label}] routed {len(keys) - len(missing)}/{len(keys)} in {time.time() - t0:.0f}s; missing {missing}', flush=True)
    return missing


def items_of(R, keys, width):
    """(wires, vias) of the given route keys: wires dict(net, layer 1/2, width, pts), vias dict(net, x, y), mm."""
    W, V = [], []
    for k in keys:
        if k not in R.routes: continue
        net = k.split('#')[0]
        for li, pts in R.routes[k]['wires']:
            W.append(dict(net=net, layer=int(li) + 1, width=width, pts=[(round(float(p[0]), 3), round(float(p[1]), 3)) for p in pts]))
        for x, y in R.routes[k]['vias']:
            V.append(dict(net=net, x=round(float(x), 3), y=round(float(y), 3)))
    return W, V


def fixed_from(B, wires, vias, pours, extra_vias):
    out = []
    for ko in B.keepout.values():
        out += [('-KO', 0, ko, False), ('-KO', 1, ko, False)]
    for w in wires:
        out.append((w['net'], w['layer'] - 1, LineString(w['pts']).buffer(w['width'] / 2, 12), True))
    for v in vias + extra_vias:
        g = Point(v['x'], v['y']).buffer(VR, 16)
        out += [(v['net'], 0, g, False), (v['net'], 1, g, False)]
    for po in pours:
        out.append((po['net'], 0, po['geom'], False))
    return out


def fine_pitch_parts(B):
    """{ref: body box} of parts with >= 5 SMD pads and a pad pitch below 0.8 mm (their pins need escape room)."""
    out = {}
    for ref, c in B.comp.items():
        P = [(pd['x'], pd['y']) for pd in B.pads if pd['ref'] == ref and pd['smd']]
        if len(P) >= 5 and min(math.dist(p, q) for i, p in enumerate(P) for q in P[i + 1:]) < 0.8:
            bb = c['bbox']; out[ref] = box(bb['minX'] * MM, bb['minY'] * MM, bb['maxX'] * MM, bb['maxY'] * MM)
    return out


def lanes(B, ref, skip=None, layer=None, lane=1.5, both=False):
    """Escape lanes of a part's pins: each pad stretched `lane` mm outward (and inward with both=True)."""
    bb = B.comp[ref]['bbox']; cx, cy = (bb['minX'] + bb['maxX']) / 2 * MM, (bb['minY'] + bb['maxY']) / 2 * MM
    out = []
    for q in B.pads:
        if q['ref'] != ref or q['key'] == skip or (layer is not None and layer not in q['layers']): continue
        x0, y0, x1, y1 = q['geom'].bounds
        if (x1 - x0) >= (y1 - y0):
            o, i = (box(x1, y0, x1 + lane, y1), box(x0 - lane, y0, x0, y1)) if q['x'] > cx else (box(x0 - lane, y0, x0, y1), box(x1, y0, x1 + lane, y1))
        else:
            o, i = (box(x0, y1, x1, y1 + lane), box(x0, y0 - lane, x1, y0)) if q['y'] > cy else (box(x0, y0 - lane, x1, y0), box(x0, y1, x1, y1 + lane))
        out += [o, i] if both else [o]
    return unary_union(out)


def dump(path, W, V, pours, missing, fp_vias, exc=None, gfail=None):
    json.dump(dict(wires=W, vias=V, pours=[dict(name=p['name'], net=p['net'], layer=1, poly=list(p['geom'].exterior.coords),
                                              holes=[list(h.coords) for h in p['geom'].interiors]) for p in pours],
                   missing=missing, exceptions=exc or {}, gnd_fail=gfail or []), open(path, 'w'), indent=0)
    print('wrote', path, len(W), 'wires', len(V), 'vias', len(pours), 'pours', flush=True)


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dump'); ap.add_argument('out')
    ap.add_argument('--stage', type=int, default=3, help='stop after stage 1 or 2 (writes what exists so far)')
    ap.add_argument('--iters', type=int, default=800)
    ap.add_argument('--polish', type=int, default=1)
    ap.add_argument('--max-rips', type=int, default=12, help='stop and report when one net was ripped this often')
    ap.add_argument('--window', type=float, default=8.0, help='search window around the pins first, mm (0 = board)')
    ap.add_argument('--clearance', type=float, default=None, help='grid clearance mm (default route.router_clearance_mm)')
    ap.add_argument('--rule', type=float, default=None, help='exact-check clearance mm (default clearance - 0.04)')
    ap.add_argument('--victim-margin', type=float, default=VICTIM_MARGIN)
    ap.add_argument('--layer-dir', action='store_true', help='east-west nets prefer the bottom, north-south the top')
    ap.add_argument('--bot-ew', type=float, default=0.7)
    ap.add_argument('--bot-ns', type=float, default=2.5)
    ap.add_argument('--stitch-max', type=int, default=STITCH_MAX)
    ap.add_argument('--stitch-per', type=float, default=STITCH_PER)
    ap.add_argument('--gnd-fallback', action='store_true', help='route via-less ground pads in stage 3 (default: plane_fixup.py)')
    return ap


def main():
    global STITCH_MAX, STITCH_PER, VICTIM_MARGIN
    cli = parser().parse_args()
    B = PlaneBoard(cli.dump)
    clear = cli.clearance if cli.clearance is not None else B.cfg.route.get('router_clearance_mm', 0.2)
    RB.SLACK = 0.02
    RB.CLR = clear - RB.SLACK
    RB.RULE = cli.rule if cli.rule is not None else clear - 0.04
    STITCH_MAX, STITCH_PER, VICTIM_MARGIN = cli.stitch_max, cli.stitch_per, cli.victim_margin
    print('params', dict(vars(cli), grid_clearance=clear, exact_rule=RB.RULE), flush=True)
    GND = B.gnd
    fp_vias = [dict(net=v['net'], x=v['x'] * MM, y=v['y'] * MM) for v in B.d['copper']['vias']
               if len(v['primitiveId']) > 16]          # footprint vias only (board vias have 16-character ids)
    base = dict(open=0.0, d=0.3, bot=1e4, via=1e4, pen=30.0, padpen=0.0, keep_gnd=True, padzone=0.6, maxw=10.0,
                iters=cli.iters, polish=0, bot_hug=False, polish_mul=2.0, max_rips=cli.max_rips, window=cli.window)
    # ------------------------------------------------------------------ stage 1: power skeleton (top only)
    pw = max([1.0] + list(B.wide_nets.values()))
    a1 = SimpleNamespace(**base, width=pw)
    R1 = PR(B, a1, fixed_from(B, [], [], [], fp_vias), vias=fp_vias, keep_new_vias=True)
    groups = {}
    for name, (net, labels) in B.pour_groups.items():
        pads = ([pd for pd in B.pads if pd['net'] == net and 1 in pd['layers']] if not labels else
                [pd for lab in labels for pd in B.by_key[lab]])
        groups[f'{net}#{name}'] = dict(net=net, pads=[pd['key'] for pd in pads], pour=True)
    for net in B.wide_nets:
        groups[f'{net}#W'] = dict(net=net, pads=[pd['key'] for pd in B.pads if pd['net'] == net], pour=False)
    for k, g in groups.items():
        R1.pins[k] = list(g['pads'])
    keys1 = sorted(groups, key=lambda k: -len(groups[k]['pads']))
    run_queue(R1, keys1, a1, 'power')
    W1, V1 = items_of(R1, keys1, pw)
    pours = []
    top_pads = [pd for pd in B.pads if 1 in pd['layers']]
    ko = unary_union(list(B.keepout.values()))
    for k, g in groups.items():
        if not g['pour'] or k not in R1.routes: continue
        own = [pd for pd in top_pads if pd['key'] in g['pads']]
        skel = [LineString(pts).buffer(pw / 2 + POUR_GROW, join_style=2) for li, pts in R1.routes[k]['wires'] if li == 0]
        shape = unary_union(skel + [pd['geom'].buffer(POUR_GROW, join_style=2) for pd in own])
        foreign = unary_union([pd['geom'] for pd in top_pads if pd['net'] != g['net']]).buffer(0.2 + POUR_ESC)
        other_sk = unary_union([LineString(w['pts']).buffer(pw / 2 + 0.3) for w in W1 if w['net'] != g['net']])
        shape = shape.intersection(B.outline.buffer(-0.6)).difference(ko).difference(foreign).difference(other_sk)
        core = unary_union(skel + [pd['geom'] for pd in own])
        for p in getattr(shape, 'geoms', [shape]):
            if p.geom_type == 'Polygon' and p.intersects(core) and p.area > 0.5:
                pours.append(dict(name=k, net=g['net'], geom=p.simplify(0.02)))
    print('pours', len(pours), 'skeleton tracks', len(W1), flush=True)
    if cli.stage == 1:
        return dump(cli.out, W1, V1, pours, [], fp_vias)
    # ------------------------------------------------------------------ stage 2: fanout into the planes
    a2 = SimpleNamespace(**base, width=0.5)
    R2 = PR(B, a2, fixed_from(B, W1, V1, pours, fp_vias), vias=V1 + fp_vias, keep_new_vias=True)
    in_group = {p for g in groups.values() for p in g['pads']}
    regions = {n: g.buffer(-(VR + 0.15)) for n, g in B.regions.items()}
    ko_mask = R2.mask(ko.buffer(VR + 0.15))
    fine = fine_pitch_parts(B)
    if fine:   # no via under a fine-pitch body or in the outward lane of its pins (they would wall pins in)
        ko_mask |= R2.mask(unary_union(list(fine.values())).buffer(VR + 0.1))
        ko_mask |= R2.mask(unary_union([lanes(B, r).buffer(VR) for r in fine]))
    stitch, fan, exc, foreign_of = [], [], collections.defaultdict(list), {}
    for i, po in enumerate(pours):
        if po['net'] not in regions: continue
        area_ok = po['geom'].buffer(-(VR + 0.1)).intersection(regions[po['net']])
        if area_ok.is_empty: continue
        want = int(min(STITCH_MAX, max(2, po['geom'].area / STITCH_PER)))
        key = f"{po['net']}#st{i}"
        R2.pins[key] = []
        R2.pseudo_pin(key, f'pour{i}', po['net'], po['geom'], layers=(0,))
        if po['net'] not in foreign_of:   # only OTHER nets' copper blocks a stitching via (not the pour's own skeleton)
            foreign_of[po['net']] = unary_union([g for L in LAY for n, g in R2.fixed_geoms[L] if n != po['net']]).buffer(RB.CLR + RB.SLACK + VR)
        pads_own = unary_union([pd['geom'] for pd in B.pads if pd['net'] == po['net']]).buffer(VR + 0.1)
        ok = R2.mask(area_ok.difference(foreign_of[po['net']]).difference(pads_own)) & ~ko_mask & R2.edge[R2.vr] & ~R2.tht_keep
        cells = np.argwhere(ok)
        got = []
        if len(cells):     # nearest the pour's own pads first: the pour middle stays free for bottom signals
            src = [(pd['x'], pd['y']) for pd in B.pads if pd['net'] == po['net'] and pd['geom'].intersects(po['geom'])] or [(po['geom'].centroid.x, po['geom'].centroid.y)]
            for j, ii in sorted(cells.tolist(), key=lambda c: min(math.hypot(c[1] * RES - x, c[0] * RES - y) for x, y in src)):
                x, y = round(ii * RES, 2), round(j * RES, 2)
                if all(math.hypot(x - gx, y - gy) >= 1.2 for gx, gy in got):
                    got.append((x, y))
                if len(got) >= want: break
        R2.commit(key, dict(wires=[], vias=got))
        stitch.append(key)
    for pd in B.pads:      # plane-net SMD pads outside the groups: stub + via inside their region
        net = pd['net']
        if net not in regions or pd['key'] in in_group: continue
        if not pd['smd']:
            if not B.regions[net].contains(pd['geom'].centroid): exc[net].append(pd['key'])
            continue
        key = f'{net}#f{pd["key"]}'
        R2.pins[key] = [pd['key']]
        r = R2.fanout_any(key, pd['key'], pd['layers'][0] - 1, R2.mask(regions[net]) & ~ko_mask, max_len=4.0)
        if r is None: exc[net].append(pd['key'])
        else: R2.commit(key, r); fan.append(key)
    via_in_pad = lambda pd: any(pd['geom'].contains(Point(v['x'], v['y'])) for v in fp_vias if v['net'] == GND)
    gfail = []
    for pd in B.pads:      # every SMD ground pad its own via to the ground layer
        if pd['net'] != GND or not pd['smd'] or via_in_pad(pd): continue
        key = f'{GND}#g{pd["key"]}'
        R2.pins[key] = [pd['key']]
        r = R2.fanout_any(key, pd['key'], pd['layers'][0] - 1, ~ko_mask, max_len=3.0)
        if r is None: gfail.append(pd['key'])
        else: R2.commit(key, r); fan.append(key)
    print(f'stitch vias {sum(len(R2.routes[k]["vias"]) for k in stitch)} in {len(stitch)} pours; fanouts {len(fan)}; '
          f'ground fanout failed {gfail}', flush=True)
    W2, V2 = items_of(R2, fan, 0.5)
    for w in W2:
        if w['net'] == GND: w['width'] = 0.4
    V2 += items_of(R2, stitch, 0.5)[1]
    # second try with a 0.25 mm stub, straight out of its own pin
    pad_of = {pd['key']: pd for pd in B.pads}
    R2n = PR(B, SimpleNamespace(**base, width=0.25), fixed_from(B, W1 + W2, V1 + V2, pours, fp_vias), vias=V1 + V2 + fp_vias, keep_new_vias=True)
    esc = lambda pd: R2n.mask(lanes(B, pd['ref'], skip=pd['key'], layer=pd['layers'][0], lane=2.0, both=True))
    overlaps = lambda k: any(q['net'] == GND and q['key'] != k and q['geom'].intersects(pad_of[k]['geom']) for q in B.pads)
    thin = []
    for k in list(gfail):
        pd = pad_of[k]
        if overlaps(k): continue
        key = f'{GND}#n{k}'; R2n.pins[key] = [k]
        r = R2n.fanout_any(key, k, pd['layers'][0] - 1, ~ko_mask, max_len=3.0, block=esc(pd))
        if r is not None: R2n.commit(key, r); thin.append(key); gfail.remove(k)
    for net in list(exc):
        for k in list(exc[net]):
            pd = pad_of[k]
            if not pd['smd']: continue
            key = f'{net}#n{k}'; R2n.pins[key] = [k]
            r = R2n.fanout_any(key, k, pd['layers'][0] - 1, R2n.mask(regions[net]) & ~ko_mask, max_len=4.0, block=esc(pd))
            if r is not None: R2n.commit(key, r); thin.append(key); exc[net].remove(k)
        if not exc[net]: del exc[net]
    # ground pads still without a via: pad-to-pad (top only) to a ground pad of the same part that has one,
    # under the body if need be; the other pins keep their outward lanes
    has_via = {k.split('#', 1)[1][1:] for k in fan + thin if k.startswith(GND + '#')}
    for k in list(gfail):
        pd = pad_of[k]
        if overlaps(k): continue
        mates = [q for q in B.pads if q['ref'] == pd['ref'] and q['net'] == GND and q['key'] in has_via and pd['layers'][0] in q['layers']]
        if not mates: continue
        key = f'{GND}#p{k}'; R2n.pins[key] = [k]
        R2n.pseudo_pin(key, key + '.m', GND, unary_union([q['geom'] for q in mates]), layers=(pd['layers'][0] - 1,))
        out_l = R2n.mask(lanes(B, pd['ref'], skip=pd['key'], layer=pd['layers'][0], lane=2.0))
        r = R2n.route_net(key, forbid=np.stack([out_l, out_l]))
        if r is not None and not R2n.check(key, r)[0]:
            R2n.commit(key, r); thin.append(key); gfail.remove(k)
    Wn, Vn = items_of(R2n, thin, 0.25)
    W2 += Wn; V2 += Vn
    print(f'thin (0.25 mm) fanouts {len(thin)}: {thin}; ground still without via {gfail}; exceptions {dict(exc)}', flush=True)
    if cli.stage == 2:
        return dump(cli.out, W1 + W2, V1 + V2, pours, [], fp_vias, exc=exc, gfail=gfail)
    # ------------------------------------------------------------------ stage 3: signals
    a3 = SimpleNamespace(**{**base, 'open': 4.0, 'bot': 1.5, 'via': 25.0, 'padpen': 2.0, 'polish': cli.polish,
                            'bot_hug': True, 'layer_dir': cli.layer_dir, 'bot_ew': cli.bot_ew, 'bot_ns': cli.bot_ns}, width=0.25)
    R3 = PR(B, a3, fixed_from(B, W1 + W2, V1 + V2, pours, fp_vias), vias=V1 + V2 + fp_vias)
    keys3, covered = [], collections.defaultdict(list)
    for k, g in groups.items():
        if k in R1.routes:
            unit = unary_union([LineString(pts).buffer(pw / 2) for li, pts in R1.routes[k]['wires']] +
                               [pd['geom'] for pd in B.pads if pd['key'] in g['pads']] +
                               [po['geom'] for po in pours if po['name'] == k])
            covered[g['net']].append((k, unit, set(g['pads'])))
    region_vias = collections.defaultdict(list)
    for v in V2:
        if v['net'] in regions or v['net'] == GND: region_vias[v['net']].append((v['x'], v['y']))
    gsolo = [k for k in gfail if not overlaps(k)]
    if gsolo and cli.gnd_fallback: exc[GND] = gsolo
    for net, keys in exc.items():      # exception clusters: one tree each, ending on any nearby via of the net
        pads = [pd for k in keys for pd in B.pads if pd['key'] == k]
        lab = list(range(len(pads)))
        def root(i):
            while lab[i] != i: i = lab[i]
            return i
        for i, p in enumerate(pads):
            for j, q in enumerate(pads[:i]):
                if net != GND and math.dist((p['x'], p['y']), (q['x'], q['y'])) < EXC_LINK: lab[root(i)] = root(j)
        clusters = collections.defaultdict(list)
        for i, pd in enumerate(pads): clusters[root(i)].append(pd)
        for i, cl in enumerate(clusters.values()):
            key = f'{net}#x{i}'
            R3.pins[key] = [pd['key'] for pd in cl]
            if region_vias[net]:
                reach = 6.0 if net == GND else 15.0
                near = [(x, y) for x, y in region_vias[net] if min(math.dist((x, y), (pd['x'], pd['y'])) for pd in cl) < reach]
                near = near or sorted(region_vias[net], key=lambda v: min(math.dist(v, (pd['x'], pd['y'])) for pd in cl))[:3]
                R3.pseudo_pin(key, f'{key}.via', net, unary_union([Point(x, y).buffer(VR, 16) for x, y in near]))
            print(f'  exception {key}: {R3.pins[key][:-1]}', flush=True)
            keys3.append(key)
    for net in sorted({pd['net'] for pd in B.pads}):
        if not net or net == GND or net in regions: continue
        pins = [pd['key'] for pd in B.pads if pd['net'] == net]
        done = set().union(*[u[2] for u in covered.get(net, [])]) if covered.get(net) else set()
        rest = [p for p in dict.fromkeys(pins) if p not in done]
        units = covered.get(net, [])
        if len(rest) + len(units) < 2: continue
        R3.pins[net] = rest
        for k, unit, _ in units:
            R3.pseudo_pin(net, f'{k}.unit', net, unit, layers=(0,))
        keys3.append(net)

    def span(n):
        ps = [g.centroid for p in R3.pins[n] for g, _ in R3.pad_geo[p].values()]
        return max(p.x for p in ps) - min(p.x for p in ps) + max(p.y for p in ps) - min(p.y for p in ps)
    keys3.sort(key=lambda n: (not n.startswith(GND + '#'), -span(n)))
    print('stage 3 nets', len(keys3), flush=True)
    miss = run_queue(R3, keys3, a3, 'signal')
    W3, V3 = items_of(R3, keys3, 0.25)
    dump(cli.out, W1 + W2 + W3, V1 + V2 + V3, pours, miss, fp_vias, exc=exc, gfail=gfail)


if __name__ == '__main__':
    main()
