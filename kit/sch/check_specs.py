"""Offline check of one board's page specs before touching EasyEDA.

usage: python check_specs.py <spec.json> [<spec.json> ...]      (all pages of ONE board)
Reports: net count, single-pin nets (almost always a typo or a missing part), duplicate designators, parts without
an orderable source (lcsc, or lib+uuid), nets used as power/ground on one page but declared differently on another,
and pins listed both as a net and as no-connect. Exit code 1 when anything blocking is found.
"""
import collections, json, sys

pins = collections.defaultdict(list)
refs = collections.Counter()
kind = collections.defaultdict(set)
problems = []
for path in sys.argv[1:]:
    s = json.load(open(path, encoding='utf-8'))
    for net in s.get('power', []): kind[net].add('power')
    for net in s.get('ground', []): kind[net].add('ground')
    for p in s['parts']:
        refs[p['ref']] += 1
        if not (p.get('lcsc') or (p.get('lib') and p.get('uuid'))):
            problems.append(f"{p['ref']}: no lcsc / lib+uuid ({path})")
        clash = set(k.rstrip('*') for k in p['nets']) & set(p.get('nc', []))
        if clash: problems.append(f"{p['ref']}: pins both netted and no-connect {sorted(clash)}")
        for k, net in p['nets'].items():
            if net: pins[net].append(f"{p['ref']}.{k}")

single = {n: v for n, v in pins.items() if len(v) == 1}
dups = [r for r, n in refs.items() if n > 1]
mixed = {n: k for n, k in kind.items() if len(k) > 1}
print(f'pages {len(sys.argv) - 1} | parts {len(refs)} | nets {len(pins)}')
print('single-pin nets:', single or 'none')
print('duplicate designators:', dups or 'none')
print('power/ground declared both ways:', mixed or 'none')
for p in problems: print('  ', p)
bad = bool(single or dups or problems)
print('RESULT', 'FAIL' if bad else 'OK')
sys.exit(1 if bad else 0)
