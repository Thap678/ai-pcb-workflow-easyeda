"""(Re)pour GND on top and bottom inside an inset outline, rebuild, save. usage: python pour_gnd.py [inset_mm]"""
import json, sys
sys.path.insert(0, '../sch')
from del_parts import run
MIL = 1 / 0.0254; W, H = 80, 87; i = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5; ch = 3.2
ids = [p['primitiveId'] for p in (run(['--doc', '55840169c52b8bea', 'pcb', 'pour-list']).get('result') or {}).get('pours', [])]
if ids: run(['--doc', '55840169c52b8bea', 'pcb', 'pour-delete', '--ids', ','.join(ids)])
pts = [(i + ch, i), (W - i - ch, i), (W - i, i + ch), (W - i, H - i - ch), (W - i - ch, H - i), (i + ch, H - i), (i, H - i - ch), (i, i + ch)]
P = json.dumps([[round(x * MIL, 2), round(y * MIL, 2)] for x, y in pts])
for L in (1, 2):
    print('pour', L, run(['--doc', '55840169c52b8bea', 'pcb', 'pour', '--points', P, '--net', 'GND', '--layer', str(L), '--name', f'GND_{"TOP" if L == 1 else "BOT"}'], tries=1).get('ok'))
print('rebuild', run(['--doc', '55840169c52b8bea', 'pcb', 'pour-rebuild']).get('ok'), 'save', run(['--doc', '55840169c52b8bea', 'pcb', 'save']).get('ok'))
