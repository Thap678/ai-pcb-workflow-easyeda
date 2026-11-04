"""Put every component back where an earlier `pcb dump` had it (anchor x/y, rotation, layer, lock).

usage: python place_from_dump.py <reference dump.json>
Matched by designator; parts missing on either side are listed, not guessed. Use it to restore a layout onto a
re-imported board, or to carry a layout to a copy of the project. Same footprints are assumed (anchor = origin).
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

ref = {c['designator']: c for c in K.load_dump(sys.argv[1])['components']}
live = {c['designator']: c for c in K.run(['pcb', 'list'])['result']['components']}
print('missing on board:', sorted(set(ref) - set(live)), '| not in reference:', sorted(set(live) - set(ref)))
bad = []
for des, c in sorted(live.items()):
    r = ref.get(des)
    if not r: continue
    # a patch that carries `layer` together with x/y silently does not land: flip the side on its own first
    if c['layer'] != r['layer']:
        K.run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps({'layer': r['layer']})])
    patch = {'x': r['x'], 'y': r['y'], 'rotation': r['rotation']}
    if K.run(['pcb', 'modify', '--id', c['primitiveId'], '--patch', json.dumps(patch)]).get('ok') is not True:
        bad.append(des)
# readback: a late ack is common, so judge by position, not by the write reply
live = {c['designator']: c for c in K.run(['pcb', 'list'])['result']['components']}
off = [d for d, r in ref.items() if d in live and (abs(live[d]['x'] - r['x']) > 0.5 or abs(live[d]['y'] - r['y']) > 0.5
       or (live[d]['rotation'] - r['rotation']) % 360 or live[d]['layer'] != r['layer'])]
print('write errors:', bad, '| off after readback:', off)
if not off:
    ids = [live[d]['primitiveId'] for d in ref if d in live and ref[d].get('locked')]
    if ids: print('lock', len(ids), K.run(['pcb', 'lock', '--ids', ','.join(ids)]).get('ok'))
    print('save', K.run(['pcb', 'save']).get('ok'))
