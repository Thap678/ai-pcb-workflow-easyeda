"""Route a prepared DSN with Freerouting, several parameter variants in parallel, and rank the results.

usage: python route.py <prepared.dsn> [--only id1,id2]
Variants come from [[boards.<b>.route.variants]] in the config; each runs in route_runs/<board>/<id>/
(in.dsn, out.ses, run.log). Ranking: unrouted, then violations, then via count. Freerouting with -mt 2 is not
deterministic, so running the same recipe twice is a cheap way to get a second sample.

Variant keys (Freerouting autoroute_settings): via (via_costs, default 50 - lower values explode the via count),
top / bot = [preferred, against-preferred] trace costs, top_dir / bot_dir = horizontal | vertical,
plane_via (5), ripup (start_ripup_costs, 100), fanout (on), passes (100).
Board-wide: [route] edge_clearance_um -> FREEROUTING__ROUTER__COPPER_TO_EDGE_CLEARANCE_UM (copper to board edge).
"""
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
RC = B.route
FR = RC.get('freerouting') or os.environ.get('FR_EXE') or r'C:\fr_extract\freerouting\freerouting.exe'
DEF = dict(fanout='on', plane_via=5, ripup=100, passes=RC.get('passes', 100), via=50, top=[1.0, 2.1], bot=[1.0, 2.1],
           top_dir='horizontal', bot_dir='vertical')
LOG_STAGE = re.compile(r'Auto-routing stage completed:.*?final score:\s*([\d.]+)\s*\((\d+) unrouted and (\d+) violations\)')


def settings_block(p):
    return f"""      (autoroute_settings
      (fanout {p['fanout']})
      (autoroute on)
      (postroute on)
      (vias on)
      (via_costs {p['via']})
      (plane_via_costs {p['plane_via']})
      (start_ripup_costs {p['ripup']})
      (start_pass_no 1)
      (layer_rule TopLayer
        (active on)
        (preferred_direction {p['top_dir']})
        (preferred_direction_trace_costs {p['top'][0]})
        (against_preferred_direction_trace_costs {p['top'][1]})
      )
      (layer_rule BottomLayer
        (active on)
        (preferred_direction {p['bot_dir']})
        (preferred_direction_trace_costs {p['bot'][0]})
        (against_preferred_direction_trace_costs {p['bot'][1]})
      )
    )"""


def insert_settings(text, block):
    """Insert `block` just before the closing paren of the (structure ...) section."""
    i = text.index('(structure')
    depth, j = 0, i
    while j < len(text):
        depth += {'(': 1, ')': -1}.get(text[j], 0)
        if depth == 0 and text[j] == ')':
            break
        j += 1
    return text[:j] + block + '\n  ' + text[j:]


def run(p, base, root):
    vdir = os.path.join(root, p['id']); os.makedirs(vdir, exist_ok=True)
    open(os.path.join(vdir, 'in.dsn'), 'w', encoding='utf-8').write(insert_settings(base, settings_block(p)))
    env = dict(os.environ)
    if RC.get('edge_clearance_um'):
        env['FREEROUTING__ROUTER__COPPER_TO_EDGE_CLEARANCE_UM'] = str(RC['edge_clearance_um'])
    t0 = time.time()
    with open(os.path.join(vdir, 'run.log'), 'w', encoding='utf-8', errors='replace') as lf:
        subprocess.run([FR, '-de', 'in.dsn', '-do', 'out.ses', '-l', 'en', '-mt', '2', '-mp', str(p['passes']),
                        '--gui.enabled=false', '--usage_and_diagnostic_data.disable_analytics=true',
                        '--router.optimizer.enabled=false'], cwd=vdir, env=env, stdout=lf, stderr=lf,
                       timeout=RC.get('timeout_s', 3600))
    txt = open(os.path.join(vdir, 'run.log'), encoding='utf-8', errors='replace').read()
    m = LOG_STAGE.findall(txt)
    pre = re.search(r'(\d+) pre-existing clearance violation', txt)
    ses = os.path.join(vdir, 'out.ses')
    vias = len(re.findall(r'\(via ', open(ses, encoding='utf-8').read())) if os.path.exists(ses) else None
    return dict(id=p['id'], unrouted=int(m[-1][1]) if m else 999, violations=int(m[-1][2]) if m else 999, vias=vias,
                pre_existing=int(pre.group(1)) if pre else 0, secs=round(time.time() - t0), ses=ses)


if __name__ == '__main__':
    if not os.path.exists(FR):
        raise SystemExit(f'Freerouting not found: {FR} (docs/00_准备环境.md)')
    base = open(sys.argv[1], encoding='utf-8').read()
    variants = [dict(DEF, **v) for v in RC.get('variants', [{'id': 'default'}])]
    only = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else None
    if only: variants = [v for v in variants if v['id'] in only]
    root = B.path(os.path.join('route_runs', B.name))
    with ThreadPoolExecutor(RC.get('workers', 4)) as ex:
        res = list(ex.map(lambda p: run(p, base, root), variants))
    res.sort(key=lambda r: (r['unrouted'], r['violations'], r['vias'] or 1e9))
    json.dump(res, open(os.path.join(root, 'summary.json'), 'w'), indent=1)
    print(f"{'id':<12}{'unrouted':>9}{'viol':>6}{'vias':>6}{'pre':>5}{'secs':>6}")
    for r in res:
        print(f"{r['id']:<12}{r['unrouted']:>9}{r['violations']:>6}{r['vias'] or '-':>6}{r['pre_existing']:>5}{r['secs']:>6}")
    if any(r['pre_existing'] for r in res):
        print('!! pre-existing violations in the input (overlapping pads?): fix the placement / footprints first; '
              'see "pre-existing clearance" in run.log')
    best = res[0]
    print('best:', best['id'], best['ses'] if best['unrouted'] == 0 and best['violations'] == 0 else '(not clean: no usable result)')
