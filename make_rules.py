"""Design rules for PCB6 (upper board: 2 layers, 1 oz, JLC standard process), written in the
exact structure of EasyEDA's exported Config_DesignRule_*.json so it imports directly.

usage: python make_rules.py <exported_template.json> <out.json>
"""
import json, sys, copy

src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding='utf-8'))
ROWS = ['Track', 'SMD Pad', 'TH Pad', 'SMD Test Point', 'TH Test Point', 'Via', 'Fill Region/Teardrop',
        'Copper/Plane Zone', 'Slot Region', 'Line', 'Text/Image', 'Board Outline', 'Hole']
COPPER = ROWS[:7]                      # discrete copper objects


def spacing(base, zone, slot, outline, outline_zone, hole, hole_zone):
    """Lower-triangular clearance table, same row/column order as the export."""
    def v(r, c):
        a, b = ROWS[r], ROWS[c]
        pair = {a, b}
        if 'Hole' in pair:          return hole_zone if 'Copper/Plane Zone' in pair else hole
        if 'Board Outline' in pair: return outline_zone if 'Copper/Plane Zone' in pair else outline
        if 'Slot Region' in pair:   return slot
        if 'Copper/Plane Zone' in pair: return zone
        if pair & {'Line', 'Text/Image'}: return 0.15
        return base
    # row lengths follow the export: 1..11, Board Outline has no outline-outline cell (11), Hole has 12
    widths = list(range(1, 12)) + [11, 12]
    return [[v(r, c) for c in range(widths[r])] for r in range(len(ROWS))]


s = d['Spacing']['Safe Spacing']
# 1 oz (default): 0.15 mm copper-to-copper (JLC min 0.1), pours 0.3 mm, keep copper 0.5 mm off the M3 cut-outs,
# 0.3 mm off the board edge (0.5 mm for pours), 0.25 mm from any drilled hole
s['copperThickness1oz']['tables']['1']['content'] = spacing(0.15, 0.25, 0.5, 0.3, 0.3, 0.25, 0.3)   # pours 0.25 mm, 0.3 mm to edge (ground-area pass)
for _t in ('copperThickness1oz', 'copperThickness2oz'):     # slot regions: copper 0.3 mm (pegs/lugs), pours stay 0.5
    for _c in range(7): s[_t]['tables']['1']['content'][8][_c] = 0.3
s['copperThickness2oz']['tables']['1']['content'] = spacing(0.2, 0.35, 0.5, 0.3, 0.5, 0.25, 0.35)
d['Spacing']['Other Spacing']['otherClearance']['form'].update({'device2device': 0, 'thru2Smd': 0, 'hole2Hole': 0.5})

t = d['Physics']['Track']
t['copperThickness1oz']['form']['data']['1'].update({'minValue': 0.15, 'defaultValue': 0.25, 'maxValue': 3.0})
t['copperThickness2oz']['form']['data']['1'].update({'minValue': 0.2, 'defaultValue': 0.3, 'maxValue': 3.0})
# extra track rule sets, picked per net in 网络规则 → 导线 (1 oz outer layer, IPC-2221, ~10 °C rise):
# 5V trunk up to 3 A (stack 5V budget) -> 1.2 mm; 3V3 rails <= 0.5 A -> 0.5 mm; GND tracks 0.3 mm + pours both layers
for name, mn, df in (('PWR_5V', 0.5, 1.2), ('PWR_3V3', 0.3, 0.5), ('GND', 0.15, 0.3)):
    r = copy.deepcopy(t['copperThickness1oz'])
    r.update({'editName': name, 'isSetDefault': False})
    r['form']['data']['1'].update({'minValue': mn, 'defaultValue': df, 'maxValue': 3.0})
    t[name] = r
d['Physics']['Via Size']['viaSize']['form'].update({
    'viaOuterdiameterMin': 0.5, 'viaOuterdiameterMax': 10.0, 'viaOuterdiameterDefault': 0.6,
    'viaInnerdiameterMin': 0.3, 'viaInnerdiameterMax': 6.3, 'viaInnerdiameterDefault': 0.3})
dp = d['Physics']['Differential Pair']['differentialPair']['form']
dp['strokeWidthTables']['data']['1'].update({'minValue': 0.15, 'defaultValue': 0.25, 'maxValue': 3.0})
dp['diffPairSpacingTables']['data']['1'].update({'minValue': 0.15, 'defaultValue': 0.15})

for key in ('singleLayerPadModel', 'multiLayerPadModel'):
    d['Plane']['Copper Zone']['copperRegion']['form'][key]['data']['1'].update({'lineClearance': 0.25, 'lineWidth': 0.3})
d['Plane']['Plane Zone']['innerPlane']['form']['data']['1'].update({'lineClearance': 0.3, 'lineWidth': 0.3})

json.dump(d, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

# structural check: identical key tree and table shapes to the template
t0 = json.load(open(src, encoding='utf-8')); t1 = json.load(open(out, encoding='utf-8'))
def shape(x):
    if isinstance(x, dict): return {k: shape(v) for k, v in x.items()}
    if isinstance(x, list): return [shape(v) for v in x]
    return type(x).__name__ if not isinstance(x, (int, float)) or isinstance(x, bool) else 'num'
print('structure identical:', shape(t0) == shape(t1))
for name in ('copperThickness1oz', 'copperThickness2oz'):
    c = t1['Spacing']['Safe Spacing'][name]['tables']['1']['content']
    print(name, 'row lengths', [len(r) for r in c])
