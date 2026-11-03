"""Prepare the EasyEDA DSN export for Freerouting (units: mil).

- router clearance 0.2 mm (rule 0.15 mm; 0.25 mm left 28 nets unrouted), class members unquoted so the per-net power widths apply
- M3 hole pads grown to r 1.9 mm and other netless holes/slots grown 0.3 mm (keepout areas crash freerouting-cli)
- routing boundary inset 0.15 mm so copper ends >= 0.35 mm (0.15 boundary + 0.2 clearance) from the board edge
"""
import re, sys
src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()
MIL = 1 / 0.0254
W, H = 80 * MIL, 87 * MIL
CLR = round(0.2 * MIL, 2)

s = re.sub(r'\(clearance [\d.]+\)', f'(clearance {CLR})', s)
# EasyEDA writes class members as 'NET' (single quotes); Freerouting doesn't match them, so the per-net widths
# (5V 1.2 mm, 3V3/GND 0.5 mm from the net rules) were ignored. Unquote the members.
s, nq = re.subn(r"\(class (\S+) '([^']+)'", r'(class \1 \2', s)
assert nq > 0, 'no quoted class members found'
s = re.sub(r'\(rule\(clear [\d.]+', f'(rule(clear {CLR}', s)

i, ch = 0.15 * MIL, 3.0 * MIL          # inset, corner chamfer
pts = [(i + ch, i), (W - i - ch, i), (W - i, i + ch), (W - i, H - i - ch), (W - i - ch, H - i), (i + ch, H - i),
       (i, H - i - ch), (i, i + ch), (i + ch, i)]
boundary = '(boundary(path signal 0 ' + ' '.join(f'{x:.2f} {y:.2f}' for x, y in pts) + ' ))'
s, n = re.subn(r'\(boundary\(path signal 0 [^)]*\)\s*\)', boundary, s, count=1)
assert n == 1, 'boundary not found'

# EasyEDA exports each M3 cut-out as a netless pad of the bare hole radius (1.6 mm). Grow those pads to r 1.9 mm:
# + router clearance 0.2 = copper >= 0.5 mm from the hole edge. (keepout areas crash the native freerouting-cli 2.5.0)
holes = [(4 * MIL, 4 * MIL), (76 * MIL, 4 * MIL), (4 * MIL, 83 * MIL), (76 * MIL, 83 * MIL)]
hole_ps = {ps for ps, x, y in re.findall(r'\(pin (\S+) \S+ ([\d.-]+) ([\d.-]+)\)', s)
           if any(abs(float(x) - hx) < 1 and abs(float(y) - hy) < 1 for hx, hy in holes)}
assert len(hole_ps) >= 1, 'hole pads not found'
k = 1.9 / 1.6
def grow(m):
    nums = m.group(2).split()
    return m.group(1) + nums[0] + ' ' + ' '.join(f'{float(v) * k:.2f}' for v in nums[1:]) + ')'
for ps in hole_ps:
    a = s.index(f'(padstack {ps}\n'); b = s.index('\n    )', a)    # block ends at its own closing paren
    s = s[:a] + re.sub(r'(\(polygon \S+ )([^)]*)\)', grow, s[a:b]) + s[b:]
# Other netless plated/non-plated holes (5-way key locating pegs, EC11 mounting lugs) count as slot regions in the
# EasyEDA rules (0.5 mm). Grow them 0.3 mm per side: 0.3 + router clearance 0.2 >= 0.5.
net_pins = set(re.findall(r'u1-(\S+?)[\s)]', s[s.index('(network'):]))
G = 0.1 * MIL
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
print('clearance', CLR, 'mil | hole pads grown', len(hole_ps), '| boundary inset 0.15 mm')
