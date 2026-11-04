import json, collections, re, sys
d = json.loads((lambda s: s[s.find('{'):])(open(sys.argv[1], encoding='utf-8').read()))
rows = d.get('violations', [])
print('passed', d.get('passed'), 'total', d.get('total'), d.get('counts'))
print(collections.Counter(r['objType'] for r in rows))
for r in rows[:int(sys.argv[2]) if len(sys.argv) > 2 else 25]:
    print(' ', r['message'][:150], '@(%.1f,%.1f)' % (r['x'] * 0.0254, r['y'] * 0.0254) if 'x' in r else '')
