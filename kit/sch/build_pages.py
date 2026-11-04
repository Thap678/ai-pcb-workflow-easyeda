"""Build schematic pages from page specs: place -> connect -> no-connect flags -> save -> verify the saved file.

usage: python build_pages.py <spec.json> [<spec.json> ...]
Each spec names its page (`page` uuid). The saved page to verify is the .esch2 with that uuid under the config's
`project_dir` (folder projects, .eprj3); otherwise (database projects, .eprj2, or no project_dir) the project is
exported to an .epro2 and the page is cut out of it. Place is retried (the connector sometimes acks late); a page is done when verify_file prints
"0 problems". A pin that stayed unconnected: `python drv.py <spec> connect <REF>`, save, rerun this script.
"""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import common as K

B = K.board()
proj_dir = K.cfg().get('project_dir')
proj_dir = B.path(proj_dir) if proj_dir else None


def page_file(page, work):
    tag = f'"docType":"SCH_PAGE"'
    if proj_dir and os.path.isdir(proj_dir):
        for f in glob.glob(os.path.join(proj_dir, 'sch', '**', '*.esch2'), recursive=True):
            with open(f, encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    if tag in line and f'"uuid":"{page}"' in line:
                        return f
    arc = os.path.join(work, '_project.epro2')            # database project: cut the page out of an export
    r = K.export_project(arc)
    if not r.get('ok'):
        print('  export failed:', json.dumps(r.get('error'), ensure_ascii=False)[:200]); return None
    txt = K.page_text_from_epro2(arc, page)
    if txt is None: return None
    out = os.path.join(work, page + '.esch2')
    open(out, 'w', encoding='utf-8').write(txt)
    return out


def py(*a):
    p = subprocess.run([sys.executable, os.path.join(HERE, a[0])] + list(a[1:]), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, p.stdout + p.stderr


for spec in sys.argv[1:]:
    s = json.load(open(spec, encoding='utf-8'))
    project = s.get('project') or B.project
    name = os.path.basename(spec)
    for attempt in range(4):
        rc, out = py('drv.py', spec, 'place')
        if rc == 0: break
    print(name, 'place', 'ok' if rc == 0 else 'FAILED\n' + out[-800:], flush=True)
    if rc: continue
    rc, out = py('drv.py', spec, 'connect')
    print(name, 'connect', out.strip().splitlines()[0][:120] if rc == 0 else 'FAILED\n' + out[-1500:], flush=True)
    rc, out = py('drv.py', spec, 'nc')
    if 'failed' in out: print(name, 'nc', out.strip()[-400:], flush=True)
    subprocess.run(['easyeda', '--project', project, '--doc', s['page'], 'sch', 'save'], capture_output=True)
    work = os.path.join(os.path.dirname(os.path.abspath(spec)), 'work'); os.makedirs(work, exist_ok=True)
    f = page_file(s['page'], work)
    if not f:
        print(name, 'verify SKIPPED: saved page', s['page'], 'not found (folder project nor export)'); continue
    rc, out = py('verify_file.py', project, f, spec)
    print(name, 'verify', out.strip().splitlines()[-1] if out.strip() else rc, flush=True)
