"""Fast Pillow preview renderer for the 电赛小车2026 board.

Why: the matplotlib montage (batch_plot.py) re-parses and re-lays-out everything per figure (~seconds per
run).  For "render each routing round immediately" we only need to rasterise what changes:

    static base  = board outline + drills + pads (+ pours, when rendering an EDA dump)  -> drawn ONCE
    dynamic layer= the SES copper (tracks + vias)                                      -> composited per run

Measured on this board: base build ~20 ms, per-variant composite ~15-40 ms at 16 px/mm
(1280x1392), i.e. millisecond-scale per frame.

Sources
  --dsn   boardR_prep.dsn          unrouted board: outline from (structure boundary), pads from (library)
  --dump  board6_*.json            EDA `pcb dump --include-copper` snapshot: outline, pads, pours, copper
  --ses   <run>/out.ses            Freerouting copper overlay (tracks + vias)
  --batch <round dir> [--top N]    render every <round>/<id>/out.ses and a contact sheet
  --bench                          print timings

Examples
  python fast_render.py --dsn boardR_prep.dsn --ses batch_runs/round3/r3-03/out.ses --out r3-03.png
  python fast_render.py --dump board6_r2-02_final2.json --out board_final.png
  python fast_render.py --dsn boardR_prep.dsn --batch batch_runs/round3 --top 6 --out-dir renders/round3
"""
import argparse
import json
import math
import os
import re
import sys
import time

from PIL import Image, ImageDraw, ImageFont

import batch_score as BS          # reuse the tested DSN / SES parsers (S-expression, mil -> mm)

# ----------------------------------------------------------------------------- palette
BG = (12, 15, 20)
BOARD = (32, 39, 46)
EDGE = (110, 122, 134)
DRILL = (6, 8, 10)
PAD_TOP = (201, 116, 74)
PAD_BOT = (79, 127, 191)
PAD_EDGE = (20, 24, 28)
TRACK_TOP = (255, 107, 74)
TRACK_BOT = (89, 165, 255)
TRACK_GND = (150, 158, 168)
VIA_SIG = (255, 208, 87)
VIA_GND = (154, 164, 173)
POUR_TOP = (122, 59, 44)
POUR_BOT = (44, 76, 108)
TEXT = (210, 216, 222)
DIM = (140, 150, 160)


# ----------------------------------------------------------------------------- helpers
def _saved(img, path):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    img.save(path)


def _font(size):
    for p in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _ring_mm(src, k):
    """EasyEDA poured fill ring -> [(x_mm, y_mm)]; `src` entries are [x, y, 'L', x, y, ...]."""
    pts, i = [(src[0] * k, src[1] * k)], 2
    while i < len(src):
        t = src[i]
        if t == 'L':
            i += 1
            while i + 1 < len(src) and not isinstance(src[i], str):
                pts.append((src[i] * k, src[i + 1] * k))
                i += 2
        elif t == 'ARC':
            pts.append((src[i + 2] * k, src[i + 3] * k))
            i += 4
        else:
            i += 1
    return pts


class Raster:
    """millimetre <-> pixel transform; board origin bottom-left, image y grows downwards."""

    def __init__(self, w_mm, h_mm, pxmm):
        self.w, self.h, self.s = w_mm, h_mm, pxmm
        self.pw = int(round(w_mm * pxmm)) + 16
        self.ph = int(round(h_mm * pxmm)) + 16

    def p(self, x, y):
        return (8 + x * self.s, 8 + (self.h - y) * self.s)

    def pt(self, pts):
        return [self.p(x, y) for x, y in pts]

    def r(self, r_mm):
        return max(1, int(round(r_mm * self.s)))


# ----------------------------------------------------------------------------- static base
def base_from_dsn(path, pxmm):
    """Unrouted board: outline + drills + pads (one raster reused by every SES variant)."""
    root = BS.parse_sexpr(open(path, encoding='utf-8', errors='replace').read())
    dsn = BS.Dsn(path)

    structure = BS.child(root, 'structure')
    bnd = BS.child(structure, 'boundary')
    # (boundary(path signal 0 x1 y1 x2 y2 ...)) -- drop the 'path' / 'signal' / layer-id tokens
    pnode = BS.child(bnd, 'path')
    nums = [float(v) for v in pnode[3:]]
    outline = [(v * dsn.unit_mm, w * dsn.unit_mm) for v, w in zip(nums[0::2], nums[1::2])]
    W = max(p[0] for p in outline)
    H = max(p[1] for p in outline)
    R = Raster(W, H, pxmm)
    img = Image.new('RGB', (R.pw, R.ph), BG)
    d = ImageDraw.Draw(img)
    d.polygon(R.pt(outline), fill=BOARD, outline=EDGE)

    # drills first (netless pads = M3 cut-outs / locating pegs / mounting lugs), then copper pads
    for p in dsn.pads:
        if p['net'] is None:
            if p['kind'] == 'circle':
                x, y = R.p(p['x'], p['y'])
                r = R.r(p['r'])
                d.ellipse([x - r, y - r, x + r, y + r], fill=DRILL)
            else:
                d.polygon(R.pt(p['pts']), fill=DRILL)
    for p in dsn.pads:
        if p['net'] is None:
            continue
        col = PAD_TOP if p['layer'] == 'TopLayer' else PAD_BOT
        if p['kind'] == 'circle':
            x, y = R.p(p['x'], p['y'])
            r = R.r(p['r'])
            d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=PAD_EDGE)
        else:
            d.polygon(R.pt(p['pts']), fill=col, outline=PAD_EDGE)
    return img, R, dict(kind='dsn', path=path, W=W, H=H)


def base_from_dump(path, pxmm):
    """EDA snapshot: outline + pours + drills + pads (+ its own copper), rendered once."""
    d0 = json.loads((lambda s: s[s.find('{'):])(open(path, encoding='utf-8').read()))
    K = 0.0254                                            # dump units: mil
    outline = [(x * K, y * K) for x, y in d0['outline']['points']]
    W = max(p[0] for p in outline)
    H = max(p[1] for p in outline)
    R = Raster(W, H, pxmm)
    img = Image.new('RGB', (R.pw, R.ph), BG)
    d = ImageDraw.Draw(img)
    d.polygon(R.pt(outline), fill=BOARD, outline=EDGE)

    # poured copper: bottom first, then top, both alpha-blended so either side stays visible.
    # Each fill = one outer ring + hole rings (clearance); holes must stay transparent.
    for layer, col, alpha in ((2, POUR_BOT, 150), (1, POUR_TOP, 150)):
        ov = Image.new('RGBA', img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        hole_mask = Image.new('L', img.size, 0)
        hm = ImageDraw.Draw(hole_mask)
        for pd in d0['copper'].get('poured', []):
            if (pd.get('layer') or pd.get('layerId')) != layer:
                continue
            for f in pd['fills']:
                rings = [_ring_mm(r, K) for r in f['source']]
                rings = [r for r in rings if len(r) >= 3]
                if not rings:
                    continue
                od.polygon(R.pt(rings[0]), fill=col + (alpha,))
                for hole in rings[1:]:
                    hm.polygon(R.pt(hole), fill=255)
        ov.paste((0, 0, 0, 0), (0, 0), hole_mask)          # punch the clearance holes back out
        img = Image.alpha_composite(img.convert('RGBA'), ov).convert('RGB')
        d = ImageDraw.Draw(img)

    # drills first (netless pads = cut-outs / locating pegs / mounting lugs)
    for c in d0['components']:
        for p in c.get('pads', []):
            if not p.get('net'):
                x, y = R.p(p['x'] * K, p['y'] * K)
                rx, ry = R.r(p['width'] * K / 2), R.r(p['height'] * K / 2)
                d.ellipse([x - rx, y - ry, x + rx, y + ry], fill=DRILL)
    # pads by side: layer 1 = top, 2 = bottom, 12 = through-hole (top colour + drill dot)
    for c in d0['components']:
        for p in c.get('pads', []):
            if not p.get('net'):
                continue
            x, y = R.p(p['x'] * K, p['y'] * K)
            rx, ry = max(1, R.r(p['width'] * K / 2)), max(1, R.r(p['height'] * K / 2))
            shape = (p.get('shape') or [''])[0].upper()
            lay = p.get('layer')
            col = PAD_BOT if lay == 2 else PAD_TOP
            box = [x - rx, y - ry, x + rx, y + ry]
            if 'ELLIPSE' in shape or 'CIRCLE' in shape or abs(rx - ry) <= 1:
                d.ellipse(box, fill=col, outline=PAD_EDGE)
            else:
                d.rectangle(box, fill=col, outline=PAD_EDGE)
            if lay == 12:
                h = max(1, min(rx, ry) // 2)
                d.ellipse([x - h, y - h, x + h, y + h], fill=DRILL)
    return img, R, dict(kind='dump', path=path, W=W, H=H)


# ----------------------------------------------------------------------------- dynamic copper
def draw_ses(img, R, ses, gnd_dim=True):
    """Composite one SES routing result onto a copy of the base. Returns (image, stats)."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    top = bot = 0
    for w in ses.wires:
        if w['layer'] == 'TopLayer':
            col = TRACK_GND if (gnd_dim and w['net'] == 'GND') else TRACK_TOP
            top += 1
        else:
            col = TRACK_GND if (gnd_dim and w['net'] == 'GND') else TRACK_BOT
            bot += 1
        width = max(1, int(round(w['width'] * R.s)))
        pts = R.pt(w['pts'])
        d.line(pts, fill=col, width=width, joint='curve')
        if len(pts) == 2:                                   # round caps for straight segments
            r = width / 2
            for x, y in pts:
                d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    for v in ses.vias:
        x, y = R.p(v['x'], v['y'])
        r = R.r(ses.via_diam / 2)
        col = VIA_GND if (gnd_dim and v['net'] == 'GND') else VIA_SIG
        d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(20, 24, 28))
        h = max(1, r // 2)
        d.ellipse([x - h, y - h, x + h, y + h], fill=DRILL)
    return out, dict(tracks=top + bot, top=top, bottom=bot, vias=len(ses.vias))


def draw_dump_copper(img, R, dump_path):
    """Render the copper that is already stored in an EDA dump (final board view)."""
    d0 = json.loads((lambda s: s[s.find('{'):])(open(dump_path, encoding='utf-8').read()))
    K = 0.0254
    out = img.copy()
    d = ImageDraw.Draw(out)
    for l in d0['copper']['lines']:
        col = TRACK_GND if l['net'] == 'GND' else (TRACK_TOP if l['layer'] == 1 else TRACK_BOT)
        w = max(1, int(round(l['lineWidth'] * K * R.s)))
        d.line([R.p(l['startX'] * K, l['startY'] * K), R.p(l['endX'] * K, l['endY'] * K)], fill=col, width=w)
    for v in d0['copper']['vias']:
        x, y = R.p(v['x'] * K, v['y'] * K)
        r = R.r((v.get('diameter') or 24) * K / 2)
        col = VIA_GND if v['net'] == 'GND' else VIA_SIG
        d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(20, 24, 28))
    return out, dict(tracks=len(d0['copper']['lines']), vias=len(d0['copper']['vias']))


# ----------------------------------------------------------------------------- chrome
def chrome(img, title, sub, stats):
    d = ImageDraw.Draw(img)
    f1, f2 = _font(max(14, img.width // 62)), _font(max(11, img.width // 90))
    d.text((10, 4), title, fill=TEXT, font=f1)
    d.text((10, 4 + f1.size + 2), sub, fill=DIM, font=f2)
    leg = f"tracks {stats.get('tracks', 0)}   vias {stats.get('vias', 0)}"
    tw = d.textlength(leg, font=f2)
    d.text((img.width - tw - 10, 4), leg, fill=DIM, font=f2)
    y = img.height - f2.size - 8
    x = 10
    for label, col in (("TopLayer", TRACK_TOP), ("BottomLayer", TRACK_BOT),
                       ("GND", TRACK_GND), ("signal via", VIA_SIG), ("GND via", VIA_GND)):
        d.ellipse([x, y + f2.size // 2 - 4, x + 8, y + f2.size // 2 + 4], fill=col)
        d.text((x + 12, y), label, fill=DIM, font=f2)
        x += 12 + d.textlength(label, font=f2) + 14
    return img


def contact_sheet(entries, out_path, cols=3, thumb_w=620):
    """entries: [(png_path, label)], laid out in a dark grid with captions."""
    ims = [(Image.open(p).convert('RGB'), lab) for p, lab in entries]
    if not ims:
        raise SystemExit('nothing to tile')
    h = int(ims[0][0].height * thumb_w / ims[0][0].width)
    rows = math.ceil(len(ims) / cols)
    pad, cap = 10, 22
    W = cols * thumb_w + (cols + 1) * pad
    H = rows * (h + cap) + (rows + 1) * pad
    sheet = Image.new('RGB', (W, H), (8, 10, 13))
    d = ImageDraw.Draw(sheet)
    f = _font(15)
    for i, (im, lab) in enumerate(ims):
        r, c = divmod(i, cols)
        x = pad + c * (thumb_w + pad)
        y = pad + r * (h + cap + pad)
        sheet.paste(im.resize((thumb_w, h), Image.LANCZOS), (x, y))
        d.text((x + 2, y + h + 3), lab, fill=TEXT, font=f)
    sheet.save(out_path)
    return sheet.size


# ----------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dsn', default='boardR_prep.dsn')
    ap.add_argument('--dump', default=None, help='EDA pcb dump json (renders pours + stored copper)')
    ap.add_argument('--ses', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--batch', default=None, help='round dir containing <id>/out.ses')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--top', type=int, default=6)
    ap.add_argument('--pxmm', type=int, default=16)
    ap.add_argument('--title', default=None)
    ap.add_argument('--bench', action='store_true')
    a = ap.parse_args()

    t0 = time.perf_counter()
    if a.dump:
        base, R, meta = base_from_dump(a.dump, a.pxmm)
        t_base = time.perf_counter() - t0
        t1 = time.perf_counter()
        img, st = draw_dump_copper(base, R, a.dump)
        t_frame = time.perf_counter() - t1
        title = a.title or f"PCB6  {os.path.basename(a.dump)}"
        sub = f"{meta['W']:.0f} x {meta['H']:.0f} mm   {a.pxmm} px/mm"
        img = chrome(img, title, sub, st)
        if a.bench:
            print(f"base {t_base * 1000:.1f} ms | composited frame {t_frame * 1000:.1f} ms | "
                  f"{img.width}x{img.height}")
        img.save(a.out or 'render_dump.png')
        print(f"{a.out or 'render_dump.png'}  tracks {st['tracks']} vias {st['vias']}")
        return

    base, R, meta = base_from_dsn(a.dsn, a.pxmm)
    t_base = time.perf_counter() - t0
    if a.bench:
        print(f"base build {t_base * 1000:.1f} ms  ({R.pw}x{R.ph} @ {a.pxmm} px/mm, cached)")

    if a.ses:
        t1 = time.perf_counter()
        ses = BS.Ses(a.ses)
        img, st = draw_ses(base, R, ses)
        t_frame = time.perf_counter() - t1
        title = a.title or f"PCB6  {os.path.basename(os.path.dirname(os.path.abspath(a.ses)))}"
        sub = (f"{meta['W']:.0f} x {meta['H']:.0f} mm   tracks {st['top']} top / {st['bottom']} bottom"
               f"   vias {st['vias']}")
        img = chrome(img, title, sub, st)
        if a.bench:
            print(f"composited frame {t_frame * 1000:.1f} ms")
        img.save(a.out or 'render_ses.png')
        print(f"{a.out or 'render_ses.png'}")
        return

    if a.batch:
        out_dir = a.out_dir or os.path.join(a.batch, 'renders')
        os.makedirs(out_dir, exist_ok=True)
        ses_of = {}
        for name in sorted(os.listdir(a.batch)):
            ses_path = os.path.join(a.batch, name, 'out.ses')
            if os.path.exists(ses_path):
                ses_of[name] = ses_path
        # prefer the batch's own ranking (best first); fall back to directory order
        order = []
        rj = os.path.join(a.batch, 'ranking.json')
        if os.path.exists(rj):
            try:
                rows = json.load(open(rj, encoding='utf-8'))['rows']
                order = [r['id'] for r in rows if r.get('id') in ses_of and r.get('ok')]
            except (KeyError, ValueError):
                order = []
        runs = [(n, ses_of[n]) for n in order] + [(n, p) for n, p in ses_of.items() if n not in order]
        got, times = [], []
        for name, ses_path in runs[:max(a.top, 0) or None]:
            t1 = time.perf_counter()
            ses = BS.Ses(ses_path)
            img, st = draw_ses(base, R, ses)
            t = time.perf_counter() - t1
            times.append(t)
            sub = (f"tracks {st['top']}/{st['bottom']}   vias {st['vias']}")
            img = chrome(img, name, sub, st)
            p = os.path.join(out_dir, f'{name}.png')
            img.save(p)
            got.append((p, f"{name}  vias {st['vias']}  tracks {st['tracks']}"))
        if got:
            sheet = os.path.join(out_dir, 'contact_sheet.png')
            size = contact_sheet(got, sheet)
            t_all = sum(times)
            print(f"rendered {len(got)} frames in {t_all * 1000:.0f} ms total, "
                  f"{1000 * t_all / len(got):.1f} ms/frame "
                  f"(base build {t_base * 1000:.1f} ms once) -> {out_dir}")
            print("contact sheet", sheet, size)
        return

    ap.error('give --ses, --dump or --batch')


if __name__ == '__main__':
    main()
