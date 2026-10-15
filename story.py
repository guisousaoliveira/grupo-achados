"""Gera imagens de story (1080x1920) no padrão Alpha Dominus: preto, dourado, Playfair + Inter.

  story_fixo(canal)            -> PNG do story fixo de divulgação do canal
  story_oferta(item, foto)     -> PNG de "prova": oferta que já saiu no canal
  story_venda(item, foto)      -> PNG para vender: foto, preço e vagas para os stickers de link (produto e canal)

Precisa do Pillow (pip install pillow). As fontes e o leão ficam em assets/.
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    "Promoção de verdade é assim.",
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
    "Mais uma promo do dia.",
]

# Textos que dá para mudar no painel (aba Stories > Personalizar). Fica guardado em estado["story_cfg"].
PADRAO = {
    "frases": FRASES,
    "cabecalho": "SAIU NO ALPHA PROMO",
    "mostrar_desconto": True,
    "mostrar_de": True,
    "fixo_subtitulo": "PROMOÇÕES PARA O HOMEM QUE SE CUIDA",
    "fixo_texto": "Chega de pagar mais caro. Encontramos os melhores preços da internet para você economizar de verdade.",
    "fixo_rotulo": "O QUE VOCÊ VAI ENCONTRAR NO CANAL",
    "fixo_itens": ["Promoções diárias: tech, estilo, treino e casa",
                   "As melhores ofertas da Amazon, Shopee e Mercado Livre",
                   "Cupons e preços conferidos, todo dia"],
    "fixo_chamada": "100% gratuito. Toque para entrar.",
}


def config(cfg=None):
    """Junta o que você mudou no painel com o padrão (campo vazio volta para o padrão)."""
    c = dict(PADRAO)
    for k, v in (cfg or {}).items():
        if k not in PADRAO:
            continue
        if isinstance(PADRAO[k], bool):
            c[k] = bool(v)
        elif isinstance(PADRAO[k], list):
            lista = [str(x).strip() for x in (v or []) if str(x).strip()]
            if k == "fixo_itens":
                lista = (lista + PADRAO[k])[:3] if lista else PADRAO[k]
            c[k] = lista or PADRAO[k]
        elif str(v or "").strip():
            c[k] = str(v).strip()
    return c


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


def pilula_canal(img, d, y, canal, altura=78):
    """Pílula dourada com o ícone do Telegram e t.me/canal (a mesma do story de prova, um pouco menor)."""
    alvo = f"t.me/{canal}"
    f = fonte("Inter-ExtraBold.ttf", 40)
    r = altura // 2 - 12
    w = d.textlength(alvo, font=f) + 2 * r + 20 + 76
    x0 = (L - w) / 2
    d.rounded_rectangle((x0, y, x0 + w, y + altura), radius=altura // 2, fill=DOURADO)
    tg = logo("telegram", 2 * r)
    if tg is not None:
        img.paste(tg, (int(x0 + 38), int(y + altura / 2 - r)), tg)
    d.text((x0 + 38 + 2 * r + 20, y + altura / 2), alvo, font=f, fill=PRETO, anchor="lm")


def png(img):
    saida = io.BytesIO()
    img.save(saida, "PNG", optimize=True)
    return saida.getvalue()


def icone(nome, altura):
    arq = PASTA / "icones" / f"{nome}.png"  # ícones Lucide (licença ISC), em dourado
    if not arq.exists():
        return None
    img = Image.open(arq).convert("RGBA")
    return img.resize((altura, altura), Image.LANCZOS)


def story_fixo(canal="", cfg=None):
    c = config(cfg)
    img = fundo()
    d = ImageDraw.Draw(img)
    lv = leao(240)
    img.paste(lv, ((L - lv.width) // 2, 140), lv)
    centro(d, 405, "ALPHA PROMO", fonte("Playfair-Black.ttf", 112), DOURADO)
    centro(d, 545, c["fixo_subtitulo"].upper(), fonte("Inter-SemiBold.ttf", 30), CINZA, espaco=3)
    divisor(d, 625)

    f = fonte("Inter-SemiBold.ttf", 42)
    y = 680
    for linha in quebrar(d, c["fixo_texto"], f, L - 150, 4):
        centro(d, y, linha, f, BRANCO)
        y += 56

    y += 44
    centro(d, y, c["fixo_rotulo"].upper(), fonte("Inter-ExtraBold.ttf", 28), DOURADO, espaco=3)
    y += 66
    itens = list(zip(("package", "shopping-cart", "ticket-percent"), c["fixo_itens"]))
    f = fonte("Inter-Regular.ttf", 38)
    x = 120
    for nome, texto in itens:
        ic = icone(nome, 56)
        linhas = quebrar(d, texto, f, L - x - 80 - 110, 2)
        altura = len(linhas) * 50
        if ic is not None:
            img.paste(ic, (x, int(y + (altura - 56) / 2) + 2), ic)
        for i, linha in enumerate(linhas):
            d.text((x + 80, y + i * 52), linha, font=f, fill=BRANCO)
        y += altura + 30
    rodape(img, d, canal, c["fixo_chamada"])
    return png(img)


def encaixar_foto(foto, largura, altura):
    """Foto do produto do tamanho da área: corta a borda branca em volta (fotos de loja costumam ter muita)
    e amplia fotos pequenas, para o produto não ficar minúsculo no meio do card."""
    f_img = Image.open(io.BytesIO(foto)).convert("RGB")
    fundo_ = Image.new("RGB", f_img.size, (255, 255, 255))
    caixa = ImageChops.difference(f_img, fundo_).convert("L").point(lambda v: 255 if v > 18 else 0).getbbox()
    if caixa:
        x0, y0, x1, y1 = caixa
        folga = int(max(x1 - x0, y1 - y0) * 0.04)
        f_img = f_img.crop((max(0, x0 - folga), max(0, y0 - folga), min(f_img.width, x1 + folga), min(f_img.height, y1 + folga)))
    escala = min(largura / f_img.width, altura / f_img.height, 4)  # amplia até 4x; mais que isso fica borrado
    return f_img.resize((max(1, int(f_img.width * escala)), max(1, int(f_img.height * escala))), Image.LANCZOS)


def tem_fundo_proprio(foto):
    """A foto já tem cenário (não é produto recortado no fundo branco)? Olha a borda da imagem."""
    f = Image.open(io.BytesIO(foto)).convert("L")
    f.thumbnail((200, 200))
    w, h = f.size
    borda = [f.getpixel((x, y)) for x in range(w) for y in (0, 1, h - 2, h - 1)]
    borda += [f.getpixel((x, y)) for y in range(h) for x in (0, 1, w - 2, w - 1)]
    claros = sum(1 for v in borda if v >= 235)
    return claros / len(borda) < 0.7


def foto_cheia(img, foto, caixa, raio):
    """Foto ocupando o card inteiro (corta o excesso pelo centro), com os cantos arredondados do card."""
    x0, y0, x1, y1 = caixa
    f_img = ImageOps.fit(Image.open(io.BytesIO(foto)).convert("RGB"), (x1 - x0, y1 - y0), Image.LANCZOS)
    mascara = Image.new("L", f_img.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, f_img.width - 1, f_img.height - 1), radius=raio, fill=255)
    img.paste(f_img, (x0, y0), mascara)


def usar_cheia(foto, encaixe):
    if encaixe == "cheio":
        return True
    if encaixe == "branco" or not foto:
        return False
    try:
        return tem_fundo_proprio(foto)
    except Exception:
        return False


def story_oferta(item, foto=None, canal="", frase=None, cfg=None, encaixe="auto"):
    c = config(cfg)
    img = fundo()
    d = ImageDraw.Draw(img)
    lv = leao(110)
    img.paste(lv, ((L - lv.width) // 2, 95), lv)
    centro(d, 230, c["cabecalho"].upper(), fonte("Inter-ExtraBold.ttf", 38), DOURADO, espaco=6)

    # Cartão branco com a foto do produto
    cx0, cy0, cx1, cy1 = 170, 320, L - 170, 960
    d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=40, fill=(255, 255, 255))
    if not foto:  # sem foto: o leão no lugar
        d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=40, fill=CARTAO, outline=(60, 50, 20), width=2)
        lv2 = leao(380)
        img.paste(lv2, ((L - lv2.width) // 2, cy0 + (cy1 - cy0 - lv2.height) // 2), lv2)
    if foto and usar_cheia(foto, encaixe):
        try:
            foto_cheia(img, foto, (cx0, cy0, cx1, cy1), 40)
            foto = None  # já colou
        except Exception:
            pass
    if foto:
        try:
            f_img = encaixar_foto(foto, cx1 - cx0 - 80, cy1 - cy0 - 80)
            img.paste(f_img, (cx0 + (cx1 - cx0 - f_img.width) // 2, cy0 + (cy1 - cy0 - f_img.height) // 2))
        except Exception:
            pass
    de, por = item.get("de"), item.get("por")
    if de and por and de > por and c["mostrar_desconto"]:
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
    if de and por and de > por and c["mostrar_de"]:
        f_de = fonte("Inter-Regular.ttf", 42)
        t = f"de {reais(de)}"
        w = d.textlength(t, font=f_de)
        x = (L - w) / 2
        d.text((x, y), t, font=f_de, fill=CINZA)
        d.line((x + d.textlength("de ", font=f_de), y + 28, x + w, y + 28), fill=CINZA, width=3)
        y += 62
    if por:
        centro(d, y - 10, f"por {reais(por)}", fonte("Playfair-Black.ttf", 104), DOURADO)
    rodape(img, d, canal, frase or random.choice(c["frases"]))
    return png(img)


def topo_frase(d, y, texto, largura=L - 180):
    """Frase do topo do story de venda: caixa alta espaçada, diminui a fonte até caber (até 2 linhas)."""
    texto = texto.upper()
    for tam in (34, 31, 28, 26):
        f = fonte("Inter-ExtraBold.ttf", tam)
        esp = max(3, tam // 5)
        if sum(d.textlength(ch, font=f) for ch in texto) + esp * len(texto) <= largura:
            centro(d, y, texto, f, DOURADO, espaco=esp)
            return
    f = fonte("Inter-ExtraBold.ttf", 28)
    for i, linha in enumerate(quebrar(d, texto, f, largura - 60, 2)):
        centro(d, y - 6 + i * 38, linha, f, DOURADO, espaco=3)


def vaga_sticker(d, y, w, h, legenda, dica):
    """Legenda e um espaço tracejado onde você cola o sticker de link do Instagram."""
    centro(d, y, legenda, fonte("Inter-ExtraBold.ttf", 30), DOURADO, espaco=4)
    x0, y0, cor = (L - w) / 2, y + 58, (110, 92, 30)
    for x in range(int(x0 + h / 2), int(x0 + w - h / 2), 26):
        d.line((x, y0, x + 14, y0), fill=cor, width=3)
        d.line((x, y0 + h, x + 14, y0 + h), fill=cor, width=3)
    d.arc((x0, y0, x0 + h, y0 + h), 90, 270, fill=cor, width=3)
    d.arc((x0 + w - h, y0, x0 + w, y0 + h), 270, 90, fill=cor, width=3)
    f = fonte("Inter-Regular.ttf", 28)
    d.text(((L - d.textlength(dica, font=f)) / 2, y0 + h / 2), dica, font=f, fill=(95, 95, 95), anchor="lm")
    return y0 + h


def story_venda(item, foto=None, canal="", frase=None, cfg=None, encaixe="auto", link="produto"):
    """Story mais limpo, para vender: a frase sorteada vai no topo e embaixo fica a vaga de UM sticker de link
    (o Instagram só deixa um por story). link="produto": seu link de afiliado, e o canal vai escrito embaixo.
    link="canal": o link do canal, avisando que o link do produto está lá."""
    c = config(cfg)
    img = fundo()
    d = ImageDraw.Draw(img)
    lv = leao(76)
    img.paste(lv, ((L - lv.width) // 2, 150), lv)
    topo_frase(d, 245, frase or random.choice(c["frases"]))

    cx0, cy0, cx1, cy1 = 120, 320, L - 120, 975
    d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=44, fill=(255, 255, 255))
    if foto and usar_cheia(foto, encaixe):
        try:
            foto_cheia(img, foto, (cx0, cy0, cx1, cy1), 44)
        except Exception:
            foto = None
    elif foto:
        try:
            topo = cy0 + 105  # abaixo do selo da loja e do desconto
            f_img = encaixar_foto(foto, cx1 - cx0 - 90, cy1 - topo - 40)
            img.paste(f_img, (cx0 + (cx1 - cx0 - f_img.width) // 2, topo + (cy1 - 40 - topo - f_img.height) // 2))
        except Exception:
            foto = None
    if not foto:
        d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=44, fill=CARTAO, outline=(60, 50, 20), width=2)
        lv2 = leao(360)
        img.paste(lv2, ((L - lv2.width) // 2, cy0 + (cy1 - cy0 - lv2.height) // 2), lv2)
    de, por = item.get("de"), item.get("por")
    if de and por and de > por and c["mostrar_desconto"]:
        f = fonte("Inter-ExtraBold.ttf", 50)
        t = f"-{round((1 - por / de) * 100)}%"
        w = d.textlength(t, font=f) + 60
        d.rounded_rectangle((cx1 - w - 24, cy0 + 24, cx1 - 24, cy0 + 110), radius=43, fill=DOURADO)
        d.text((cx1 - w + 6, cy0 + 67), t, font=f, fill=PRETO, anchor="lm")
    selo_loja(img, item.get("loja", ""), cx0 + 24, cy0 + 30, 80)

    f_nome = fonte("Inter-SemiBold.ttf", 44)
    y = 1010
    for linha in quebrar(d, item.get("nome", ""), f_nome, L - 220, 2):
        centro(d, y, linha, f_nome, BRANCO)
        y += 58
    y += 18
    if de and por and de > por and c["mostrar_de"]:
        f_de = fonte("Inter-Regular.ttf", 38)
        t = f"de {reais(de)}"
        w = d.textlength(t, font=f_de)
        x = (L - w) / 2
        d.text((x, y), t, font=f_de, fill=CINZA)
        d.line((x + d.textlength("de ", font=f_de), y + 25, x + w, y + 25), fill=CINZA, width=3)
        y += 52
    if por:
        centro(d, y - 8, reais(por), fonte("Playfair-Black.ttf", 112), DOURADO)

    f_pe = fonte("Inter-Regular.ttf", 32)
    if link == "canal":
        fim = vaga_sticker(d, 1400, 640, 120, "▼  LINK DESSA PROMO NO CANAL  ▼", "cole aqui o link do canal")
        centro(d, fim + 50, "e mais promoções todo dia", f_pe, CINZA)
    else:
        fim = vaga_sticker(d, 1378, 640, 108, "▼  TOQUE PARA COMPRAR  ▼", "cole aqui o link do produto")
        if canal:  # o canal vai na pílula do Telegram (sem clique: o Instagram só deixa um link por story)
            centro(d, fim + 40, "MAIS PROMOÇÕES TODO DIA NO CANAL", fonte("Inter-ExtraBold.ttf", 26), CINZA, espaco=3)
            pilula_canal(img, d, fim + 76, canal)
    return png(img)
