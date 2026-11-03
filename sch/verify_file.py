"""Independent pin->net reconciliation from the SAVED .esch2 page file.

Live API (sch list) supplies pin coordinates only; the net comes from the saved file:
WIRE --(ATTR key NET)--> net name, LINE.lineGroup = wire id with start/end coordinates.
A pin's net = NET of the wire whose line endpoint coincides with the pin point.

usage: python verify_file.py <project> <page_file.esch2> <spec.json> [spec2.json ...]
"""
import json, subprocess, sys, collections, time

def run(args):
    for _ in range(4):  # the connector occasionally answers with an empty body: retry
        p = subprocess.run(['easyeda'] + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if p.stdout.strip(): return json.loads(p.stdout)
        time.sleep(3)
    raise SystemExit(f"no response: easyeda {' '.join(args)}\n{p.stderr[:500]}")

project, page_file, specs = sys.argv[1], sys.argv[2], sys.argv[3:]
recs = []
started = False
for line in open(page_file, encoding='utf-8'):
    if '"docType":"SCH_PAGE"' in line: started = True
    if not started or not line.startswith('{'): continue
    h, rest = line.rstrip('\n').split('||', 1)
    recs.append((json.loads(h), json.loads(rest.rstrip('|') or '{}')))
# the file may carry several records for one id (incremental updates): merge, later fields win
merged, order = {}, []
for h, b in recs:
    key = (h['type'], h.get('id'))
    if key not in merged: merged[key] = (h, {}); order.append(key)
    merged[key][1].update({k: v for k, v in b.items() if v is not None or k not in merged[key][1]})
recs = [merged[k] for k in order]
wire_net = {}
for h, b in recs:
    if h['type'] == 'ATTR' and b.get('key') == 'NET': wire_net[b.get('parentId')] = b.get('value') or ''
# power/ground flags: COMPONENT whose ATTR 'Global Net Name' gives the net; its x,y is the attach point
comp_xy = {h['id']: (round(b['x']), round(b['y'])) for h, b in recs if h['type'] == 'COMPONENT' and b.get('x') is not None}
flag_at = {}
cattr = collections.defaultdict(dict)
for h, b in recs:
    if h['type'] == 'ATTR' and b.get('parentId') in comp_xy: cattr[b['parentId']][b.get('key')] = b.get('value')
for cid, a in cattr.items():
    if a.get('Global Net Name'):  # power / ground flags
        flag_at[comp_xy[cid]] = a['Global Net Name']
    elif 'Designator' not in a and a.get('Name') and not str(a['Name']).startswith('='):  # net ports
        flag_at[comp_xy[cid]] = a['Name']
wire_pts = collections.defaultdict(list)
for h, b in recs:
    if h['type'] == 'LINE' and b.get('lineGroup') in wire_net:
        wire_pts[b['lineGroup']] += [(round(b['startX']), round(b['startY'])), (round(b['endX']), round(b['endY']))]
for w, pts in wire_pts.items():
    if not wire_net[w]:
        named = {flag_at[p] for p in pts if p in flag_at}
        if len(named) == 1: wire_net[w] = named.pop()
endpoint = collections.defaultdict(set)
for w, pts in wire_pts.items():
    for p in pts: endpoint[p].add(wire_net[w])

parts = []
for s in specs: parts += json.load(open(s, encoding='utf-8'))['parts']
page = json.load(open(specs[0], encoding='utf-8'))['page']
d = run(['--project', project, 'sch', 'list', '--page', page, '--stay', '--include-pins'])
live = {c.get('designator'): c for c in d['result']['components'] if c.get('componentType') == 'part'}
problems, checked = [], 0
for p in parts:
    c = live.get(p['ref'])
    if not c: problems.append(f"missing {p['ref']}"); continue
    exp = {}
    for key, net in p['nets'].items():
        k = key.rstrip('*')
        hits = [pp for pp in c['pins'] if pp['pinNumber'] == k] or [pp for pp in c['pins'] if pp['pinName'] == k]
        if not key.endswith('*'): hits = hits[:1]
        for t in hits: exp[t['pinNumber']] = net
    for pp in c['pins']:
        n = pp['pinNumber']; nets = endpoint.get((round(pp['x']), round(pp['y'])), set())
        checked += 1
        if n in p.get('nc', []):
            if nets: problems.append(f"{p['ref']}.{n} marked NC but wired to {nets}")
            continue
        if n not in exp: problems.append(f"{p['ref']}.{n} not in spec, file nets {nets}"); continue
        if nets != {exp[n]}: problems.append(f"{p['ref']}.{n} ({pp['pinName']}) expected {exp[n]} got {nets or 'nothing'}")
extra = set(live) - {p['ref'] for p in parts}
print(f"{page_file}: {checked} pins checked, {len(problems)} problems, extra parts {sorted(extra)}")
for x in problems: print('  ', x)
