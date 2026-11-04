"""Remove parts cleanly: disconnect every pin (stub + marker), clear no-connect flags, then delete the part.

usage: python del_parts.py <page_uuid> REF [REF ...]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

def run(args, tries=4):
    return K.run(args, tries=tries, doc=False)

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
