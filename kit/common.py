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
        if i + 1 >= len(sys.argv):
            raise SystemExit(f'{name} needs a value')
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
        for w in check_keys(_cfg):
            print(f'config warning ({os.path.basename(_CFG_PATH)}): {w}', file=sys.stderr)
        _cfg['_dir'] = os.path.dirname(os.path.abspath(_CFG_PATH))
    return _cfg


# Every key the kit reads (board.example.toml documents them). A key not listed is a typo or a stale option and
# would otherwise be ignored in silence, the default taking its place.
KEYS = {
    '': {'project', 'project_dir', 'default_board', 'backup', 'boards'},
    'backup': {'dir'},
    'board': {'pcb', 'schematic', 'width_mm', 'height_mm', 'corner_mm', 'outline_mm', 'mount_holes_mm',
              'mount_hole_d_mm', 'pages', 'rules', 'track_classes', 'route', 'ground', 'fab', 'planes'},
    'rules': {'copper_mm', 'pour_mm', 'slot_copper_mm', 'slot_pour_mm', 'edge_mm', 'edge_pour_mm', 'hole_mm',
              'hole_pour_mm', 'track_default_mm', 'track_min_mm', 'via_d_mm', 'via_hole_mm', 'hole_to_hole_mm'},
    'track_classes': {'name', 'min_mm', 'default_mm', 'nets'},
    'route': {'freerouting', 'router_clearance_mm', 'boundary_inset_mm', 'edge_clearance_um', 'passes', 'workers',
              'timeout_s', 'variants'},
    'variants': {'id', 'via', 'top', 'bot', 'top_dir', 'bot_dir', 'plane_via', 'ripup', 'fanout', 'passes', 'note'},
    'ground': {'net', 'pour_inset_mm', 'stitch_grid_mm'},
    'fab': {'name', 'layers'},
    'planes': {'plane_layer', 'ground_layer', 'gap_mm', 'keepout_refs', 'wide_nets', 'regions', 'pours'},
    'plane_regions': {'net', 'boxes', 'cut'},
    'plane_pours': {'name', 'net', 'pads'},
}
REQUIRED_BOARD = ('pcb', 'width_mm', 'height_mm')


def check_keys(c):
    """Problems in a parsed config: unknown keys (typos), missing required ones, wrong table types."""
    out = []
    def unknown(d, kind, where):
        if not isinstance(d, dict):
            out.append(f'{where or "top level"}: expected a table'); return
        out.extend(f'unknown key {where + "." if where else ""}{k}' for k in d if k not in KEYS[kind] and k != '_dir')
    unknown(c, '', '')
    if 'project' not in c: out.append('missing key project')
    unknown(c.get('backup', {}), 'backup', 'backup')
    for name, b in (c.get('boards') or {}).items():
        w = f'boards.{name}'
        unknown(b, 'board', w)
        if not isinstance(b, dict): continue
        out.extend(f'missing key {w}.{k}' for k in REQUIRED_BOARD if k not in b)
        for kind in ('rules', 'route', 'ground', 'fab'):
            unknown(b.get(kind, {}), kind, f'{w}.{kind}')
        for i, tc in enumerate(b.get('track_classes', [])):
            unknown(tc, 'track_classes', f'{w}.track_classes[{i}]')
        for i, v in enumerate((b.get('route') or {}).get('variants', [])):
            unknown(v, 'variants', f'{w}.route.variants[{i}]')
        if 'planes' in b:
            unknown(b['planes'], 'planes', f'{w}.planes')
            for kind, key in (('plane_regions', 'regions'), ('plane_pours', 'pours')):
                for i, x in enumerate((b.get('planes') or {}).get(key, [])):
                    unknown(x, kind, f'{w}.planes.{key}[{i}]')
    if not c.get('boards'): out.append('no [boards.<name>] table')
    return out


class Board:
    """One board of the config, with defaults for everything optional (units: mm unless the name says mil)."""

    def __init__(self, name, c, top):
        g = lambda k, d=None: c.get(k, d)
        self.name, self.project, self.cfg_dir = name, top['project'], top['_dir']
        self.pcb, self.schematic = g('pcb'), g('schematic')
        self.w, self.h, self.corner = g('width_mm'), g('height_mm'), g('corner_mm', 3.0)
        self.outline = [tuple(p) for p in g('outline_mm', [])]   # optional polygon; else the rounded w x h rectangle
        self.holes = [tuple(h) for h in g('mount_holes_mm', [])]
        self.hole_d = g('mount_hole_d_mm', 3.2)
        self.pages = g('pages', {})                       # spec file -> {page uuid, saved .esch2 path}
        self.rules = g('rules', {})
        self.track_classes = g('track_classes', [])
        self.route = g('route', {})
        self.ground = g('ground', {})
        self.fab = g('fab', {})
        self.planes = g('planes', {})                     # split power plane layout (4-layer boards), docs/05 s.8

    def path(self, p):
        """Resolve a path from the config relative to the config file."""
        return p if os.path.isabs(p) else os.path.join(self.cfg_dir, p)

    def class_min_mil(self):
        """net -> track rule minimum (mil), from [[track_classes]]."""
        return {n: tc['min_mm'] * MIL for tc in self.track_classes for n in tc.get('nets', [])}

    @property
    def gnd(self):
        return self.ground.get('net', 'GND')

    def freerouting(self):
        return freerouting_exe(self)


def board(name=None):
    c = cfg()
    name = name or _BOARD or c.get('default_board') or next(iter(c['boards']))
    if name not in c['boards']:
        raise SystemExit(f'board {name!r} not in {_CFG_PATH}: {list(c["boards"])}')
    return Board(name, c['boards'][name], c)


def try_board():
    """The configured board, or None when there is no config (offline scripts then use their defaults)."""
    return board() if os.path.exists(_CFG_PATH) else None


FR_DEFAULT = r'C:\fr_extract\freerouting\freerouting.exe'


def freerouting_exe(b=None):
    """Freerouting executable: [route] freerouting in the config, else FR_EXE, else the docs/00 default path."""
    return (b.route.get('freerouting') if b else None) or os.environ.get('FR_EXE') or FR_DEFAULT


def rect_outline(w, h, corner, inset=0.0):
    """Closed w x h rectangle (mm) inset by `inset`, corners chamfered at `corner` - the fallback board shape."""
    i, ch = inset, corner
    return [(i + ch, i), (w - i - ch, i), (w - i, i + ch), (w - i, h - i - ch), (w - i - ch, h - i), (i + ch, h - i),
            (i, h - i - ch), (i, i + ch)]


def outline_mm(dump=None, b=None):
    """Board outline as [(x, y)] mm (centre line, arcs sampled) from a dump dict, or a fresh outline-only
    `pcb dump`. Falls back to the configured shape (outline_mm, else the w x h rectangle) and says so: EasyEDA
    returns no outline when the PCB is not the foreground document."""
    b = b or board()
    if dump is None:
        p = subprocess.run(['easyeda', '--project', b.project, '--doc', b.pcb, 'pcb', 'dump', '--no-silk', '--no-rules',
                            '--no-layers'], capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=600)
        dump = parse(p.stdout) or {}
        dump = dump.get('result', dump)
    pts = ((dump or {}).get('outline') or {}).get('points') or []
    if len(pts) >= 3:
        return [(x * MM, y * MM) for x, y in pts]
    print('outline: not readable from EasyEDA (is the PCB the foreground document?) - using the configured shape',
          file=sys.stderr)
    return list(b.outline) or rect_outline(b.w, b.h, b.corner)


def inset_polygon(pts, d, tol=0.01):
    """`pts` (mm) shrunk by d mm (round joins keep arcs round), simplified to `tol`; [(x, y)] of the largest piece."""
    from shapely.geometry import Polygon
    g = Polygon(pts).buffer(0).buffer(-d, join_style=1).simplify(tol)
    if g.geom_type == 'MultiPolygon':
        g = max(g.geoms, key=lambda p: p.area)
    if g.is_empty:
        raise SystemExit(f'board outline vanishes when inset by {d} mm')
    return list(g.exterior.coords)[:-1]


def parse(stdout):
    """First JSON object in a CLI stdout (warnings may precede it)."""
    i = stdout.find('{')
    if i < 0:
        return None
    try:
        return json.loads(stdout[i:])
    except json.JSONDecodeError:
        return None


# Commands that may be resent when a reply is lost: reads, and writes that set an absolute state (save, move to a
# position, lock, delete by id, replace the rules / outline), where a second copy changes nothing. Everything else
# - track, via, slot, pour, import-autoroute, debug exec, any command not listed - creates something and is sent once:
# a reply that never came may hide a write that landed, so the caller reads back before sending it again.
# `pcb drc` is a read but is sent once too: each retry piles another recompute onto the EasyEDA webview.
RETRY_SAFE = {
    'pcb list', 'pcb dump', 'pcb drc-rules', 'pcb pour-list', 'pcb track-list', 'pcb via-list',
    'pcb outline-get', 'pcb nets', 'pcb fill list', 'sch list', 'lib by-lcsc', 'lib search', 'doc ls',
    'pcb save', 'sch save', 'pcb pour-rebuild', 'pcb modify', 'pcb lock', 'pcb rip-up', 'pcb pour-delete',
    'pcb track-delete', 'pcb via-delete', 'sch prim-delete', 'pcb drc-rules-set', 'pcb outline-round',
    'pcb outline-set', 'pcb layout-lint', 'doc reload',
}


def retry_safe(args):
    """True when `args` (the command after --project/--doc) is in RETRY_SAFE; `pcb fill` is keyed by its subcommand."""
    return ' '.join(args[:3] if args[:2] == ['pcb', 'fill'] else args[:2]) in RETRY_SAFE


def run(args, tries=None, doc=True, timeout=600, board_=None):
    """easyeda --project <P> [--doc <PCB>] <args>; reads are retried on empty/garbled replies, creates are not.

    doc=True pins the board's PCB, doc=False adds nothing, a string pins that document (page / PCB uuid).
    tries=None: 4 for commands in RETRY_SAFE, 1 for everything else. The connector sometimes reports a failure
    for a write that landed (and vice versa): callers that write should read back instead of trusting `ok`.
    """
    if tries is None:
        tries = 4 if retry_safe(args) else 1
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


def js(code, timeout=60, doc=True, tries=1):
    """Run eda.* JavaScript in the connector (debug exec) and return its value. Sent once; pass tries > 1 only for
    code that just reads."""
    d = run(['debug', 'exec', '--timeout', str(timeout), '--code', code], tries=tries, doc=doc, timeout=timeout + 30)
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
