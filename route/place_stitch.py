"""Place the GND stitching vias from stitch2_plan.json onto PCB6, idempotently.

NOTE: stitch2.py writes the plan as `round(mm / 0.0254, 2)` -> the numbers are **mil** (same unit as the
dump/CLI), NOT mm.  Pass them through unchanged.

Idempotent: reads the current board dump (dump.json argument or board6_stitch_state.json) and skips plan
points that already have a GND via within 1 mil, so the script can be re-run after a connector drop.
"""
import json, os, subprocess, sys

P = '5be68595f2b413c75a7f2937434f239f53e66b44c02f15a2e2e3494fe05d691c'
DOC = '55840169c52b8bea'
state = sys.argv[1] if len(sys.argv) > 1 else 'board6_stitch_state.json'

existing = []
if os.path.exists(state):
    d = json.loads((lambda s: s[s.find('{'):])(open(state, encoding='utf-8').read()))
    existing = [(v['x'], v['y']) for v in d['copper']['vias'] if v.get('net') == 'GND']

plan = json.load(open('stitch2_plan.json', encoding='utf-8'))


def near(x, y):
    return any(abs(x - ex) < 1.0 and abs(y - ey) < 1.0 for ex, ey in existing)


ok = skipped = bad = 0
for x_mil, y_mil in plan:
    if near(x_mil, y_mil):
        skipped += 1
        continue
    args = ['easyeda', '--project', P, '--doc', DOC, 'pcb', 'via',
            '--x', f'{x_mil:.2f}', '--y', f'{y_mil:.2f}', '--net', 'GND']
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = r.stdout or ''
    if '"ok": true' in out or '"ok":true' in out:
        ok += 1
        existing.append((x_mil, y_mil))
    else:
        bad += 1
        print('FAIL', x_mil, y_mil, (out or r.stderr)[:160].replace('\n', ' '))
print(f'vias placed ok={ok} skipped={skipped} failed={bad} of {len(plan)}')
if bad == 0:
    r = subprocess.run(['easyeda', '--project', P, '--doc', DOC, 'pcb', 'save'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print('save', '"ok": true' in (r.stdout or ''))
