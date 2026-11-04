# -*- coding: utf-8 -*-
import json, subprocess
P = '5be68595f2b413c75a7f2937434f239f53e66b44c02f15a2e2e3494fe05d691c'
PAGES = [
 ('42fa4b30c4583105', '上板-1 核心板插座', '天空星 STM32F407 插座(引脚映射 v2.0), JY901S(I2C)'),
 ('aec7fc9eab802692', '上板-2 电源与扩展', 'J603叠板(奇偶对调), SS54防倒灌, LDOx2, EEPROM, I2C上拉'),
 ('c15a4e9053cdb09e', '上板-3 人机交互', '五向键, EC11, 蜂鸣器, RGB, OLED 直连MCU'),
 ('0ed970111fce6268', '上板-4 灰度与无线', '6路灰度ADC(3.3/5V可选), E01 无线'),
 ('7629f430a37f2138', '上板-5 串口与I2C', '5路串口XH-4P(USART1/3/6, UART4/5), 3路I2C Qwiic'),
]
def run(a):
    p = subprocess.run(['easyeda'] + a, capture_output=True, encoding='utf-8', errors='replace')
    t = p.stdout; i = t.find('{')
    try: return json.loads(t[i:]) if i >= 0 else {}
    except Exception: return {}
for d, name, desc in PAGES:
    data = json.dumps({'Name': {'value': name, 'showTitle': True, 'showValue': True},
                       'Description': {'value': desc, 'showTitle': True, 'showValue': True}}, ensure_ascii=False)
    run(['--project', P, '--doc', d, 'sch', 'titleblock', '--data', data])
    got = (run(['--project', P, 'sch', 'titleblock-get', '--page', d]).get('result', {}).get('titleBlockData') or {}).get('Name', {}).get('value')
    sv = run(['--project', P, '--doc', d, 'sch', 'save']).get('result', {}).get('saved')
    print(d, 'name ok' if got == name else f'name MISMATCH {got!r}', 'saved' if sv else 'NOT saved')
