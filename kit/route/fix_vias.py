"""Bring every via up to the rule size after an SES import (EasyEDA imports Freerouting's 17.7 mil via with a
7.8 mil hole, one hair under a 0.2 mm hole rule: DRC flags every via as "Via Size").

usage: python fix_vias.py [--hole-mil 8.0] [--diameter-mil 17.8] [--apply]
Default sizes: rules.via_hole_mm / via_d_mm rounded up to 0.1 mil. A via is re-created at the same centre and net
(track ends meet at the centre, so connectivity is unchanged); deletes go in batches, creates once each, then a
read-back re-creates only what is missing and deletes duplicates.
"""
import collections, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
opt = lambda k, d: float(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
HOLE = opt('--hole-mil', math.ceil(B.rules.get('via_hole_mm', 0.2) * K.MIL * 10 - 1e-6) / 10)
DIAM = opt('--diameter-mil', math.ceil(B.rules.get('via_d_mm', 0.45) * K.MIL * 10 - 1e-6) / 10)


def vias():
    return K.run(['pcb', 'via-list'])['result']['vias']


bad = [v for v in vias() if v['holeDiameter'] < HOLE - 0.05 or v['diameter'] < DIAM - 0.05]
print(f'vias to resize: {len(bad)} (target hole {HOLE} / pad {DIAM} mil)')
if '--apply' not in sys.argv or not bad: sys.exit(0)
want = collections.Counter((v['net'], round(v['x'], 1), round(v['y'], 1)) for v in bad)
ids = [v['primitiveId'] for v in bad]
for k in range(0, len(ids), 40):
    K.run(['pcb', 'via-delete', '--ids', ','.join(ids[k:k + 40])])
todo = bad
for attempt in range(2):
    for v in todo:
        K.run(['pcb', 'via', '--x', f"{v['x']:.3f}", '--y', f"{v['y']:.3f}", '--hole', str(HOLE),
               '--diameter', str(DIAM), '--net', v['net']], tries=1)
    # EasyEDA rounds the hole (7.9 mil is stored as 8): count every via at least the target size
    have = collections.Counter((v['net'], round(v['x'], 1), round(v['y'], 1)) for v in vias()
                               if v['holeDiameter'] >= HOLE - 0.05)
    left = want - have
    todo = [v for v in bad if left[(v['net'], round(v['x'], 1), round(v['y'], 1))] > 0]
    if not todo: break
# duplicates: two good vias on one spot (a create that reported failure but landed, then was re-sent)
have = collections.Counter((v['net'], round(v['x'], 1), round(v['y'], 1)) for v in vias() if v['holeDiameter'] >= HOLE - 0.05)
extra = collections.Counter({k: n - 1 for k, n in have.items() if n > 1})
rm = []
if extra:
    for v in vias():
        k = (v['net'], round(v['x'], 1), round(v['y'], 1))
        if extra[k] > 0 and v['holeDiameter'] >= HOLE - 0.05: rm.append(v['primitiveId']); extra[k] -= 1
    if rm: K.run(['pcb', 'via-delete', '--ids', ','.join(rm)])
small = [v for v in vias() if v['holeDiameter'] < HOLE - 0.05]
print(f'resized {len(bad) - len(todo)} | still missing {len(todo)} | duplicates removed {len(rm)} | '
      f'undersized left {len(small)} | save', K.run(['pcb', 'save']).get('ok'))
