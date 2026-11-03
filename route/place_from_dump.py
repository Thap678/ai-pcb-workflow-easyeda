"""Put every component of a PCB back where an earlier `pcb dump` had it (anchor x/y, rotation, layer, lock).

usage: python place_from_dump.py <project> <pcb uuid> <reference dump.json>
Matched by designator; parts missing on either side are listed, not guessed.
"""
import json, subprocess, sys

P, DOC, REF = sys.argv[1:4]
def run(args):
    for _ in range(3):
        p = subprocess.run(['easyeda', '--project', P, '--doc', DOC] + args, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        out = p.stdout[p.stdout.find('{'):]
        if out.strip():
            try: return json.loads(out)
            except json.JSONDecodeError: pass
    return {'ok': False, 'raw': p.stderr[:300]}

load = lambda d: d.get('result', d)
ref = {c['designator']: c for c in load(json.load(open(REF, encoding='utf-8')))['components']}
live = {c['designator']: c for c in load(run(['pcb', 'list']))['components']}
print('missing on board:', sorted(set(ref) - set(live)), '| not in reference:', sorted(set(live) - set(ref)))
bad = []
for des, c in sorted(live.items()):
    r = ref.get(des)
    if not r: continue
    # a patch that carries `layer` together with x/y silently does not land: flip the side on its own first
    if c['layer'] != r['layer']:
        run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps({'layer': r['layer']})])
    patch = {'x': r['x'], 'y': r['y'], 'rotation': r['rotation']}
    if run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps(patch)]).get('ok') is not True: bad.append(des)
# readback: a late ack is common, so judge by position, not by the write reply
live = {c['designator']: c for c in load(run(['pcb', 'list']))['components']}
off = [d for d, r in ref.items() if d in live and (abs(live[d]['x'] - r['x']) > 0.5 or abs(live[d]['y'] - r['y']) > 0.5
       or (live[d]['rotation'] - r['rotation']) % 360 or live[d]['layer'] != r['layer'])]
print('write errors:', bad, '| off after readback:', off)
if not off:
    ids = [live[d]['primitiveId'] for d in ref if d in live and ref[d].get('locked')]
    print('lock', len(ids), run(['pcb', 'lock', '--ids', ','.join(ids)]).get('ok'))
