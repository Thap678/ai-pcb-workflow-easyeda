"""Check a release folder made by export.py before anything is uploaded to the fab house.

usage: python check.py <release_dir>          -> prints the checklist and writes <release_dir>/release_check.md
Checks: Gerber zip has every layer a 2/4-layer board needs (copper, mask, silkscreen, outline, drill); the outline
measures the configured width x height; drill hit count and smallest drill; BOM and pick-and-place list the same
designators; BOM rows without an LCSC part number (cannot be assembled); placement centres inside the outline;
IPC-D-356A netlist and project backup present. Exit code 1 on any FAIL. WARN items need a human look.
"""
import csv, glob, io, os, re, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
d = sys.argv[1]
layers = B.fab.get('layers', 2)
rows = []                                              # (status, item, detail)
ok = lambda item, detail='': rows.append(('OK', item, detail))
warn = lambda item, detail: rows.append(('WARN', item, detail))
fail = lambda item, detail: rows.append(('FAIL', item, detail))
one = lambda pat: (sorted(glob.glob(os.path.join(d, pat))) or [None])[0]


def read_text(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-16', 'gb18030'):
        try: return raw.decode(enc)
        except UnicodeDecodeError: pass
    return raw.decode('latin-1')


def table(path):
    txt = read_text(path)
    delim = '\t' if txt.count('\t') > txt.count(',') else ','
    r = list(csv.reader(io.StringIO(txt), delimiter=delim))
    hi = next(i for i, row in enumerate(r) if any('esignator' in c for c in row))
    head = [c.strip() for c in r[hi]]
    return head, [dict(zip(head, [c.strip() for c in row])) for row in r[hi + 1:] if any(c.strip() for c in row)]


col = lambda head, *keys: next((h for h in head for k in keys if k.lower() in h.lower()), None)
split_refs = lambda s: [x for x in re.split(r'[,\s]+', s) if x]

# ---- Gerber ----
gz = one('*Gerber*.zip') or one('*.zip')
if not gz:
    fail('Gerber', 'no Gerber zip in the folder')
else:
    z = zipfile.ZipFile(gz); names = z.namelist(); low = [n.lower() for n in names]
    need = {'top copper': ('.gtl', 'toplayer'), 'bottom copper': ('.gbl', 'bottomlayer'),
            'top solder mask': ('.gts', 'topsoldermask'), 'bottom solder mask': ('.gbs', 'bottomsoldermask'),
            'top silkscreen': ('.gto', 'topsilk'), 'board outline': ('.gko', 'outline', '.gm1'),
            'drill': ('.drl', '.xln', 'drill')}
    if layers >= 4:
        need.update({'inner 1': ('.g1', 'inner1'), 'inner 2': ('.g2', 'inner2')})
    for item, keys in need.items():
        hit = [n for n, l in zip(names, low) if any(k in l for k in keys)]
        (ok(f'Gerber: {item}', ', '.join(hit)) if hit else fail(f'Gerber: {item}', 'missing in ' + os.path.basename(gz)))
    # outline size
    out = next((n for n, l in zip(names, low) if '.gko' in l or 'outline' in l), None)
    if out:
        g = z.read(out).decode('latin-1')
        fs = re.search(r'%FSLAX(\d)(\d)Y(\d)(\d)\*%', g); unit = 25.4 if '%MOIN*%' in g else 1.0
        dec = int(fs.group(2)) if fs else 4
        xs = [int(v) / 10 ** dec * unit for v in re.findall(r'X(-?\d+)', g)]
        ys = [int(v) / 10 ** dec * unit for v in re.findall(r'Y(-?\d+)', g)]
        if xs and ys:
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            msg = f'{w:.2f} x {h:.2f} mm (config {B.w} x {B.h}; line width included in the extents)'
            (ok('Outline size', msg) if abs(w - B.w) < 0.5 and abs(h - B.h) < 0.5 else fail('Outline size', msg))
    # drills
    drills = [n for n, l in zip(names, low) if any(k in l for k in ('.drl', '.xln'))]
    hits, sizes = 0, []
    for n in drills:
        t = z.read(n).decode('latin-1')
        sizes += [float(v) for v in re.findall(r'T\d+C([\d.]+)', t)]
        hits += len(re.findall(r'^X-?[\d.]+Y-?[\d.]+', t, re.M))
    if drills:
        msg = f'{hits} hits in {len(drills)} file(s), smallest tool {min(sizes) if sizes else "?"} (file units)'
        (ok('Drill', msg) if hits else fail('Drill', 'no drill hits'))

# ---- BOM / pick-and-place ----
bom, cpl = one('*BOM*.csv'), one('*PickAndPlace*.csv') or one('*Pick*.csv')
bom_refs = set()
if not bom: fail('BOM', 'no BOM csv')
else:
    head, items = table(bom)
    cr, cl = col(head, 'designator'), col(head, 'supplier part', 'lcsc', 'jlcpcb part')
    no_lcsc = []
    for it in items:
        refs = split_refs(it.get(cr, ''))
        bom_refs |= set(refs)
        if cl and not re.match(r'C\d+', it.get(cl, '')): no_lcsc.append(','.join(refs) or '?')
    ok('BOM', f'{len(items)} lines, {len(bom_refs)} designators')
    if not cl: warn('BOM LCSC column', f'no supplier-part column in {head}')
    elif no_lcsc: warn('BOM lines without an LCSC part (not assembled unless sourced)', '; '.join(no_lcsc))
if not cpl: fail('Pick-and-place', 'no pick-and-place csv')
else:
    head, items = table(cpl)
    cr = col(head, 'designator'); cx, cy = col(head, 'mid x', 'center-x', 'x'), col(head, 'mid y', 'center-y', 'y')
    num = lambda s: float(re.sub(r'[^\d.\-]', '', s) or 'nan')
    refs = {it[cr] for it in items}
    outside = [it[cr] for it in items if cx and cy and not (0 <= num(it[cx]) <= B.w and 0 <= num(it[cy]) <= B.h)]
    ok('Pick-and-place', f'{len(items)} placements')
    if outside: warn('Placements outside the outline (units mm?)', ', '.join(outside[:20]))
    if bom_refs:
        a, b = sorted(bom_refs - refs), sorted(refs - bom_refs)
        (ok('BOM vs pick-and-place') if not (a or b) else
         warn('BOM vs pick-and-place', f'only in BOM: {a[:20]} | only in placement: {b[:20]}'))

# ---- netlist / backup ----
ipc = one('*IPC356*') or one('*.ipc')
if ipc:
    nets = {l[3:17].strip() for l in read_text(ipc).splitlines() if l.startswith('317')}
    ok('IPC-D-356A netlist', f'{len(nets)} nets')
else:
    warn('IPC-D-356A netlist', 'missing (only needed for electrical test)')
bk = one('*.epro2')
(ok('Project backup', os.path.basename(bk)) if bk and os.path.getsize(bk) > 0 else fail('Project backup', 'no .epro2'))

w = max(len(r[1]) for r in rows)
lines = [f'{s:4}  {i:<{w}}  {x}' for s, i, x in rows]
print('\n'.join(lines))
md = ['# Release check', '', f'folder: `{d}`  board: {B.name} ({B.w} x {B.h} mm, {layers} layers)', '',
      '| status | item | detail |', '|---|---|---|'] + [f'| {s} | {i} | {x} |' for s, i, x in rows]
md += ['', 'Manual before ordering: open the Gerber zip in the fab house viewer, check silkscreen legibility and '
       'polarity marks, confirm board thickness / copper weight / surface finish / color in the order form.']
open(os.path.join(d, 'release_check.md'), 'w', encoding='utf-8').write('\n'.join(md) + '\n')
sys.exit(1 if any(s == 'FAIL' for s, _, _ in rows) else 0)
