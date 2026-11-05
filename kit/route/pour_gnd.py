"""(Re)pour the ground net on top and bottom inside the board outline, inset, then rebuild and save.

usage: python pour_gnd.py [inset_mm]          (default [ground] pour_inset_mm, 0.3; net [ground] net, GND)
The outline is read from the board (any shape; keep the PCB the foreground document in EasyEDA), falling back to
outline_mm / the width x height rectangle of the config.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
run = K.run
B = K.board()
MIL = K.MIL
i = float(sys.argv[1]) if len(sys.argv) > 1 else B.ground.get('pour_inset_mm', 0.3)
NET = B.gnd
pts = K.inset_polygon(K.outline_mm(), i)                # read before deleting anything
ids = [p['primitiveId'] for p in (run(['pcb', 'pour-list']).get('result') or {}).get('pours', [])]
if ids: run(['pcb', 'pour-delete', '--ids', ','.join(ids)])
P = json.dumps([[round(x * MIL, 2), round(y * MIL, 2)] for x, y in pts])
for L in (1, 2):
    print('pour', L, run(['pcb', 'pour', '--points', P, '--net', NET, '--layer', str(L), '--name', f'{NET}_{"TOP" if L == 1 else "BOT"}'], tries=1).get('ok'))
print('rebuild', run(['pcb', 'pour-rebuild']).get('ok'), 'save', run(['pcb', 'save']).get('ok'))
