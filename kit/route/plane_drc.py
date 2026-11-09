"""Reload the PCB, rebuild every pour net by net, save, run DRC and summarise it.

usage: python plane_drc.py [out drc.json]        (default: drc_plane.json next to the config)

Why this order (EasyEDA Pro 4.1.60, measured 2026-11-08):
  - after thousands of tracks / vias created through the API the connectivity cache is stale: DRC reported 239
    "track is disconnected" errors on nets whose copper was fully connected; `doc reload` (save, close, reopen)
    clears it. Also needed after editing tracks / vias / parts through the API again.
  - `pcb pour-rebuild` of all 22 pours of a 4-layer board times out on the connector ("context deadline
    exceeded"); `pcb pour-rebuild --net <net>` takes a few seconds per net.
  - after the reload the pours are stale, so they are rebuilt AFTER it, then DRC straight away (kit/pcb/guard.py:
    a pour rebuilt before a reload came back stale).
"""
import collections, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K  # noqa: E402

B = K.board()
out = sys.argv[1] if len(sys.argv) > 1 else B.path('drc_plane.json')
p = subprocess.run(['easyeda', '--project', B.project, 'doc', 'reload', B.pcb], capture_output=True, text=True,
                   encoding='utf-8', errors='replace', timeout=600)      # plain-text reply, not JSON: no K.run retries
if 'reloaded' not in p.stdout:
    raise SystemExit('doc reload failed - reopen the PCB in EasyEDA, then run this again:\n' + (p.stdout + p.stderr)[-400:])
print('reload ok')
nets = K.js("return [...new Set((await eda.pcb_PrimitivePour.getAll()).map(p => p.getState_Net()))];") or []
bad = [n for n in nets if not K.run(['pcb', 'pour-rebuild', '--net', n], tries=1, timeout=900).get('ok')]
print(f'pours rebuilt for {len(nets) - len(bad)} of {len(nets)} nets', f'FAILED {bad}' if bad else '')
print('save', K.run(['pcb', 'save']).get('ok'))
d = K.run(['pcb', 'drc', '--json', '--timeout', '600'], tries=1, timeout=900)
d = d if 'violations' in d else (d.get('result') or {})
if 'violations' not in d:
    raise SystemExit('DRC did not run (is the PCB the foreground document?)')
json.dump(d, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"DRC total {d.get('total')}  {d.get('counts')}  -> {out}")
c = collections.Counter((v['rule'], v.get('objType')) for v in d['violations'])
for k, n in c.most_common(): print(f'  {n:4d}  {k[0]} / {k[1]}')
for v in d['violations'][:20]:
    print('   ', v['rule'][:16], '|', v.get('message', '')[:110], '@ %.2f, %.2f mm' % (v.get('x', 0) * 0.0254, v.get('y', 0) * 0.0254))
if any(v['rule'] == 'Netlist Error' for v in d['violations']):
    print('Netlist Error: compare pad nets with `easyeda sch netlist` (plane_check.py --netlist) before importing changes '
          'into a routed board - on the nuedc-car boards it was an attribute-level difference, every pad net matched')
