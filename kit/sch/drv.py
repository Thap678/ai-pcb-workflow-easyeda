"""Page driver for easyeda-agent: place -> readback pins -> autoconnect (label style).

usage: python drv.py <spec.json> place|pins|connect|check
spec: {project?, page, parts:[{ref, lcsc | lib+uuid, x, y, rot?, mirror?, nets:{pinNameOrNumber: net}, nc:[...]}],
       power:[...], ground:[...]}     (format: docs/03_原理图.md; project defaults to the board config's)
Working files (<name>_pins.json, <name>_conn.json, <name>_conn_result.json) go to a `work/` folder next to the
spec; the LCSC -> library cache is shared (kit/sch/lcsc_cache.json).
"""
import json, subprocess, sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE                                    # set per spec in __main__ (<spec dir>/work)

def run(args, check=True):
    p = subprocess.run(['easyeda'] + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = p.stdout.strip()
    try:
        d = json.loads(out) if out else {}
    except json.JSONDecodeError:
        d = {'raw': out}
    if check and (p.returncode != 0 or (isinstance(d, dict) and d.get('ok') is False)):
        raise SystemExit(f"FAILED easyeda {' '.join(args)}\n{out[:3000]}\n{p.stderr[:2000]}")
    return d

def load(path):
    return json.load(open(path, encoding='utf-8'))

def resolve(spec):
    cache_path = os.path.join(HERE, 'lcsc_cache.json')
    cache = json.load(open(cache_path, encoding='utf-8')) if os.path.exists(cache_path) else {}
    need = sorted({p['lcsc'] for p in spec['parts'] if 'lcsc' in p} - set(cache))
    if need:
        d = run(['lib', 'by-lcsc', '--lcsc', ','.join(need)])
        for c in d['result']['components']:
            cache[c['lcsc']] = {'lib': c['libraryUuid'], 'uuid': c['uuid'], 'mpn': c.get('manufacturerId'),
                                'fp': c.get('footprintName')}
        missing = [n for n in need if n not in cache]
        if missing: raise SystemExit(f'unresolved LCSC: {missing}')
        json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return cache

def g(spec):
    return ['--project', spec['project'], '--doc', spec['page']]

def place(spec):
    cache = resolve(spec)
    have = {c.get('designator') for c in listing(spec)}
    for p in spec['parts']:
        if p['ref'] in have:
            print('skip (exists)', p['ref']); continue
        c = {'lib': p['lib'], 'uuid': p['uuid'], 'mpn': p['ref']} if 'lib' in p else cache[p['lcsc']]
        args = g(spec) + ['sch', 'place', '--lib', c['lib'], '--uuid', c['uuid'], '--x', str(p['x']), '--y', str(p['y']),
                          '--designator', p['ref']]
        if p.get('rot'): args += ['--rotation', str(p['rot'])]
        if p.get('mirror'): args += ['--mirror']
        d = run(args, check=False)
        if not (isinstance(d, dict) and d.get('ok')):
            have2 = {x.get('designator') for x in listing(spec)}
            if p['ref'] not in have2: raise SystemExit(f"place failed for {p['ref']}: {json.dumps(d)[:500]}")
            print('placed (late ack)', p['ref'], c['mpn']); continue
        print('placed', p['ref'], c['mpn'])

def listing(spec):
    d = run(['--project', spec['project'], 'sch', 'list', '--page', spec['page'], '--stay', '--include-pins', '--include-bbox'])
    cs = [c for c in d['result']['components'] if c.get('componentType') != 'sheet']
    for c in cs:
        for pp in c.get('pins', []):
            pp['number'] = pp.get('pinNumber'); pp['name'] = pp.get('pinName')
    return cs

def pins(spec):
    comps = listing(spec)
    out = {}
    for c in comps:
        out[c.get('designator')] = {'bbox': c.get('bbox'), 'pins': [{'number': pp.get('pinNumber'), 'name': pp.get('pinName'), 'x': pp.get('x'), 'y': pp.get('y'), 'net': pp.get('net')} for pp in c.get('pins', [])]}
    json.dump(out, open(os.path.join(OUT, spec['name'] + '_pins.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for ref, v in sorted(out.items()):
        print(ref, v['bbox'], [(p['number'], p['name']) for p in v['pins']])

def kind_for(spec, net):
    if net in spec.get('ground', []): return 'gnd'
    if net in spec.get('power', []): return 'power'
    return 'net_label'

def connect(spec, only=None):
    comps = {c.get('designator'): c for c in listing(spec)}
    conns, errors = [], []
    for p in spec['parts']:
        if only and p['ref'] not in only: continue
        c = comps.get(p['ref'])
        if not c: errors.append(f"{p['ref']} not placed"); continue
        pl = c.get('pins', [])
        for key, net in p['nets'].items():
            if net == '': continue
            star = key.endswith('*')
            k = key.rstrip('*')
            matches = [pp for pp in pl if pp.get('number') == k] or [pp for pp in pl if (pp.get('name') or '') == k]
            if not matches: errors.append(f"{p['ref']}:{key} no such pin; pins={[(x.get('number'), x.get('name')) for x in pl]}"); continue
            targets = matches if star else matches[:1]
            if not star and len(matches) > 1 and not k.isdigit():
                errors.append(f"{p['ref']}:{key} ambiguous name ({len(matches)} pins) - use number or trailing *"); continue
            for t in targets:
                if (t.get('net') or '') == net: continue
                conns.append({'pin': f"{p['ref']}:{t['number']}", 'kind': kind_for(spec, net), 'net': net})
        # every pin must be mapped or declared nc
        mapped = set()
        for key in p['nets']:
            k = key.rstrip('*')
            for pp in pl:
                if pp.get('number') == k or (pp.get('name') or '') == k: mapped.add(pp['number'])
        unm = [pp['number'] for pp in pl if pp['number'] not in mapped and pp['number'] not in p.get('nc', [])]
        if unm: errors.append(f"{p['ref']} unmapped pins {unm}")
    if errors:
        print('\n'.join(errors)); raise SystemExit('fix spec first')
    path = os.path.join(OUT, spec['name'] + '_conn.json')
    json.dump({'connections': conns, 'rules': {'avoidTitleBlock': True, 'avoidPinFanout': True, 'staggerLabels': True,
               'offsetRange': [18, 80], 'offsetStep': 6, 'minLabelGap': 12}}, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(len(conns), 'connections ->', path)
    if conns:
        d = run(g(spec) + ['sch', 'autoconnect', '--spec', path, '--json'], check=False)
        json.dump(d, open(os.path.join(OUT, spec['name'] + '_conn_result.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        txt = json.dumps(d, ensure_ascii=False)
        print(txt[:1500])

def nc(spec):
    for p in spec['parts']:
        bad = []
        for k in p.get('nc', []):
            d = run(g(spec) + ['sch', 'no-connect', '--designator', p['ref'], '--pin', k], check=False)
            if not d.get('ok'): bad.append(k)
        if p.get('nc'):
            print(p['ref'], 'nc', p['nc'], 'failed: ' + ','.join(bad) if bad else 'ok')

def move(spec, path, moves):
    """moves: {ref: (x, y)} -> disconnect all pins, relocate, update spec file, reconnect those refs"""
    comps = {c.get('designator'): c for c in listing(spec)}
    for ref, (x, y) in moves.items():
        c = comps[ref]
        for pp in c.get('pins', []):  # pin.net readback can be empty even when a stub exists: always try
            run(g(spec) + ['sch', 'disconnect', '--pin', f"{ref}:{pp['pinNumber']}"], check=False)
        d = run(g(spec) + ['sch', 'modify', '--id', c['primitiveId'], '--x', str(x), '--y', str(y)], check=False)
        print('moved', ref, 'ok' if d.get('ok') else json.dumps(d)[:200])
        for p in spec['parts']:
            if p['ref'] == ref: p['x'], p['y'] = x, y
    json.dump(spec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    connect(spec, list(moves))

def with_project(spec):
    """Fill spec['project'] from the board config when the spec leaves it out."""
    if not spec.get('project'):
        sys.path.insert(0, os.path.dirname(HERE))
        import common as K
        spec['project'] = K.board().project
    return spec

if __name__ == '__main__':
    spec = with_project(load(sys.argv[1])); cmd = sys.argv[2]
    spec.setdefault('name', os.path.splitext(os.path.basename(sys.argv[1]))[0])
    OUT = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), 'work'); os.makedirs(OUT, exist_ok=True)
    if cmd == 'place': place(spec)
    elif cmd == 'pins': pins(spec)
    elif cmd == 'connect': connect(spec, sys.argv[3].split(',') if len(sys.argv) > 3 else None)
    elif cmd == 'nc': nc(spec)
    elif cmd == 'move': move(spec, sys.argv[1], {k: tuple(v) for k, v in json.loads(sys.argv[3]).items()})
