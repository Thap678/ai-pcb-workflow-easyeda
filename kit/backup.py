"""Export the whole EasyEDA project to a dated .epro2 archive OUTSIDE the working folder.

usage: python backup.py <label> [backup_dir]       (default dir: [backup] dir in the config, else ~/Documents/pcb_backups)
Do it before every big step (re-route, re-import, rule change) and after every milestone. The .eprj3 project
folder is the only copy of the design otherwise - losing the working folder loses the boards.
"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as K

B = K.board()
label = sys.argv[1] if len(sys.argv) > 1 else 'snapshot'
d = sys.argv[2] if len(sys.argv) > 2 else K.cfg().get('backup', {}).get('dir') or os.path.join(os.path.expanduser('~'), 'Documents', 'pcb_backups')
d = B.path(os.path.expanduser(d)); os.makedirs(d, exist_ok=True)
out = os.path.join(d, f"{time.strftime('%Y-%m-%d_%H%M')}_{label}.epro2")
p = subprocess.run(['easyeda', 'project', 'export', '--window', K.window_id(), '--project-uuid', B.project, '--out', out],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
r = K.parse(p.stdout) or {}
print(out if r.get('ok') else 'FAILED: ' + (p.stdout + p.stderr)[-400:], (r.get('result') or {}).get('bytes', ''))
sys.exit(0 if r.get('ok') else 1)
