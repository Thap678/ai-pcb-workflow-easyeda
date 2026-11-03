"""Re-route the rebuilt PCB6 with the chosen r9-02 recipe (2026-11-03 rebuild after the workspace loss).

r9-02 = both layers prefer horizontal, via_costs 70, top 1.0/2.1, bottom 3.5/4.5, plane_via 5, ripup 100,
passes 100, optimizer off, COPPER_TO_EDGE_CLEARANCE_UM=450. Freerouting with -mt 2 is not deterministic, so the
recipe runs twice next to its via 60 / 80 neighbours; pick the clean run with the fewest vias.
usage: python rebuild_route.py   (writes batch_runs/rebuild/<id>/{in.dsn,out.ses,run.log})
"""
import os, json, time, subprocess, re
from concurrent.futures import ThreadPoolExecutor
import batch_route as BR

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, 'boardR_prep.dsn')
ROOT = os.path.join(HERE, 'batch_runs', 'rebuild')
R9 = dict(fanout='on', plane_via=5, ripup=100, passes=100, top=(1.0, 2.1), bot=(3.5, 4.5),
          top_dir='horizontal', bot_dir='horizontal', via=70)
VARIANTS = [dict(R9, id='r9-02a'), dict(R9, id='r9-02b'), dict(R9, id='v60', via=60), dict(R9, id='v80', via=80)]
ENV = {'FREEROUTING__ROUTER__COPPER_TO_EDGE_CLEARANCE_UM': '450'}

def run(p):
    vdir = os.path.join(ROOT, p['id']); os.makedirs(vdir, exist_ok=True)
    open(os.path.join(vdir, 'in.dsn'), 'w', encoding='utf-8').write(
        BR.insert_settings(open(BASE, encoding='utf-8').read(), BR.settings_block(p)))
    t0 = time.time()
    with open(os.path.join(vdir, 'run.log'), 'w', encoding='utf-8', errors='replace') as lf:
        subprocess.run([BR.FR_EXE, '-de', 'in.dsn', '-do', 'out.ses', '-l', 'en', '-mt', '2', '-mp', str(p['passes']),
                        '--gui.enabled=false', '--usage_and_diagnostic_data.disable_analytics=true',
                        '--router.optimizer.enabled=false'], cwd=vdir, env=dict(os.environ, **ENV),
                       stdout=lf, stderr=lf, timeout=3600)
    txt = open(os.path.join(vdir, 'run.log'), encoding='utf-8', errors='replace').read()
    m = BR.LOG_STAGE.findall(txt)
    ses = os.path.join(vdir, 'out.ses')
    vias = len(re.findall(r'\(via ', open(ses, encoding='utf-8').read())) if os.path.exists(ses) else None
    return dict(id=p['id'], via_cost=p['via'], unrouted=m[-1][1] if m else '?', violations=m[-1][2] if m else '?',
                vias=vias, secs=round(time.time() - t0))

if __name__ == '__main__':
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(run, VARIANTS))
    json.dump(res, open(os.path.join(ROOT, 'summary.json'), 'w'), indent=1)
    for r in res: print(r)
