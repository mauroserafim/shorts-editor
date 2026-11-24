"""
Gera o título do Short como imagem PNG (transparente), em estilos "americanos" profissionais.
Uso: render_title(["LINHA 1", "LINHA 2"], estilo, "saida.png")
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

NAVY = (14, 33, 84)
NAVY2 = (28, 52, 120)
RED = (200, 16, 46)
RED2 = (150, 8, 30)
WHITE = (255, 255, 255)
CREAM = (246, 238, 220)
BLACK = (12, 12, 14)
GOLD = (245, 190, 30)

FONT_DIRS = [os.getenv("TITLE_FONT_DIR", ""), "/usr/share/fonts/truetype/google-fonts/ofl/anton",
             "/usr/share/fonts/truetype/anton", os.path.join(os.path.dirname(__file__), "fonts")]


def _font(size):
    for d in FONT_DIRS:
        p = os.path.join(d, "Anton-Regular.ttf") if d else ""
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("Fonte Anton-Regular.ttf não encontrada")


def _text_img(text, size, fill, shear=0.0, stroke=0, stroke_fill=BLACK, shadow=False):
    f = _font(size)
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    w, h = r - l + 40, b - t + 30
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((20 - l + 5, 15 - t + 6), text, font=f, fill=(0, 0, 0, 170))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(4)))
        d = ImageDraw.Draw(im)
    d.text((20 - l, 15 - t), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    if shear:
        extra = int(abs(shear) * h)
        im = im.transform((w + extra, h), Image.AFFINE, (1, shear, -extra if shear > 0 else 0, 0, 1, 0),
                          resample=Image.BICUBIC)
    return im.crop(im.getbbox())


def _poly(d, pts, fill):
    d.polygon([(int(x), int(y)) for x, y in pts], fill=fill)


def render_title(lines, style="navy_slant", out=None, max_w=1000):
    lines = [l.replace("*", "").strip().upper() for l in lines if l.strip()][:2]
    size = 118
    while True:
        imgs = [_text_img(l, size, WHITE) for l in lines]
        if max(i.width for i in imgs) <= max_w - 120 or size < 60:
            break
        size -= 6
    tw = max(i.width for i in imgs)
    lh = imgs[0].height
    gap = int(size * 0.18)
    th = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
    W, H = tw + 160, th + 140
    c = Image.new("RGBA", (W + 80, H + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    ox, oy = 40, 40

    def place(text_imgs, x_center, y0, spacing=gap):
        y = y0
        for im in text_imgs:
            c.alpha_composite(im, (int(x_center - im.width / 2), int(y)))
            y += im.height + spacing

    cx = ox + W / 2
    if style == "navy_slant":  # parecido com a opção 2 dele
        s = 40
        _poly(d, [(ox + 120, oy - 18), (ox + W * 0.62, oy - 18), (ox + W * 0.62 - s, oy + 22), (ox + 120 - s, oy + 22)], RED)
        _poly(d, [(ox + s, oy + 10), (ox + W, oy + 10), (ox + W - s, oy + H - 10), (ox, oy + H - 10)], WHITE)
        _poly(d, [(ox + s + 10, oy + 18), (ox + W - 8, oy + 18), (ox + W - s - 8, oy + H - 18), (ox + 10, oy + H - 18)], NAVY)
        _poly(d, [(ox + W * 0.38 + s, oy + H - 22), (ox + W - 100 + s, oy + H - 22), (ox + W - 100, oy + H + 18), (ox + W * 0.38, oy + H + 18)], RED)
        imgs = [_text_img(l, size, WHITE, shear=0.18, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2)
    elif style == "stars_badge":  # parecido com a opção 6
        r = 34
        d.rounded_rectangle([ox, oy, ox + W, oy + H + 40], r, fill=CREAM)
        d.rounded_rectangle([ox + 10, oy + 10, ox + W - 10, oy + H + 30], r - 8, fill=NAVY)
        sx = ox + W - 110
        for k, col in enumerate([RED, CREAM, RED, CREAM, RED]):
            x = sx + k * 20
            _poly(d, [(x + 30, oy + 10), (x + 50, oy + 10), (x + 20, oy + H + 30), (x, oy + H + 30)], col)
        star_y = oy + 46
        for k, (dx, rr) in enumerate([(-70, 13), (0, 20), (70, 13)]):
            _star(d, cx - 50 + dx, star_y, rr, CREAM)
        d.line([ox + 60, star_y, cx - 150, star_y], fill=CREAM, width=4)
        d.line([cx + 50, star_y, ox + W - 140, star_y], fill=CREAM, width=4)
        imgs = [_text_img(l, size, CREAM, shadow=True) for l in lines]
        place(imgs, cx - 50, oy + 80 + (H - 40 - th) / 2)
    elif style == "black_redbar":  # parecido com a opção 5
        d.rectangle([ox, oy, ox + W, oy + H], fill=BLACK)
        d.rectangle([ox, oy, ox + 28, oy + H], fill=RED)
        place(imgs, cx + 14, oy + (H - th) / 2)
    elif style == "news_tag":  # estilo telejornal americano
        tag = _text_img("MECANISMO AMERICANO", 46, WHITE)
        d.rectangle([ox, oy + 70, ox + W, oy + H + 40], fill=WHITE)
        d.rectangle([ox, oy + H + 28, ox + W, oy + H + 40], fill=NAVY)
        d.rectangle([ox + 30, oy, ox + 30 + tag.width + 50, oy + 80], fill=RED)
        c.alpha_composite(tag, (ox + 55, oy + 40 - tag.height // 2))
        imgs = [_text_img(l, size, NAVY) for l in lines]
        place(imgs, cx, oy + 80 + (H - 40 - th) / 2)
    elif style == "split_slant":  # azul e vermelho com corte diagonal
        mid = W * 0.55
        _poly(d, [(ox, oy), (ox + mid + 60, oy), (ox + mid - 60, oy + H), (ox, oy + H)], NAVY)
        _poly(d, [(ox + mid + 60, oy), (ox + W, oy), (ox + W, oy + H), (ox + mid - 60, oy + H)], RED)
        _poly(d, [(ox + mid + 52, oy), (ox + mid + 68, oy), (ox + mid - 52, oy + H), (ox + mid - 68, oy + H)], WHITE)
        d.rectangle([ox + 50, oy + H - 30, ox + 300, oy + H - 22], fill=GOLD)
        imgs = [_text_img(l, size, WHITE, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2)
    elif style == "two_bars":  # cada linha em uma faixa inclinada, vermelha e azul
        imgs = [_text_img(l, size, WHITE, shear=0.15) for l in lines]
        y = oy
        for k, im in enumerate(imgs):
            bw, bh = im.width + 110, im.height + 50
            x0 = cx - bw / 2 + (-30 if k == 0 else 30)
            _poly(d, [(x0 + 30, y), (x0 + bw + 30, y), (x0 + bw, y + bh), (x0, y + bh)], RED if k == 0 else NAVY)
            c.alpha_composite(im, (int(x0 + 15 + (bw - im.width) / 2), int(y + (bh - im.height) / 2)))
            y += bh + 8
    elif style == "glossy_red":  # vermelho com relevo, mais moderno que a opção 1
        d.rounded_rectangle([ox, oy, ox + W, oy + H], 26, fill=WHITE)
        grad = Image.new("RGBA", (W - 16, H - 16))
        gd = ImageDraw.Draw(grad)
        for yy in range(H - 16):
            t = yy / (H - 16)
            col = tuple(int(RED[i] * (1 - t) + RED2[i] * t) for i in range(3)) + (255,)
            gd.line([0, yy, W, yy], fill=col)
        mask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, grad.width - 1, grad.height - 1], 20, fill=255)
        c.paste(grad, (ox + 8, oy + 8), mask)
        imgs = [_text_img(l, size, WHITE, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2)
    elif style == "navy_gold":  # azul-marinho com detalhe dourado e estrela (premium)
        d.rectangle([ox, oy, ox + W, oy + H], fill=NAVY)
        d.rectangle([ox, oy, ox + W, oy + 10], fill=RED)
        d.rectangle([ox, oy + H - 10, ox + W, oy + H], fill=RED)
        d.rectangle([ox + 40, oy + H - 40, ox + W - 40, oy + H - 34], fill=GOLD)
        _star(d, ox + 60, oy + 60, 22, WHITE)
        _star(d, ox + W - 60, oy + 60, 22, WHITE)
        imgs = [_text_img(l, size, WHITE, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2 - 8)
    elif style == "espn_lower":  # barra de esporte americano: bloco vermelho com estrelas + barra azul
        d.rectangle([ox + 150, oy, ox + W, oy + H], fill=NAVY)
        _poly(d, [(ox, oy), (ox + 190, oy), (ox + 150, oy + H), (ox, oy + H)], RED)
        for k in range(3):
            _star(d, ox + 75, oy + H / 2 + (k - 1) * 55, 20, WHITE)
        d.rectangle([ox + 150, oy + H - 12, ox + W, oy + H], fill=WHITE)
        imgs = [_text_img(l, size, WHITE, shear=0.12) for l in lines]
        place(imgs, ox + 150 + (W - 150) / 2, oy + (H - th) / 2 - 6)
    elif style == "word_box":  # estilo MrBeast: texto branco com contorno + última linha em tarja vermelha
        im1 = _text_img(lines[0], size, WHITE, stroke=9, stroke_fill=BLACK, shadow=True)
        c.alpha_composite(im1, (int(cx - im1.width / 2), oy))
        if len(lines) > 1:
            im2 = _text_img(lines[1], size, WHITE)
            y2 = oy + im1.height + 6
            bw, bh = im2.width + 70, im2.height + 40
            _poly(d, [(cx - bw / 2 + 20, y2), (cx + bw / 2 + 20, y2), (cx + bw / 2 - 20, y2 + bh), (cx - bw / 2 - 20, y2 + bh)], RED)
            c.alpha_composite(im2, (int(cx - im2.width / 2), int(y2 + 20)))
    elif style == "white_red_stack":  # telejornal: linha 1 branca com texto azul, linha 2 vermelha com texto branco
        y = oy
        for k, l in enumerate(lines):
            im = _text_img(l, size, NAVY if k == 0 else WHITE)
            bw, bh = im.width + 80, im.height + 40
            d.rectangle([cx - bw / 2, y, cx + bw / 2, y + bh], fill=WHITE if k == 0 else RED)
            c.alpha_composite(im, (int(cx - im.width / 2), int(y + 20)))
            y += bh
        d.rectangle([cx - 260, y, cx + 260, y + 10], fill=NAVY)
    elif style == "pill_tag":  # pílula azul arredondada + etiqueta vermelha "EUA"
        tag = _text_img("🇺🇸 EUA" if False else "EUA", 48, WHITE)
        d.rounded_rectangle([ox, oy + 40, ox + W, oy + H + 40], 70, fill=NAVY)
        d.rounded_rectangle([cx - tag.width / 2 - 40, oy, cx + tag.width / 2 + 40, oy + 76], 38, fill=RED)
        c.alpha_composite(tag, (int(cx - tag.width / 2), int(oy + 38 - tag.height / 2)))
        place(imgs, cx, oy + 40 + (H - th) / 2)
    elif style == "stripes_band":  # azul com faixa de listras vermelha e branca embaixo
        d.rectangle([ox, oy, ox + W, oy + H], fill=NAVY)
        for k in range(7):
            d.rectangle([ox, oy + H + k * 9, ox + W, oy + H + k * 9 + 9], fill=RED if k % 2 == 0 else WHITE)
        _star(d, ox + W - 55, oy + 50, 22, WHITE)
        imgs = [_text_img(l, size, WHITE, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2)
    elif style == "frame_corners":  # minimalista: texto branco forte com cantos vermelhos
        imgs = [_text_img(l, size, WHITE, stroke=6, stroke_fill=BLACK, shadow=True) for l in lines]
        place(imgs, cx, oy + (H - th) / 2)
        L = 90
        for (x, y, sx, sy) in [(ox, oy, 1, 1), (ox + W, oy, -1, 1), (ox, oy + H, 1, -1), (ox + W, oy + H, -1, -1)]:
            d.rectangle(sorted([x, x + sx * L]) [:1] + [min(y, y + sy * 14)] + sorted([x, x + sx * L])[1:] + [max(y, y + sy * 14)], fill=RED)
            d.rectangle([min(x, x + sx * 14), min(y, y + sy * L), max(x, x + sx * 14), max(y, y + sy * L)], fill=RED)
    im = c.crop(c.getbbox())
    if out:
        im.save(out)
    return im


def _star(d, x, y, r, fill):
    import math
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.42
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    _poly(d, pts, fill)


STYLES = ["navy_slant", "stars_badge", "black_redbar", "news_tag", "split_slant", "two_bars", "glossy_red", "navy_gold",
          "espn_lower", "word_box", "white_red_stack", "pill_tag", "stripes_band", "frame_corners"]


def _torn_poly(x0, y0, x1, y1, rough, rng, step=9):
    """Contorno de papel rasgado: passeio aleatório suave + fibras finas (parece rasgo de verdade)."""
    pts = []

    def edge(a, b, fixed, horizontal, sign):
        out, off, pos = [], 0.0, a
        n = 1 if b > a else -1
        while (pos < b) if n > 0 else (pos > b):
            off = max(-rough, min(rough, off * 0.85 + rng.gauss(0, rough * 0.45)))
            fib = rng.uniform(0, rough * 0.35)
            v = fixed + sign * (off + fib)
            out.append((pos, v) if horizontal else (v, pos))
            pos += n * rng.uniform(step * 0.35, step * 0.9)
        return out
    pts += edge(x0, x1, y0, True, -1)
    pts += edge(y0, y1, x1, False, 1)
    pts += edge(x1, x0, y1, True, 1)
    pts += edge(y1, y0, x0, False, -1)
    return pts


def render_torn_paper(lines, out=None, max_w=980, seed=7, angle=-2.0, size=128, blank=False, scale=1.0):
    """Título em papel branco rasgado + papel vermelho aparecendo no canto inferior direito."""
    import random
    import numpy as np
    rng = random.Random(seed)
    lines = [l.replace("*", "").strip().upper() for l in lines if l.strip()][:2]
    size = int(size * scale)
    max_w = int(max_w * scale)
    while True:
        imgs = [_text_img(l, size, NAVY) for l in lines]
        imgs = [i.resize((int(i.width * 0.94), i.height), Image.LANCZOS) for i in imgs]  # letras mais juntas
        if max(i.width for i in imgs) <= max_w - int(170 * scale) or size < 60:
            break
        size -= 6
    tw = max(i.width for i in imgs)
    gap = int(size * 0.10)
    th = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
    PW, PH = int(tw + 150 * scale), int(th + 115 * scale)
    M = int(80 * scale)
    R = 5 * scale
    c = Image.new("RGBA", (PW + 2 * M, PH + 2 * M), (0, 0, 0, 0))
    # papel vermelho por baixo, aparecendo só no canto inferior direito
    red_mask = Image.new("L", c.size, 0)
    ImageDraw.Draw(red_mask).polygon(_torn_poly(M + PW * 0.60, M + PH * 0.80, M + PW + 8 * scale, M + PH + 24 * scale,
                                                R * 1.1, rng, 6 * scale), fill=255)
    red_mask = red_mask.rotate(-4, resample=Image.BICUBIC, center=(M + PW, M + PH)).filter(ImageFilter.GaussianBlur(0.6 * scale))
    rn = np.random.default_rng(seed + 1).normal(0, 6, (c.size[1], c.size[0]))
    red_tex = np.zeros((c.size[1], c.size[0], 3), np.float32)
    red_tex[...] = (204, 20, 38)
    red_tex = (red_tex + rn[..., None]).clip(0, 255).astype("uint8")
    red = Image.fromarray(red_tex, "RGB").convert("RGBA")
    red.putalpha(red_mask)
    # papel branco
    paper_mask = Image.new("L", c.size, 0)
    ImageDraw.Draw(paper_mask).polygon(_torn_poly(M, M, M + PW, M + PH, R, rng, 6 * scale), fill=255)
    paper_mask = paper_mask.filter(ImageFilter.GaussianBlur(0.5 * scale))
    noise = np.random.default_rng(seed).normal(0, 5, (c.size[1], c.size[0]))
    fib = np.random.default_rng(seed + 2).normal(0, 1, (c.size[1] // 3 + 1, c.size[0] // 3 + 1))
    fib = np.kron(fib, np.ones((3, 3)))[:c.size[1], :c.size[0]] * 3  # textura de fibra leve
    base = np.full((c.size[1], c.size[0], 3), (247, 247, 244), dtype=np.float32)
    grad = np.linspace(3, -7, c.size[1])[:, None, None]
    tex = (base + (noise + fib)[..., None] + grad).clip(0, 255).astype("uint8")
    paper = Image.fromarray(tex, "RGB").convert("RGBA")
    paper.putalpha(paper_mask)
    # franja branca do rasgo (fibras mais claras na borda)
    edge = paper_mask.filter(ImageFilter.MaxFilter(3 if scale < 1.5 else 5))
    fringe = Image.new("RGBA", c.size, (255, 255, 255, 0))
    fringe.putalpha(edge.point(lambda v: int(v * 0.85)))
    # sombras
    sh = Image.new("RGBA", c.size, (0, 0, 0, 0))
    sh.putalpha(paper_mask.point(lambda v: int(v * 0.45)))
    sh = sh.filter(ImageFilter.GaussianBlur(9 * scale))
    rsh = Image.new("RGBA", c.size, (0, 0, 0, 0))
    rsh.putalpha(red_mask.point(lambda v: int(v * 0.35)))
    rsh = rsh.filter(ImageFilter.GaussianBlur(6 * scale))
    c.alpha_composite(rsh, (int(3 * scale), int(6 * scale)))
    c.alpha_composite(red)
    c.alpha_composite(sh, (int(4 * scale), int(8 * scale)))
    c.alpha_composite(fringe)
    c.alpha_composite(paper)
    if not blank:
        y = M + (PH - th) / 2
        for im in imgs:
            c.alpha_composite(im, (int(M + (PW - im.width) / 2), int(y)))
            y += im.height + gap
    c = c.rotate(angle, resample=Image.BICUBIC, expand=True)
    c = c.crop(c.getbbox())
    if out:
        c.save(out)
    return c


def _tear_line(a, b, base, rng, rough, step):
    """Linha rasgada de a até b (coordenada principal), oscilando em torno de 'base' (função ou valor)."""
    out, off, pos = [], 0.0, a
    n = 1 if b > a else -1
    while (pos < b) if n > 0 else (pos > b):
        off = max(-rough, min(rough, off * 0.8 + rng.gauss(0, rough * 0.5)))
        bv = base(pos) if callable(base) else base
        out.append((pos, bv + off + rng.uniform(-rough * 0.3, rough * 0.3)))
        pos += n * rng.uniform(step * 0.3, step)
    return out


def render_paper_title(text, out=None, blank=False, scale=2.0, seed=5):
    """
    Caixa de título 'Mecanismo Americano': faixa reta de papel branco rasgado, textura leve,
    papel vermelho aparecendo só no canto inferior direito, texto azul-marinho condensado numa linha.
    """
    import random
    import numpy as np
    rng = random.Random(seed)
    S = scale
    NAVY_D = (14, 26, 60)
    f = _font(int(150 * S))
    # texto condensado e com pouco espaço entre palavras (igual à referência)
    words = text.upper().split() if text else ["BRASIL", "X", "EUA"]
    wimgs = []
    for w in words:
        im = _text_img(w, int(150 * S), NAVY_D)
        wimgs.append(im.resize((int(im.width * 0.86), im.height), Image.LANCZOS))
    sp = int(26 * S)
    tw = sum(i.width for i in wimgs) + sp * (len(wimgs) - 1)
    th = max(i.height for i in wimgs)
    PW, PH = int(tw / 0.80), int(th * 2.25)
    M = int(40 * S)
    Wc, Hc = PW + 2 * M, PH + 2 * M
    rough, step = 3.2 * S, 5 * S

    # contorno do papel branco: retângulo reto rasgado; canto inferior direito arrancado em diagonal
    x0, y0, x1, y1 = M, M, M + PW, M + PH
    rip_start = x0 + PW * 0.60
    rip_rise = PH * 0.24

    def bottom(x):
        if x < rip_start:
            return y1
        return y1 - rip_rise * ((x - rip_start) / (x1 - rip_start)) ** 1.2

    import math

    def rounded_outline(ax0, ay0, ax1, ay1, r, warp=None, rough_=rough * 0.7, wave=2.8 * S):
        """Perímetro de retângulo com cantos arredondados, com rasgo: ondulação larga + fibras finas."""
        pts = []
        def arc(cx, cy, a0, a1):
            n = max(6, int(abs(a1 - a0) * r / (2.5 * S)))
            return [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n),
                     math.cos(a0 + (a1 - a0) * k / n), math.sin(a0 + (a1 - a0) * k / n)) for k in range(n)]
        def line(xa, ya, xb, yb, nx, ny):
            L = math.hypot(xb - xa, yb - ya); n = max(2, int(L / (2.5 * S)))
            return [(xa + (xb - xa) * k / n, ya + (yb - ya) * k / n, nx, ny) for k in range(n)]
        seq = (line(ax0 + r, ay0, ax1 - r, ay0, 0, -1) + arc(ax1 - r, ay0 + r, -math.pi / 2, 0)
               + line(ax1, ay0 + r, ax1, ay1 - r, 1, 0) + arc(ax1 - r, ay1 - r, 0, math.pi / 2)
               + line(ax1 - r, ay1, ax0 + r, ay1, 0, 1) + arc(ax0 + r, ay1 - r, math.pi / 2, math.pi)
               + line(ax0, ay1 - r, ax0, ay0 + r, -1, 0) + arc(ax0 + r, ay0 + r, math.pi, 1.5 * math.pi))
        off, ph = 0.0, rng.uniform(0, 6.28)
        out = []
        for k, (x, y, nx, ny) in enumerate(seq):
            if warp:
                x, y = warp(x, y)
            off = max(-rough_, min(rough_, off * 0.85 + rng.gauss(0, rough_ * 0.35)))
            w = wave * math.sin(k * 0.035 + ph) + wave * 0.6 * math.sin(k * 0.11 + ph * 2)
            d = off + w + (rng.uniform(0, rough_ * 0.6) if rng.random() < 0.08 else 0)
            out.append((x + nx * d, y + ny * d))
        return out

    R = PH * 0.10

    def rip(x, y):  # arranca o canto inferior direito do papel branco (nada de branco abaixo da linha do rasgo)
        if x > rip_start:
            t = min(1.0, (x - rip_start) / (x1 - rip_start))
            lim = y1 - rip_rise * (t ** 1.3)
            if y > lim:
                return x, lim
        return x, y

    paper_poly = rounded_outline(x0, y0, x1, y1, R, warp=rip)
    red_poly = rounded_outline(rip_start - 40 * S, y1 - rip_rise - 30 * S, x1 + 2 * S, y1 + 3 * S, R * 0.8,
                               rough_=rough * 0.8, wave=1.0 * S)

    def mask(poly, blur=0.6):
        m = Image.new("L", (Wc, Hc), 0)
        ImageDraw.Draw(m).polygon([(float(x), float(y)) for x, y in poly], fill=255)
        return m.filter(ImageFilter.GaussianBlur(blur * S))

    pm = mask(paper_poly)
    rm = mask(red_poly)
    nrng = np.random.default_rng(seed)

    def tex(color, grain=5, mottle=9):
        g = nrng.normal(0, grain, (Hc, Wc))
        m = nrng.normal(0, 1, (Hc // 24 + 2, Wc // 24 + 2))
        mi = Image.fromarray(((m - m.min()) / (np.ptp(m) + 1e-6) * 255).astype("uint8")).resize((Wc, Hc), Image.BICUBIC)
        mo = (np.asarray(mi, np.float32) / 255 - 0.5) * mottle
        arr = np.zeros((Hc, Wc, 3), np.float32) + color
        return (arr + (g + mo)[..., None]).clip(0, 255).astype("uint8")

    # escurece levemente perto das bordas do papel (volume)
    dist = np.asarray(pm.filter(ImageFilter.GaussianBlur(14 * S)), np.float32) / 255
    ptex = tex((244, 244, 241)).astype(np.float32)
    ptex = (ptex * (0.90 + 0.10 * dist[..., None])).clip(0, 255).astype("uint8")
    paper = Image.fromarray(ptex, "RGB").convert("RGBA")
    paper.putalpha(pm)
    rtex = tex((212, 18, 32), grain=6, mottle=14).astype(np.float32)
    yy = np.linspace(1.08, 0.85, Hc)[:, None, None]
    red = Image.fromarray((rtex * yy).clip(0, 255).astype("uint8"), "RGB").convert("RGBA")
    red.putalpha(rm)
    # fibras brancas saindo do rasgo
    fib = Image.new("L", (Wc, Hc), 0)
    fd = ImageDraw.Draw(fib)
    for x, y in paper_poly[::5]:
        cx, cy = x0 + PW / 2, y0 + PH / 2
        dx, dy = x - cx, y - cy
        n = (dx * dx + dy * dy) ** 0.5 + 1e-6
        L = rng.uniform(1, 4) * S
        fd.line([x, y, x + dx / n * L, y + dy / n * L], fill=rng.randint(140, 230), width=max(1, int(S)))
    fib = fib.filter(ImageFilter.GaussianBlur(0.4 * S))
    fiber = Image.new("RGBA", (Wc, Hc), (250, 250, 248, 0))
    fiber.putalpha(fib)
    # sombras
    def shadow(m, a, b, off):
        s_ = Image.new("RGBA", (Wc, Hc), (0, 0, 0, 0))
        s_.putalpha(m.point(lambda v: int(v * a)))
        return s_.filter(ImageFilter.GaussianBlur(b * S)), off

    c = Image.new("RGBA", (Wc, Hc), (0, 0, 0, 0))
    s1, o1 = shadow(rm, 0.30, 8, (int(1 * S), int(4 * S)))
    c.alpha_composite(s1, o1)
    c.alpha_composite(red)
    s2, o2 = shadow(pm, 0.32, 12, (int(1 * S), int(5 * S)))
    c.alpha_composite(s2, o2)
    c.alpha_composite(fiber)
    c.alpha_composite(paper)
    if not blank:
        x = x0 + (PW - tw) / 2
        yb = y0 + (PH - th) / 2 - PH * 0.02
        for im in wimgs:
            c.alpha_composite(im, (int(x), int(yb + (th - im.height))))
            x += im.width + sp
    c = c.crop(c.getbbox())
    if out:
        c.save(out)
    return c
