"""Batch Freerouting driver (第 6.5 节批量布线调参 / offline, no EasyEDA).

For every parameter variant of the selected round:
  1. derive  batch_runs/round<N>/<id>/in.dsn  from route/boardR_prep.dsn  (never re-export from EDA)
     - inject an (autoroute_settings ...) block into (structure ...)   [format from runK4_input.dsn]
  2. run Freerouting v2.5.0 (C:\\fr_extract\\freerouting\\freerouting.exe) -> out.ses, stdout -> run.log
     (the DEBUG copy lands in <variant>/en/freerouting.log; `=== Routing Parameters ===` there proves the
      DSN settings were applied)
  3. resumable: a variant whose out.ses + COMPLETED run.log exist is skipped unless --force/--rerun-failed
     (a failing variant never blocks the others; each subprocess has its own timeout)

v2.5.0 CLI facts (verified):
  - `-mp N` still works (deprecated alias of router.max_passes); `--router.autorouter.max_passes` is NOT a
    recognised CLI flag ("Unknown command line argument") -> always use -mp.
  - DSN `(autoroute_settings ...)` is honoured (shown in the per-run `=== Routing Parameters ===` block).
  - `(fanout off)` in the DSN is IGNORED (r1-02 and r1-06 produced byte-identical SES).
  - default via_costs = 50 (that is why round-1's via 10/25/40 exploded the via count).

usage:
  python batch_route.py gen    --round 2 [--only r2-01,r2-03]    # write in.dsn + meta.json only
  python batch_route.py run    --round 2 [--only ...] [--workers 5] [--force] [--rerun-failed]
  python batch_route.py status --round 2
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DSN_BASE = os.path.join(HERE, 'boardR_prep.dsn')
# v2.5.0 jpackage image.  NOTE: `msiexec /a` fails (1603/1708) when TARGETDIR contains non-ASCII chars, so the
# lead unpacked to C:\fr_extract; the MSI itself is kept as route/_fr/fr250.msi (sha256 f5144acb...).
FR_CANDIDATES = [
    os.environ.get('FR_EXE', ''),
    r'C:\fr_extract\freerouting\freerouting.exe',
    os.path.join(HERE, '_fr', 'freerouting', 'freerouting.exe'),
]
FR_EXE = next((p for p in FR_CANDIDATES if p and os.path.exists(p)), FR_CANDIDATES[1])
ROOTS = {n: os.path.join(HERE, 'batch_runs', f'round{n}') for n in (1, 2, 3, 4, 5)}

# --------------------------------------------------------------------------- parameter tables
#   top/bot      = (preferred_direction_trace_costs, against_preferred_direction_trace_costs)
#   top_dir/bot_dir = preferred_direction written into the DSN layer_rule (horizontal/vertical)
#   via           = via_costs (Freerouting default 50), plane_via = plane_via_costs
#   fanout        = DSN (fanout on|off)  [v2.5.0 ignores it, kept for the record]
#   ripup         = start_ripup_costs, passes = -mp
PARAMS1 = [
    dict(id='r1-01', note='control: K4 setting (bot 3.5/4.5, via 25)',
         fanout='on', via=25, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(3.5, 4.5), passes=100),
    dict(id='r1-02', note='bottom 6/8',
         fanout='on', via=25, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(6.0, 8.0), passes=100),
    dict(id='r1-03', note='bottom 10/14',
         fanout='on', via=25, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(10.0, 14.0), passes=100),
    dict(id='r1-04', note='bot 6/8 + via 40',
         fanout='on', via=40, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(6.0, 8.0), passes=100),
    dict(id='r1-05', note='bot 6/8 + via 10',
         fanout='on', via=10, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(6.0, 8.0), passes=100),
    dict(id='r1-06', note='bot 6/8 + fanout off (ignored by v2.5.0)',
         fanout='off', via=25, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(6.0, 8.0), passes=100),
    dict(id='r1-07', note='bot 6/8 + ripup 300',
         fanout='on', via=25, plane_via=5, ripup=300, top=(1.0, 1.2), bot=(6.0, 8.0), passes=100),
    dict(id='r1-08', note='bottom-friendly 2/3 + via 10 (min vias probe)',
         fanout='on', via=10, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(2.0, 3.0), passes=100),
    dict(id='r1-09', note='bot 10/14 + via 40 + ripup 300 + passes 150',
         fanout='on', via=40, plane_via=5, ripup=300, top=(1.0, 1.2), bot=(10.0, 14.0), passes=150),
    dict(id='r1-10', note='bot 14/20 + via 40 + passes 150 (strongest top push)',
         fanout='on', via=40, plane_via=5, ripup=100, top=(1.0, 1.2), bot=(14.0, 20.0), passes=150),
]

# Round 2: baseline uses via_costs=50 and board-adaptive directions (Top vertical / Bottom horizontal).
# Round 1's via 10/25/40 were all BELOW the default 50 -> that is what exploded the via count.
_R2 = dict(fanout='on', plane_via=5, ripup=100, passes=100,
           top=(1.0, 2.1), top_dir='vertical', bot_dir='horizontal')
PARAMS2 = [
    dict(_R2, id='r2-01', note='baseline replica (via 50, bot 1.0/1.9)', via=50, bot=(1.0, 1.9)),
    dict(_R2, id='r2-02', note='via 50, bot 3.5/4.5', via=50, bot=(3.5, 4.5)),
    dict(_R2, id='r2-03', note='via 50, bot 6/8', via=50, bot=(6.0, 8.0)),
    dict(_R2, id='r2-04', note='via 50, bot 10/14', via=50, bot=(10.0, 14.0)),
    dict(_R2, id='r2-05', note='via 75, bot 3.5/4.5', via=75, bot=(3.5, 4.5)),
    dict(_R2, id='r2-06', note='via 75, bot 6/8', via=75, bot=(6.0, 8.0)),
    dict(_R2, id='r2-07', note='via 75, bot 10/14', via=75, bot=(10.0, 14.0)),
    dict(_R2, id='r2-08', note='via 100, bot 6/8', via=100, bot=(6.0, 8.0)),
    dict(_R2, id='r2-09', note='via 100, bot 10/14', via=100, bot=(10.0, 14.0)),
    dict(_R2, id='r2-10', note='via 150, bot 14/20 (extreme via suppression)', via=150, bot=(14.0, 20.0)),
]
# Round 3: local refinement around r2-02 (via 50, bottom 3.5/4.5) - keep ~160-180 signal vias while pushing the
# ground proxy score; plus the two near-miss variants from round 2 retried with more passes.
_R3 = dict(fanout='on', plane_via=5, ripup=100, passes=100,
           top=(1.0, 2.1), top_dir='vertical', bot_dir='horizontal')
PARAMS3 = [
    dict(_R3, id='r3-01', note='via 40, bot 3.5/4.5', via=40, bot=(3.5, 4.5)),
    dict(_R3, id='r3-02', note='via 45, bot 3.0/4.5', via=45, bot=(3.0, 4.5)),
    dict(_R3, id='r3-03', note='via 50, bot 2.5/3.5', via=50, bot=(2.5, 3.5)),
    dict(_R3, id='r3-04', note='via 55, bot 3.5/4.5', via=55, bot=(3.5, 4.5)),
    dict(_R3, id='r3-05', note='via 60, bot 3.5/4.5', via=60, bot=(3.5, 4.5)),
    dict(_R3, id='r3-06', note='via 50, bot 3.5/4.5, ripup 200', via=50, bot=(3.5, 4.5), ripup=200),
    dict(_R3, id='r3-07', note='via 50, bot 3.5/4.5, ripup 300', via=50, bot=(3.5, 4.5), ripup=300),
    dict(_R3, id='r3-08', note='via 45, bot 3.0/4.5, ripup 200', via=45, bot=(3.0, 4.5), ripup=200),
    dict(_R3, id='r3-09', note='retry r2-06: via 75, bot 6/8, passes 150', via=75, bot=(6.0, 8.0), passes=150),
    dict(_R3, id='r3-10', note='retry r2-08: via 100, bot 6/8, passes 150', via=100, bot=(6.0, 8.0), passes=150),
]
# Round 4: local sweep around r3-03 (via 50, bottom 2.5/3.5) - the round-3 winner.
_R4 = dict(fanout='on', plane_via=5, ripup=100, passes=100,
           top=(1.0, 2.1), top_dir='vertical', bot_dir='horizontal')
PARAMS4 = [
    dict(_R4, id='r4-01', note='via 50, bot 2.0/3.0', via=50, bot=(2.0, 3.0)),
    dict(_R4, id='r4-02', note='r3-03 replica: via 50, bot 2.5/3.5', via=50, bot=(2.5, 3.5)),
    dict(_R4, id='r4-03', note='via 50, bot 3.0/4.0', via=50, bot=(3.0, 4.0)),
    dict(_R4, id='r4-04', note='via 45, bot 2.5/3.5', via=45, bot=(2.5, 3.5)),
    dict(_R4, id='r4-05', note='via 55, bot 2.5/3.5', via=55, bot=(2.5, 3.5)),
    dict(_R4, id='r4-06', note='via 60, bot 2.5/3.5', via=60, bot=(2.5, 3.5)),
    dict(_R4, id='r4-07', note='via 50, bot 2.5/3.5, ripup 200', via=50, bot=(2.5, 3.5), ripup=200),
    dict(_R4, id='r4-08', note='via 50, bot 2.5/3.5, ripup 300', via=50, bot=(2.5, 3.5), ripup=300),
    dict(_R4, id='r4-09', note='via 50, bot 2.0/2.5 (very low bottom cost)', via=50, bot=(2.0, 2.5)),
    dict(_R4, id='r4-10', note='via 40, bot 2.5/3.5', via=40, bot=(2.5, 3.5)),
]
# Round 5 (final round): micro-sweep around r4-02 plus the previously untouched autorouter item-selection
# strategy (`-is`, v2.5.0 accepts Sequential / random / prioritized, case-insensitive).
_R5 = dict(fanout='on', plane_via=5, ripup=100, passes=100,
           top=(1.0, 2.1), top_dir='vertical', bot_dir='horizontal')
PARAMS5 = [
    dict(_R5, id='r5-01', note='reference: via 50, bot 2.5/3.5', via=50, bot=(2.5, 3.5)),
    dict(_R5, id='r5-02', note='via 48, bot 2.5/3.5', via=48, bot=(2.5, 3.5)),
    dict(_R5, id='r5-03', note='via 52, bot 2.5/3.5', via=52, bot=(2.5, 3.5)),
    dict(_R5, id='r5-04', note='via 50, bot 2.75/3.75', via=50, bot=(2.75, 3.75)),
    dict(_R5, id='r5-05', note='via 48, bot 2.75/3.75', via=48, bot=(2.75, 3.75)),
    dict(_R5, id='r5-06', note='via 52, bot 2.75/3.75', via=52, bot=(2.75, 3.75)),
    dict(_R5, id='r5-07', note='via 50, bot 2.5/3.5, -is PRIORITIZED', via=50, bot=(2.5, 3.5),
         extra_args=['-is', 'PRIORITIZED']),
    dict(_R5, id='r5-08', note='via 50, bot 2.5/3.5, -is SEQUENTIAL', via=50, bot=(2.5, 3.5),
         extra_args=['-is', 'SEQUENTIAL']),
    dict(_R5, id='r5-09', note='via 50, bot 2.75/3.75, -is PRIORITIZED', via=50, bot=(2.75, 3.75),
         extra_args=['-is', 'PRIORITIZED']),
]
PARAMS_ROUNDS = {1: PARAMS1, 2: PARAMS2, 3: PARAMS3, 4: PARAMS4, 5: PARAMS5}


def settings_block(p):
    top_dir = p.get('top_dir', 'horizontal')
    bot_dir = p.get('bot_dir', 'vertical')
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
        (preferred_direction {top_dir})
        (preferred_direction_trace_costs {p['top'][0]})
        (against_preferred_direction_trace_costs {p['top'][1]})
      )
      (layer_rule BottomLayer
        (active on)
        (preferred_direction {bot_dir})
        (preferred_direction_trace_costs {p['bot'][0]})
        (against_preferred_direction_trace_costs {p['bot'][1]})
      )
    )"""


def insert_settings(text, block):
    """Insert `block` just before the closing paren of the (structure ...) section."""
    i = text.index('(structure')
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                break
        j += 1
    return text[:j] + block + '\n  ' + text[j:]


def write_variant(p, root):
    vdir = os.path.join(root, p['id'])
    os.makedirs(vdir, exist_ok=True)
    dsn = insert_settings(open(DSN_BASE, encoding='utf-8').read(), settings_block(p))
    open(os.path.join(vdir, 'in.dsn'), 'w', encoding='utf-8').write(dsn)
    meta = dict(param=p, freerouting=FR_EXE, dsn='in.dsn', ses='out.ses', log='run.log',
                generated=time.strftime('%Y-%m-%d %H:%M:%S'))
    json.dump(meta, open(os.path.join(vdir, 'meta.json'), 'w', encoding='utf-8'), indent=1)


def freerouting_cmd(p, vdir):
    return [
        FR_EXE,
        '-de', os.path.join(vdir, 'in.dsn'),
        '-do', os.path.join(vdir, 'out.ses'),
        '-l', 'en',
        '-mt', '2',
        '-mp', str(p['passes']),
        '--gui.enabled=false',
        '--usage_and_diagnostic_data.disable_analytics=true',
        '--router.optimizer.enabled=false',
        *p.get('extra_args', []),
    ]


LOG_STATE = re.compile(r"Job .* finished with state:\s*(\w+)")
LOG_STAGE = re.compile(r'Auto-routing stage completed:.*?final score:\s*([\d.]+)\s*'
                       r'\((\d+) unrouted and (\d+) violations\)')


def run_state(vdir):
    """'done' (COMPLETED), 'failed' (finished but not clean), 'partial', 'none'."""
    log, ses = os.path.join(vdir, 'run.log'), os.path.join(vdir, 'out.ses')
    if not os.path.exists(log):
        return 'none', ''
    txt = open(log, encoding='utf-8', errors='replace').read()
    st = LOG_STATE.findall(txt)
    m = LOG_STAGE.search(txt)
    if ses and st and st[-1] == 'COMPLETED' and m:
        return ('done' if (m.group(2) == '0' and m.group(3) == '0') else 'failed'), \
            f'{m.group(2)} unrouted / {m.group(3)} violations'
    if st and st[-1] in ('FAILED', 'CANCELLED', 'TERMINATED'):
        return 'failed', f'state {st[-1]}'
    return 'partial', ''


def run_one(p, root, timeout, force=False, rerun_failed=False):
    vdir = os.path.join(root, p['id'])
    write_variant(p, root)
    state, why = run_state(vdir)
    if not force:
        if state == 'done':
            return p['id'], 'skip (done)'
        if state == 'failed' and not rerun_failed:
            return p['id'], f'skip (failed: {why})'
    cmd = freerouting_cmd(p, vdir)
    log = os.path.join(vdir, 'run.log')
    t0 = time.time()
    with open(log, 'w', encoding='utf-8', errors='replace') as fh:
        fh.write('# ' + ' '.join(cmd) + '\n')
        fh.flush()
        try:
            r = subprocess.run(cmd, cwd=vdir, stdout=fh, stderr=subprocess.STDOUT,
                               timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            rc = r.returncode
        except subprocess.TimeoutExpired:
            rc = 'timeout'
        except OSError as e:
            fh.write(f'# OSError: {e}\n')
            rc = 'oserror'
    state, why = run_state(vdir)
    return p['id'], f'{rc} -> {state} {why} ({time.time() - t0:.0f}s)'


def preflight():
    if not os.path.exists(FR_EXE):
        print(f'Freerouting exe missing: {FR_EXE}\n'
              f'  unpack the official v2.5.0 MSI with `msiexec /a fr250.msi /qn TARGETDIR=C:\\fr_extract` '
              f'(ASCII path only) and retry')
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['gen', 'run', 'status'])
    ap.add_argument('--round', type=int, choices=(1, 2, 3, 4, 5), default=1)
    ap.add_argument('--only', default=None, help='comma separated ids')
    ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--timeout', type=int, default=2700)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--rerun-failed', action='store_true')
    a = ap.parse_args()
    params, root = PARAMS_ROUNDS[a.round], ROOTS[a.round]
    by_id = {p['id']: p for p in params}
    todo = params if not a.only else [by_id[x.strip()] for x in a.only.split(',') if x.strip()]
    if a.cmd == 'gen':
        os.makedirs(root, exist_ok=True)
        json.dump(dict(round=a.round, params=params), open(os.path.join(root, 'params.json'), 'w',
                                                           encoding='utf-8'), indent=1)
        for p in todo:
            write_variant(p, root)
            print(f"{p['id']}: wrote in.dsn ({p['note']})")
        return
    if a.cmd == 'status':
        for p in params:
            st, why = run_state(os.path.join(root, p['id']))
            print(f"{p['id']:7s} {st:8s} {why}")
        return
    if not preflight():
        return 1
    os.makedirs(root, exist_ok=True)
    print(f'round {a.round}: running {len(todo)} variant(s), workers={a.workers}')
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(run_one, p, root, a.timeout, a.force, a.rerun_failed): p for p in todo}
        for f in cf.as_completed(futs):
            vid, msg = f.result()
            print(f'{vid}: {msg}', flush=True)
    print('--- status ---')
    for p in params:
        st, why = run_state(os.path.join(root, p['id']))
        print(f"{p['id']:7s} {st:8s} {why}")


if __name__ == '__main__':
    sys.exit(main() or 0)
