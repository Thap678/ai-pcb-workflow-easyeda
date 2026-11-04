"""Shared plumbing for every kit script: the board config, the easyeda CLI wrapper, units and dumps.

Config: a TOML file (see board.example.toml). Picked, in order, from `--cfg <path>` on the command line, the
KIT_CFG environment variable, or ./board.toml. The board inside it comes from `--board <name>`, KIT_BOARD, or
`default_board`. Both flags are removed from sys.argv on import, so scripts keep their own positional arguments.

    import common as K
    B = K.board()                 # .project .pcb .w .h .corner .holes .hole_d ... (mm)
    K.run(['pcb', 'save'])        # easyeda --project <P> --doc <PCB> pcb save, retried, parsed JSON
"""
import json, os, subprocess, sys, time, tomllib

MIL = 1 / 0.0254          # mm -> mil
MM = 0.0254               # mil -> mm
KIT = os.path.dirname(os.path.abspath(__file__))


def _pop_flag(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        val = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return val
    return None


_CFG_PATH = os.path.abspath(_pop_flag('--cfg') or os.environ.get('KIT_CFG') or 'board.toml')
_BOARD = _pop_flag('--board') or os.environ.get('KIT_BOARD')
os.environ['KIT_CFG'] = _CFG_PATH                      # child scripts (build_pages -> drv) see the same config
if _BOARD: os.environ['KIT_BOARD'] = _BOARD
_cfg = None


def cfg():
    global _cfg
    if _cfg is None:
        if not os.path.exists(_CFG_PATH):
            raise SystemExit(f'board config not found: {_CFG_PATH} (copy board.example.toml, or pass --cfg / set KIT_CFG)')
        with open(_CFG_PATH, 'rb') as f:
            _cfg = tomllib.load(f)
        _cfg['_dir'] = os.path.dirname(os.path.abspath(_CFG_PATH))
    return _cfg


class Board:
    """One board of the config, with defaults for everything optional (units: mm unless the name says mil)."""

    def __init__(self, name, c, top):
        g = lambda k, d=None: c.get(k, d)
        self.name, self.project, self.cfg_dir = name, top['project'], top['_dir']
        self.pcb, self.schematic = g('pcb'), g('schematic')
        self.w, self.h, self.corner = g('width_mm'), g('height_mm'), g('corner_mm', 3.0)
        self.holes = [tuple(h) for h in g('mount_holes_mm', [])]
        self.hole_d = g('mount_hole_d_mm', 3.2)
        self.pages = g('pages', {})                       # spec file -> {page uuid, saved .esch2 path}
        self.rules = g('rules', {})
        self.track_classes = g('track_classes', [])
        self.route = g('route', {})
        self.ground = g('ground', {})
        self.fab = g('fab', {})

    def path(self, p):
        """Resolve a path from the config relative to the config file."""
        return p if os.path.isabs(p) else os.path.join(self.cfg_dir, p)

    def class_min_mil(self):
        """net -> track rule minimum (mil), from [[track_classes]]."""
        return {n: tc['min_mm'] * MIL for tc in self.track_classes for n in tc.get('nets', [])}


def board(name=None):
    c = cfg()
    name = name or _BOARD or c.get('default_board') or next(iter(c['boards']))
    if name not in c['boards']:
        raise SystemExit(f'board {name!r} not in {_CFG_PATH}: {list(c["boards"])}')
    return Board(name, c['boards'][name], c)


def parse(stdout):
    """First JSON object in a CLI stdout (warnings may precede it)."""
    i = stdout.find('{')
    if i < 0:
        return None
    try:
        return json.loads(stdout[i:])
    except json.JSONDecodeError:
        return None


def run(args, tries=4, doc=True, timeout=600, board_=None):
    """easyeda --project <P> [--doc <PCB>] <args>, retried on empty/garbled replies.

    doc=True pins the board's PCB, doc=False adds nothing, a string pins that document (page / PCB uuid).
    The connector sometimes reports a failure for a write that landed (and vice versa): callers that write
    should read back instead of trusting `ok`.
    """
    B = board_ or board()
    pre = ['easyeda', '--project', B.project]
    if doc is True:
        pre += ['--doc', B.pcb]
    elif doc:
        pre += ['--doc', doc]
    last = ''
    for k in range(tries):
        try:
            p = subprocess.run(pre + args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            last = 'timeout'
            continue
        d = parse(p.stdout)
        if d is not None:
            return d
        last = (p.stderr or p.stdout)[:300]
        if k + 1 < tries:
            time.sleep(4)
    return {'ok': False, 'error': {'message': last}}


def load_dump(path):
    d = json.loads((lambda s: s[s.find('{'):])(open(path, encoding='utf-8').read()))
    return d.get('result', d) if 'components' not in d else d


def dump(path, doc=None):
    """pcb dump --include-copper of the board's PCB (or `doc`) -> file; returns the parsed dict."""
    B = board()
    p = subprocess.run(['easyeda', '--project', B.project, '--doc', doc or B.pcb, 'pcb', 'dump', '--include-copper'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    open(path, 'w', encoding='utf-8').write(p.stdout)
    return load_dump(path)


def js(code, timeout=60, doc=True):
    """Run eda.* JavaScript in the connector (debug exec) and return its value."""
    d = run(['debug', 'exec', '--timeout', str(timeout), '--code', code], tries=1, doc=doc, timeout=timeout + 30)
    if not d.get('ok'):
        raise SystemExit('debug exec failed: ' + json.dumps(d.get('error'), ensure_ascii=False)[:400])
    return d['result']['value']


def window_id(project=None):
    """The connected EasyEDA window showing `project` (default: the config's); commands like `project export`
    need an explicit window, and several windows can be connected at once."""
    project = project or board().project
    p = subprocess.run(['easyeda', 'health'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    d = parse(p.stdout) or {}
    wins = ((d.get('found') or {}).get('raw') or {}).get('windows') or []
    if not wins:
        raise SystemExit('no EasyEDA window connected: start `easyeda daemon start`, open the project, check `easyeda health`')
    mine = [w for w in wins if (w.get('context') or {}).get('projectUuid') == project]
    if mine:
        return mine[0]['windowId']
    if len(wins) == 1:
        return wins[0]['windowId']
    raise SystemExit(f'{len(wins)} EasyEDA windows connected, none shows project {project[:12]}...: open it in one window')


def export_project(out):
    """Export the whole project to a new .epro2 (removes an existing file at `out` first). Returns the CLI reply."""
    B = board()
    if os.path.exists(out):
        os.remove(out)
    p = subprocess.run(['easyeda', 'project', 'export', '--window', window_id(B.project), '--project-uuid', B.project,
                        '--out', out], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    return parse(p.stdout) or {'ok': False, 'error': {'message': (p.stdout + p.stderr)[-300:]}}


def page_text_from_epro2(epro2, page):
    """One schematic page out of an .epro2 archive, in .esch2 record format (the archive's .epru is the documents
    concatenated, each starting with a DOCHEAD record). Works for folder (.eprj3) and database (.eprj2) projects."""
    import zipfile
    z = zipfile.ZipFile(epro2)
    name = next(n for n in z.namelist() if n.endswith('.epru'))
    lines = z.read(name).decode('utf-8', 'replace').split('\n')
    start = next((i for i, l in enumerate(lines) if '"docType":"SCH_PAGE"' in l and f'"uuid":"{page}"' in l), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('{"type":"DOCHEAD"}')), len(lines))
    return '\n'.join(lines[start:end]) + '\n'
