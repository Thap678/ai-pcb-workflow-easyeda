"""Board outline (rounded rectangle) + round mounting-hole cut-outs from the config, then save.

usage: python outline.py [--board <name>]          (geometry: width_mm / height_mm / corner_mm / mount_holes_mm)
Run before placement and routing: changing the outline after copper exists can strand it.
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
M = K.MIL
d = K.run(['pcb', 'outline-round', '--rect', f'0,0,{B.w * M:.3f},{B.h * M:.3f}', '--radius', f'{B.corner * M:.3f}'])
print('outline', B.w, 'x', B.h, 'mm, corner', B.corner, '->', d.get('ok'), d.get('error') or '')
r = B.hole_d / 2 * M
for x, y in B.holes:
    cx, cy = x * M, y * M
    pts = [[round(cx + r * math.cos(2 * math.pi * i / 48), 3), round(cy + r * math.sin(2 * math.pi * i / 48), 3)]
           for i in range(48)]
    d = K.run(['pcb', 'slot', '--points', json.dumps(pts), '--locked'])
    print(f'hole ({x}, {y}) d={B.hole_d}', d.get('ok'), d.get('error') or '')
print('save', K.run(['pcb', 'save']).get('ok'))
