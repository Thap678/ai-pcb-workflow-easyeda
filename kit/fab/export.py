"""Export the fabrication set of one board from EasyEDA into a release folder, plus a project backup.

usage: python export.py [out_dir]          (default: <config dir>/release/<board>_<yyyymmdd-HHMM>)
Writes: Gerber zip (copper, mask, paste, silkscreen, outline, drill), pick-and-place CSV, BOM CSV,
IPC-D-356A netlist (bare-board electrical test), and <project>.epro2 (whole-project backup).
Then run check.py on the folder. Ordering stays manual: this script never calls the place*Order APIs.

Prerequisite: the board passed DRC (0) and conn.py (0 open nets), and the PCB is saved.
"""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                      B.path(os.path.join('release', f"{B.name}_{time.strftime('%Y%m%d-%H%M')}")))
os.makedirs(out, exist_ok=True)
name = B.fab.get('name', B.name)
code = f"""
const U = (typeof ESYS_Unit !== 'undefined') ? ESYS_Unit.MILLIMETER : undefined;
const out = {json.dumps(out)}; const M = eda.pcb_ManufactureData; const S = eda.sys_FileSystem; const r = {{}};
async function keep(k, f) {{ r[k] = f ? [f.name, f.size, await S.saveFileToFileSystem(out, f, f.name, true)] : null; }}
await keep('gerber', await M.getGerberFile({json.dumps(name + '_Gerber')}, false, U));
await keep('cpl', await M.getPickAndPlaceFile({json.dumps(name + '_PickAndPlace')}, 'csv', U));
await keep('bom', await M.getBomFile({json.dumps(name + '_BOM')}, 'csv'));
await keep('ipc356', await M.getIpcD356AFile({json.dumps(name + '_IPC356A')}));
return r;
"""
v = K.js(code, timeout=300)
for k, item in v.items():
    print(f'{k:8}', item if item else 'NOT EXPORTED')
missing = [k for k, item in v.items() if not item or not item[2]]

# whole-project backup next to the fabrication files (the archive is what restores the design later)
backup = os.path.join(out, f'{name}_project.epro2')
if not os.path.exists(backup):
    p = subprocess.run(['easyeda', 'project', 'export', '--window', K.window_id(), '--project-uuid', B.project,
                        '--out', backup], capture_output=True, text=True, encoding='utf-8', errors='replace')
    d = K.parse(p.stdout) or {}
    print('backup  ', backup if d.get('ok') else 'FAILED ' + (p.stdout + p.stderr)[-300:])
    if not d.get('ok'): missing.append('backup')
print('->', out)
print('next: python check.py', out)
sys.exit(1 if missing else 0)
