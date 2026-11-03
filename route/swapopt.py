"""Offline pin/gate-swap evaluation for the upper board (no board or schematic changes).

Model: every signal net = a set of endpoints; some endpoints are 'roles' whose physical pad depends on the state
(MCU GPIO, J603 slot, UART destination connector, RX series resistor, grey divider column, ADC pin, Qwiic/pull-up).
Ratsnest = MST per net (straight lines). Objective = crossings between different nets + 0.1 * length_mm.
usage: python swapopt.py dump.json pinmap_sky.json [iterations]
"""
import json, sys, os, math, random, copy, collections, itertools
import numpy as np

MM = 0.0254
d = json.loads((lambda s: s[s.find('{'):])(open(sys.argv[1], encoding='utf-8').read()))
pm = json.load(open(sys.argv[2], encoding='utf-8'))
ITER = int(sys.argv[3]) if len(sys.argv) > 3 else 40000
comp = {c['designator']: c for c in d['components']}
pad = {(c['designator'], p['padNumber']): (p['x'] * MM, p['y'] * MM) for c in d['components'] for p in c['pads']}
padnet = {(c['designator'], p['padNumber']): p['net'] for c in d['components'] for p in c['pads']}
sock = {int(k): v for k, v in pm['sock'].items()}
gpio_pos = {}
for s, g in sock.items():
    if g.startswith('P'): gpio_pos[g] = pad[('J601', str(s))] if s <= 40 else pad[('J602', str(s - 40))]
MAP = {n: v[0] for n, v in pm['map'].items()}
POWER = {'GND', '5V_MAIN', '5V_LOGIC', '3V3_MAIN', '3V3_RF', 'VCC_GRAY', ''}

# ---------------- groups and the current state ----------------
J603_SIG = [n for (r, p), n in padnet.items() if r == 'J603' and n not in POWER]
# sweep a subset of slots out of the search (e.g. keep J603.38 free for the lower board's IMON: SWAP_LOCK_J603=38)
# note: padNet keys are the dump's padNumber strings, so compare numerically
LOCK_J603 = {int(x) for x in os.environ.get('SWAP_LOCK_J603', '').replace(' ', '').split(',') if x}
J603_SLOTS = [p for (r, p), n in padnet.items() if r == 'J603' and int(p) not in LOCK_J603
              and n not in POWER - {''} and n != 'GND' and not n.startswith('5V')]
UARTS = {'U1': ('PB6', 'PB7'), 'U2': ('PD5', 'PD6'), 'U3': ('PD8', 'PD9'), 'U4': ('PC10', 'PC11'), 'U5': ('PC12', 'PD2'), 'U6': ('PC6', 'PC7')}
UCONN = ['J621', 'J607', 'J619', 'J620', 'J608']
URES = ['R638', 'R615', 'R636', 'R637', 'R616']
GCONN = ['J609', 'J610', 'J611', 'J612', 'J622', 'J623']
GCOL = [('R617', 'R618', 'C614'), ('R619', 'R620', 'C615'), ('R621', 'R622', 'C616'),
        ('R623', 'R624', 'C617'), ('R639', 'R640', 'C623'), ('R641', 'R642', 'C624')]
ADC_POOL = ['PA2', 'PA3', 'PB0', 'PB1', 'PC4', 'PC5', 'PC0', 'PC1', 'PC2', 'PC3']
GPIO_SIG = ['KEY1', 'KEY2', 'KEY3', 'KEY4', 'KEY5', 'EC11_A', 'EC11_B', 'EC11_P', 'BEEP', 'RGB_R', 'RGB_G', 'RGB_B',
            'E01_CS', 'E01_CE', 'E01_IRQ', 'IMU_INT', 'IOX_INT', 'MOT_EN']
GPIO_POOL = sorted({MAP[s] for s in GPIO_SIG} | {'PE15', 'PD3', 'PE6'})
EXTI_SIG = {'EC11_A', 'EC11_B', 'E01_IRQ', 'IMU_INT', 'IOX_INT'}
OUT_SIG = {'BEEP', 'RGB_R', 'RGB_G', 'RGB_B', 'E01_CS', 'E01_CE', 'MOT_EN'}
TIM1 = ['PE9', 'PE11', 'PE13', 'PE14']; MOT = ['MOT_L1', 'MOT_L2', 'MOT_R1', 'MOT_R2']
TIM4 = ['PD12', 'PD13', 'PD14', 'PD15']; SRV = ['SRV1', 'SRV2', 'SRV3', 'SRV4']
ENC_PAIRS = [('PA15', 'PA1'), ('PB4', 'PB5')]
QW = ['J616', 'J617', 'J624']; PULL = [('R603', 'R604'), ('R632', 'R633'), ('R634', 'R635')]
BUS = {'I2C1': ('I2C_SCL', 'I2C_SDA', 'PB8', 'PB9'), 'I2C2': ('I2C2_SCL', 'I2C2_SDA', 'PB10', 'PB11'), 'I2C3': ('I2C3_SCL', 'I2C3_SDA', 'PA8', 'PC9')}
j603_cur = {n: p for (r, p), n in padnet.items() if r == 'J603' and n in J603_SIG}

state0 = {
    'j603': dict(j603_cur),                                            # signal -> J603 pad
    'udest': {'U1': 'J621', 'U3': 'J607', 'U4': 'J619', 'U5': 'J620', 'U6': 'J608', 'U2': 'LOWER'},
    'ures': {'U1': 'R638', 'U3': 'R615', 'U4': 'R636', 'U5': 'R637', 'U6': 'R616'},
    'gcol': list(range(6)), 'adc': {**{k: MAP[f'GRAY{k + 1}'] for k in range(6)}, 'BAT': MAP['BATADC']},
    'gpio': {s: MAP[s] for s in GPIO_SIG},
    'mot': {m: MAP[m] for m in MOT}, 'srv': {s: MAP[s] for s in SRV},
    'enc': (0, False, False),                                          # (left timer index, swap L A/B, swap R A/B)
    'stp': {'STP_L': MAP['STP_L'], 'STP_R': MAP['STP_R']},
    'qw': {'I2C1': 'J616', 'I2C2': 'J617', 'I2C3': 'J624'}, 'pull': {'I2C1': 0, 'I2C2': 1, 'I2C3': 2},
}
FIXED_OTHER = collections.defaultdict(list)       # pads of a net outside the permuted parts
PERMUTED_REFS = {'J601', 'J602', 'J603', *UCONN, *URES, *GCONN, *[x for c in GCOL for x in c], *QW, *[x for p in PULL for x in p]}
for (r, p), n in padnet.items():
    if n and n not in POWER and r not in PERMUTED_REFS: FIXED_OTHER[n].append(pad[(r, p)])


def nets(st):
    N = {}
    j = lambda sig: pad[('J603', st['j603'][sig])]
    g = lambda pin: gpio_pos[pin]
    # UARTs
    for u, (tx, rx) in UARTS.items():
        dest = st['udest'][u]
        if dest == 'LOWER':
            N[f'{u}_TX'] = [g(tx), j('USTEP_TX')]; N[f'{u}_RX'] = [g(rx), j('USTEP_RX')]
        else:
            r = st['ures'][u]
            N[f'{u}_TX'] = [g(tx), pad[(dest, '2')]]
            N[f'{u}_RX'] = [g(rx), pad[(r, '1')]]
            N[f'{u}_RXD'] = [pad[(r, '2')], pad[(dest, '3')]]
    # grey
    for k in range(6):
        rt, rb, c = GCOL[st['gcol'][k]]
        N[f'G{k}_IN'] = [pad[(GCONN[k], '2')], pad[(rt, '1')]]
        N[f'G{k}'] = [pad[(rt, '2')], pad[(rb, '1')], pad[(c, '1')], g(st['adc'][k])]
    N['BATADC'] = [g(st['adc']['BAT']), j('BATADC')]
    # I2C buses
    for b, (scl, sda, pscl, psda) in BUS.items():
        q = st['qw'][b]; pu = PULL[st['pull'][b]]
        for net, pin, qpad, ppad in ((scl, pscl, '4', pu[0]), (sda, psda, '3', pu[1])):
            pts = [g(pin), pad[(q, qpad)], pad[(ppad, '2')]] + FIXED_OTHER[net]
            if net in st['j603']: pts.append(j(net))
            N[net] = pts
    # timers to the lower board
    for m in MOT: N[m] = [g(st['mot'][m]), j(m)]
    for s in SRV: N[s] = [g(st['srv'][s]), j(s)]
    left, sl, sr = st['enc']
    la, lb = ENC_PAIRS[left]; ra, rb = ENC_PAIRS[1 - left]
    if sl: la, lb = lb, la
    if sr: ra, rb = rb, ra
    for n, p in (('ENC_L_A', la), ('ENC_L_B', lb), ('ENC_R_A', ra), ('ENC_R_B', rb)): N[n] = [g(p), j(n)]
    for s in ('STP_L', 'STP_R'): N[s] = [g(st['stp'][s]), j(s)]
    N['CAN_RX'] = [g('PD0'), j('CAN_RX')]; N['CAN_TX'] = [g('PD1'), j('CAN_TX')]
    # plain GPIO
    for s in GPIO_SIG:
        pts = [g(st['gpio'][s])] + FIXED_OTHER[s]
        if s in st['j603']: pts.append(j(s))
        N[s] = pts
    # everything else that is fixed (SPI, RGB_xK, BEEP_B, ...)
    for n, pts in FIXED_OTHER.items():
        if n in N or n in ('USTEP_TX', 'USTEP_RX') or n.startswith(('U1_', 'U3_', 'U4_', 'U5_', 'U6_', 'GRAY', 'I2C')): continue
        mcu = [gpio_pos[MAP[n]]] if n in MAP and MAP[n] in gpio_pos else []
        N[n] = mcu + pts
    return N


def mst(pts):
    if len(pts) < 2: return []
    used, edges = {0}, []
    while len(used) < len(pts):
        best = min(((math.dist(pts[i], pts[k]), i, k) for i in used for k in range(len(pts)) if k not in used))
        used.add(best[2]); edges.append((pts[best[1]], pts[best[2]]))
    return edges


def evaluate(st):
    E, owner = [], []
    for n, pts in nets(st).items():
        for e in mst(pts): E.append(e); owner.append(n)
    A = np.array([[a[0], a[1], b[0], b[1]] for a, b in E]); own = np.array([hash(o) for o in owner])
    length = float(np.hypot(A[:, 2] - A[:, 0], A[:, 3] - A[:, 1]).sum())
    p, q = A[:, None, :2], A[:, None, 2:]; r, s = A[None, :, :2], A[None, :, 2:]
    def orient(a, b, c): return np.sign((b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0]))
    o1, o2 = orient(p, q, r), orient(p, q, s); o3, o4 = orient(r, s, p), orient(r, s, q)
    X = (o1 * o2 < 0) & (o3 * o4 < 0) & (own[:, None] != own[None, :])
    cross = int(np.triu(X, 1).sum())
    return cross, length, cross + 0.1 * length


def valid(st):
    ex = [int(st['gpio'][s][2:]) for s in EXTI_SIG]
    if len(set(ex)) != len(ex): return False
    if any(st['gpio'][s] == 'PC13' for s in OUT_SIG): return False
    used = list(st['gpio'].values()) + [v for v in st['adc'].values()]
    return len(set(used)) == len(used)


def move(st):
    s = copy.deepcopy(st)
    kinds = ['j603', 'j603', 'j603', 'udest', 'ures', 'gcol', 'adc', 'gpio', 'gpio', 'mot', 'srv', 'enc', 'stp', 'qw', 'pull']
    allow = os.environ.get('SWAP_KINDS')                     # ablation: restrict the levers, e.g. SWAP_KINDS=gpio,adc
    if allow: kinds = [k for k in kinds if k in allow.split(',')]
    kind = random.choice(kinds)
    if kind == 'j603':
        a = random.choice(J603_SIG); free = [x for x in J603_SLOTS if x not in s['j603'].values()]
        b = random.choice(J603_SIG + free)
        if b in J603_SIG: s['j603'][a], s['j603'][b] = s['j603'][b], s['j603'][a]
        else: s['j603'][a] = b
    elif kind == 'udest':
        a, b = random.sample(list(s['udest']), 2); s['udest'][a], s['udest'][b] = s['udest'][b], s['udest'][a]
        rs = [u for u in s['udest'] if s['udest'][u] != 'LOWER']           # keep one resistor per connector UART
        pool = URES[:]; s['ures'] = {u: s['ures'].get(u) for u in rs}
        left = [r for r in pool if r not in s['ures'].values()]
        for u in rs:
            if s['ures'][u] is None: s['ures'][u] = left.pop()
    elif kind == 'ures':
        a, b = random.sample(list(s['ures']), 2); s['ures'][a], s['ures'][b] = s['ures'][b], s['ures'][a]
    elif kind == 'gcol':
        a, b = random.sample(range(6), 2); s['gcol'][a], s['gcol'][b] = s['gcol'][b], s['gcol'][a]
    elif kind == 'adc':
        a = random.choice(list(s['adc'])); free = [x for x in ADC_POOL if x not in s['adc'].values()]
        b = random.choice(list(s['adc']) + free)
        if b in s['adc']: s['adc'][a], s['adc'][b] = s['adc'][b], s['adc'][a]
        else: s['adc'][a] = b
    elif kind == 'gpio':
        a = random.choice(GPIO_SIG); free = [x for x in GPIO_POOL if x not in s['gpio'].values()]
        b = random.choice(GPIO_SIG + free)
        if b in GPIO_SIG: s['gpio'][a], s['gpio'][b] = s['gpio'][b], s['gpio'][a]
        else: s['gpio'][a] = b
    elif kind == 'mot':
        a, b = random.sample(MOT, 2); s['mot'][a], s['mot'][b] = s['mot'][b], s['mot'][a]
    elif kind == 'srv':
        a, b = random.sample(SRV, 2); s['srv'][a], s['srv'][b] = s['srv'][b], s['srv'][a]
    elif kind == 'enc':
        s['enc'] = (random.randint(0, 1), random.random() < .5, random.random() < .5)
    elif kind == 'stp':
        s['stp'] = {'STP_L': s['stp']['STP_R'], 'STP_R': s['stp']['STP_L']}
    elif kind == 'qw':
        a, b = random.sample(list(BUS), 2); s['qw'][a], s['qw'][b] = s['qw'][b], s['qw'][a]
    else:
        a, b = random.sample(list(BUS), 2); s['pull'][a], s['pull'][b] = s['pull'][b], s['pull'][a]
    return s


def optimize(seed=1, iters=None, start=None, verbose=True):
    """One simulated-annealing pass from `start` (default: the current board state).

    SWAP_LOCK_J603=<slots> keeps those J603 pads out of the search, SWAP_KINDS=<csv> restricts
    the levers (e.g. no `j603` move = plan A). Returns (best_state, best_value, start_value).
    """
    iters = ITER if iters is None else iters
    st0 = state0 if start is None else start
    c0 = evaluate(st0)
    random.seed(seed)
    cur, cv = st0, c0; best, bv = cur, cv
    T0 = 3.0
    for it in range(iters):
        T = T0 * (1 - it / iters) + 0.02
        cand = move(cur)
        if not valid(cand): continue
        v = evaluate(cand)
        if v[2] < cv[2] or random.random() < math.exp((cv[2] - v[2]) / T):
            cur, cv = cand, v
            if v[2] < bv[2]: best, bv = cand, v
        if verbose and it % 5000 == 0: print(f'  it {it}: best crossings {bv[0]}, {bv[1]:.0f} mm', flush=True)
    return best, bv, c0


if __name__ == '__main__':
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    c0 = evaluate(state0)
    print(f'current : crossings {c0[0]}, ratsnest {c0[1]:.0f} mm, objective {c0[2]:.1f}', flush=True)
    best, bv, _ = optimize(seed)
    print(f'optimized: crossings {bv[0]}, ratsnest {bv[1]:.0f} mm, objective {bv[2]:.1f}')
    json.dump({'state0': state0, 'best': best, 'cur': c0, 'opt': bv}, open(f"swap_best_{os.environ.get('SWAP_TAG', '')}{seed}.json", 'w'), indent=1)
