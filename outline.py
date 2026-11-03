"""80x87 outline (3 mm corners) + 4 symmetric M3 holes on both boards (requirement v1.1)."""
import json, math, subprocess, sys
P = '5be68595f2b413c75a7f2937434f239f53e66b44c02f15a2e2e3494fe05d691c'
MIL = 1 / 0.0254
HOLES_MM = {'BL': (4, 4), 'BR': (76, 4), 'TR': (76, 83), 'TL': (4, 83)}
def run(args):
    p = subprocess.run(['easyeda', '--project', P] + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = p.stdout[p.stdout.find('{'):]
    return json.loads(out) if out.strip() else {'ok': False, 'raw': p.stderr[:300]}
for doc in sys.argv[1:]:
    if True:
        d = run(['--doc', doc, 'pcb', 'outline-round', '--rect', '0,0,3149.606,3425.197', '--radius', '118.11'])
        print(doc, 'outline', d.get('ok'), d.get('error'))
    for name, (x, y) in HOLES_MM.items():
        cx, cy, r = x * MIL, y * MIL, 1.6 * MIL
        pts = [[round(cx + r * math.cos(2 * math.pi * i / 48), 3), round(cy + r * math.sin(2 * math.pi * i / 48), 3)] for i in range(48)]
        d = run(['--doc', doc, 'pcb', 'slot', '--points', json.dumps(pts), '--locked'])
        print(doc, 'hole', name, d.get('ok'), d.get('error'))
    print(doc, 'save', run(['--doc', doc, 'pcb', 'save']).get('ok'))
