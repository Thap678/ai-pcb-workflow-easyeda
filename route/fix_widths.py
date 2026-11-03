"""After an SES import: every track gets the width it was routed with (EasyEDA widens to the net-rule default),
but never below the net's rule minimum. A track matches the routed segment that contains its midpoint.
usage: python fix_widths.py dump.json file.ses [--apply]
"""
import json, re, sys, math
sys.path.insert(0, '../sch')
from del_parts import run

MIN = {'5V_MAIN': 19.69, '5V_LOGIC': 19.69, '3V3_MAIN': 11.81, '3V3_RF': 11.81, 'VCC_GRAY': 11.81}   # mil
MIN_DEFAULT = 5.91                                                                                    # 0.15 mm
d = json.loads((lambda s: s[s.find('{'):])(open(sys.argv[1], encoding='utf-8').read()))
ses = open(sys.argv[2], encoding='utf-8').read(); body = ses[ses.index('(network_out'):]
segs = {}
for m in re.finditer(r'\(net ("?[^\s"]+"?)(.*?)(?=\n      \(net |\Z)', body, re.S):
    net = m.group(1).strip('"')
    for w in re.finditer(r'\(path (\S+) (\d+)\s+([\d\s-]+)\)', m.group(2)):
        v = [int(x) / 1000 for x in w.group(3).split()]; pts = list(zip(v[0::2], v[1::2]))
        L = 1 if w.group(1) == 'TopLayer' else 2
        for a, b in zip(pts, pts[1:]): segs.setdefault((net, L), []).append((a, b, int(w.group(2)) / 1000))


def on_seg(p, a, b, tol=1.0):
    dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
    if L2 == 0: return math.dist(p, a) <= tol
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    return -0.01 <= t <= 1.01 and math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) <= tol


fix = []
for l in d['copper']['lines']:
    net, L, w = l['net'], l['layer'], l['lineWidth']
    a, b = (l['startX'], l['startY']), (l['endX'], l['endY'])
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    cand = [s for s in segs.get((net, L), []) if on_seg(mid, s[0], s[1]) and on_seg(a, s[0], s[1], 2) and on_seg(b, s[0], s[1], 2)]
    target = min(c[2] for c in cand) if cand else w
    target = max(target, MIN.get(net, MIN_DEFAULT))
    if abs(target - w) > 0.05: fix.append((l['primitiveId'], net, L, round(target, 3), a, b, w))
print('tracks to re-width:', len(fix))
for f in fix[:12]: print('  ', f[1], 'L', f[2], f'{f[6] * 0.0254:.3f} -> {f[3] * 0.0254:.3f} mm')
if '--apply' in sys.argv and fix:
    for k in range(0, len(fix), 40):
        run(['--doc', '55840169c52b8bea', 'pcb', 'track-delete', '--ids', ','.join(f[0] for f in fix[k:k + 40])])
    bad = 0
    for _, net, L, w, (x1, y1), (x2, y2), _ in fix:
        bad += not run(['--doc', '55840169c52b8bea', 'pcb', 'track', '--x1', str(x1), '--y1', str(y1), '--x2', str(x2), '--y2', str(y2),
                        '--layer', str(L), '--width', str(w), '--net', net], tries=2).get('ok')
    print('re-widthed', len(fix) - bad, 'failed', bad, '| save', run(['--doc', '55840169c52b8bea', 'pcb', 'save']).get('ok'))
