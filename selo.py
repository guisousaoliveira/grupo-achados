"""Selo do curador na foto da oferta: fotinha do deus da seção + nome na cor dele, discreto, no canto inferior direito.
Mesmo estilo do site Ofertas do Olimpo (medalhões em assets/deuses, Cinzel, cores das seções)."""
import io
import os

from PIL import Image, ImageDraw, ImageFont

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
CURADORES = {  # seção: (deus, arquivo do medalhão, cor da seção no site)
    "academia": ("Ares", "ares", "#b8432e"), "relogios": ("Cronos", "cronos", "#4f6fa8"),
    "barba": ("Apolo", "apolo", "#d9862a"), "casa": ("Héstia", "hestia", "#6f8c45"),
    "tech": ("Atena", "atena", "#2f8a8f"), "carro": ("Hefesto", "hefesto", "#7d8790"),
    "estilo": ("Perseu", "perseu", "#8a55a8"), "outros": ("Zeus", "todas", "#c9a03a"),
}
PAPEL = (242, 233, 214)
OPACIDADE = 0.72  # semi-transparente: o produto aparece por baixo
TAMANHO = 0.85


def _rgb(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def carimbar(dados, secao):
    """Devolve o JPEG com o selo do curador. Sem seção conhecida, devolve a foto como veio."""
    if secao not in CURADORES or not dados:
        return dados
    deus, arquivo, cor = CURADORES[secao]
    cor = _rgb(cor)
    foto = Image.open(io.BytesIO(dados)).convert("RGBA")
    largura, altura = foto.size
    u = max(largura, 400) / 1000 * TAMANHO
    fonte = ImageFont.truetype(os.path.join(PASTA, "fontes", "Cinzel-Bold.ttf"), round(28 * u))
    letras, espaco = deus.upper(), 28 * u * 0.14
    larguras = [fonte.getlength(c) for c in letras]
    caixa = fonte.getbbox(letras)
    texto_w = round(sum(larguras) + espaco * (len(letras) - 1))
    h = round(64 * u)
    diam, pad = h - round(8 * u), round(4 * u)
    w = pad + diam + round(12 * u) + texto_w + round(20 * u)
    selo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(selo)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=PAPEL + (200,), outline=cor + (230,), width=max(1, round(2 * u)))
    # medalhão redondo com aro na cor do deus
    med = Image.open(os.path.join(PASTA, "deuses", f"{arquivo}.png")).convert("RGBA").resize((diam, diam), Image.LANCZOS)
    fundo = Image.new("RGBA", (diam, diam), PAPEL + (255,))
    fundo.alpha_composite(med)
    mascara = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, diam - 1, diam - 1), fill=255)
    selo.paste(fundo, (pad, pad), mascara)
    d.ellipse((pad, pad, pad + diam - 1, pad + diam - 1), outline=cor + (255,), width=max(1, round(2 * u)))
    x, y = pad + diam + round(12 * u), (h - (caixa[3] - caixa[1])) / 2 - caixa[1]
    for c, lc in zip(letras, larguras):
        d.text((x, y), c, font=fonte, fill=cor + (255,))
        x += lc + espaco
    selo.putalpha(selo.getchannel("A").point(lambda v: round(v * OPACIDADE)))
    margem = round(22 * u)
    foto.alpha_composite(selo, (largura - w - margem, altura - h - margem))
    saida = io.BytesIO()
    foto.convert("RGB").save(saida, "JPEG", quality=92)
    return saida.getvalue()
