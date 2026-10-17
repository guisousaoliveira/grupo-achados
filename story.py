"""Gera imagens de story (1080x1920) no visual do site Ofertas do Olimpo (alphadominustv.com/promocoes, tema escuro):
papel escuro, dourado, gravuras de templo e colunas, medalhões dos deuses curadores, Cinzel + Montserrat + Inter.

  story_fixo(canal)            -> PNG do story fixo de divulgação do canal
  story_oferta(item, foto)     -> PNG de "prova": oferta que já saiu no canal
  story_venda(item, foto)      -> PNG para vender: foto, preço e vagas para os stickers de link (produto e canal)

Precisa do Pillow (pip install pillow). Fontes em assets/fontes, gravuras e marca em assets/olimpo, medalhões em assets/deuses.
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

PASTA = Path(__file__).resolve().parent / "assets"
L, A = 1080, 1920
# Cores do tema escuro do site (variáveis --papel, --ouro, --txt... de promocoes.html)
PAPEL = (18, 15, 10)
SUP = (29, 25, 18)
PLACA = (239, 230, 210)       # fundo claro do card da foto
TXT = (241, 232, 212)
TXT2 = (198, 183, 151)
DIM = (143, 129, 104)
OURO = (212, 175, 55)
OURO_VIV = (230, 194, 90)
GRAV = (168, 133, 63)         # cor das gravuras
TINTA = (20, 15, 5)
LINHA = (92, 76, 27)          # dourado a 38% sobre o papel
PRETO, CARTAO, DOURADO, DOURADO_ESCURO, BRANCO, CINZA = TINTA, SUP, OURO, GRAV, TXT, TXT2
OLIMPO = PASTA / "olimpo"
CURADORES = {  # seção do site: (deus, medalhão, cor da seção, nome da seção) — o mesmo do selo.py e do site
    "academia": ("Ares", "ares", (184, 67, 46), "Academia"), "relogios": ("Cronos", "cronos", (79, 111, 168), "Relógios"),
    "barba": ("Apolo", "apolo", (217, 134, 42), "Barba"), "casa": ("Héstia", "hestia", (111, 140, 69), "Casa"),
    "tech": ("Atena", "atena", (47, 138, 143), "Tech"), "carro": ("Hefesto", "hefesto", (125, 135, 144), "Carro"),
    "estilo": ("Perseu", "perseu", (138, 85, 168), "Estilo"), "outros": ("Zeus", "todas", (201, 160, 58), "Todas"),
}
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
    "Isso aqui é o Olimpo.",
    "Economia de homem esperto.",
    "Entra antes da próxima.",
    "Só quem segue vê a tempo.",
    "Mais uma promo do dia.",
    "Escolhida pela curadoria.",
    "Preço conferido na loja.",
]

# Textos que dá para mudar no painel (aba Stories > Personalizar). Fica guardado em estado["story_cfg"].
PADRAO = {
    "frases": FRASES,
    "cabecalho": "SAIU NO OLIMPO",
    "mostrar_desconto": True,
    "mostrar_de": True,
    "fixo_subtitulo": "PROMOÇÕES PARA O HOMEM QUE SABE O QUE COMPRA",
    "fixo_texto": "Academia, relógio, barba, casa, tech, carro e estilo. O preço é conferido na loja antes de publicar.",
    "fixo_rotulo": "O QUE VOCÊ VAI ENCONTRAR NO CANAL",
    "fixo_itens": ["Promoções todo dia, das 8h às 23h",
                   "Amazon, Mercado Livre e Shopee",
                   "Cupons e preço no Pix avisados no post"],
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


def _gravura(nome, largura, cor, opacidade, espelhar=False):
    """Gravura do site (templo, colunas, céu) como no CSS: a forma vira máscara pintada numa cor só."""
    m = Image.open(OLIMPO / f"{nome}.png").convert("L")
    m = m.resize((largura, round(m.height * largura / m.width)), Image.LANCZOS)
    if espelhar:
        m = ImageOps.mirror(m)
    camada = Image.new("RGBA", m.size, cor + (0,))
    camada.putalpha(m.point(lambda v: round(v * opacidade)))
    return camada


_FUNDO = []


def fundo():
    """Papel escuro do site com o céu de colunata em cima, colunas nas laterais, grão e moldura fina dourada.
    É sempre igual: monta uma vez e devolve uma cópia."""
    if not _FUNDO:
        _FUNDO.append(_montar_fundo())
    return _FUNDO[0].copy()


def _montar_fundo():
    img = Image.new("RGBA", (L, A), PAPEL + (255,))
    brilho = Image.new("L", (L, A), 0)
    ImageDraw.Draw(brilho).ellipse((-240, -420, L + 240, 760), fill=46)
    brilho = brilho.filter(ImageFilter.GaussianBlur(200))
    img = Image.composite(Image.new("RGBA", (L, A), (70, 55, 18, 255)), img, brilho)
    img.alpha_composite(_gravura("ceu", 1240, OURO, 0.13), (-80, -30))
    col = _gravura("colunas", 360, GRAV, 0.30)
    img.alpha_composite(col, (-110, A - col.height + 60))
    col_d = _gravura("colunas", 360, GRAV, 0.17, espelhar=True)
    img.alpha_composite(col_d, (L - col_d.width + 110, A - col_d.height + 60))
    grao = Image.open(OLIMPO / "grao.png").convert("RGBA")
    grao.putalpha(grao.getchannel("A").point(lambda v: round(v * 0.32)))
    for x in range(0, L, grao.width):
        for y in range(0, A, grao.height):
            img.alpha_composite(grao, (x, y))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, L - 40, A - 40), outline=LINHA, width=2)
    d.rectangle((52, 52, L - 52, A - 52), outline=(40, 33, 15), width=1)
    return img


def marca_icone(altura, cor=OURO):
    """Medalhão da marca (raposa de Hermes) pintado de dourado, como o .icone do site."""
    m = Image.open(OLIMPO / "marca.png").convert("LA").getchannel("A")
    m = m.resize((altura, altura), Image.LANCZOS)
    ic = Image.new("RGBA", (altura, altura), cor + (0,))
    ic.putalpha(m)
    return ic


def marca(img, y, icone=84, grande=50, pequeno=21):
    """OFERTAS / DO OLIMPO ao lado do medalhão, centralizado, igual ao topo do site."""
    d = ImageDraw.Draw(img)
    fb, fs = fonte("Montserrat-ExtraBold.ttf", grande), fonte("Montserrat-ExtraBold.ttf", pequeno)
    esp = round(pequeno * 0.32)
    wb = d.textlength("OFERTAS", font=fb) + grande * 0.02 * 6
    ws = sum(d.textlength(c, font=fs) for c in "DO OLIMPO") + esp * 8
    gap = round(icone * 0.22)
    total = icone + gap + max(wb, ws)
    x = round((L - total) / 2)
    ic = marca_icone(icone)
    img.paste(ic, (x, y), ic)
    tx = x + icone + gap
    ty = y + (icone - (grande * 0.9 + pequeno + pequeno * 0.4)) / 2
    for c in "OFERTAS":
        d.text((tx, ty), c, font=fb, fill=TXT, anchor="la")
        tx += d.textlength(c, font=fb) + grande * 0.02
    tx = x + icone + gap
    ty2 = ty + grande * 0.9 + pequeno * 0.45
    for c in "DO OLIMPO":
        d.text((tx, ty2), c, font=fs, fill=OURO)
        tx += d.textlength(c, font=fs) + esp
    return y + icone


def medalhao(secao, diam, aro=4):
    """Medalhão redondo do deus da seção, com aro na cor da seção (o mesmo do site)."""
    deus, arq, cor, _ = CURADORES.get(secao, CURADORES["outros"])
    med = Image.open(PASTA / "deuses" / f"{arq}.png").convert("RGBA").resize((diam, diam), Image.LANCZOS)
    base = Image.new("RGBA", (diam, diam), PLACA + (255,))
    base.alpha_composite(med)
    mascara = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, diam - 1, diam - 1), fill=255)
    saida = Image.new("RGBA", (diam, diam), (0, 0, 0, 0))
    saida.paste(base, (0, 0), mascara)
    ImageDraw.Draw(saida).ellipse((1, 1, diam - 2, diam - 2), outline=cor + (255,), width=aro)
    return saida


def secao_do(item):
    if item.get("secao") in CURADORES:
        return item["secao"]
    try:
        import bot
        return bot.secao_site(item)
    except Exception:
        return "outros"


def selo_curador(img, secao, x1, y1):
    """Selo do curador no canto de baixo do card (o mesmo das fotos do canal): medalhão + nome do deus."""
    deus, _, cor, _ = CURADORES.get(secao, CURADORES["outros"])
    d = ImageDraw.Draw(img)
    f = fonte("Cinzel-ExtraBold.ttf", 30)
    letras, esp = deus.upper(), 4
    tw = sum(d.textlength(c, font=f) for c in letras) + esp * (len(letras) - 1)
    h, diam = 70, 60
    w = 5 + diam + 14 + tw + 24
    x0, y0 = x1 - w, y1 - h
    d.rounded_rectangle((x0, y0, x1, y1), radius=h // 2, fill=PLACA, outline=cor, width=3)
    med = medalhao(secao, diam, 3)
    img.paste(med, (int(x0 + 5), int(y0 + 5)), med)
    x = x0 + 5 + diam + 14
    for c in letras:
        d.text((x, y0 + h / 2 + 2), c, font=f, fill=cor, anchor="lm")
        x += d.textlength(c, font=f) + esp


def leao(altura):
    """Mantido para quem ainda chama: agora devolve o medalhão da marca."""
    return marca_icone(altura)


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


def legenda_setas(d, y, texto, f, cor, espaco=4):
    """Texto centralizado entre dois triângulos desenhados (a Cinzel não tem o ▼)."""
    tw = sum(d.textlength(c, font=f) for c in texto) + espaco * (len(texto) - 1)
    lado, gap = round(f.size * 0.62), round(f.size * 0.75)
    x = (L - tw) / 2
    cy = y + f.size * 0.55
    for cx in (x - gap - lado / 2, x + tw + gap + lado / 2):
        d.polygon(((cx - lado / 2, cy - lado * 0.38), (cx + lado / 2, cy - lado * 0.38), (cx, cy + lado * 0.48)), fill=cor)
    centro(d, y, texto, f, cor, espaco=espaco)


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


def equilibrar(d, texto, f, largura, max_linhas=3):
    """Como quebrar(), mas com as linhas do mesmo tamanho (sem uma palavra sozinha na última)."""
    linhas = quebrar(d, texto, f, largura, max_linhas)
    n, w = len(linhas), largura
    while n > 1 and w > largura * 0.5:
        teste = quebrar(d, texto, f, w - 10, max_linhas)
        if len(teste) > n or teste[-1].endswith("…"):
            break
        linhas, w = teste, w - 10
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
    centro(d, y, chamada, fonte("Inter-SemiBold.ttf", 40), TXT)
    pilula_canal(img, d, y + 75, canal, altura=92, tamanho=46)
    legenda_setas(d, A - FAIXA_STICKER + 36, "TOQUE NO LINK", fonte("Cinzel-ExtraBold.ttf", 28), GRAV, espaco=5)


def pilula_canal(img, d, y, canal, altura=78, tamanho=40):
    """Botão claro com o ícone do Telegram e t.me/canal (o botão "Entrar no Telegram" do site, no tema escuro)."""
    alvo = f"t.me/{canal}" if canal else "Canal no Telegram"
    f = fonte("Inter-ExtraBold.ttf", tamanho)
    r = altura // 2 - 12
    w = d.textlength(alvo, font=f) + 2 * r + 20 + 76
    x0 = (L - w) / 2
    d.rounded_rectangle((x0, y, x0 + w, y + altura), radius=altura // 2, fill=PLACA)
    tg = logo("telegram", 2 * r)
    if tg is not None:
        img.paste(tg, (int(x0 + 38), int(y + altura / 2 - r)), tg)
    d.text((x0 + 38 + 2 * r + 20, y + altura / 2), alvo, font=f, fill=TINTA, anchor="lm")


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
    ic = marca_icone(230)
    img.paste(ic, ((L - ic.width) // 2, 120), ic)
    fb = fonte("Montserrat-ExtraBold.ttf", 118)
    centro(d, 372, "OFERTAS", fb, TXT, espaco=3)
    centro(d, 508, "DO OLIMPO", fonte("Montserrat-ExtraBold.ttf", 44), OURO, espaco=14)
    f_sub = fonte("Cinzel-ExtraBold.ttf", 30)
    for i, linha in enumerate(equilibrar(d, c["fixo_subtitulo"].upper(), f_sub, L - 240, 2)):
        centro(d, 592 + i * 44, linha, f_sub, TXT2, espaco=3)
    divisor(d, 700)

    f = fonte("Inter-SemiBold.ttf", 38)
    y = 740
    for linha in equilibrar(d, c["fixo_texto"], f, L - 170, 3):
        centro(d, y, linha, f, TXT)
        y += 52

    # os curadores do canal, como na faixa de seções do site
    y += 34
    centro(d, y, c["fixo_rotulo"].upper(), fonte("Cinzel-ExtraBold.ttf", 27), OURO, espaco=4)
    y += 62
    secoes = ["academia", "relogios", "barba", "casa", "tech", "carro", "estilo"]
    diam, passo = 118, 136
    f_s, f_d = fonte("Inter-SemiBold.ttf", 25), fonte("Cinzel-ExtraBold.ttf", 22)
    x0 = (L - passo * len(secoes) + (passo - diam)) / 2
    for i, sec in enumerate(secoes):
        deus, _, cor, nome = CURADORES[sec]
        x = x0 + i * passo
        med = medalhao(sec, diam, 4)
        img.paste(med, (int(x), int(y)), med)
        cx = x + diam / 2
        d.text((cx, y + diam + 14), nome, font=f_s, fill=TXT, anchor="ma")
        d.text((cx, y + diam + 48), deus.upper(), font=f_d, fill=OURO, anchor="ma")
    y += diam + 104

    f = fonte("Inter-Regular.ttf", 34)
    x = (L - max(d.textlength(t, font=f) for t in c["fixo_itens"]) - 34) / 2
    for texto in c["fixo_itens"]:  # lista alinhada à esquerda, o bloco no centro
        d.regular_polygon((x + 8, y + 22, 8), 4, fill=OURO)
        d.text((x + 34, y), texto, font=f, fill=TXT2)
        y += 54
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


def card_produto(img, item, foto, caixa, encaixe, c, raio=44):
    """Card claro com a foto (fundo branco da loja some no papel claro, como no site), desconto, loja e curador."""
    d = ImageDraw.Draw(img)
    cx0, cy0, cx1, cy1 = caixa
    d.rounded_rectangle(caixa, radius=raio, fill=PLACA, outline=LINHA, width=2)
    if foto and usar_cheia(foto, encaixe):
        try:
            foto_cheia(img, foto, caixa, raio)
        except Exception:
            foto = None
    elif foto:
        try:
            topo = cy0 + 105  # abaixo do selo da loja e do desconto
            f_img = encaixar_foto(foto, cx1 - cx0 - 90, cy1 - topo - 100)
            px, py = cx0 + (cx1 - cx0 - f_img.width) // 2, topo + (cy1 - 90 - topo - f_img.height) // 2
            area = img.crop((px, py, px + f_img.width, py + f_img.height))
            img.paste(ImageChops.multiply(area, f_img), (px, py))  # branco da foto vira o creme do card
        except Exception:
            foto = None
    if not foto:
        ic = marca_icone(300, GRAV)
        img.paste(ic, ((cx0 + cx1 - ic.width) // 2, (cy0 + cy1 - ic.height) // 2), ic)
    de, por = item.get("de"), item.get("por")
    if de and por and de > por and c["mostrar_desconto"]:
        f = fonte("Inter-ExtraBold.ttf", 50)
        t = f"−{round((1 - por / de) * 100)}%"
        w = d.textlength(t, font=f) + 60
        d.rounded_rectangle((cx1 - w - 24, cy0 + 24, cx1 - 24, cy0 + 110), radius=43, fill=TINTA)
        d.text((cx1 - w + 6, cy0 + 67), t, font=f, fill=TXT, anchor="lm")
    selo_loja(img, item.get("loja", ""), cx0 + 24, cy0 + 30, 80)
    selo_curador(img, secao_do(item), cx1 - 24, cy1 - 24)


def nome_e_preco(img, item, y, c, f_nome_tam=44, preco_tam=112):
    d = ImageDraw.Draw(img)
    f_nome = fonte("Inter-SemiBold.ttf", f_nome_tam)
    for linha in quebrar(d, item.get("nome", ""), f_nome, L - 220, 2):
        centro(d, y, linha, f_nome, TXT)
        y += round(f_nome_tam * 1.32)
    loja = {"amazon": "na Amazon", "mercadolivre": "no Mercado Livre", "shopee": "na Shopee"}.get(item.get("loja", ""))
    if loja:
        centro(d, y + 4, loja, fonte("Inter-Regular.ttf", 30), DIM)
        y += 50
    y += 10
    de, por = item.get("de"), item.get("por")
    if de and por and de > por and c["mostrar_de"]:
        f_de = fonte("Inter-Regular.ttf", 36)
        t = f"de {reais(de)}"
        w = d.textlength(t, font=f_de)
        x = (L - w) / 2
        d.text((x, y), t, font=f_de, fill=TXT2)
        d.line((x + d.textlength("de ", font=f_de), y + 24, x + w, y + 24), fill=TXT2, width=3)
        y += 50
    if por:
        centro(d, y - 6, reais(por), fonte("Cinzel-ExtraBold.ttf", preco_tam), OURO_VIV)
        y += round(preco_tam * 1.2)
    return y


def story_oferta(item, foto=None, canal="", frase=None, cfg=None, encaixe="auto"):
    c = config(cfg)
    img = fundo()
    d = ImageDraw.Draw(img)
    marca(img, 96, icone=78, grande=46, pequeno=19)
    topo_frase(d, 222, c["cabecalho"])
    card_produto(img, item, foto, (150, 300, L - 150, 930), encaixe, c, 40)
    nome_e_preco(img, item, 966, c, 42, 100)
    rodape(img, d, canal, frase or random.choice(c["frases"]))
    return png(img)


def topo_frase(d, y, texto, largura=L - 180):
    """Frase do topo: Cinzel dourada em caixa alta, diminui até caber (até 2 linhas)."""
    texto = texto.upper()
    for tam in (40, 36, 33, 30):
        f = fonte("Cinzel-ExtraBold.ttf", tam)
        esp = max(3, tam // 7)
        if sum(d.textlength(ch, font=f) for ch in texto) + esp * len(texto) <= largura:
            centro(d, y, texto, f, OURO, espaco=esp)
            return
    f = fonte("Cinzel-ExtraBold.ttf", 30)
    for i, linha in enumerate(quebrar(d, texto, f, largura - 60, 2)):
        centro(d, y - 10 + i * 40, linha, f, OURO, espaco=3)


def vaga_sticker(d, y, w, h, legenda, dica):
    """Legenda e um espaço tracejado onde você cola o sticker de link do Instagram."""
    legenda_setas(d, y, legenda, fonte("Cinzel-ExtraBold.ttf", 31), OURO, espaco=4)
    x0, y0, cor = (L - w) / 2, y + 60, GRAV
    for x in range(int(x0 + h / 2), int(x0 + w - h / 2), 26):
        d.line((x, y0, x + 14, y0), fill=cor, width=3)
        d.line((x, y0 + h, x + 14, y0 + h), fill=cor, width=3)
    d.arc((x0, y0, x0 + h, y0 + h), 90, 270, fill=cor, width=3)
    d.arc((x0 + w - h, y0, x0 + w, y0 + h), 270, 90, fill=cor, width=3)
    f = fonte("Inter-Regular.ttf", 28)
    d.text(((L - d.textlength(dica, font=f)) / 2, y0 + h / 2), dica, font=f, fill=DIM, anchor="lm")
    return y0 + h


def story_venda(item, foto=None, canal="", frase=None, cfg=None, encaixe="auto", link="produto"):
    """Story mais limpo, para vender: a frase sorteada vai no topo e embaixo fica a vaga de UM sticker de link
    (o Instagram só deixa um por story). link="produto": seu link de afiliado, e o canal vai escrito embaixo.
    link="canal": o link do canal, avisando que o link do produto está lá."""
    c = config(cfg)
    img = fundo()
    d = ImageDraw.Draw(img)
    marca(img, 92, icone=74, grande=44, pequeno=18)
    topo_frase(d, 214, frase or random.choice(c["frases"]))
    card_produto(img, item, foto, (120, 290, L - 120, 935), encaixe, c)
    nome_e_preco(img, item, 968, c)

    f_pe = fonte("Inter-Regular.ttf", 32)
    if link == "canal":
        fim = vaga_sticker(d, 1400, 640, 120, "LINK DESSA PROMO NO CANAL", "cole aqui o link do canal")
        centro(d, fim + 50, "e mais promoções todo dia", f_pe, TXT2)
    else:
        fim = vaga_sticker(d, 1386, 640, 108, "TOQUE PARA COMPRAR", "cole aqui o link do produto")
        if canal:  # o canal vai no botão do Telegram (sem clique: o Instagram só deixa um link por story)
            centro(d, fim + 40, "MAIS PROMOÇÕES TODO DIA NO CANAL", fonte("Inter-ExtraBold.ttf", 25), DIM, espaco=3)
            pilula_canal(img, d, fim + 76, canal)
    return png(img)


