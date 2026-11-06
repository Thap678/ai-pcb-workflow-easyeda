"""Prepare the EasyEDA DSN export for Freerouting (units: mil).

usage: python prep_dsn.py <exported.dsn> <out.dsn>
- router clearance [route] router_clearance_mm (0.2: one step above the 0.15 rule, so EDA's DRC passes after the
  SES import), class members unquoted so the per-net track classes apply
- mounting-hole pads grown so copper stays >= rules.slot_pour_mm from the hole edge; other netless holes (locating
  pegs, mounting lugs) grown so copper stays >= rules.slot_copper_mm (keepout areas crash freerouting-cli)
- routing boundary = the exported board outline (any shape) inset by [route] boundary_inset_mm (0.15), so copper
  ends inset + clearance from the board edge
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K
B = K.board()
src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()
# EasyEDA exports the whole board as ONE image (named u1 so far) placed once at 0 0, every pad a pin of it with a
# unique name; the hole-pad lookup (pin coordinates = board coordinates) and the netted-pin test rely on that.
images = re.findall(r'\(image (\S+)', s)
places = re.findall(r'\(place (\S+) ([\d.-]+) ([\d.-]+) (\S+) ([\d.-]+)', s)
if len(images) != 1 or len(places) != 1 or [float(v) for v in places[0][1:3]] != [0, 0] or float(places[0][4]) != 0:
    raise SystemExit(f'unexpected DSN layout: images {images[:5]}, placements {places[:5]} - prep_dsn.py expects one '
                     'board image placed at 0 0 (the EasyEDA export); check the EasyEDA / easyeda-agent version')
comp = places[0][0]
MIL = K.MIL
CLR_MM = B.route.get('router_clearance_mm', 0.2)
CLR = round(CLR_MM * MIL, 2)

s = re.sub(r'\(clearance [\d.]+\)', f'(clearance {CLR})', s)
# EasyEDA writes class members as 'NET' (single quotes); Freerouting doesn't match them, so the per-net widths
# (5V 1.2 mm, 3V3/GND 0.5 mm from the net rules) were ignored. Unquote the members.
s, nq = re.subn(r"\(class (\S+) '([^']+)'", r'(class \1 \2', s)
assert nq > 0, 'no quoted class members found'
s = re.sub(r'\(rule\(clear [\d.]+', f'(rule(clear {CLR}', s)

# Exported from a routed board, the DSN still carries the old routing: tracks + vias in (wiring ...) as `protect`,
# and every via ALSO as a pin of the board image, bound to its net. Freerouting then routes around (and through)
# the old vias. apply_ses.py rips all of that up before importing, so drop it here.
i = s.find('(wiring')
if i >= 0:
    depth, j = 0, i
    while True:
        depth += {'(': 1, ')': -1}.get(s[j], 0)
        if depth == 0: break
        j += 1
    s = s[:i] + s[j + 1:]
via_d = {re.findall(r'circle \S+ ([\d.]+)', blk)[0] for blk in re.findall(r'\(padstack via\d+\s*\n(.*?)\n    \)', s, re.S)}
pad_d = {}
for ps, blk in re.findall(r'\(padstack (\S+)\s*\n(.*?)\n    \)', s, re.S):
    shapes = re.findall(r'\(shape\((\w+) (\S+) ([\d.]+)', blk)
    if {sh[1] for sh in shapes} == {'TopLayer', 'BottomLayer'} and all(sh[0] == 'circle' for sh in shapes) \
            and len({sh[2] for sh in shapes}) == 1:
        pad_d[ps] = shapes[0][2]
via_pins = {name for ps, name in re.findall(r'\(pin (\S+) (\S+) [\d.-]+ [\d.-]+\)', s) if pad_d.get(ps) in via_d}
if via_pins:
    s = re.sub(r'\n\s*\(pin (\S+) (\S+) [\d.-]+ [\d.-]+\)', lambda m: '' if m.group(2) in via_pins else m.group(0), s)
    s = re.sub(rf'\s{re.escape(comp)}-(\S+?)(?=[\s)])', lambda m: '' if m.group(1) in via_pins else m.group(0), s)
# A routed board exports every via size it holds as a via type, e.g. (via via0 via1 via2) for 17.7 / 17.8 / 24 mil.
# Offered several, Freerouting placed vias that broke clearance (366 via-to-copper shortfalls on a 0.127 mm rule,
# against 0 when only one type was listed). Keep the one closest to the rule via size.
vm = re.search(r'\(via ((?:via\d+\s*)+)\)', s)
if vm and len(vm.group(1).split()) > 1:
    sizes = {n: float(re.search(r'\(padstack ' + n + r'\s*\n\s*\(shape\(circle \S+ ([\d.]+)', s).group(1))
             for n in vm.group(1).split()}
    keep = min(sizes, key=lambda n: abs(sizes[n] - B.rules.get('via_d_mm', 0.45) * MIL))
    s = s[:vm.start()] + f'(via {keep}\n    )' + s[vm.end():]
    print('via types', sizes, '-> keep', keep)
print('old routing dropped:', 'wiring section' if i >= 0 else 'no wiring', f'| {len(via_pins)} vias exported as pins')

# the export's boundary is the board outline (mil, arcs as segments): shrink that shape, whatever it is
m = re.search(r'\(boundary\(path signal 0 ([^)]*)\)\s*\)', s)
assert m, 'boundary not found'
v = [float(t) for t in m.group(1).split()]
inset = B.route.get('boundary_inset_mm', 0.15)
pts = K.inset_polygon([(x * K.MM, y * K.MM) for x, y in zip(v[0::2], v[1::2])], inset)
pts = [(x * MIL, y * MIL) for x, y in pts + pts[:1]]
boundary = '(boundary(path signal 0 ' + ' '.join(f'{x:.2f} {y:.2f}' for x, y in pts) + ' ))'
s = s[:m.start()] + boundary + s[m.end():]

# EasyEDA exports each mounting cut-out as a netless pad of the bare hole radius. Grow it so that
# grown radius + router clearance = hole radius + slot_pour_mm. (keepout areas crash the native freerouting-cli)
holes = [(x * MIL, y * MIL) for x, y in B.holes]
hole_ps = {ps for ps, x, y in re.findall(r'\(pin (\S+) \S+ ([\d.-]+) ([\d.-]+)\)', s)
           if any(abs(float(x) - hx) < 1 and abs(float(y) - hy) < 1 for hx, hy in holes)}
assert len(hole_ps) >= 1 or not holes, 'hole pads not found'
R0 = B.hole_d / 2
k = (R0 + B.rules.get('slot_pour_mm', 0.5) - CLR_MM) / R0
def grow(m):
    nums = m.group(2).split()
    return m.group(1) + nums[0] + ' ' + ' '.join(f'{float(v) * k:.2f}' for v in nums[1:]) + ')'
for ps in hole_ps:
    a = s.index(f'(padstack {ps}\n'); b = s.index('\n    )', a)    # block ends at its own closing paren
    s = s[:a] + re.sub(r'(\(polygon \S+ )([^)]*)\)', grow, s[a:b]) + s[b:]
# Other netless plated/non-plated holes (switch locating pegs, encoder mounting lugs) count as slot regions in the
# EasyEDA rules (slot_copper_mm). Grow them so growth + router clearance >= slot_copper_mm.
net_pins = set(re.findall(rf'\b{re.escape(comp)}-(\S+?)[\s)]', s[s.index('(network'):]))
G = max(0.0, B.rules.get('slot_copper_mm', 0.3) - CLR_MM) * MIL
grown = set()
all_pins = re.findall(r'\(pin (\S+) (\S+) ([\d.-]+) ([\d.-]+)\)', s)
# a padstack is shared by every pin of the same shape: NC header pins share theirs with connected pins, so only
# padstacks used exclusively by netless pins are pegs/lugs (ids change per project, so test usage, not names)
netted_ps = {ps for ps, name, x, y in all_pins if name in net_pins}
for ps, name, x, y in all_pins:
    if name in net_pins or ps in hole_ps or ps in grown or ps in netted_ps: continue
    a = s.index(f'(padstack {ps}\n'); b = s.index('\n    )', a); blk = s[a:b]
    if 'BottomLayer' not in blk or 'TopLayer' not in blk: continue          # SMD tabs stay as they are
    blk = re.sub(r'\(circle (\S+) ([\d.]+)', lambda m: f'(circle {m.group(1)} {float(m.group(2)) + 2 * G:.2f}', blk)
    def grow_poly(m):
        v = [float(t) for t in m.group(2).split()[1:]]; xs, ys = v[0::2], v[1::2]
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        sx = (max(xs) - min(xs) + 2 * G) / (max(xs) - min(xs)); sy = (max(ys) - min(ys) + 2 * G) / (max(ys) - min(ys))
        return m.group(1) + m.group(2).split()[0] + ' ' + ' '.join(
            f'{cx + (px - cx) * sx:.2f} {cy + (py - cy) * sy:.2f}' for px, py in zip(xs, ys)) + ')'
    blk = re.sub(r'(\(polygon \S+ )([^)]*)\)', grow_poly, blk)
    s = s[:a] + blk + s[b:]; grown.add(ps)
print('slot/peg padstacks grown:', sorted(grown))
open(out, 'w', encoding='utf-8').write(s)
print('clearance', CLR, 'mil | hole pads grown', len(hole_ps), f'(x{k:.3f}) | boundary inset', B.route.get('boundary_inset_mm', 0.15), 'mm')
