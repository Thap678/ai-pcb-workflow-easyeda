"""Ground-integrity metrics from a `pcb dump --include-copper` snapshot.

per layer: GND pour area, number of pour pieces, largest piece, non-GND track length (copper that cuts the plane);
board: GND islands (pieces joined through vias/THT pads), dead islands (no pads), GND via count.
usage: python gmetrics.py dump.json [label]
"""
import json, os, sys, math, collections
import gndconn_lib as G
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
_B = K.board()
NET = _B.ground.get('net', 'GND')

path = sys.argv[1]
items, groups = G.islands(path, NET)
d = K.load_dump(path)
cu = d['copper']; MM = G.MM
board = _B.w * _B.h
out = {}
for L in (1, 2):
    pieces = [it[2] for it in items if it[0] == 'pour' and L in it[1]]
    area = sum(p.area for p in pieces)
    tl = sum(math.hypot(l['endX'] - l['startX'], l['endY'] - l['startY']) * MM for l in cu['lines'] if l['layer'] == L and l['net'] != NET)
    out[L] = dict(area=round(area), cover=round(100 * area / board, 1), pieces=len(pieces),
                  largest=round(max((p.area for p in pieces), default=0)), sig_len=round(tl))
main = groups[0]
dead = [g for g in groups if not any(it[0] == 'pad' for it in g)]
for L in (1, 2):                                       # pour area that is actually tied into the main GND
    out[L]['main'] = round(sum(it[2].area for it in main if it[0] == 'pour' and L in it[1]))
gnd_vias = sum(1 for v in cu['vias'] if v['net'] == NET)
label = sys.argv[2] if len(sys.argv) > 2 else path
print(f"[{label}]")
for L, n in ((1, 'TOP'), (2, 'BOT')):
    o = out[L]
    print(f"  {n}: GND {o['area']} mm2 ({o['cover']}% of board) in {o['pieces']} pieces, largest {o['largest']} mm2 | non-GND track {o['sig_len']} mm")
print(f"  GND islands {len(groups)} (dead {len(dead)}, {round(sum(x[2].area for g in dead for x in g))} mm2) | GND vias {gnd_vias} | all vias {len(cu['vias'])}")
# Ground-integrity score (higher = better). Bottom is the reference plane, so it weighs most; poured area tied
# into the main GND earns a bonus (user request); dead copper does not count and is penalised.
#   S = 1.0*bottom main-GND cover% + 0.5*top main-GND cover% + 0.4*bottom largest-piece%   (integrity)
#     + 0.3*total main-GND area as % of 2x board                                           (pour-area bonus)
#     - 0.3*(bottom pieces - 1) - 2*dead islands - 0.005*bottom non-GND track mm
pct = lambda a: 100 * a / board
S = (1.0 * pct(out[2]['main']) + 0.5 * pct(out[1]['main']) + 0.4 * pct(out[2]['largest'])
     + 0.3 * 100 * (out[1]['main'] + out[2]['main']) / (2 * board)
     - 0.3 * (out[2]['pieces'] - 1) - 2 * len(dead) - 0.005 * out[2]['sig_len'])
print(f"  main-GND pour: TOP {out[1]['main']} mm2, BOT {out[2]['main']} mm2 | SCORE {S:.1f}")
json.dump({'layers': out, 'islands': len(groups), 'dead': len(dead), 'gnd_vias': gnd_vias, 'score': round(S, 1)},
          open(f'gm_{label}.json', 'w'))
