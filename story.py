"""Gera imagens de story (1080x1920) no padrão Alpha Dominus: preto, dourado, Playfair + Inter.

  story_fixo(canal)            -> PNG do story fixo de divulgação do canal
  story_oferta(item, foto)     -> PNG de "prova": oferta que já saiu no canal

Precisa do Pillow (pip install pillow). As fontes e o leão ficam em assets/.
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PASTA = Path(__file__).resolve().parent / "assets"
L, A = 1080, 1920
PRETO = (10, 10, 10)
CARTAO = (22, 22, 22)
DOURADO = (212, 175, 55)
DOURADO_ESCURO = (181, 149, 47)
BRANCO = (245, 245, 245)
CINZA = (150, 150, 150)
# Faixa de baixo fica livre para o sticker de link do Instagram.
FAIXA_STICKER = 280

# Sorteada a cada /story (no máximo ~30 caracteres para caber numa linha).
FRASES = [
    "Quem estava no canal pegou.",
    "Saiu primeiro no canal.",
    "Quem viu, garantiu.",
    "Esse já voou no canal.",
    "Promoção assim não espera.",
    "No canal chega antes.",
    "Pegou quem estava de olho.",
    "Achado de verdade é assim.",
    "Desconto real, conferido.",
    "Quem chegou cedo levou.",
    "Ofertas assim saem todo dia.",
    "Não perca a próxima.",
    "O próximo pode ser seu.",
    "Ativa as notificações.",
    "Preço baixo dura pouco.",
    "Isso aqui é o Alpha Promo.",
    "Economia de homem esperto.",
    "Entra antes da próxima.",
    "Só quem segue vê a tempo.",
    "Mais um achado do dia.",
]

LOGOS = PASTA / "logos"  # logos oficiais (PNG transparente): amazon, mercadolivre, shopee, telegram


def fonte(nome, tamanho):
    return ImageFont.truetype(str(PASTA / "fontes" / nome), tamanho)


def reais(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fundo():
    img = Image.new("RGB", (L, A), PRETO)
    brilho = Image.new("L", (L, A), 0)
    d = ImageDraw.Draw(brilho)
    d.ellipse((-200, -300, L + 200, 900), fill=70)
    brilho = brilho.filter(ImageFilter.GaussianBlur(220))
    ouro = Image.new("RGB", (L, A), (60, 48, 12))
    img = Image.composite(ouro, img, brilho)
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, L - 40, A - 40), outline=(60, 50, 20), width=2)
    return img


def leao(altura):
    img = Image.open(PASTA / "leao.png").convert("RGBA")
    return img.resize((int(img.width * altura / img.height), altura), Image.LANCZOS)


def centro(d, y, texto, f, cor, espaco=0):
    if espaco:
        largura = sum(d.textlength(c, font=f) for c in texto) + espaco * (len(texto) - 1)
        x = (L - largura) / 2
        for c in texto:
            d.text((x, y), c, font=f, fill=cor)
            x += d.textlength(c, font=f) + espaco
        return
    w = d.textlength(texto, font=f)
    d.text(((L - w) / 2, y), texto, font=f, fill=cor)


def quebrar(d, texto, f, largura, max_linhas=3):
    palavras, linhas, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if d.textlength(teste, font=f) <= largura:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    if len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        while d.textlength(linhas[-1] + "…", font=f) > largura and " " in linhas[-1]:
            linhas[-1] = linhas[-1].rsplit(" ", 1)[0]
        linhas[-1] += "…"
    return linhas


def divisor(d, y, largura=260):
    d.line(((L - largura) / 2, y, (L + largura) / 2, y), fill=DOURADO, width=3)
    d.regular_polygon((L / 2, y, 9), 4, fill=DOURADO)


def logo(nome, altura):
    arq = LOGOS / f"{nome}.png"
    if not arq.exists():
        return None
    img = Image.open(arq).convert("RGBA")
    return img.resize((int(img.width * altura / img.height), altura), Image.LANCZOS)


def selo_loja(img, loja, x, y, altura=72):
    """Selo branco pequeno com o logo da loja (Mercado Livre e Shopee levam o nome ao lado do ícone)."""
    lg = logo(loja, {"amazon": 34, "mercadolivre": 46, "shopee": 44}.get(loja, 44))
    if lg is None:
        return
    nome, cor = {"mercadolivre": ("Mercado Livre", (45, 50, 119)), "shopee": ("Shopee", (238, 77, 45))}.get(loja, ("", None))
    d = ImageDraw.Draw(img)
    f = fonte("Inter-ExtraBold.ttf", 28)
    w = lg.width + 48 + (d.textlength(nome, font=f) + 12 if nome else 0)
    d.rounded_rectangle((x, y, x + w, y + altura), radius=altura // 2, fill=(255, 255, 255), outline=(60, 50, 20), width=2)
    img.paste(lg, (int(x + 24), int(y + (altura - lg.height) / 2 + (4 if loja == "amazon" else 0))), lg)
    if nome:
        d.text((x + 24 + lg.width + 12, y + altura / 2), nome, font=f, fill=cor, anchor="lm")


def rodape(img, d, canal, chamada):
    y = A - FAIXA_STICKER - 210
    centro(d, y, chamada, fonte("Inter-SemiBold.ttf", 40), BRANCO)
    alvo = f"t.me/{canal}" if canal else "Canal no Telegram"
    f = fonte("Inter-ExtraBold.ttf", 46)
    tw = d.textlength(alvo, font=f)
    r = 27
    w = tw + 2 * r + 22 + 90
    x0 = (L - w) / 2
    d.rounded_rectangle((x0, y + 75, x0 + w, y + 165), radius=45, fill=DOURADO)
    tg = logo("telegram", 2 * r)
    if tg is not None:
        img.paste(tg, (int(x0 + 45), int(y + 120 - r)), tg)
    d.text((x0 + 45 + 2 * r + 22, y + 120), alvo, font=f, fill=PRETO, anchor="lm")
    centro(d, A - FAIXA_STICKER + 40, "▼  toque no link  ▼", fonte("Inter-Regular.ttf", 30), DOURADO_ESCURO)


def png(img):
    saida = io.BytesIO()
    img.save(saida, "PNG", optimize=True)
    return saida.getvalue()


def story_fixo(canal=""):
    img = fundo()
    d = ImageDraw.Draw(img)
    lv = leao(400)
    img.paste(lv, ((L - lv.width) // 2, 170), lv)
    centro(d, 640, "ALPHA PROMO", fonte("Playfair-Black.ttf", 118), DOURADO)
    centro(d, 790, "ACHADOS PARA O HOMEM QUE SE CUIDA", fonte("Inter-SemiBold.ttf", 30), CINZA, espaco=3)
    divisor(d, 880)
    itens = ["Promoção de verdade, conferida", "Amazon, Mercado Livre e Shopee", "Academia, estilo, cuidados e gadgets", "Cupons que funcionam, todo dia"]
    f = fonte("Inter-Regular.ttf", 44)
    y = 950
    for t in itens:
        x = 170
        d.regular_polygon((x, y + 28, 11), 4, fill=DOURADO)
        d.text((x + 38, y), t, font=f, fill=BRANCO)
        y += 92
    rodape(img, d, canal, "Entra no canal. É de graça.")
    return png(img)


def story_oferta(item, foto=None, canal="", frase=None):
    img = fundo()
    d = ImageDraw.Draw(img)
    lv = leao(110)
    img.paste(lv, ((L - lv.width) // 2, 95), lv)
    centro(d, 230, "SAIU NO ALPHA PROMO", fonte("Inter-ExtraBold.ttf", 38), DOURADO, espaco=6)

    # Cartão branco com a foto do produto
    cx0, cy0, cx1, cy1 = 170, 320, L - 170, 960
    d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=40, fill=(255, 255, 255))
    if not foto:  # sem foto: o leão no lugar
        d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=40, fill=CARTAO, outline=(60, 50, 20), width=2)
        lv2 = leao(380)
        img.paste(lv2, ((L - lv2.width) // 2, cy0 + (cy1 - cy0 - lv2.height) // 2), lv2)
    if foto:
        try:
            f_img = Image.open(io.BytesIO(foto)).convert("RGB")
            f_img.thumbnail((cx1 - cx0 - 80, cy1 - cy0 - 80), Image.LANCZOS)
            img.paste(f_img, (cx0 + (cx1 - cx0 - f_img.width) // 2, cy0 + (cy1 - cy0 - f_img.height) // 2))
        except Exception:
            pass
    de, por = item.get("de"), item.get("por")
    if de and por and de > por:
        off = round((1 - por / de) * 100)
        f = fonte("Inter-ExtraBold.ttf", 52)
        texto = f"-{off}%"
        w = d.textlength(texto, font=f) + 60
        d.rounded_rectangle((cx1 - w + 30, cy0 - 35, cx1 + 30, cy0 + 55), radius=45, fill=DOURADO)
        d.text((cx1 - w + 60, cy0 - 25), texto, font=f, fill=PRETO)
    selo_loja(img, item.get("loja", ""), cx0 - 30, cy0 - 36)

    f_nome = fonte("Inter-SemiBold.ttf", 46)
    y = 1000
    for linha in quebrar(d, item.get("nome", ""), f_nome, L - 200, 2):
        centro(d, y, linha, f_nome, BRANCO)
        y += 60
    y += 20
    if de and por and de > por:
        f_de = fonte("Inter-Regular.ttf", 42)
        t = f"de {reais(de)}"
        w = d.textlength(t, font=f_de)
        x = (L - w) / 2
        d.text((x, y), t, font=f_de, fill=CINZA)
        d.line((x + d.textlength("de ", font=f_de), y + 28, x + w, y + 28), fill=CINZA, width=3)
        y += 62
    if por:
        centro(d, y - 10, f"por {reais(por)}", fonte("Playfair-Black.ttf", 104), DOURADO)
    rodape(img, d, canal, frase or random.choice(FRASES))
    return png(img)
