"""(Re)pour the ground net on top and bottom inside an inset outline, rebuild, save.

usage: python pour_gnd.py [inset_mm]          (default [ground] pour_inset_mm, 0.3; net [ground] net, GND)
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
run = K.run
B = K.board()
MIL = K.MIL; W, H = B.w, B.h; ch = B.corner + 0.2
i = float(sys.argv[1]) if len(sys.argv) > 1 else B.ground.get('pour_inset_mm', 0.3)
NET = B.ground.get('net', 'GND')
ids = [p['primitiveId'] for p in (run(['pcb', 'pour-list']).get('result') or {}).get('pours', [])]
if ids: run(['pcb', 'pour-delete', '--ids', ','.join(ids)])
pts = [(i + ch, i), (W - i - ch, i), (W - i, i + ch), (W - i, H - i - ch), (W - i - ch, H - i), (i + ch, H - i), (i, H - i - ch), (i, i + ch)]
P = json.dumps([[round(x * MIL, 2), round(y * MIL, 2)] for x, y in pts])
for L in (1, 2):
    print('pour', L, run(['pcb', 'pour', '--points', P, '--net', NET, '--layer', str(L), '--name', f'{NET}_{"TOP" if L == 1 else "BOT"}'], tries=1).get('ok'))
print('rebuild', run(['pcb', 'pour-rebuild']).get('ok'), 'save', run(['pcb', 'save']).get('ok'))
