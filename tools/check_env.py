"""Check everything the kit needs, before the first real step. Read-only.

usage: python tools/check_env.py [--cfg board.toml]
Checks: Python >= 3.11 and the packages, the easyeda CLI (v1.8.x), the daemon and a connected EasyEDA window
(which project it has open), Freerouting (exe present; the MSI's SHA-256 when the installer is next to it),
and - with a config - that the configured project / PCB are the ones open in EasyEDA.
"""
import hashlib, importlib, json, os, shutil, subprocess, sys

FR_SHA = 'f5144acbeefa53248a0e0f2078a13600be73710bb386c013ee11cf0aa9cc10fb'   # freerouting-2.5.0-windows-x64.msi
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kit'))
import common as K                                     # takes --cfg / --board off the command line
res = []
def line(ok, item, detail=''):
    res.append(ok); print(f"{'OK  ' if ok else 'FAIL'}  {item:<26} {detail}")

line(sys.version_info >= (3, 11), 'python >= 3.11', sys.version.split()[0])
B = K.try_board()
if B:
    bad = K.check_keys(K.cfg())
    line(not bad, 'config keys', '; '.join(bad[:5]) or K._CFG_PATH)
for mod in ('shapely', 'numpy', 'scipy', 'PIL'):
    try: importlib.import_module(mod); line(True, f'python: {mod}')
    except ImportError: line(False, f'python: {mod}', 'pip install -r requirements.txt')

exe = shutil.which('easyeda')
line(bool(exe), 'easyeda CLI', exe or 'install easyeda-agent (docs/00_准备环境.md)')
if exe:
    v = subprocess.run(['easyeda', 'version'], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout.strip()
    line('1.8' in v, 'easyeda version', v.splitlines()[0] if v else '?')
    h = subprocess.run(['easyeda', 'health'], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    try: d = json.loads(h[h.find('{'):])
    except json.JSONDecodeError: d = {}
    found = d.get('status') == 'found'
    line(found, 'daemon', 'running' if found else 'start it: easyeda daemon start (keep it running)')
    wins = ((d.get('found') or {}).get('raw') or {}).get('windows') or []
    line(bool(wins), 'EasyEDA window', ', '.join(f"{w['context'].get('projectName')} ({w['context'].get('projectUuid', '')[:8]}...)"
                                                 for w in wins) or 'open EasyEDA Pro + the project; reload the easyeda-agent extension')
    if wins and B:
        line(any(w['context'].get('projectUuid') == B.project for w in wins), 'config project is open', B.project[:12] + '...')

fr = K.freerouting_exe(B)                              # [route] freerouting, else FR_EXE, else the default path
line(os.path.exists(fr), 'Freerouting exe', fr if os.path.exists(fr) else f'{fr} missing (docs/00_准备环境.md)')
msi = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'freerouting', 'freerouting-2.5.0-windows-x64.msi')
if os.path.exists(msi):
    sha = hashlib.sha256(open(msi, 'rb').read()).hexdigest()
    line(sha == FR_SHA, 'Freerouting MSI sha256', 'matches' if sha == FR_SHA else f'MISMATCH {sha}')
print('\nall good' if all(res) else '\nfix the FAIL lines first')
sys.exit(0 if all(res) else 1)
