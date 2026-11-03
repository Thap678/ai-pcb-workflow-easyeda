"""Assign the track rule sets (PWR_5V / PWR_3V3 / GND, created by make_rules.py) to the power nets of a PCB.

The CLI's drc-rules-set does not take net -> rule assignments, so this goes through eda.pcb_Drc.overwriteNetRules.
usage: python set_netrules.py <project> <pcb uuid>
"""
import json, subprocess, sys

P, DOC = sys.argv[1:3]
MAP = {'5V_MAIN': 'PWR_5V', '5V_LOGIC': 'PWR_5V', '3V3_MAIN': 'PWR_3V3', '3V3_RF': 'PWR_3V3', 'VCC_GRAY': 'PWR_3V3',
       'GND': 'GND'}
code = (f"const m={json.dumps(MAP)}; const r = await eda.pcb_Drc.getNetRules(); "
        "for (const x of r) if (m[x.name]) x.Track = m[x.name]; const ok = await eda.pcb_Drc.overwriteNetRules(r); "
        "const b = await eda.pcb_Drc.getNetRules(); return {ok, set: b.filter(x => m[x.name]).map(x => [x.name, x.Track])}")
p = subprocess.run(['easyeda', '--project', P, '--doc', DOC, 'debug', 'exec', '--timeout', '60', '--code', code],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
v = json.loads(p.stdout[p.stdout.find('{'):])['result']['value']
print('ok', v['ok'], v['set'])
subprocess.run(['easyeda', '--project', P, '--doc', DOC, 'pcb', 'save'], capture_output=True)
