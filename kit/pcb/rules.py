"""Design rules from the config: clearance tables, track widths, via size, pour clearance, plus per-net track
rule sets ([[track_classes]]) assigned to their nets. Uses the board's live rule set as the template, so the
written JSON always has the exact structure the running EasyEDA expects.

usage: python rules.py [--dry-run]       writes rules_<board>.json next to the config; without --dry-run also
                                          applies it (drc-rules-set) and assigns the net rules, then saves
"""
import copy, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as K

B = K.board()
R = dict(copper_mm=0.15, pour_mm=0.25, slot_copper_mm=0.3, slot_pour_mm=0.5, edge_mm=0.3, edge_pour_mm=0.3,
         hole_mm=0.25, hole_pour_mm=0.3, track_default_mm=0.25, track_min_mm=0.15, via_d_mm=0.6, via_hole_mm=0.3,
         hole_to_hole_mm=0.5)
R.update(B.rules)

ROWS = ['Track', 'SMD Pad', 'TH Pad', 'SMD Test Point', 'TH Test Point', 'Via', 'Fill Region/Teardrop',
        'Copper/Plane Zone', 'Slot Region', 'Line', 'Text/Image', 'Board Outline', 'Hole']


def spacing(base, zone, slot, slot_zone, outline, outline_zone, hole, hole_zone):
    """Lower-triangular clearance table in EasyEDA's row/column order (Board Outline row has 11 cells, Hole 12)."""
    def v(r, c):
        pair = {ROWS[r], ROWS[c]}
        z = 'Copper/Plane Zone' in pair
        if 'Hole' in pair: return hole_zone if z else hole
        if 'Board Outline' in pair: return outline_zone if z else outline
        if 'Slot Region' in pair: return slot_zone if z else slot
        if z: return zone
        if pair & {'Line', 'Text/Image'}: return 0.15
        return base
    widths = list(range(1, 12)) + [11, 12]
    return [[v(r, c) for c in range(widths[r])] for r in range(len(ROWS))]


live = K.run(['pcb', 'drc-rules'])
if not live.get('ok'):
    raise SystemExit('cannot read the live rules: ' + json.dumps(live.get('error'), ensure_ascii=False))
rules = live['result']['rules']
d = rules['config']
s = d['Spacing']['Safe Spacing']
s['copperThickness1oz']['tables']['1']['content'] = spacing(
    R['copper_mm'], R['pour_mm'], R['slot_copper_mm'], R['slot_pour_mm'], R['edge_mm'], R['edge_pour_mm'],
    R['hole_mm'], R['hole_pour_mm'])
s['copperThickness2oz']['tables']['1']['content'] = spacing(       # 2 oz: one step stricter everywhere
    max(0.2, R['copper_mm']), R['pour_mm'] + 0.1, R['slot_copper_mm'], R['slot_pour_mm'], R['edge_mm'],
    max(0.5, R['edge_pour_mm']), R['hole_mm'], R['hole_pour_mm'] + 0.05)
d['Spacing']['Other Spacing']['otherClearance']['form'].update(
    {'device2device': 0, 'thru2Smd': 0, 'hole2Hole': R['hole_to_hole_mm']})

t = d['Physics']['Track']
t['copperThickness1oz']['form']['data']['1'].update(
    {'minValue': R['track_min_mm'], 'defaultValue': R['track_default_mm'], 'maxValue': 3.0})
t['copperThickness2oz']['form']['data']['1'].update(
    {'minValue': max(0.2, R['track_min_mm']), 'defaultValue': max(0.3, R['track_default_mm']), 'maxValue': 3.0})
base = {k: v for k, v in t.items() if k.startswith('copperThickness')}
for k in [k for k in t if not k.startswith('copperThickness')]:
    del t[k]                                            # rebuild the class rule sets exactly from the config
for tc in B.track_classes:
    r = copy.deepcopy(base['copperThickness1oz'])
    r.update({'editName': tc['name'], 'isSetDefault': False})
    r['form']['data']['1'].update({'minValue': tc['min_mm'], 'defaultValue': tc['default_mm'], 'maxValue': 3.0})
    t[tc['name']] = r
d['Physics']['Via Size']['viaSize']['form'].update({
    'viaOuterdiameterMin': min(0.5, R['via_d_mm']), 'viaOuterdiameterMax': 10.0, 'viaOuterdiameterDefault': R['via_d_mm'],
    'viaInnerdiameterMin': min(0.3, R['via_hole_mm']), 'viaInnerdiameterMax': 6.3, 'viaInnerdiameterDefault': R['via_hole_mm']})
for key in ('singleLayerPadModel', 'multiLayerPadModel'):
    d['Plane']['Copper Zone']['copperRegion']['form'][key]['data']['1'].update({'lineClearance': R['pour_mm'], 'lineWidth': 0.3})
d['Plane']['Plane Zone']['innerPlane']['form']['data']['1'].update({'lineClearance': max(0.3, R['pour_mm']), 'lineWidth': 0.3})

out = B.path(f'rules_{B.name}.json')
json.dump(rules, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('rules ->', out, '| track classes', [(c['name'], c['default_mm']) for c in B.track_classes])
if '--dry-run' in sys.argv:
    raise SystemExit

print('drc-rules-set', K.run(['pcb', 'drc-rules-set', '--from', out]).get('ok'))
# net -> track rule set: drc-rules-set does not carry these, eda.pcb_Drc.overwriteNetRules does
m = {n: tc['name'] for tc in B.track_classes for n in tc.get('nets', [])}
if m:
    v = K.js(f"const m={json.dumps(m)}; const r = await eda.pcb_Drc.getNetRules(); "
             "for (const x of r) if (m[x.name]) x.Track = m[x.name]; const ok = await eda.pcb_Drc.overwriteNetRules(r); "
             "const b = await eda.pcb_Drc.getNetRules(); return {ok, set: b.filter(x => m[x.name]).map(x => [x.name, x.Track])}")
    got = dict(v['set'])
    print('net rules', v['ok'], '| missing on board:', sorted(set(m) - set(got)),
          '| wrong:', {n: got[n] for n in got if got[n] != m[n]})
print('save', K.run(['pcb', 'save']).get('ok'))
