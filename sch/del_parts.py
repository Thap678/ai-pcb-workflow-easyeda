"""Remove parts cleanly: disconnect every pin (stub + marker), clear no-connect flags, then delete the part.

usage: python del_parts.py <page_uuid> REF [REF ...]
"""
import json, subprocess, sys, time
P = '5be68595f2b413c75a7f2937434f239f53e66b44c02f15a2e2e3494fe05d691c'

def run(args, tries=4):
    for _ in range(tries):
        p = subprocess.run(['easyeda', '--project', P] + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
        s = p.stdout
        if s.strip() and '{' in s:
            try: return json.loads(s[s.find('{'):])
            except json.JSONDecodeError: pass
        time.sleep(4)
    return {'ok': False, 'error': {'message': (p.stderr or s)[:200]}}

def main():
  page, refs = sys.argv[1], sys.argv[2:]
  d = run(['sch', 'list', '--page', page, '--stay', '--include-pins'])
  comps = {c.get('designator'): c for c in d['result']['components']}
  for ref in refs:
    c = comps.get(ref)
    if not c: print(ref, 'not on page'); continue
    stats = {'disc': 0, 'none': 0, 'err': 0}
    for pp in c.get('pins', []):
        n = pp['pinNumber']
        r = run(['--doc', page, 'sch', 'disconnect', '--pin', f'{ref}:{n}'])
        if r.get('ok'): stats['disc'] += 1
        elif 'No stub' in json.dumps(r, ensure_ascii=False): stats['none'] += 1
        else: stats['err'] += 1
        if pp.get('noConnected'): run(['--doc', page, 'sch', 'no-connect', '--designator', ref, '--pin', n, '--clear'])
    r = run(['--doc', page, 'sch', 'prim-delete', '--ids', c['primitiveId']])
    print(ref, stats, 'deleted' if r.get('ok') else 'DELETE FAILED ' + json.dumps(r.get('error'), ensure_ascii=False)[:150])
  print('save', run(['--doc', page, 'sch', 'save']).get('ok'))

if __name__ == '__main__':
    main()
