"""Hefesto: desenha o carrossel Alpha Dominus (mesmo layout do gerador do PC, 2160x2700).
Primeiro slide = capa, último = finalização, meio = frames."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent
W, H = 2160, 2700
ESCURECER = 0.28
ARROBA = "@alphadominus.tv"
LIMITE_Y = 2150  # o texto não pode descer até o @alphadominus.tv

# Layout medido nos PNGs exportados do Figma (posição = topo das maiúsculas)
LAYOUT = {
    "capa":  {"titulo": ("SemiBold", 128, 744), "texto": ("Bold", 128, 1116),   "x": 195, "dir": 1880, "entrelinha": 164},
    "frame": {"titulo": ("Bold", 128, 315),     "texto": ("Regular", 96, 789),  "x": 218, "dir": 2000, "entrelinha": 122},
    "final": {"titulo": ("Bold", 128, 494),     "texto": ("Regular", 128, 926), "x": 270, "dir": 1886, "entrelinha": 164},
}


def fonte(peso, tam):
    f = ImageFont.truetype(str(AQUI / "fonts" / "Lora.ttf"), tam)
    f.set_variation_by_name(peso)
    return f


def quebrar(texto, f, largura):
    linhas = []
    for par in texto.split("\n"):
        atual = ""
        for p in par.split():
            teste = (atual + " " + p).strip()
            if f.getlength(teste) <= largura or not atual:
                atual = teste
            else:
                linhas.append(atual)
                atual = p
        linhas.append(atual)
    return linhas


def tipo_do(i, total):
    return "capa" if i == 0 else "final" if i == total - 1 else "frame"


def entrelinha_texto(L):
    return L["entrelinha"] if L["texto"][1] == 128 else 122


def problemas_de_espaco(slides):
    """Lista os slides cujo texto não cabe, pra pedir uma versão mais curta."""
    erros = []
    for i, s in enumerate(slides):
        L = LAYOUT[tipo_do(i, len(slides))]
        larg = L["dir"] - L["x"]
        pt, tt, yt = L["titulo"]
        px, tx, yx = L["texto"]
        n_tit = len(quebrar(s["titulo"], fonte(pt, tt), larg)) if s["titulo"] else 0
        n_txt = len(quebrar(s["texto"], fonte(px, tx), larg)) if s["texto"] else 0
        if n_tit and yt + n_tit * L["entrelinha"] > yx - 20:
            erros.append(f"Slide {i + 1}: título longo demais ({n_tit} linhas), encurte.")
        if n_txt and yx + n_txt * entrelinha_texto(L) > LIMITE_Y:
            erros.append(f"Slide {i + 1}: texto longo demais ({n_txt} linhas), encurte.")
    return erros


def fundo():
    bg = Image.open(AQUI / "fundo.jpg").convert("RGB").resize((W, H))
    return Image.blend(bg, Image.new("RGB", (W, H), "black"), ESCURECER)


def escrever(d, txt, peso, tam, y, x, larg, entre):
    f = fonte(peso, tam)
    topo = f.getbbox("H")[1]
    for i, ln in enumerate(quebrar(txt, f, larg)):
        d.text((x, y + i * entre - topo), ln, font=f, fill="white")


def slide(tipo, titulo, texto):
    L = LAYOUT[tipo]
    im = fundo()
    d = ImageDraw.Draw(im)
    larg = L["dir"] - L["x"]
    pt, tt, yt = L["titulo"]
    escrever(d, titulo, pt, tt, yt, L["x"], larg, L["entrelinha"])
    px, tx, yx = L["texto"]
    escrever(d, texto, px, tx, yx, L["x"], larg, entrelinha_texto(L))
    fa = fonte("Regular", 80)
    d.text(((W - fa.getlength(ARROBA)) / 2, 2258 - fa.getbbox("a")[1] + 20), ARROBA, font=fa, fill="white")
    return im


def gerar(slides, saida):
    saida.mkdir(parents=True, exist_ok=True)
    arquivos = []
    for i, s in enumerate(slides):
        caminho = saida / f"{i + 1:02d}.jpg"
        slide(tipo_do(i, len(slides)), s["titulo"], s["texto"]).save(caminho, quality=92)
        arquivos.append(caminho)
    return arquivos
