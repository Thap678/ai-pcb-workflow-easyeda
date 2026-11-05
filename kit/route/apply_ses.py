"""Replace the board's routing with a Freerouting SES, keeping the routed widths.

delete pours -> rip-up -> import SES -> dump -> recreate segments the importer widened to the net-rule default
-> save -> dump again to <ses>.imported.json and report per-net connectivity (conn.py).
usage: python apply_ses.py file.ses          (check it first: python ses_conn.py <dsn> file.ses)
"""
import json, os, re, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
run = K.run

ses_path = sys.argv[1]
ids = [p['primitiveId'] for p in (run(['pcb', 'pour-list']).get('result') or {}).get('pours', [])]
if ids: print('delete pours', run(['pcb', 'pour-delete', '--ids', ','.join(ids)]).get('ok'))
print('rip-up', run(['pcb', 'rip-up']).get('ok'))
print('import', run(['pcb', 'import-autoroute', ses_path], tries=1).get('ok'))
run(['pcb', 'save'])
d = K.dump(ses_path + '.imported.json')

ses = open(ses_path, encoding='utf-8').read(); body = ses[ses.index('(network_out'):]
segs = []
for m in re.finditer(r'\(net ("?[^\s"]+"?)(.*?)(?=\n      \(net |\Z)', body, re.S):
    net = m.group(1).strip('"')
    for w in re.finditer(r'\(path (\S+) (\d+)\s+([\d\s-]+)\)', m.group(2)):
        v = [int(x) / 1000 for x in w.group(3).split()]; pts = list(zip(v[0::2], v[1::2]))
        for a, b in zip(pts, pts[1:]): segs.append((net, 1 if w.group(1) == 'TopLayer' else 2, int(w.group(2)) / 1000, a, b))
key = lambda net, L, a, b: (net, L, tuple(sorted([(round(a[0]), round(a[1])), (round(b[0]), round(b[1]))])))
idx = {}
for l in d['copper']['lines']:
    idx.setdefault(key(l['net'], l['layer'], (l['startX'], l['startY']), (l['endX'], l['endY'])), []).append(l)
need = []
for net, L, w, a, b in segs:
    for l in idx.get(key(net, L, a, b), []):
        if l['lineWidth'] > w + 0.05: need.append((l['primitiveId'], net, L, w, a, b))
print('segments to restore to routed width:', len(need))
for k in range(0, len(need), 40):
    run(['pcb', 'track-delete', '--ids', ','.join(n[0] for n in need[k:k + 40])])
bad = 0
for _, net, L, w, (x1, y1), (x2, y2) in need:
    r = run(['pcb', 'track', '--x1', str(x1), '--y1', str(y1), '--x2', str(x2), '--y2', str(y2),
             '--layer', str(L), '--width', f'{w:.3f}', '--net', net])
    bad += not r.get('ok')
print('restored', len(need) - bad, 'reported failed', bad, '| save', run(['pcb', 'save']).get('ok'))
d = K.dump(ses_path + '.imported.json')
# read back: a "failed" track may have landed (and is not resent), so count what is on the board now
idx = {}
for l in d['copper']['lines']:
    idx.setdefault(key(l['net'], l['layer'], (l['startX'], l['startY']), (l['endX'], l['endY'])), []).append(l)
got = [len([l for l in idx.get(key(net, L, a, b), []) if abs(l['lineWidth'] - w) <= 0.05]) for _, net, L, w, a, b in need]
print('read back: missing', got.count(0), '| duplicated', sum(g > 1 for g in got),
      '(rerun only for missing ones; conn.py below shows whether they matter)')
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'conn.py'), ses_path + '.imported.json'])
