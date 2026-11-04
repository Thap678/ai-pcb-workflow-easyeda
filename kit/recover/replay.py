"""Rebuild files from agent transcripts by replaying write / edit / full-read events in time order.
usage: python replay.py --root <lost dir> <out_dir> <claude jsonl>... -- <other agent jsonl>...
       (or REPLAY_ROOT=<lost dir>; default ./02_硬件设计)
Only paths under the root are written, relative to it, into out_dir; _report.csv lists each path, its last op and
whether the edit chain is intact (BROKEN = an edit did not apply: the file was also changed outside the log).
Claude Code logs: ~/.claude/projects/<dir>/*.jsonl. The other agent's zstd logs: decompress with dsh_dump.js first."""
import json, sys, os, re, datetime, csv, collections
if '--root' in sys.argv:                          # --root <lost dir> wins over REPLAY_ROOT
    _i = sys.argv.index('--root'); os.environ['REPLAY_ROOT'] = sys.argv[_i + 1]; del sys.argv[_i:_i + 2]
ROOT = os.path.normpath(os.environ.get('REPLAY_ROOT') or os.path.join(os.getcwd(), '02_硬件设计')).lower()  # 只回写该目录下的文件
out = sys.argv[1]; args = sys.argv[2:]; k = args.index('--'); cl, ds = args[:k], args[k + 1:]
ev = []  # (time, seq, path, op, payload)

def norm(p, cwd):
    p = p.replace('/', chr(92))
    if not re.match(r'^[A-Za-z]:[\\/]', p): p = os.path.join(cwd, p)
    return os.path.normpath(p)

for f in cl:
    pend = {}
    for n, line in enumerate(open(f, encoding='utf-8', errors='replace')):
        try: o = json.loads(line)
        except Exception: continue
        t = o.get('timestamp'); cwd = o.get('cwd') or ROOT
        if not t: continue
        ts = datetime.datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp()
        m = o.get('message') or {}
        if o.get('type') == 'assistant':
            for c in m.get('content') or []:
                if c.get('type') == 'tool_use' and c['name'] in ('Write', 'Edit'):
                    pend[c['id']] = (c['name'], c['input'], cwd)
        if o.get('type') == 'user':
            r = o.get('toolUseResult')
            for c in (m.get('content') if isinstance(m.get('content'), list) else []):
                if c.get('type') != 'tool_result': continue
                err = c.get('is_error')
                if c['tool_use_id'] in pend and not err:
                    nm, i, cw = pend.pop(c['tool_use_id'])
                    p = norm(i['file_path'], cw)
                    if nm == 'Write': ev.append((ts, n, p, 'write', i['content']))
                    else: ev.append((ts, n, p, 'edit', (i['old_string'], i['new_string'], i.get('replace_all', False))))
            if isinstance(r, dict) and isinstance(r.get('file'), dict) and 'content' in r['file']:
                fi = r['file']
                if fi.get('startLine', 1) == 1 and fi.get('numLines') == fi.get('totalLines'):
                    ev.append((ts, n, norm(fi['filePath'], cwd), 'read', fi['content']))

LINE = re.compile(r'^(\d+): ?(.*)$')
for f in ds:
    cwd = ROOT; calls = {}; chunks = collections.defaultdict(dict)
    for n, line in enumerate(open(f, encoding='utf-8', errors='replace')):
        try: o = json.loads(line)
        except Exception: continue
        if o.get('type') == 'session': cwd = o.get('cwd') or cwd
        d = o.get('data') or {}; ts = (o.get('time') or 0) / 1000
        if o.get('type') == 'tool/call' and d.get('name') in ('write', 'edit', 'read'):
            try: calls[d['callId']] = (d['name'], json.loads(d['arguments']))
            except Exception: pass
        if o.get('type') == 'tool/result':
            msg = d.get('message') or {}; cid = msg.get('toolCallId')
            if cid not in calls: continue
            nm, a = calls.pop(cid)
            txt = ''.join(x.get('text', '') for x in msg.get('content') or [] if isinstance(x, dict))
            if re.search(r'^(Error|error|FsError)', txt.strip()[:40]) : continue
            p = norm(a['file_path'], cwd)
            if nm == 'write': ev.append((ts, n, p, 'write', a['content']))
            elif nm == 'edit': ev.append((ts, n, p, 'edit', (a['old_string'], a['new_string'], a.get('replace_all', False))))
            else:
                if '<content>' not in txt: continue
                body = txt.split('<content>\n', 1)[1].rsplit('</content>', 1)[0]
                ls = body.split('\n'); lines = {}
                for l in ls:
                    mm = LINE.match(l)
                    if mm: lines[int(mm.group(1))] = mm.group(2)
                    elif l.startswith('(End of file') or l.startswith('(Output capped'): break
                ch = chunks[p]
                if a.get('offset') in (None, 0, 1): ch.clear()
                ch.update(lines)
                me = re.search(r'\(End of file - total (\d+) lines\)', body)
                if me and ch and set(ch) == set(range(1, int(me.group(1)) + 1)):
                    ev.append((ts, n, p, 'read', '\n'.join(ch[i] for i in range(1, int(me.group(1)) + 1)) + '\n'))

ev = [(a, b, c, d, (e.replace(chr(13)+chr(10), chr(10)) if isinstance(e, str) else tuple(x.replace(chr(13)+chr(10), chr(10)) if isinstance(x, str) else x for x in e))) for a, b, c, d, e in ev]
ev.sort(key=lambda e: (e[0], e[1]))
state, info = {}, {}
for ts, n, p, op, pl in ev:
    key = p.lower()
    if op in ('write', 'read'):
        if op == 'read' and key in state and state[key] is not None and state[key].rstrip('\n') == pl.rstrip('\n'): pass
        state[key] = pl; info[key] = [p, op, ts, 'ok']
    else:
        s = state.get(key); old, new, ra = pl
        if s is not None and old not in s and new in s: pass
        elif s is not None and old in s:
            state[key] = s.replace(old, new) if ra else s.replace(old, new, 1); info[key][1:3] = ['edit', ts]
        else:
            info.setdefault(key, [p, 'edit', ts, 'BROKEN'])[3] = 'BROKEN'; info[key][2] = ts
os.makedirs(out, exist_ok=True)
rows = []
for key, (p, op, ts, okf) in info.items():
    if not key.startswith(ROOT): continue
    rel = p[len(ROOT) + 1:]
    s = state.get(key)
    if s is not None:
        dst = os.path.join(out, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'w', encoding='utf-8', newline='').write(s)
    rows.append([rel, op, datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M'), okf, len(s or '')])
rows.sort()
with open(os.path.join(out, '_report.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
    csv.writer(fh).writerows([['path', 'last_op', 'time', 'chain', 'chars']] + rows)
print(len(rows), 'paths;', sum(r[3] == 'BROKEN' for r in rows), 'broken')

if os.environ.get('DIAG'):
    st = {}
    for ts, n, p, op, pl in ev:
        key = p.lower()
        if not any(x in key for x in os.environ['DIAG'].split(',')): continue
        if op in ('write', 'read'): st[key] = pl; continue
        s = st.get(key); old, new, ra = pl
        if s is None: print('NOSTATE', os.path.basename(p), datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')); continue
        if old in s: st[key] = s.replace(old, new) if ra else s.replace(old, new, 1)
        else: print('MISS', os.path.basename(p), datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M'), 'new-already-present' if new in s else '', repr(old[:70]))
