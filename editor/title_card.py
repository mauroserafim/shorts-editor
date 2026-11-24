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
