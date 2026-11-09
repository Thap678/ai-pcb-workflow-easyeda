"""Picture of a route_plane.py / plane_fixup.py result before it goes into the PCB: top and bottom side by side,
plane regions (dotted), top power pours, keepouts (hatched), pads, tracks, vias (ground green, plane nets orange).
usage: python plane_render.py <dump.json> <routes.json> <out.png> [--win x0 y0 x1 y1]"""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from shapely.geometry import LineString, Point, Polygon  # noqa: E402
from plane_board import PlaneBoard  # noqa: E402

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
ap = argparse.ArgumentParser(); ap.add_argument('dump'); ap.add_argument('routes'); ap.add_argument('out')
ap.add_argument('--win', type=float, nargs=4)
a = ap.parse_args()
B = PlaneBoard(a.dump); R = json.load(open(a.routes, encoding='utf-8'))
cmap = plt.get_cmap('tab10'); col = {n: cmap(i % 10) for i, n in enumerate(B.regions)}


def fill(ax, g, **kw):
    for p in getattr(g, 'geoms', [g]):
        if p.is_empty or p.geom_type != 'Polygon': continue
        ax.fill(*p.exterior.xy, **kw)
        for h in p.interiors: ax.fill(*h.xy, color='white')


fig, axs = plt.subplots(1, 2, figsize=(20, 11))
for ax, L in zip(axs, (1, 2)):
    fill(ax, B.outline, color='#f4f4f4')
    for n, p in B.regions.items():
        for g in getattr(p, 'geoms', [p]): ax.plot(*g.exterior.xy, color=col[n], lw=.7, ls=':')
    for ko in B.keepout.values(): fill(ax, ko, color='none', hatch='xxx', ec='#999')
    for pd in B.pads:
        if L in pd['layers']: fill(ax, pd['geom'], color='#c9a227' if pd['smd'] else '#888')
    if L == 1:
        for po in R.get('pours', []): fill(ax, Polygon(po['poly'], po.get('holes') or []), color=col.get(po['net'], '#b07aa1'), alpha=.45)
    for w in R['wires']:
        if w['layer'] == L: fill(ax, LineString(w['pts']).buffer(w['width'] / 2), color='#d62728' if L == 1 else '#1f77b4', alpha=.8)
    for v in R['vias']:
        fill(ax, Point(v['x'], v['y']).buffer(0.3), color='#2ca02c' if v['net'] == B.gnd else ('#ff7f0e' if v['net'] in B.regions else '#333'))
    ax.set_aspect('equal'); ax.set_title('top' if L == 1 else 'bottom (seen from the top)')
    if a.win: ax.set_xlim(a.win[0], a.win[2]); ax.set_ylim(a.win[1], a.win[3])
plt.tight_layout(); plt.savefig(a.out, dpi=110); print('wrote', a.out)
