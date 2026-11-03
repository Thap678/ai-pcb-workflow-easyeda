"""Rebuild every schematic page from the c_*.json specs: place -> connect -> nc -> save -> verify_file.

usage: python build_all.py c_u2 c_u3 ...   (place is retried because the connector sometimes acks late)
"""
import json, subprocess, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, '..', '..', '电赛小车2026', 'sch')
FILES = {'c_u1': 'Schematic6/1_核心板插座', 'c_u2': 'Schematic6/2_电源与扩展', 'c_u3': 'Schematic6/3_人机交互',
         'c_u4': 'Schematic6/4_传感器与无线', 'c_u5': 'Schematic6/5_串口与I2C',
         'c_l1': 'Schematic5/1_电源输入', 'c_l2': 'Schematic5/2_电机域开关', 'c_l3': 'Schematic5/3_降压电源',
         'c_l4': 'Schematic5/4_电机驱动', 'c_l5': 'Schematic5/5_叠板与扩展', 'c_l6': 'Schematic5/6_对外接口'}

def py(*a):
    p = subprocess.run([sys.executable, os.path.join(HERE, a[0])] + list(a[1:]), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=HERE)
    return p.returncode, p.stdout + p.stderr

for name in sys.argv[1:]:
    spec = os.path.join(HERE, name + '.json'); s = json.load(open(spec, encoding='utf-8'))
    for attempt in range(4):
        rc, out = py('drv.py', spec, 'place')
        if rc == 0: break
    print(name, 'place', 'ok' if rc == 0 else 'FAILED\n' + out[-800:], flush=True)
    if rc: continue
    rc, out = py('drv.py', spec, 'connect')
    print(name, 'connect', out.strip().splitlines()[0][:120] if rc == 0 else 'FAILED\n' + out[-1500:], flush=True)
    rc, out = py('drv.py', spec, 'nc')
    if 'failed' in out: print(name, 'nc', out.strip()[-400:], flush=True)
    subprocess.run(['easyeda', '--project', s['project'], '--doc', s['page'], 'sch', 'save'], capture_output=True)
    rc, out = py('verify_file.py', s['project'], os.path.join(SCH, FILES[name] + '.esch2'), spec)
    print(name, 'verify', out.strip().splitlines()[-1] if out.strip() else rc, flush=True)
