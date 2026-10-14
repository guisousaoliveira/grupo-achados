"""Bot do canal de achados (Telegram).

Faz três coisas a cada execução:
  1. Lê as mensagens que você mandou no privado do bot (links da Amazon e do
     Mercado Livre) e coloca as ofertas na fila.
  2. Uma vez por dia busca ofertas da Shopee pela Affiliate Open API, filtra
     pelo nicho e coloca no máximo 2 na fila.
  3. Se chegou a hora de um dos horários de postagem, posta a próxima oferta
     da fila no canal.

Só usa a biblioteca padrão do Python (nada para instalar).

Uso:
  python bot.py rodar            ciclo completo (o GitHub Actions roda isso a cada hora)
  python bot.py rodar --simular  mostra na tela o que faria, sem mandar nada
  python bot.py ids              descobre o seu ID e o ID do canal
  python bot.py previa "texto"   mostra como uma mensagem sua vira post
  python bot.py teste-canal      manda uma mensagem de teste no canal
"""

import base64
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ_CONFIG = PASTA / "config.json"
ARQ_ESTADO = PASTA / "dados" / "estado.json"
ARQ_MOCK = PASTA / "dados" / "shopee_exemplo.json"
ARQ_ENV = PASTA / ".env"

# Horário de Brasília (sem horário de verão desde 2019).
BRT = timezone(timedelta(hours=-3))

SHOPEE_URL = "https://open-api.affiliate.shopee.com.br/graphql"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SIMULAR = False
FORCAR = False  # --agora: posta a próxima da fila sem esperar o horário (para teste)


# ---------------------------------------------------------------- utilidades

def log(msg):
    print(f"[{agora():%d/%m %H:%M}] {msg}", flush=True)


def agora():
    hora = os.environ.get("BOT_HORA_TESTE")  # só para teste: força a hora
    dt = datetime.now(BRT)
    if hora:
        dt = dt.replace(hour=int(hora), minute=5)
    return dt


def carregar_env():
    """Lê o .env local (no GitHub os segredos já vêm como variáveis)."""
    if not ARQ_ENV.exists():
        return
    for linha in ARQ_ENV.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def segredo(nome):
    return os.environ.get(nome, "").strip()


def carregar_json(caminho, padrao):
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return padrao


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def estado_vazio():
    return {
        "offset_telegram": 0,
        "fila": [],
        "postados": [],       # {chave, loja, nome, data}
        "slots_usados": {},   # {"2026-10-11": [12, 18]}
        "shopee_buscado_em": "",
        "revisar": [],        # ofertas esperando você aprovar
        "proximo_id": 1,
    }


# ---------------------------------------------------------------- onde a fila fica guardada
# No PC: dados/estado.json. No GitHub Actions e no Render: o mesmo arquivo, mas lido e gravado
# direto no repositório pela API do GitHub, para os dois enxergarem sempre a mesma fila.

class Conflito(Exception):
    pass


def _repo():
    return segredo("GITHUB_REPO") or os.environ.get("GITHUB_REPOSITORY", "")


def usar_github():
    return bool(segredo("GITHUB_TOKEN") and _repo()) and not SIMULAR


def _github(metodo, corpo=None):
    url = f"https://api.github.com/repos/{_repo()}/contents/dados/estado.json"
    req = urllib.request.Request(url, method=metodo,
                                 data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={"Authorization": f"Bearer {segredo('GITHUB_TOKEN')}",
                                          "Accept": "application/vnd.github+json",
                                          "User-Agent": "bot-achados"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) or (e.code == 404 and metodo == "PUT"):
            log(f"GitHub recusou ({e.code}). Confira: GITHUB_REPO = '{_repo()}' (tem que ser usuario/grupo-achados, "
                "sem https) e se o token tem o repositório grupo-achados com Contents: Read and write.")
        raise


def chamar_github_agora(motivo="Pedi ao GitHub para reler a página agora.", inputs=None):
    """No Render: pede ao GitHub Actions para rodar já (a Amazon bloqueia o Render, mas costuma abrir para o GitHub;
    e o /tick do servidor usa isto para rodar o radar de 5 em 5 minutos).
    Precisa que o token tenha também a permissão Actions: Read and write."""
    if not (os.environ.get("RENDER") and usar_github()):
        return False
    url = f"https://api.github.com/repos/{_repo()}/actions/workflows/bot.yml/dispatches"
    corpo = {"ref": "main"}
    if inputs:
        corpo["inputs"] = inputs
    req = urllib.request.Request(url, method="POST", data=json.dumps(corpo).encode(),
                                 headers={"Authorization": f"Bearer {segredo('GITHUB_TOKEN')}",
                                          "Accept": "application/vnd.github+json",
                                          "User-Agent": "bot-achados"})
    try:
        urllib.request.urlopen(req, timeout=20).close()
        log(motivo)
        return True
    except urllib.error.HTTPError as e:
        log(f"GitHub não deixou rodar agora ({e.code}). Dê ao token a permissão Actions: Read and write.")
        return False


def carregar_estado():
    sha = None
    if usar_github():
        try:
            r = _github("GET")
            estado, sha = json.loads(base64.b64decode(r["content"]).decode("utf-8")), r["sha"]
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            estado = estado_vazio()
    else:
        estado = carregar_json(ARQ_ESTADO, estado_vazio())
    for chave, valor in estado_vazio().items():
        estado.setdefault(chave, valor)
    return estado, sha


def salvar_estado(estado, sha):
    if SIMULAR:
        return
    if not usar_github():
        salvar_json(ARQ_ESTADO, estado)
        return
    texto = json.dumps(estado, ensure_ascii=False, indent=2)
    corpo = {"message": "estado do bot", "content": base64.b64encode(texto.encode("utf-8")).decode()}
    if sha:
        corpo["sha"] = sha
    try:
        _github("PUT", corpo)
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            raise Conflito() from e
        raise


def com_estado(acao, tentativas=3):
    """Carrega a fila, roda a ação e salva. Se outro processo salvou no meio, tenta de novo."""
    for _ in range(tentativas):
        estado, sha = carregar_estado()
        antes = json.dumps(estado, sort_keys=True)
        acao(estado)
        if json.dumps(estado, sort_keys=True) == antes:
            return estado
        try:
            salvar_estado(estado, sha)
            return estado
        except Conflito:
            log("A fila mudou enquanto eu trabalhava. Tentando de novo.")
    log("Não consegui salvar a fila depois de 3 tentativas.")
    return None


def http(url, dados=None, headers=None, timeout=20):
    corpo = None
    if dados is not None:
        corpo = dados if isinstance(dados, bytes) else urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace"), resp.geturl()


def reais(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def numero_br(texto):
    """'1.299,90' -> 1299.9 ; '129' -> 129.0 ; '129.90' -> 129.9"""
    t = texto.strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    return float(t)


# ---------------------------------------------------------------- telegram

def telegram(metodo, **params):
    token = segredo("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Falta o TELEGRAM_TOKEN (no .env ou nos segredos do GitHub).")
    dados = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    try:
        texto, _ = http(f"https://api.telegram.org/bot{token}/{metodo}", dados, timeout=30)
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", errors="replace")
    resp = json.loads(texto)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram recusou {metodo}: {resp.get('description')}")
    return resp["result"]


def responder_admin(texto, botoes=None, responder_a=None, foto=None):
    """Manda mensagem no seu privado. Devolve a mensagem enviada (para guardar o ID)."""
    admin = segredo("ADMIN_ID")
    if SIMULAR or not admin:
        log(f"(resposta para você) {texto}" + (f"  [botões: {botoes}]" if botoes else ""))
        return {}
    if foto and len(texto) <= 1024:  # revisão com a foto que vai no post
        if not foto_so_por_arquivo(foto):
            try:
                return telegram("sendPhoto", chat_id=admin, photo=foto, caption=texto, parse_mode="HTML",
                                reply_markup=botoes, reply_to_message_id=responder_a)
            except RuntimeError as e:
                log(f"O Telegram não buscou a foto da revisão ({e}). Baixando e enviando o arquivo.")
            except OSError as e:  # demorou demais: pode ter chegado mesmo assim, então não manda de novo
                log(f"O Telegram demorou para mandar a revisão ({e}); não reenvio para não duplicar.")
                return {}
        dados = baixar_foto(foto)
        if dados:
            try:
                return enviar_multipart("sendPhoto", {"chat_id": admin, "caption": texto, "parse_mode": "HTML",
                                                      "reply_markup": botoes, "reply_to_message_id": responder_a},
                                        "photo", dados, "oferta.jpg", "image/jpeg")
            except Exception as e:
                log(f"Foto da revisão falhou ({e}), mandando só o texto.")
    try:
        return telegram("sendMessage", chat_id=admin, text=texto, parse_mode="HTML",
                        disable_web_page_preview="true", reply_markup=botoes,
                        reply_to_message_id=responder_a)
    except Exception as e:
        log(f"Não consegui te responder no privado: {e}")
        return {}


def foto_so_por_arquivo(foto):
    """Fotos de outros canais (telesco.pe): o Telegram demora ou recusa buscar pelo endereço.
    Mandar o endereço e depois o arquivo gerava revisão duplicada, então essas vão direto como arquivo."""
    return "telesco.pe" in foto


def publicar(item):
    texto = montar_post(item)
    canal = segredo("TELEGRAM_CHANNEL")
    if SIMULAR:
        log("(simulação) post que iria para o canal:\n" + "-" * 40 + f"\n{texto}\n" + "-" * 40)
        return
    if not canal:
        raise RuntimeError("Falta o TELEGRAM_CHANNEL (no .env ou nos segredos do GitHub).")
    foto = item.get("foto")
    if not foto and not os.environ.get("RENDER"):  # sem foto: tenta pegar da página (no GitHub a Amazon costuma abrir)
        try:
            foto = ler_pagina(item["link"]).get("foto")
        except Exception:
            foto = None
    if foto and len(texto) <= 1024:
        if not foto_so_por_arquivo(foto):
            try:
                telegram("sendPhoto", chat_id=canal, photo=foto, caption=texto, parse_mode="HTML")
                return
            except RuntimeError as e:
                log(f"O Telegram não buscou a foto pelo endereço ({e}). Baixando e enviando o arquivo.")
            except OSError as e:  # demorou demais: pode ter saído mesmo assim, então não posta de novo
                log(f"O Telegram demorou para postar ({e}); considero postado para não duplicar no canal.")
                return
        dados = baixar_foto(foto)  # fotos de outros canais (telesco.pe) o Telegram não busca sozinho
        if dados:
            try:
                enviar_multipart("sendPhoto", {"chat_id": canal, "caption": texto, "parse_mode": "HTML"},
                                 "photo", dados, "oferta.jpg", "image/jpeg")
                return
            except RuntimeError as e:
                log(f"Foto falhou de novo ({e}), mandando só o texto.")
    # Sem foto: mostra a prévia do link grande, em cima do texto, para o post não ficar "pelado".
    telegram("sendMessage", chat_id=canal, text=texto, parse_mode="HTML",
             link_preview_options={"url": item["link"], "prefer_large_media": True, "show_above_text": True})


# ---------------------------------------------------------------- formato do post

def montar_post(item):
    """Post do canal em blocos separados por uma linha em branco (no celular fica menos apertado)."""
    def e(t):
        return html.escape(t, quote=False)
    # Título (a chamada) em cima, depois o nome. Emoji em cada linha: título (🚨 ou 🔥, sorteado por oferta,
    # sempre o mesmo para a mesma oferta), 📦 produto, 💰 preço, 🎟️ cupom e 🛒 link.
    # "frase" é o nome antigo do título (ofertas que já estavam na fila).
    titulo = item.get("titulo") or item.get("frase")
    if titulo:
        blocos = [f"{emoji_titulo(item)} <b>{e(titulo.upper())}</b>", f"📦 {e(item['nome'])}"]
    else:
        blocos = [f"📦 <b>{e(item['nome'])}</b>"]
    preco = []
    de, por = item.get("de"), item.get("por")
    pix = " no Pix" if item.get("pix") else ""
    if por and de and de > por:
        off = round((1 - por / de) * 100)
        preco.append(f"💰 De <s>{reais(de)}</s> por <b>{reais(por)}</b>{pix} ({off}% off)")
    elif por:
        preco.append(f"💰 Por <b>{reais(por)}</b>{pix}")
    if item.get("parcelas") and por:
        preco.append(f"💳 <i>{texto_parcelas(item)}</i>")
    if item.get("prime"):
        extra = f" (sem Prime: {reais(item['sem_prime'])})" if item.get("sem_prime") else ""
        preco.append(f"Preço exclusivo para membros Prime{extra}")
    if preco:
        blocos.append("\n".join(preco))
    if item.get("obs"):  # observação livre embaixo do preço ("Frete grátis acima de R$ 79", "Só no app")
        blocos.append(f"📌 {e(item['obs'])}")
    if item.get("cupom"):
        if re.search(r"\s", item["cupom"].strip()):  # frase da loja ("Aplique o cupom de 10% OFF"), não é código
            blocos.append(f"🎟️ {e(item['cupom'])}")
        else:
            blocos.append(f"🎟️ Cupom: <code>{e(item['cupom'])}</code>")
    elif item.get("cupom_loja"):
        blocos.append(f"🎟️ {e(item['cupom_loja'])}")
    loja = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}.get(item.get("loja"), "")
    blocos.append(f"🛒 {e(item['link'])}" + (f"  ({loja})" if loja else ""))
    rodape = CONFIG.get("rodape", "")
    if rodape:
        blocos.append(e(rodape))
    return "\n\n".join(blocos)


def emoji_titulo(item):
    """🚨 ou 🔥, sorteado pela oferta (o mesmo na prévia, na revisão e no post)."""
    chave = item.get("chave") or item.get("link") or item.get("nome") or ""
    return ("🚨", "🔥")[int(hashlib.md5(chave.encode()).hexdigest(), 16) % 2]


def texto_parcelas(item):
    """'ou 3x de R$ 32,33 (R$ 96,99)': discreto, o destaque fica no preço à vista. Sem o valor da parcela, divide o preço."""
    n = int(item["parcelas"])
    if item.get("parcela_valor"):
        total = reais(n * item["parcela_valor"]).replace(" ", "\u00a0")
        return f"ou {n}x de {reais(item['parcela_valor'])} ({total})"
    return f"ou {n}x de {reais(item['por'] / n)}"


def ler_parcelas(texto):
    """'4', '4x', '10x 39,90', 'em até 10x de R$ 39,90 sem juros' -> (10, 39.9). '-' ou nada -> (None, None)."""
    m = re.search(r"(\d{1,2})\s*(?:x|vezes)?(?:\s*(?:de|sem juros de)?\s*(?:R\$)?\s*(\d[\d.]*,\d{2}|\d+(?:\.\d{2})?))?",
                  str(texto or ""), re.I)
    if not m or not 2 <= int(m.group(1)) <= 24:
        return None, None
    return int(m.group(1)), (numero_br(m.group(2)) if m.group(2) else None)


def aplicar_parcelas(item, texto):
    n, valor = ler_parcelas(texto)
    item.pop("parcela_valor", None)
    if n:
        item["parcelas"] = n
        if valor:
            item["parcela_valor"] = valor
    else:
        item.pop("parcelas", None)


# ---------------------------------------------------------------- seus links (Amazon / ML / Shopee)

def loja_do_link(link):
    host = urllib.parse.urlparse(link).netloc.lower()
    if "amzn" in host or "amazon" in host:
        return "amazon"
    if "mercadolivre" in host or "mercadolibre" in host or "meli.la" in host:
        return "mercadolivre"
    if "shopee" in host or "shp.ee" in host:
        return "shopee"
    return "outra"


def _limpar(texto):
    texto = html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))
    return re.sub(r"\s+", " ", texto).strip()


def _preco(texto):
    """'R$ 1.299,90' / '1299.90' / 1299.9 -> 1299.9"""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto) or None
    m = re.search(r"\d[\d.,]*", str(texto))
    if not m:
        return None
    t = m.group(0).rstrip(".,")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", t):
        t = t.replace(".", "")
    try:
        return float(t) or None
    except ValueError:
        return None


def nome_curto(nome, limite=70):
    """Títulos de loja são enormes. Corta no primeiro separador e no limite de letras."""
    nome = _limpar(nome)
    nome = re.sub(r"\s*[|]\s*(Mercado Livre|Amazon\.com\.br|Shopee Brasil).*$", "", nome, flags=re.I)
    nome = re.sub(r"\s*[-–]\s*(Mercado Livre|Amazon\.com\.br|Shopee Brasil)\s*$", "", nome, flags=re.I)
    for sep in (" | ", " - ", " – ", ", "):
        antes = nome.split(sep)[0]
        if sep in nome and len(antes) >= 15:
            nome = antes
            break
    if len(nome) > limite:
        nome = nome[:limite].rsplit(" ", 1)[0]
    return nome.strip(" -,|")


def extrair_da_pagina(pagina, loja):
    """Lê nome, preço atual, preço antigo e foto do HTML de uma página de produto."""
    achado = {}

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name|itemprop)=["\']{prop}["\'][^>]*content=["\']([^"\']+)', pagina, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\']{prop}["\']', pagina, re.I)
        return html.unescape(m.group(1)).strip() if m else ""

    # 1) Dados estruturados (JSON-LD), quando a loja coloca.
    for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', pagina, re.S | re.I):
        try:
            dados = json.loads(bloco.strip())
        except ValueError:
            continue
        pilha = dados if isinstance(dados, list) else [dados]
        while pilha:
            d = pilha.pop()
            if isinstance(d, list):
                pilha.extend(d)
                continue
            if not isinstance(d, dict):
                continue
            pilha.extend(d.get("@graph", []) if isinstance(d.get("@graph"), list) else [])
            if "Product" not in str(d.get("@type", "")):
                continue
            achado.setdefault("nome", d.get("name"))
            img = d.get("image")
            img = img[0] if isinstance(img, list) and img else img
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str):
                achado.setdefault("foto", img)
            ofertas = d.get("offers") or {}
            ofertas = ofertas[0] if isinstance(ofertas, list) and ofertas else ofertas
            if isinstance(ofertas, dict):
                p = _preco(ofertas.get("price") or ofertas.get("lowPrice"))
                if p:
                    achado.setdefault("por", p)

    # 2) Partes específicas de cada loja.
    if loja == "amazon":
        m = re.search(r'id="productTitle"[^>]*>(.*?)</span>', pagina, re.S)
        if m:
            achado["nome"] = _limpar(m.group(1))
        # Jeito mais confiável: os rótulos de acessibilidade que a Amazon põe no preço principal.
        a_pagar = [_preco(v) for v in re.findall(r'data-pricetopay-label="[^"]*"[^>]*>\s*R\$(?:&nbsp;|\s)*([\d.,]+)', pagina)]
        m_de = re.search(r'apex-basisprice-offscreen-label">\s*De:\s*R\$(?:&nbsp;|\s)*([\d.,]+)', pagina)
        prime = "exclusiva para membros do Amazon Prime" in pagina or "Assine o Prime para comprar este produto por" in pagina
        if a_pagar:
            achado["por"] = a_pagar[0]
            if prime:
                achado["prime"] = True
                if len(a_pagar) > 1 and a_pagar[1] and a_pagar[1] > a_pagar[0]:
                    achado["sem_prime"] = a_pagar[1]
        if m_de:
            achado["de"] = _preco(m_de.group(1))
        # Preço principal: o bloco "priceToPay" dentro do quadro de preço do produto.
        bloco = re.search(r'id="(?:corePriceDisplay_desktop_feature_div|corePrice_feature_div|apex_desktop)"(.*?)(?:id="(?:corePriceDisplay_mobile|tp_price_block|addToCart)|$)', pagina, re.S)
        area = bloco.group(1)[:30000] if bloco else ""
        m = re.search(r'priceToPay[^"]*"[^>]*>.*?class="a-offscreen">([^<]+)<', area, re.S) \
            or re.search(r'class="a-offscreen">([^<]+)<', area)
        if m and not achado.get("por"):
            achado["por"] = _preco(m.group(1))
        m = re.search(r'basisPrice.*?class="a-offscreen">([^<]+)<', area, re.S)
        if m and not achado.get("de"):
            achado["de"] = _preco(m.group(1))
        m = re.search(r'class="a-price a-text-price"[^>]*data-a-strike="true"[^>]*>\s*<span class="a-offscreen">([^<]+)<', area, re.S)
        if m and "de" not in achado:
            achado["de"] = _preco(m.group(1))
        m = re.search(r'(Aplicar (?:o )?cupom de [^<]{1,30}?)(?:<|\s{2})', pagina)
        if m:
            achado["cupom_loja"] = _limpar(m.group(1)).rstrip(" .") + " na página"
        m = re.search(r'"hiRes":"(https://[^"]+)"', pagina) or re.search(r'data-old-hires="(https://[^"]+)"', pagina)
        if m:
            achado["foto"] = m.group(1)
    elif loja == "mercadolivre":
        # Link de afiliado do ML abre a "vitrine" (/social/...): o produto principal é o do og:title,
        # e o preço dele vem num bloco JSON logo depois do nome (os outros blocos são recomendações).
        titulo = meta("og:title")
        if titulo:
            for pos in [mm.start() for mm in re.finditer(re.escape(json.dumps(titulo, ensure_ascii=False)[1:-1]), pagina)]:
                trecho = pagina[pos:pos + 3000]
                atual = re.search(r'"current_price":\{"value":([\d.]+)', trecho)
                if atual:
                    achado["por"] = float(atual.group(1))
                    antes = re.search(r'"previous_price":\{"value":([\d.]+)', trecho[:atual.start()])
                    if antes:
                        achado["de"] = float(antes.group(1))
                    break
        m = re.search(r'"original_price":\s*([\d.]+)', pagina)
        if m and "de" not in achado:
            achado["de"] = _preco(float(m.group(1)))
        m = re.search(r'andes-money-amount--previous[^>]*aria-label="[^"]*?(\d[\d.]*)\s*reais(?:\s*(?:com|con)\s*(\d+)\s*centavos)?', pagina)
        if m and "de" not in achado:
            achado["de"] = float(m.group(1).replace(".", "")) + (int(m.group(2)) / 100 if m.group(2) else 0)

    # 3) Etiquetas genéricas (Open Graph), como último recurso.
    achado.setdefault("nome", meta("og:title") or meta("twitter:title"))
    achado.setdefault("foto", meta("og:image"))
    if not achado.get("por"):
        achado["por"] = _preco(meta("product:price:amount") or meta("og:price:amount") or meta("price"))

    if achado.get("nome"):
        achado["nome"] = nome_curto(achado["nome"])
    if achado.get("de") and achado.get("por") and achado["de"] <= achado["por"]:
        achado.pop("de")
    return {k: v for k, v in achado.items() if v}


def ler_pagina(link):
    """Abre o link (seguindo o redirecionamento do link curto) e lê a página. Se a loja bloquear, devolve vazio."""
    cabecalhos = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    try:
        pagina, final = http(link, headers=cabecalhos, timeout=20)
    except Exception as e:
        log(f"Não consegui abrir a página ({e}).")
        return {"_bloqueado": True}
    loja = loja_do_link(final) if loja_do_link(final) != "outra" else loja_do_link(link)
    if os.environ.get("BOT_SALVAR_PAGINA"):  # usado pelo TESTAR_LINK.bat, para diagnóstico
        destino = PASTA / "dados" / f"pagina_{loja}.html"
        destino.write_text(f"<!-- {final} -->\n" + pagina, encoding="utf-8")
        log(f"Página salva em {destino.name} ({len(pagina)} letras). Endereço final: {final}")
    if "account-verification" in final:  # o Mercado Livre pede login para IPs de servidor (como o do GitHub)
        log("O Mercado Livre pediu verificação de conta (bloqueou o robô).")
        return {"_bloqueado": True}
    if "captcha" in pagina[:20000].lower() and "productTitle" not in pagina:
        log("A loja mostrou um captcha (bloqueou o robô).")
        return {"_bloqueado": True}
    achado = extrair_da_pagina(pagina, loja)
    if loja == "shopee":
        oficial = shopee_por_link(final)
        oficial.pop("link", None)
        achado.update(oficial)
    return achado


def shopee_por_link(url):
    """Com a chave da Open API, busca nome, preço e foto pelo número do produto que está no link."""
    m = re.search(r"i\.(\d+)\.(\d+)", url) or re.search(r"shopee\.com\.br/(?:[^/?]+/)?(\d{5,})/(\d{5,})", url)
    if not m or not segredo("SHOPEE_APP_ID"):
        return {}
    try:
        nos = shopee_consulta(CONSULTA_SHOPEE_ITEM, {"item": int(m.group(2)), "loja": int(m.group(1))})
    except Exception as e:
        log(f"Shopee API não achou o produto: {e}")
        return {}
    if not nos:
        return {}
    o = oferta_shopee(nos[0])
    return {k: o[k] for k in ("nome", "por", "de", "foto", "link") if o.get(k)}


def ler_precos(linha):
    nums = [numero_br(n) for n in re.findall(r"\d[\d.]*(?:,\d{1,2})?", linha)]
    if len(nums) >= 2:
        return max(nums[:2]), min(nums[:2])
    return None, (nums[0] if nums else None)


def interpretar_mensagem(texto, foto=None, buscar_pagina=True):
    """Transforma o que você mandou no privado em uma oferta.

    Basta o link. O bot tenta ler nome, preço e foto da página. O que você
    escrever nas linhas de baixo tem prioridade sobre o que ele leu:
        https://amzn.to/abc123
        Creatina 1kg Growth
        De 199 por 129
        A mais vendida, rende 200 doses
    """
    urls = re.findall(r"https?://\S+", texto)
    if not urls:
        return None, "Não achei nenhum link na mensagem. Manda o link do produto (e, se quiser, nome e preço embaixo)."
    link = urls[0].rstrip(").,")
    # Texto na mesma linha do link é ignorado (ex.: mensagem encaminhada de outro canal).
    resto = [l.strip() for l in texto.splitlines() if l.strip() and urls[0] not in l]

    item = {"loja": loja_do_link(link), "link": link}
    if foto:
        item["foto"] = foto

    tem_preco = False
    rotulo = r"(nome|t[ií]tulo|chamada|pre[cç]o|frase|cupom|prime|pix|parcelas?)\s*[:=]"
    if any(re.match(rotulo, l, re.I) for l in resto):  # mesmo formato da correção (nome:, preço:, prime:...)
        aplicar_correcao(item, "\n".join(resto))
        tem_preco = bool(item.get("por"))
        resto = []
    for linha in list(resto):
        m = re.match(r"cupom\s*[:=]?\s*(\S+)", linha, re.I)
        if m:
            item["cupom"] = m.group(1)
            resto.remove(linha)
    for linha in resto:
        if not tem_preco and re.search(r"\d", linha) and re.search(r"(r\$|\bde\b|\bpor\b|^\s*[\d.,]+(\s+[\d.,]+)?\s*$)", linha, re.I):
            de, por = ler_precos(linha)
            if de:
                item["de"] = de
            if por:
                item["por"] = por
            tem_preco = True
        elif "nome" not in item:
            item["nome"] = linha
        elif "titulo" not in item:
            item["titulo"] = linha

    if buscar_pagina and not ("nome" in item and "foto" in item and "por" in item):
        lido = ler_pagina(link)
        if lido.pop("_bloqueado", False):
            item["ler_depois"] = True
        if tem_preco:  # o preço que você escreveu vale mais que o da página
            lido.pop("de", None)
            lido.pop("por", None)
        for chave, valor in lido.items():
            item.setdefault(chave, valor)

    item["chave"] = f"{item['loja']}:{link}"
    return item, None


def faltando(item):
    falta = []
    if not item.get("nome"):
        falta.append("nome")
    if not item.get("por"):
        falta.append("preço")
    if item.get("precisa_link"):
        falta.append("seu link de afiliado")
    return falta


def botoes_revisao(item):
    """Aprovar/Descartar (só Descartar se faltar algo) e a chave do Pix (marca se o preço anunciado é no Pix)."""
    i = item["id"]
    linha = [{"text": "❌ Descartar", "callback_data": f"no:{i}"}]
    if not faltando(item):
        linha.insert(0, {"text": "✅ Aprovar", "callback_data": f"ok:{i}"})
    pix = {"text": "💠 Preço no Pix: " + ("SIM" if item.get("pix") else "não") + " (trocar)", "callback_data": f"px:{i}"}
    return {"inline_keyboard": [linha, [pix]]}


class ErroIA(Exception):
    pass


_modelo_ia = []  # modelo que funcionou (guardado para não procurar de novo)


def _modelos_disponiveis(chave, com_lite=False):
    """Pergunta ao Google quais modelos a chave tem e devolve os "flash" de texto, do mais novo para o mais velho."""
    try:
        texto, _ = http(f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key={chave}", timeout=20)
    except Exception as e:
        log(f"IA: não consegui listar os modelos ({e}).")
        return []
    nomes = [m["name"].split("/", 1)[1] for m in json.loads(texto).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    ruins = ("image", "tts", "audio", "live", "embedding", "thinking", "exp", "preview") + (() if com_lite else ("lite",))
    bons = [n for n in nomes if "flash" in n and not any(r in n for r in ruins)]
    bons += [n for n in nomes if "flash" in n and n not in bons and not any(r in n for r in ruins[:5])]

    def versao(n):
        m = re.search(r"(\d+(?:\.\d+)?)", n)
        return (n.endswith("-latest"), float(m.group(1)) if m else 0)
    return sorted(bons, key=versao, reverse=True)


def gemini(corpo, timeout=30):
    """Chama a Gemini e devolve o texto da resposta. Se o modelo do config não existir mais (404),
    descobre sozinho um modelo "flash" que a chave tem. Levanta ErroIA com o motivo, se não der."""
    chave = segredo("GEMINI_API_KEY")
    if not chave:
        raise ErroIA("sem a chave GEMINI_API_KEY")
    candidatos = list(dict.fromkeys(_modelo_ia + [CONFIG.get("ia", {}).get("modelo", "gemini-flash-latest"),
                                                  "gemini-flash-latest"]))
    procurou = False
    tentou = {}
    usados, refazer = set(), {}
    cota = False  # algum modelo já deu 429 (cota grátis acabou): a cota é por modelo, então tenta os outros
    while candidatos:
        modelo = candidatos.pop(0)
        if modelo in usados and not refazer.pop(modelo, False):
            continue
        usados.add(modelo)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}"
        try:
            texto, _ = http(url, json.dumps(corpo).encode(), {"Content-Type": "application/json"}, timeout=timeout)
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode("utf-8", "replace")
            if e.code in (404, 500, 503, 504):
                # 404: modelo não existe para a chave. 5xx: Google sobrecarregado (comum no grátis):
                # tenta de novo uma vez e depois passa para outro modelo "flash".
                if e.code != 404 and tentou.get(modelo, 0) < 1:
                    tentou[modelo] = 1
                    log(f"IA: {modelo} ocupado ({e.code}), tentando de novo.")
                    time.sleep(2)
                    candidatos.insert(0, modelo)
                    refazer[modelo] = True
                    continue
                if not procurou:
                    procurou = True
                    candidatos += [m for m in _modelos_disponiveis(chave) if m not in candidatos and m != modelo]
                if candidatos:
                    continue
                if e.code != 404:
                    raise ErroIA("o Google está sobrecarregado agora (erro 503), tente de novo em 1 minuto")
                continue
            if e.code == 429:
                # Cota grátis desse modelo acabou (minuto ou dia). Cada modelo tem a sua cota, e os "lite"
                # têm bem mais: passa para eles em vez de desistir.
                log(f"IA: cota do {modelo} acabou (429), tentando outro modelo.")
                cota = True
                if modelo in _modelo_ia:
                    _modelo_ia.clear()
                extras = ["gemini-flash-lite-latest"] + _modelos_disponiveis(chave, com_lite=True)
                candidatos += [m for m in extras if m not in usados and m not in candidatos]
                if candidatos:
                    continue
            motivo = {400: "chave inválida ou pedido recusado", 403: "chave sem permissão (confira se a API está ativa)",
                      429: "limite grátis do minuto/dia atingido, tente daqui a pouco"}.get(e.code, f"erro {e.code}")
            log(f"IA: {motivo}. {detalhe[:200]}")
            raise ErroIA(motivo)
        _modelo_ia[:] = [modelo]
        partes = json.loads(texto)["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in partes).strip()
    if cota:
        raise ErroIA("limite grátis do minuto/dia atingido em todos os modelos, tente daqui a pouco")
    raise ErroIA("nenhum modelo Gemini disponível para essa chave")


def gerar_frase(item):
    """Chamada para a oferta: a IA (Gemini) se tiver a chave e ela responder; se não, a chamada reserva."""
    return gerar_titulo(item) or titulo_reserva(item)


# A IA só pode usar o que está nos dados da oferta. Ela inventava "frete grátis", "Prime", "Pix" e
# "menor preço histórico" em oferta que não tinha nada disso (ex.: Prime em produto do Mercado Livre).
REGRA_IA = ("Tom sério e objetivo, como uma indicação de compra, nada de exagero de camelô. Sobre preço e condições, "
            "use SOMENTE o que está nos dados. Sobre o produto, pode falar do uso ou benefício óbvio dele (para que serve, "
            "para quem é), sem inventar especificação. Não mencione frete, Prime, Pix, cupom, parcelas, brinde ou estoque se isso não estiver "
            "nos dados. Nunca diga menor preço, preço histórico, nunca visto, mais barato da internet, últimas unidades "
            "ou que vai esgotar (não temos histórico de preço nem estoque).")
# A chamada não fala de desconto: o post já mostra o "% off" logo abaixo e todo título com "87% off" ficava
# repetitivo. Ela fala do produto: para que serve, para quem é, o que tem de bom.
EXEMPLOS_TITULO = ("Fale do produto: o uso, para quem é ou o que ele tem de bom. NÃO fale de desconto, porcentagem, "
                   "off, promoção nem preço (o post já mostra isso logo abaixo). Exemplos do tom certo: "
                   "Whey para você bater a proteína do dia | Mochila que carrega o notebook e a roupa do treino | "
                   "Creatina para render mais no treino | Aparador para manter a barba alinhada em casa | "
                   "Relógio prateado que combina com camisa social | Fone sem fio para treinar sem cabo enroscando | "
                   "Perfume amadeirado para usar à noite | Garrafa que segura o café quente o dia todo")
SEM_DESCONTO = re.compile(r"\d\s*%|\boff\b|descont|promo|\bpre[cç]o|\br\$|metade|barat|oferta|agora ou nunca|\bcorre\b", re.I)
PROIBIDO_IA = re.compile(r"menor\s+pre[cç]o|hist[oó]ric|nunca\s+vist|mais\s+barat[oa]\s+d[ao]|[uú]ltimas\s+unidades|"
                         r"esgot|pre[cç]o\s+m[ií]nimo|m[ií]nimo\s+hist|frete|entrega\s+gr[aá]tis|brinde|lan[cç]amento", re.I)


def texto_ia_ok(texto, item=None):
    """Descarta o que a IA escreveu se afirmar algo que não está nos dados (frete, Prime, Pix, cupom, parcelas)
    ou que a gente não tem como saber (menor preço histórico, estoque)."""
    if not texto or PROIBIDO_IA.search(texto):
        return False
    item = item or {}
    if re.search(r"\bprime\b", texto, re.I) and not item.get("prime"):
        return False
    if re.search(r"\bpix\b", texto, re.I) and not item.get("pix"):
        return False
    if re.search(r"cupo[mn]", texto, re.I) and not (item.get("cupom") or item.get("cupom_loja")):
        return False
    if re.search(r"parcel|sem\s+juros|\d+\s*x\b", texto, re.I) and not item.get("parcelas"):
        return False
    return True


def dados_ia(item):
    """Os fatos da oferta que a IA pode usar (só o que existe)."""
    de, por = item.get("de"), item.get("por")
    off = round((1 - por / de) * 100) if de and por and de > por else 0
    loja = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}.get(item.get("loja"), "")
    linhas = [f"Produto: {item.get('nome')}"]
    if loja:
        linhas.append(f"Loja: {loja}")
    if por:
        linhas.append(f"Preço: {reais(por)}" + (f" (antes {reais(de)}, {off}% off)" if off else ""))
    if item.get("cupom") or item.get("cupom_loja"):
        linhas.append(f"Cupom: {item.get('cupom') or item.get('cupom_loja')}")
    if item.get("pix"):
        linhas.append("Esse preço é no Pix")
    if item.get("prime"):
        linhas.append("Preço exclusivo para membros Prime")
    if item.get("parcelas"):
        linhas.append(f"Parcelas: {item['parcelas']}x sem juros")
    return "\n".join(linhas)


def titulo_ok(texto, item=None):
    """Chamada boa: não afirma o que não está nos dados e não fala de desconto/preço (já está no post)."""
    return texto_ia_ok(texto, item) and not SEM_DESCONTO.search(texto) and 2 <= len(texto.split()) <= 10


def titulos_recentes(estado):
    """As últimas chamadas usadas, para a IA não repetir o mesmo jeito de começar."""
    ts = [x.get("titulo") for x in estado.get("postados", [])[-8:]] + \
         [x.get("titulo") for x in estado.get("fila", []) + estado.get("revisar", [])]
    return [t for t in ts if t and titulo_ok(t)][-15:]  # as ruins ("87% off") não entram, para não virar exemplo


def gerar_titulo(item, recentes=()):
    """Chamada curta para o topo do post, feita pela IA. "" se a IA não responder (aí vai a chamada reserva)."""
    chave = segredo("GEMINI_API_KEY")
    if not chave or SIMULAR:
        return ""
    pedido = ("Escreva UMA chamada curta (até 8 palavras) em português do Brasil para o topo de um post de oferta "
              "num canal de achados masculino. Sem emoji, sem hashtag, sem aspas, sem ponto final. " + REGRA_IA + "\n"
              + EXEMPLOS_TITULO)
    if recentes:
        pedido += ("\nChamadas que já usamos (não copie a estrutura nem o começo delas): " + " | ".join(recentes))
    pedido += "\n\nDados:\n" + dados_ia(item) + "\nChamada:"
    corpo = {"contents": [{"parts": [{"text": pedido}]}], "generationConfig": {"temperature": 1.0}}
    try:
        for _ in range(2):  # se falar de desconto ou afirmar o que não está nos dados, tenta mais uma vez
            texto = gemini(corpo)
            titulo = (texto.strip().splitlines() or [""])[0].strip().strip('"“”').rstrip(".")
            if titulo_ok(titulo, item):
                return titulo[:60]
        return ""
    except Exception as e:
        log(f"IA não fez o título ({e}).")
        return ""


def titulo_reserva(item):
    """Chamada sem IA: o nome curto do produto + um complemento do tema dele ("Creatina Dark Lab para render mais
    no treino"). Fica marcada para a IA refazer depois (refazer_titulos)."""
    nome = re.split(r"\s*[(\[]", nome_curto(item.get("nome") or "", 40))[0]
    palavras = nome.split()
    nome = " ".join(palavras[:4]) if len(palavras) > 4 else nome
    nome = re.sub(r"(\s+(de|da|do|com|para|e|em|eau|pura?|original|\d+%))+$", "", nome, flags=re.I)
    if not nome:
        return ""
    frases = CONFIG.get("complementos", {})
    texto = (item.get("nome") or "").lower()
    opcoes = []
    for tema, ps in CONFIG.get("palavras", {}).items():
        if any(re.search(rf"\b{re.escape(p)}\b", texto) for p in ps):
            opcoes = frases.get(tema, [])
            break
    opcoes = opcoes or frases.get("geral", [])
    fim = random.Random(item.get("chave") or nome).choice(opcoes) if opcoes else ""
    return f"{nome} {fim}".strip()


def refazer_titulos(estado, maximo=4):
    """Ofertas que ficaram com a chamada reserva (a IA estava fora do ar ou sem cota): tenta a IA de novo.
    Se você já mudou a chamada, não mexe."""
    feitos = 0
    for item in estado["revisar"] + estado["fila"]:
        reserva = item.get("titulo_reserva")
        if not reserva:
            continue
        if item.get("titulo") != reserva:
            item.pop("titulo_reserva", None)
            continue
        if feitos >= maximo:
            break
        feitos += 1
        novo = gerar_titulo(item, titulos_recentes(estado))
        if not novo:
            break  # a IA ainda não responde: tenta na próxima rodada
        item["titulo"] = novo
        item.pop("titulo_reserva", None)
        log(f"Chamada refeita pela IA: {novo}")


def mandar_para_revisao(estado, item):
    if not (item.get("titulo") or item.get("frase")) and item.get("nome"):
        item["titulo"] = gerar_titulo(item, titulos_recentes(estado))
        if not item["titulo"]:  # IA fora do ar ou sem cota: chamada reserva, e a IA tenta de novo nas próximas rodadas
            item["titulo"] = item["titulo_reserva"] = titulo_reserva(item)
    item["id"] = estado["proximo_id"]
    estado["proximo_id"] += 1
    item.setdefault("garimpado_em", agora().isoformat(timespec="minutes"))
    item["na_revisao_desde"] = agora().isoformat(timespec="minutes")  # conta a validade do Revisar
    falta = faltando(item)
    texto = "📝 <b>Revisar oferta</b>\n\n" + (montar_post(item) if item.get("nome") else html.escape(item["link"]))
    if item.get("origem"):
        canal = html.escape(item["origem"])
        if item.get("fonte") == "promobit":
            canal = f'<a href="{html.escape(item.get("post_origem", ""))}">Promobit (ver a oferta lá)</a>'
        elif item.get("post_origem"):
            canal = f'<a href="{html.escape(item["post_origem"])}">@{canal} (ver post original)</a>'
        else:
            canal = "@" + canal
        texto += f"\n\n📡 Achei no {canal}. Confira o preço antes de aprovar: {html.escape(item.get('link_produto', ''))}"
    if item.get("aviso_preco"):
        texto += "\n⚠️ O post do canal tinha dois preços muito diferentes. Usei o maior: confira o preço na loja."
    if item.get("precisa_link"):
        texto += ("\n🔗 Gere o <b>seu</b> link desse produto no painel de afiliado e responda esta mensagem com "
                  "<code>link: https://...</code>")
        falta = [f for f in falta if f != "seu link de afiliado"]
    if falta and item.get("ler_depois"):
        prazo = "em 1 ou 2 minutos" if chamar_github_agora() else "em até 30 minutos"
        texto += (f"\n\n⏳ A loja bloqueou a leitura agora. Vou tentar de novo sozinho {prazo}, "
                  "ou você completa já respondendo esta mensagem.")
    elif falta:
        texto += (f"\n\n⚠️ Não consegui ler: <b>{', '.join(falta)}</b>. "
                  "Responda esta mensagem com o que falta e o botão ✅ Aprovar aparece.")
    if not falta:
        if item.get("foto_origem") == "canal":
            texto += "\n\n📷 Foto do canal de origem (não achei a oficial). Para trocar, responda com outra foto."
        elif item.get("foto"):
            texto += "\n\n📷 Foto: ok (é a que aparece aqui). Para trocar, responda com outra foto."
        else:
            texto += "\n\n📷 Sem foto. Se quiser, responda esta mensagem com um print do produto."
    texto += ("\n\n<i>Para corrigir, responda (arrastar para o lado) esta mensagem com uma ou mais linhas:\n"
              "título: Troca o celular e ganha o relógio\nnome: Creatina Growth 250g\npreço: 119,90 69,90\ncupom: NOITEPRIME10\nobs: Frete grátis acima de R$ 79\npix: sim\nprime: sim 189,37 (preço sem Prime)\nparcela: 4 (ou 10x 39,90)\n(título: - ou cupom: - para tirar)</i>")
    msg = responder_admin(texto, botoes=botoes_revisao(item),
                          foto=item.get("foto"))
    item["msg_id"] = msg.get("message_id") if msg else None
    if msg and msg.get("photo"):  # guarda a foto já no Telegram: o post usa essa, sem depender do link de fora
        item["foto"] = msg["photo"][-1]["file_id"]
    estado["revisar"].append(item)


def aplicar_correcao(item, texto):
    """Linhas tipo 'nome: ...', 'preço: 199 129', 'frase: ...', 'link: ...'. Linha sem rótulo com números = preço."""
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        m = re.match(r"(nome|t[ií]tulo|chamada|pre[cç]o|frase|link|de|por|cupom|obs|prime|pix|parcelas?)\s*[:=]\s*(.+)", linha, re.I)
        campo, valor = (m.group(1).lower(), m.group(2).strip()) if m else ("", linha)
        if campo.startswith("pre") or (not campo and re.fullmatch(r"[\d.,R$\s]+|de .+ por .+", linha, re.I)):
            de, por = ler_precos(valor)
            item["de"], item["por"] = de, por
            if not de:
                item.pop("de", None)
        elif campo == "de":
            item["de"] = _preco(valor)
        elif campo == "por":
            item["por"] = _preco(valor)
        elif campo == "nome" or (not campo and not item.get("nome")):
            item["nome"] = valor
        elif campo in ("título", "titulo", "chamada", "frase") or not campo:  # frase e título agora são a mesma coisa
            item["titulo"] = "" if valor == "-" else valor
            item.pop("frase", None)
        elif campo == "pix":
            item["pix"] = valor.lower().split()[0] in ("sim", "s", "1", "yes")
        elif campo.startswith("parcela"):
            aplicar_parcelas(item, "" if valor == "-" else valor)
        elif campo == "link":
            item["link"] = valor
            item["link_produto"] = valor  # o link que você editou passa a ser o que abre o produto em todo lugar
            item.pop("precisa_link", None)
            if item.get("foto_origem") == "canal" or not item.get("foto"):
                # O ML bloqueia a página do produto para servidores, mas abre o seu link de afiliado (meli.la):
                # aproveita para trocar a foto do canal pela foto oficial da loja.
                try:
                    lido = ler_pagina(valor)
                except Exception:
                    lido = {}
                if lido.get("foto") and not lido.get("_bloqueado"):
                    item["foto"], item["foto_origem"] = lido["foto"], "loja"
        elif campo == "prime":
            item["prime"] = valor.lower().split()[0] in ("sim", "s", "1", "yes")  # "prime: sim 189,37"
            num = re.search(r"\d[\d.]*(?:,\d{1,2})?", valor)
            if item["prime"] and num:
                item["sem_prime"] = numero_br(num.group(0))
            if not item["prime"]:
                item.pop("sem_prime", None)
        elif campo == "cupom":
            item["cupom"] = "" if valor == "-" else valor
        elif campo == "obs":
            if valor == "-":
                item.pop("obs", None)
            else:
                item["obs"] = valor
            if valor == "-":
                item.pop("cupom_loja", None)


def contar_garimpo(estado, item, campo, quanto=1):
    """Placar do garimpo por fonte e por dia (o dia em que a oferta foi achada): achados, aprovados, reprovados.
    Só conta o que veio do radar (canal ou Promobit); link que você mandou não entra."""
    fonte = item.get("origem")
    if not fonte:
        return
    dia = (item.get("garimpado_em") or agora().isoformat())[:10]
    g = estado.setdefault("garimpo", {})
    linha = g.setdefault(dia, {}).setdefault(fonte, {"achados": 0, "aprovados": 0, "reprovados": 0})
    linha[campo] = max(0, linha.get(campo, 0) + quanto)
    if item.get("palavra"):  # aprende quais palavras você aprova (entra na nota da peneira)
        lp = stats_palavras(estado).setdefault(item["palavra"], {"achados": 0, "aprovados": 0, "reprovados": 0})
        lp[campo] = max(0, lp.get(campo, 0) + quanto)
    for velho in sorted(g)[:-14]:  # guarda 2 semanas
        del g[velho]


def pra_lixeira(estado, item, de):
    """Guarda o que foi descartado ou tirado da fila (os 40 últimos), para dar para desfazer pelo painel."""
    if de == "revisar" and not item.get("expirou"):
        contar_garimpo(estado, item, "reprovados")
    copia = {k: v for k, v in item.items() if k != "msg_id"}
    copia["removido_em"], copia["removido_de"] = agora().isoformat(timespec="seconds"), de
    estado["lixeira"] = ([copia] + estado.get("lixeira", []))[:40]


def aprovar(estado, item):
    estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
    if any(x["chave"] == item["chave"] for x in estado["fila"]):
        return "Esse link já estava na fila."
    contar_garimpo(estado, item, "aprovados")
    limpo = {k: v for k, v in item.items() if k not in ("id", "msg_id")}
    limpo["aprovado_em"] = agora().isoformat(timespec="minutes")
    estado["fila"].append(limpo)
    return f"✅ Aprovada. Posição {len(estado['fila'])} na fila."


def postar_agora(estado, item):
    publicar(item)
    estado["fila"] = [x for x in estado["fila"] if x is not item]
    guardar_ultimo(estado, item)
    estado["postados"].append(registro_postado(item, agora()))
    responder_admin(f"📢 Postei agora: {html.escape(item['nome'])}. Fila: {len(estado['fila'])}.")


def checar_validade(estado):
    """Oferta parada na fila há muito tempo: pergunta se ainda vale (o preço pode ter mudado)."""
    horas = CONFIG.get("validade_horas", 12)
    if not horas or radar_pausado(CONFIG.get("radar", {})):  # de madrugada não incomoda
        return
    agora_ = agora()
    for item in estado["fila"]:
        if not item.get("aprovado_em"):
            item["aprovado_em"] = agora_.isoformat(timespec="minutes")
            continue
        if item.get("aviso_validade"):
            continue
        if agora_ - datetime.fromisoformat(item["aprovado_em"]) < timedelta(hours=horas):
            continue
        ident = id_story(item)
        botoes = {"inline_keyboard": [[{"text": "🚀 Postar agora", "callback_data": f"vp:{ident}"},
                                       {"text": "✅ Manter", "callback_data": f"vm:{ident}"},
                                       {"text": "🗑️ Remover", "callback_data": f"vr:{ident}"}]]}
        responder_admin(f"⏰ <b>Na fila há mais de {horas} horas.</b> Abra o link e confira se o preço ainda vale:\n\n"
                        + montar_post(item), botoes)
        item["aviso_validade"] = True


def vencer_revisao(estado):
    """Oferta esquecida no Revisar por mais de revisar_validade_horas vai para a lixeira (dá para restaurar),
    para não acumular dezenas de itens com preço que já pode ter mudado."""
    horas = CONFIG.get("revisar_validade_horas", 12)
    if not horas:
        return
    limite = agora() - timedelta(hours=horas)
    vencidos = [x for x in estado["revisar"]
                if datetime.fromisoformat(x.get("na_revisao_desde") or x.get("garimpado_em") or agora().isoformat()) < limite]
    for item in vencidos:
        estado["revisar"].remove(item)
        item["expirou"] = True
        pra_lixeira(estado, item, "revisar")
    if vencidos:
        log(f"{len(vencidos)} oferta(s) passaram de {horas}h sem revisão e foram para a lixeira.")


def avisar_antes_de_postar(estado):
    """Uns minutos antes do post automático, manda no seu privado qual oferta vai sair, para conferir o preço.
    Se não estiver valendo, o botão Remover tira da fila e sai a próxima no lugar."""
    antes = CONFIG.get("aviso_antes_minutos", 10)
    if not antes or not estado["fila"]:
        return
    dt = agora()
    prox = proximo_post(estado, dt)
    if not prox or not 0 <= (prox - dt).total_seconds() <= antes * 60:
        return
    item = estado["fila"][0]
    marca = f"{prox:%Y-%m-%d %H}|{item['chave']}"
    if estado.get("aviso_post") == marca:
        return
    ident = id_story(item)
    botoes = {"inline_keyboard": [[{"text": "🗑️ Remover da fila", "callback_data": f"vr:{ident}"}]]}
    responder_admin(f"⏳ <b>Sai às {prox:%H:%M}.</b> Confira se o preço ainda está certo. Se não estiver, "
                    "remova e sai a próxima da fila no lugar:\n\n" + montar_post(item), botoes)
    estado["aviso_post"] = marca


def botao_validade(estado, acao, ident):
    item = next((x for x in estado["fila"] if id_story(x) == ident), None)
    if not item:
        return "Essa oferta já saiu da fila."
    if acao == "vp":
        postar_agora(estado, item)
        return None
    if acao == "vm":
        item["aprovado_em"] = agora().isoformat(timespec="minutes")
        item.pop("aviso_validade", None)
        return f"✅ Mantida na fila. Se não sair antes, pergunto de novo em {CONFIG.get('validade_horas', 12)} horas."
    estado["fila"] = [x for x in estado["fila"] if x is not item]
    pra_lixeira(estado, item, "fila")
    return f"🗑️ Removida da fila. Fila: {len(estado['fila'])}."


def tratar_botao(estado, cb):
    try:
        telegram("answerCallbackQuery", callback_query_id=cb["id"])
    except Exception:
        pass  # clique antigo: o Telegram já não aceita resposta, mas a ação vale
    acao, _, ident = (cb.get("data") or "").partition(":")
    if acao in ("st", "sf", "sv"):
        botao_story(estado, acao, ident)
        return
    msg = cb.get("message") or {}
    if acao in ("vp", "vm", "vr"):
        resultado = botao_validade(estado, acao, ident)
        try:
            telegram("editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                     reply_markup={"inline_keyboard": []})
        except Exception:
            pass
        if resultado:
            responder_admin(resultado, responder_a=msg.get("message_id"))
        return
    item = next((x for x in estado["revisar"] if str(x["id"]) == ident), None)
    msg = cb.get("message") or {}
    if item and acao == "px":  # liga/desliga "no Pix" e manda a revisão de novo com o post atualizado
        item["pix"] = not item.get("pix")
        try:
            telegram("deleteMessage", chat_id=msg["chat"]["id"], message_id=msg["message_id"])
        except Exception:
            pass
        estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
        mandar_para_revisao(estado, item)
        return
    if not item:
        resultado = "Essa oferta já foi resolvida."
    elif acao == "ok":
        falta = faltando(item)
        resultado = f"Ainda falta: {', '.join(falta)}." if falta else aprovar(estado, item)
    else:
        estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
        pra_lixeira(estado, item, "revisar")
        resultado = "❌ Descartada."
    try:
        telegram("editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                 reply_markup={"inline_keyboard": []})
    except Exception:
        pass
    responder_admin(resultado, responder_a=msg.get("message_id"))


def processar_update(estado, up):
    """Trata uma mensagem ou clique vindo do Telegram (usado pelo GitHub e pelo servidor do Render)."""
    admin = segredo("ADMIN_ID")
    if up.get("callback_query"):
        cb = up["callback_query"]
        if str(cb.get("from", {}).get("id")) == admin:
            tratar_botao(estado, cb)
        return
    msg = up.get("message") or {}
    if msg.get("chat", {}).get("type") != "private":
        return
    if str(msg.get("from", {}).get("id")) != admin:
        log(f"Ignorei mensagem de {msg.get('from', {}).get('id')}: não é o ADMIN_ID cadastrado.")
        return
    texto = (msg.get("text") or msg.get("caption") or "").strip()
    foto = msg["photo"][-1]["file_id"] if msg.get("photo") else None
    respondida = (msg.get("reply_to_message") or {}).get("message_id")
    item = next((x for x in estado["revisar"] if respondida and x.get("msg_id") == respondida), None)
    if item:
        aplicar_correcao(item, texto)
        if foto:
            item["foto"] = foto
            item.pop("foto_origem", None)
        estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
        mandar_para_revisao(estado, item)
    else:
        tratar_comando(estado, texto, foto)


def ler_mensagens(estado):
    if SIMULAR:
        return
    faltam = [n for n in ("TELEGRAM_TOKEN", "TELEGRAM_CHANNEL", "ADMIN_ID") if not segredo(n)]
    if faltam:
        log(f"ATENÇÃO: faltam os segredos {', '.join(faltam)}. Sem eles não leio suas mensagens.")
        if "TELEGRAM_TOKEN" in faltam:
            return
    try:
        updates = telegram("getUpdates", offset=estado["offset_telegram"] + 1 if estado["offset_telegram"] else None,
                           timeout=0, allowed_updates=["message", "callback_query"])
    except RuntimeError as e:
        if "webhook" in str(e).lower():
            log("O servidor do Render está ligado: ele responde suas mensagens na hora. Aqui eu só posto.")
            return
        raise
    log(f"Telegram: {len(updates)} mensagem(ns)/clique(s) novos.")
    for up in updates:
        estado["offset_telegram"] = up["update_id"]
        processar_update(estado, up)


# ---------------------------------------------------------------- stories para o Instagram

def guardar_ultimo(estado, item):
    """Guarda as últimas ofertas postadas (completas) para o /story e para repostar pelo Histórico do painel."""
    limpo = {k: v for k, v in item.items()
             if k in ("nome", "de", "por", "foto", "loja", "cupom", "cupom_loja", "obs", "prime", "sem_prime", "pix", "titulo",
                      "parcelas", "parcela_valor", "chave", "link", "link_produto", "origem", "fonte", "post_origem")}
    antigos = [u for u in estado.get("ultimos", []) if u.get("chave") != limpo.get("chave")]
    estado["ultimos"] = ([limpo] + antigos)[:40]


def registro_postado(item, dt):
    """Linha do histórico (o link vai junto para o Histórico do painel abrir o produto certo)."""
    r = {"chave": item["chave"], "loja": item["loja"], "nome": item["nome"], "data": dt.isoformat(timespec="minutes")}
    for k in ("link", "link_produto", "titulo"):
        if item.get(k):
            r[k] = item[k]
    return r


def baixar_foto(foto):
    if not foto:
        return None
    try:
        if not foto.startswith("http"):  # foto que você mandou no Telegram: pega pelo file_id
            caminho = telegram("getFile", file_id=foto)["file_path"]
            foto = f"https://api.telegram.org/file/bot{segredo('TELEGRAM_TOKEN')}/{caminho}"
        req = urllib.request.Request(foto, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except Exception as e:
        log(f"Não consegui baixar a foto para o story ({e}).")
        return None


def enviar_multipart(metodo, campos, campo_arquivo, dados, nome, tipo):
    """Manda um arquivo para o Telegram como upload (em vez de passar um endereço)."""
    fronteira = "----achados" + hashlib.md5(dados[:64]).hexdigest()
    partes = []
    for campo, valor in campos.items():
        if valor is None:
            continue
        valor = json.dumps(valor) if isinstance(valor, (dict, list)) else valor
        partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{campo}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{campo_arquivo}"; filename="{nome}"\r\n'
                  f"Content-Type: {tipo}\r\n\r\n".encode() + dados + b"\r\n")
    partes.append(f"--{fronteira}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{segredo('TELEGRAM_TOKEN')}/{metodo}",
                                 data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={fronteira}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            r = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        r = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
    if not r.get("ok"):
        raise RuntimeError(f"Telegram recusou {metodo}: {r.get('description')}")
    return r["result"]


def enviar_arquivo(dados, nome, legenda=""):
    """Manda um arquivo (sem compressão) no seu privado."""
    return enviar_multipart("sendDocument", {"chat_id": segredo("ADMIN_ID"), "caption": legenda},
                            "document", dados, nome, "image/png")


def cmd_story(estado, texto):
    try:
        import story
    except ImportError:
        responder_admin("Para gerar stories o servidor precisa do Pillow. No Render, mude o Build Command para "
                        "<code>pip install -r requirements.txt</code>.")
        return
    canal = CONFIG.get("canal_publico", "").replace("@", "").replace("https://t.me/", "")
    partes = texto.split()
    if partes[0].lower() == "/storyfixo":
        enviar_arquivo(story.story_fixo(canal, estado.get("story_cfg")), "story_alpha_promo.png",
                       "Story fixo do canal. Poste e coloque o sticker de link do canal na parte de baixo.")
        return
    ultimos = estado.get("ultimos", [])
    if not ultimos:
        responder_admin("Ainda não tenho oferta postada guardada. Depois que a próxima oferta sair no canal, "
                        "mande /story de novo.")
        return
    if len(partes) == 1:  # sem número: mostra as 3 últimas postadas para você escolher
        botoes = []
        for i, x in enumerate(ultimos[:3], 1):
            botoes.append([{"text": f"🛒 {i}. {x.get('nome', '')[:30]}", "callback_data": "sv:" + id_story(x)},
                           {"text": "📸 Prova", "callback_data": "st:" + id_story(x)}])
        botoes.append([{"text": "Story fixo do canal", "callback_data": "sf:"}])
        responder_admin("De qual oferta você quer o story? 🛒 é o modelo para vender (com o link do produto e o "
                        "do canal); 📸 Prova é o de \"saiu no canal\".",
                        {"inline_keyboard": botoes})
        return
    try:
        item = ultimos[int(partes[1]) - 1]
    except (IndexError, ValueError):
        responder_admin(f"Tenho as {len(ultimos)} últimas postadas guardadas. Use /story para escolher, ou /story 2.")
        return
    gerar_story(story, item, canal, estado.get("story_cfg"))


def id_story(item):
    return hashlib.md5(item.get("chave", item.get("nome", "")).encode()).hexdigest()[:12]


def gerar_story(story, item, canal, cfg=None, modelo="prova"):
    if modelo == "venda":
        enviar_arquivo(story.story_venda(item, baixar_foto(item.get("foto")), canal, cfg=cfg), "story_venda.png",
                       f"Story para vender: {item.get('nome', '')}.")
        enviar_links_story(item, canal)
        return
    enviar_arquivo(story.story_oferta(item, baixar_foto(item.get("foto")), canal, cfg=cfg), "story_oferta.png",
                   f"Story de prova: {item.get('nome', '')}. Coloque o sticker de link do canal embaixo.")


def enviar_links_story(item, canal):
    """Os dois links do story de venda, prontos para copiar com um toque (no Telegram, texto em <code> copia)."""
    loja = {"amazon": "AMAZON", "mercadolivre": "MERCADO LIVRE", "shopee": "SHOPEE"}.get(item.get("loja"), "LOJA")
    responder_admin(
        "No Instagram: sticker de link, cole, e em <b>Personalizar texto do sticker</b> escreva o texto sugerido.\n\n"
        f"🛒 <b>Link do produto</b> (toque para copiar):\n<code>{html.escape(item.get('link', ''))}</code>\n"
        f"Texto do sticker: <code>COMPRAR NA {loja}</code>\n\n"
        f"📢 <b>Link do canal</b>:\n<code>https://t.me/{html.escape(canal)}</code>\n"
        "Texto do sticker: <code>MAIS ACHADOS AQUI</code>")


def botao_story(estado, acao, ident):
    """Clique num botão da lista do /story."""
    if acao == "sf":
        cmd_story(estado, "/storyfixo")
        return
    modelo = "venda" if acao == "sv" else "prova"
    item = next((x for x in estado.get("ultimos", []) if id_story(x) == ident), None)
    if not item:
        responder_admin("Não achei mais essa oferta. Mande /story de novo.")
        return
    try:
        import story
    except ImportError:
        cmd_story(estado, "/story 1")  # mostra o aviso do Pillow
        return
    gerar_story(story, item, CONFIG.get("canal_publico", "").replace("@", "").replace("https://t.me/", ""),
                estado.get("story_cfg"), modelo)


def tratar_comando(estado, texto, foto=None):
    cmd = texto.split()[0].lower() if texto else ""
    if cmd in ("/start", "/ajuda"):
        responder_admin(
            "Me manda só o link de afiliado (Amazon, Mercado Livre ou Shopee). Eu leio nome, preço e foto "
            "e te mando para revisar. Se quiser, escreva nas linhas de baixo o nome, o preço (De 199 por 129) "
            "e um título, e isso vale mais que o que eu ler.\n\n"
            "Comandos:\n/fila (ver a fila)\n/remover N (tirar o item N da fila)\n"
            "/postar (posta agora a 1ª da fila; /postar 3 posta a 3ª)\n"
            "/story (escolher uma das 3 últimas postadas para virar story)\n/storyfixo (story de divulgação do canal)\n"
            "/pendentes (ofertas esperando revisão)\n/aprovartudo (aprova todas as pendentes completas)\n/ajuda")
    elif cmd == "/fila":
        if not estado["fila"]:
            responder_admin("A fila está vazia.")
        else:
            linhas = [f"{i}. [{x['loja']}] {html.escape(x['nome'])}" for i, x in enumerate(estado["fila"], 1)]
            responder_admin("Fila de postagem:\n" + "\n".join(linhas))
    elif cmd == "/remover":
        try:
            n = int(texto.split()[1])
            tirado = estado["fila"].pop(n - 1)
            pra_lixeira(estado, tirado, "fila")
            responder_admin(f"Tirei da fila: {html.escape(tirado['nome'])}")
        except (IndexError, ValueError):
            responder_admin("Use assim: /remover 2 (o número que aparece no /fila)")
    elif cmd in ("/story", "/storyfixo"):
        cmd_story(estado, texto)
    elif cmd == "/postar":
        try:
            n = int(texto.split()[1]) if len(texto.split()) > 1 else 1
            item = estado["fila"][n - 1]
        except (IndexError, ValueError):
            responder_admin("Não achei esse item. Veja os números com /fila." if estado["fila"] else "A fila está vazia.")
            return
        postar_agora(estado, item)
    elif cmd == "/pendentes":
        if not estado["revisar"]:
            responder_admin("Nada esperando revisão.")
        for item in list(estado["revisar"]):
            estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
            mandar_para_revisao(estado, item)
    elif cmd == "/aprovartudo":
        prontos = [x for x in estado["revisar"] if not faltando(x)]
        for item in prontos:
            aprovar(estado, item)
        responder_admin(f"Aprovei {len(prontos)} oferta(s). Fila: {len(estado['fila'])}.")
    else:
        item, erro = interpretar_mensagem(texto, foto)
        if erro:
            responder_admin(erro)
            return
        if any(x["chave"] == item["chave"] for x in estado["fila"]):
            responder_admin("Esse link já está na fila.")
            return
        antigo = next((x for x in estado["revisar"] if x["chave"] == item["chave"]), None)
        if antigo and not faltando(antigo):
            responder_admin("Esse link já está esperando revisão (é só aprovar a mensagem anterior).")
            return
        if antigo:  # a leitura anterior falhou: troca pela nova tentativa
            estado["revisar"] = [x for x in estado["revisar"] if x["id"] != antigo["id"]]
        mandar_para_revisao(estado, item)


# ---------------------------------------------------------------- Shopee

CONSULTA_SHOPEE = """query ($palavra: String, $limite: Int) {
  productOfferV2(keyword: $palavra, sortType: 2, page: 1, limit: $limite) {
    nodes { itemId productName price priceMin priceMax priceDiscountRate
            sales ratingStar imageUrl offerLink shopName shopId }
  }
}"""


CONSULTA_SHOPEE_ITEM = """query ($item: Int64, $loja: Int64) {
  productOfferV2(itemId: $item, shopId: $loja, page: 1, limit: 1) {
    nodes { itemId productName price priceMin priceMax priceDiscountRate
            sales ratingStar imageUrl offerLink shopName }
  }
}"""


LINK_CURTO_SHOPEE = """mutation ($url: String!, $sub: [String!]) {
  generateShortLink(input: {originUrl: $url, subIds: $sub}) { shortLink }
}"""


def shopee_link_curto(url):
    """Seu link curto de afiliado da Shopee (s.shopee.com.br/...) para qualquer produto. None sem a chave da API."""
    if not (segredo("SHOPEE_APP_ID") and segredo("SHOPEE_SECRET")):
        return None
    try:
        dados = shopee_consulta(LINK_CURTO_SHOPEE, {"url": url, "sub": ["alphapromo"]}, campo="generateShortLink")
        return dados.get("shortLink")
    except Exception as e:
        log(f"Shopee não gerou o link curto: {e}")
        return None


def shopee_buscar(palavra, limite):
    if not (segredo("SHOPEE_APP_ID") and segredo("SHOPEE_SECRET")):
        exemplos = carregar_json(ARQ_MOCK, {})
        return exemplos.get(palavra, [])
    return shopee_consulta(CONSULTA_SHOPEE, {"palavra": palavra, "limite": limite})


def shopee_consulta(consulta, variaveis, campo="productOfferV2"):
    app_id, secret = segredo("SHOPEE_APP_ID"), segredo("SHOPEE_SECRET")
    corpo = json.dumps({"query": consulta, "variables": variaveis}, separators=(",", ":"))
    ts = str(int(time.time()))
    assinatura = hashlib.sha256(f"{app_id}{ts}{corpo}{secret}".encode()).hexdigest()
    headers = {"Content-Type": "application/json",
               "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={assinatura}"}
    texto, _ = http(SHOPEE_URL, corpo.encode(), headers)
    resp = json.loads(texto)
    if resp.get("errors"):
        raise RuntimeError(f"Shopee respondeu com erro: {resp['errors']}")
    dados = resp["data"][campo]
    return dados["nodes"] if campo == "productOfferV2" else dados


def oferta_shopee(no):
    preco = float(no.get("priceMin") or no.get("price") or 0)
    desconto = float(no.get("priceDiscountRate") or 0)
    de = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
    return {
        "loja": "shopee",
        "chave": f"shopee:{no.get('shopId')}.{no.get('itemId')}" if no.get("shopId") else f"shopee:{no.get('itemId')}",
        "link_produto": f"https://shopee.com.br/product/{no.get('shopId')}/{no.get('itemId')}" if no.get("shopId") else "",
        "nome": (no.get("productName") or "").strip()[:120],
        "por": preco or None,
        "de": de,
        "desconto": desconto,
        "nota": float(no.get("ratingStar") or 0),
        "vendas": int(no.get("sales") or 0),
        "foto": no.get("imageUrl"),
        "link": no.get("offerLink"),
    }


def shopee_so_api():
    """Com a chave da API, a Shopee vem só da busca própria: as dos canais costumavam ser ruins (e o link curto
    deles às vezes levava a outro produto). config shopee.so_api = false volta a aceitar dos canais."""
    return bool(segredo("SHOPEE_APP_ID")) and CONFIG.get("shopee", {}).get("so_api", True)


def buscar_shopee(estado):
    """Com a chave da Open API: a cada 15 min (config buscar_cada_minutos) procura na Shopee algumas palavras do garimpo (em rodízio) e põe as
    que passam no filtro (desconto, nota, vendas) na mesma peneira do radar. Já vêm com o seu link de afiliado."""
    regras = CONFIG["shopee"]
    if not regras.get("ativo", True) or os.environ.get("RENDER") or not segredo("SHOPEE_APP_ID"):
        return
    teste = os.environ.get("BOT_PEDIDO") == "shopee-teste"  # pedido na mão: busca agora e manda as 3 melhores para Revisar
    if radar_pausado(CONFIG.get("radar", {})) and not teste:
        return
    r = estado.setdefault("radar", {"visto": {}, "dia": "", "hoje": 0, "chaves": []})
    ajuste_radar(estado)
    cada = regras.get("buscar_cada_minutos", 15)
    ultima = r.get("shopee_em")
    if not teste and ultima and agora() - datetime.fromisoformat(ultima) < timedelta(minutes=cada - 1):
        return
    r["shopee_em"] = agora().isoformat(timespec="minutes")
    palavras = palavras_radar()
    if not palavras:
        return
    por_vez = regras.get("palavras_por_hora", 8)
    ini = r.get("shopee_indice", 0) % len(palavras)
    da_vez = (palavras + palavras)[ini:ini + por_vez]
    r["shopee_indice"] = ini + por_vez
    usadas = set(r.get("chaves", [])) | {x["chave"] for x in estado["fila"] + estado["revisar"] + r.get("peneira", [])}
    conta, novas = {}, 0
    for palavra in da_vez:
        try:
            nos = shopee_buscar(palavra, regras.get("resultados_por_palavra", 20))
        except Exception as e:
            log(f"Shopee falhou em '{palavra}': {e}")
            continue
        for no in nos:
            o = oferta_shopee(no)
            if not o["link"] or not o["nome"] or o["chave"] in usadas:
                continue
            if o["desconto"] < regras["desconto_minimo"] or o["nota"] < regras["nota_minima"] \
                    or o["vendas"] < regras["vendas_minimas"]:
                continue
            usadas.add(o["chave"])
            item = {k: o[k] for k in ("loja", "chave", "nome", "por", "de", "foto", "link", "link_produto") if o.get(k)}
            item.update(origem="shopee", fonte="shopee", foto_origem="loja", shopee_api=True)
            r["chaves"] = (r.get("chaves", []) + [item["chave"]])[-3000:]
            if peneirar(estado, r, item, conta):
                novas += 1
    if teste:
        minhas = sorted([x for x in r["peneira"] if x.get("fonte") == "shopee"], key=lambda x: -x.get("nota", 0))[:3]
        for item in minhas:
            r["peneira"].remove(item)
            r.setdefault("nomes", []).append([nome_tokens(item.get("nome")), agora().isoformat(timespec="minutes")])
            mandar_para_revisao(estado, item)
            contar_garimpo(estado, item, "achados")
        log(f"Teste da Shopee: {len(minhas)} oferta(s) mandadas para Revisar.")
    soltar_peneira(estado, r)
    log(f"Shopee: {novas} oferta(s) na peneira ({', '.join(da_vez)}).")


# ---------------------------------------------------------------- postagem

def agenda_do_dia(data_iso):
    """Minuto em que cada horário posta naquele dia: {15: 37, 16: 12, ...}. É sorteado, mas sempre igual para
    o mesmo dia (o painel mostra a mesma previsão que o bot usa), com pelo menos `intervalo_minimo` minutos
    entre um post e o próximo. Fica com cara de gente postando, não de robô às 15:00, 16:00..."""
    horarios = sorted(CONFIG.get("horarios", []))
    if not CONFIG.get("minutos_aleatorios", True):
        return {h: 0 for h in horarios}
    rnd = random.Random(f"agenda-{data_iso}")
    intervalo = CONFIG.get("intervalo_minimo", 25)
    agenda, anterior = {}, None
    for h in horarios:
        menor = 2
        if anterior is not None and anterior[0] == h - 1:
            menor = max(menor, anterior[1] + intervalo - 60)
        agenda[h] = rnd.randint(min(menor, 55), 55)
        anterior = (h, agenda[h])
    return agenda


def _ultimo_post(estado, dt):
    """Último post AGENDADO. O que você posta na mão ("postar agora") não empurra os horários da agenda."""
    agendados = [p for p in estado.get("postados", []) if p.get("slot")]
    try:
        ultimo = datetime.fromisoformat(agendados[-1]["data"])
        return ultimo.replace(tzinfo=dt.tzinfo) if ultimo.tzinfo is None else ultimo
    except (IndexError, ValueError, KeyError):
        return None


def horario_vencido(estado, dt):
    """O horário de hoje que já pode postar agora, ou None. Cada horário tem um minuto sorteado (agenda_do_dia)
    e vale a partir dele por até `janela_minutos`, para não sair rajada de posts atrasados se o GitHub demorar."""
    hoje = dt.date().isoformat()
    usados = estado["slots_usados"].get(hoje, [])
    agenda = agenda_do_dia(hoje)
    agora_min = dt.hour * 60 + dt.minute
    janela = CONFIG.get("janela_minutos", 40)
    vencidos = [h for h in CONFIG["horarios"]
                if 0 <= agora_min - (h * 60 + agenda[h]) <= janela and h not in usados]
    if not vencidos:
        return None
    ultimo = _ultimo_post(estado, dt)
    if ultimo and dt - ultimo < timedelta(minutes=CONFIG.get("intervalo_minimo", 25)):
        log("Horário chegou, mas o último post foi há pouco. Espero um pouco.")
        return None
    return vencidos[0]


def proximo_post(estado, dt):
    """Quando sai o próximo post de hoje (data e hora com o minuto sorteado), ou None."""
    hoje = dt.date().isoformat()
    usados = estado["slots_usados"].get(hoje, [])
    agenda = agenda_do_dia(hoje)
    ultimo = _ultimo_post(estado, dt)
    for h in sorted(CONFIG["horarios"]):
        quando = dt.replace(hour=h, minute=agenda[h], second=0, microsecond=0)
        if h in usados or quando + timedelta(minutes=CONFIG.get("janela_minutos", 40)) < dt:
            continue
        if ultimo:
            quando = max(quando, ultimo + timedelta(minutes=CONFIG.get("intervalo_minimo", 25)))
        return quando
    return None


def registrar_post(estado, item, slot, dt):
    estado["fila"] = [x for x in estado["fila"] if x["chave"] != item["chave"]]
    guardar_ultimo(estado, item)
    estado["postados"].append(registro_postado(item, dt))
    if slot != "teste":
        estado["slots_usados"].setdefault(dt.date().isoformat(), []).append(slot)
        estado["postados"][-1]["slot"] = slot  # o painel mostra cada horário com a hora em que saiu de verdade


def postar_se_for_hora(estado):
    dt = agora()
    slot = "teste" if FORCAR else horario_vencido(estado, dt)
    if not slot:
        prox = proximo_post(estado, dt)
        log(f"Nenhum horário de postagem agora. Próximo: {prox:%H:%M}." if prox else
            "Nenhum horário de postagem agora. Próximo: amanhã.")
        return
    if not estado["fila"]:
        log("Chegou um horário de postagem, mas a fila está vazia.")
        return
    item = estado["fila"][0]
    publicar(item)
    # Um post por execução: se o GitHub atrasar e passar mais de um horário,
    # o próximo sai na execução seguinte, sem rajada.
    registrar_post(estado, item, slot, dt)
    log(f"Postado: {item['nome']}")


def esperar_e_postar():
    """O tick roda de 5 em 5 (ou 10 em 10) minutos, o que arredondaria os horários (15:40, 16:20...).
    Se o próximo post cai antes do tick seguinte, a rodada espera até o minuto (e segundo) exato e posta,
    para sair em horários bem quebrados, tipo 13:33, 16:28, 20:31."""
    if SIMULAR or FORCAR or os.environ.get("RENDER"):
        return
    estado, _ = carregar_estado()
    dt = agora()
    prox = proximo_post(estado, dt)
    if not estado["fila"] or not prox:
        return
    espera = (prox - dt).total_seconds() + random.randint(0, 45)
    if not 0 < espera <= CONFIG.get("espera_max_minutos", 10) * 60:
        return
    log(f"Próximo post às {prox:%H:%M}. Esperando {espera / 60:.1f} min para postar no minuto certo.")
    time.sleep(espera)
    pego = {}

    def reservar(est):  # tira da fila e marca o horário ANTES de publicar: se o salvar repetir, não posta duas vezes
        pego.clear()
        dt2 = agora()
        slot = horario_vencido(est, dt2)
        if slot and est["fila"]:
            pego.update(item=est["fila"][0], slot=slot, dt=dt2)
            registrar_post(est, est["fila"][0], slot, dt2)

    if not com_estado(reservar) or not pego:
        return
    try:
        publicar(pego["item"])
        log(f"Postado: {pego['item']['nome']}")
    except Exception as e:
        log(f"Erro ao postar, devolvendo para a fila: {e}")

        def devolver(est):
            est["fila"].insert(0, pego["item"])
            dia = pego["dt"].date().isoformat()
            if pego["slot"] in est["slots_usados"].get(dia, []):
                est["slots_usados"][dia].remove(pego["slot"])
            est["postados"] = [p for p in est["postados"] if p["chave"] != pego["item"]["chave"]
                               or p["data"] != pego["dt"].isoformat(timespec="minutes")]
            est["ultimos"] = [u for u in est.get("ultimos", []) if u.get("chave") != pego["item"]["chave"]]
        com_estado(devolver)


def limpar_estado(estado):
    corte = (agora() - timedelta(days=30)).date().isoformat()
    estado["postados"] = [p for p in estado["postados"] if p["data"][:10] >= corte]
    estado["slots_usados"] = {d: v for d, v in estado["slots_usados"].items() if d >= corte}


# ---------------------------------------------------------------- comandos

# ---------------------------------------------------------------- radar de ofertas
# Lê a página pública dos canais de promoção (t.me/s/canal, sem login e sem usar sua conta),
# fica só com o que é do seu nicho, troca o link da Amazon pelo seu e manda para você revisar.
# Nunca copia o texto do canal: nome e preço viram o post no padrão do Alpha Promo, com frase própria.

ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d|d|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?&#]|$)")
LINK_LOJA = re.compile(r"https?://[^\s\"'<>]*(?:amazon\.com\.br|amzn\.to|amzn\.divulgador\.link|"
                       r"mercadolivre\.com\.br|meli\.la|shopee\.com\.br|s\.shopee\.com\.br)[^\s\"'<>]*")


def sem_acento(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def ajuste_radar(estado=None):
    """Palavras que você liga, desliga ou bloqueia pelo painel (estado['radar_cfg']), por cima do config.json."""
    global _AJUSTE_RADAR
    if estado is not None:
        _AJUSTE_RADAR = estado.get("radar_cfg") or {}
    return _AJUSTE_RADAR


_AJUSTE_RADAR = {}


def palavras_radar():
    cfg = CONFIG.get("radar", {})
    aj = ajuste_radar()
    lista = [p for ps in CONFIG.get("palavras", {}).values() for p in ps] + cfg.get("palavras_extra", []) + aj.get("novas", [])
    fora = {sem_acento(p) for p in aj.get("desligadas", [])}
    return [sem_acento(p) for p in dict.fromkeys(lista) if p.strip() and sem_acento(p) not in fora]


def palavras_bloqueadas():
    """As do painel, se você já mexeu nelas lá; senão as do config.json."""
    aj = ajuste_radar()
    lista = aj["bloqueadas"] if "bloqueadas" in aj else CONFIG.get("radar", {}).get("palavras_bloqueadas", [])
    return [sem_acento(p) for p in lista if p.strip()]


def _tem_palavra(texto, palavras):
    """Palavra inteira (aceita plural): 'bone' não casa com 'boneca', 'mochila' casa com 'mochilas'."""
    t = sem_acento(texto)
    return any(re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?:s|es)?(?![a-z0-9])", t) for p in palavras)


def do_nicho(texto):
    return bool(texto) and _tem_palavra(texto, palavras_radar()) and not _tem_palavra(texto, palavras_bloqueadas())


def ler_canal(canal):
    """Posts recentes da página pública do canal: [{id, texto, links, foto}]."""
    pagina, _ = http(f"https://t.me/s/{canal}", headers={"Accept-Language": "pt-BR,pt;q=0.9"})
    if os.environ.get("BOT_SALVAR_PAGINA"):
        (PASTA / "dados" / f"radar_{canal}.html").write_text(pagina, encoding="utf-8")
    posts = []
    for bloco in pagina.split('data-post="')[1:]:
        m = re.match(r"[^/\"]+/(\d+)\"", bloco)
        if not m:
            continue
        corpo = bloco.split('data-post="')[0]
        tm = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', corpo, re.S)
        bruto = tm.group(1) if tm else ""
        links = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', bruto)]
        texto = re.sub(r"<br\s*/?>", "\n", bruto)
        texto = html.unescape(re.sub(r"<[^>]+>", "", texto)).strip()
        links += LINK_LOJA.findall(texto)
        fm = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", corpo)
        posts.append({"id": int(m.group(1)), "texto": texto, "links": list(dict.fromkeys(links)),
                      "foto": fm.group(1) if fm else None})
    return posts


def endereco_final(link):
    """Segue o link curto até a loja. Se a loja bloquear, o endereço final já basta."""
    req = urllib.request.Request(link, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final, pagina = resp.geturl(), resp.read(300000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        final, pagina = e.geturl() or link, ""
    except Exception:
        return link, ""
    return final, pagina


def produto_do_link(link):
    """Devolve {loja, chave, link_produto, meu_link?} ou None se não for de loja conhecida."""
    final, pagina = endereco_final(link)
    m = ASIN.search(final) if "amazon" in final else None
    asin = m.group(1) if m else None
    if not asin:
        # Página no meio do caminho (ex.: amzn.divulgador.link) que leva para UM produto da Amazon.
        # Só vale se a página citar um único produto: o site do próprio canal, por exemplo, mostra
        # dezenas, e pegar o primeiro trazia foto e link de outra coisa (Pokémon no lugar da fritadeira).
        asins = {a.group(1) for a in (ASIN.search(html.unescape(x)) for x in LINK_LOJA.findall(pagina))
                 if a and "amazon" in a.string}
        if len(asins) == 1:
            asin = asins.pop()
    if asin:
        tag = CONFIG.get("radar", {}).get("tag_amazon", "")
        meu = f"https://www.amazon.com.br/dp/{asin}" + (f"?tag={tag}" if tag else "")
        return {"loja": "amazon", "chave": f"amazon:{asin}", "link_produto": meu, "meu_link": meu}
    if "/social/" in final and ("mercadoli" in final or "meli.la" in link):
        achado = produto_principal_vitrine(pagina)
        if achado:
            achado["vitrine"] = extrair_da_pagina(pagina, "mercadolivre")
            cfg = CONFIG.get("radar", {})
            if cfg.get("ml_matt_word") and cfg.get("ml_matt_tool"):
                sep = "&" if "?" in achado["link_produto"] else "?"
                achado["meu_link"] = f"{achado['link_produto']}{sep}matt_word={cfg['ml_matt_word']}&matt_tool={cfg['ml_matt_tool']}"
            return achado
    for alvo in [final, pagina[:300000]]:
        m = re.search(r"MLB-?(\d{6,})", alvo)
        if m and ("mercadoli" in final or "meli.la" in link):
            prod = re.search(r"https://(?:produto\.mercadolivre\.com\.br/MLB-\d+[^\s\"'<>?#]*|www\.mercadolivre\.com\.br/[^\s\"'<>?#]+/p/MLB\d+)", pagina)
            url = prod.group(0) if prod else f"https://produto.mercadolivre.com.br/MLB-{m.group(1)}"
            ident = re.search(r"MLB-?(\d+)", url).group(1)
            achado = {"loja": "mercadolivre", "chave": f"mercadolivre:MLB{ident}", "link_produto": url}
            if "/social/" in final:  # link de afiliado do canal: a vitrine abre para servidores (a página do produto não)
                achado["vitrine"] = extrair_da_pagina(pagina, "mercadolivre")
            cfg = CONFIG.get("radar", {})
            if cfg.get("ml_matt_word") and cfg.get("ml_matt_tool"):  # mesmo código que o seu link meli.la carrega
                achado["meu_link"] = (f"{url}?matt_word={cfg['ml_matt_word']}&matt_tool={cfg['ml_matt_tool']}")
            return achado
    m = re.search(r"i\.(\d+)\.(\d+)", final) or re.search(r"shopee\.com\.br/(?:[^/?]+/)?(\d{5,})/(\d{5,})", final)
    if m:
        achado = {"loja": "shopee", "chave": f"shopee:{m.group(1)}.{m.group(2)}",
                  "link_produto": f"https://shopee.com.br/product/{m.group(1)}/{m.group(2)}"}
        if segredo("SHOPEE_APP_ID"):  # com a API: nome, preço e foto oficiais e o SEU link curto
            oficial = shopee_por_link(final)
            if oficial:
                achado["vitrine"] = oficial
                achado["shopee_api"] = True
            meu = oficial.pop("link", None) or shopee_link_curto(achado["link_produto"])
            if meu:
                achado["meu_link"] = meu
        return achado
    return None


def produto_principal_vitrine(pagina):
    """Na vitrine de afiliado do ML (/social/...), o produto do post é o da foto principal (og:image).
    A página lista dezenas de outros produtos do perfil: pegar o primeiro link levava para um produto parecido."""
    img = re.search(r'property="og:image" content="[^"]*?/D_[A-Z_]*?(\d+-MLB\d+)_', pagina)
    if not img:
        return None
    foto = pagina.find('"id":"' + img.group(1))
    while foto != -1:
        ini = pagina.rfind('"metadata":{"id":"', 0, foto)
        card = pagina[ini:foto] if ini != -1 and foto - ini < 4000 else ""
        item = re.match(r'"metadata":\{"id":"(MLB\d+)"', card)
        if item:
            url = re.search(r'"url":"([^"]+)"', card)
            if url:
                url = "https://" + url.group(1).encode().decode("unicode_escape").lstrip("/")
                url = url.split("?")[0].split("#")[0] + f"?pdp_filters=item_id%3A{item.group(1)}"
            else:
                url = f"https://produto.mercadolivre.com.br/MLB-{item.group(1)[3:]}"
            return {"loja": "mercadolivre", "chave": f"mercadolivre:{item.group(1)}", "link_produto": url}
        foto = pagina.find('"id":"' + img.group(1), foto + 1)
    return None


def foto_amazon_por_asin(asin):
    """Foto oficial da Amazon pelo código do produto (funciona mesmo quando a página bloqueia)."""
    url = f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.LZZZZZZZ.jpg"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return url if len(resp.read(5000)) >= 2000 else None  # sem foto, a Amazon devolve um gif de 1 pixel
    except Exception:
        return None


PRECO_NAO = r"cupom|cashback|frete|resgat|economi|acima de|em compras|parcela|\b\d{1,2}\s*x\b|\bsem juros|moedas|pontos"


def oferta_do_post(post, canal):
    """Monta a oferta a partir do post. None se não for do nicho ou não tiver link de loja."""
    texto = post["texto"]
    if not do_nicho(texto):
        return None
    candidatos = [l for l in post["links"] if l.startswith("http") and "t.me/" not in l]
    # Links de loja (ou encurtador de loja) primeiro; o resto (site do canal, Instagram...) só depois.
    candidatos.sort(key=lambda l: not LINK_LOJA.match(l))
    if not candidatos:
        return None
    prod = None
    for link in candidatos[:4]:
        prod = produto_do_link(link)
        if prod:
            break
    if not prod or (prod["loja"] == "shopee" and shopee_so_api()):
        return None
    item = {"loja": prod["loja"], "chave": prod["chave"], "origem": canal,
            "link": prod.get("meu_link") or prod["link_produto"], "link_produto": prod["link_produto"],
            "post_origem": f"https://t.me/{canal}/{post['id']}", "link_canal": link}
    if not prod.get("meu_link"):
        item["precisa_link"] = True
    if post.get("foto"):  # foto do canal: só fica se não acharmos a oficial da loja
        item["foto"], item["foto_origem"] = post["foto"], "canal"
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    for l in linhas:  # nome: primeira linha que parece nome de produto (pula a chamada em CAIXA ALTA do canal)
        limpo = re.sub(r"[^\w\s,.+\-/()%°ºªçÇãõáéíóúâêôàÁÉÍÓÚÂÊÔÃÕ]", "", l).strip(" -–:.")
        letras = [c for c in limpo if c.isalpha()]
        gritando = letras and sum(c.isupper() for c in letras) / len(letras) > 0.7
        if (len(limpo) >= 10 and not gritando and
                not re.search(r"r\$|http|cupom|link|compre|clique|exclusiv|\bpor\s+[\d]|\bde\s+[\d]", limpo, re.I)):
            item["nome"] = limpo[:120]
            break
    # Preço: só das linhas de preço. Linhas de cupom, cashback, frete e parcelas também têm "R$",
    # mas não são o preço (ex.: "Resgate o cupom de R$100" virava "por R$ 100, 96% off").
    valores = []
    for l in linhas:
        if re.search(PRECO_NAO, l, re.I):
            if "parcelas" not in item and re.search(r"\d{1,2}\s*x", l, re.I) and re.search(r"sem juros", l, re.I):
                aplicar_parcelas(item, l)  # "em até 10x de R$ 39,90 sem juros" vira a linha de parcelas do post
            if re.search(r"cupom|resgat", l, re.I) and re.search(r"R\$|%", l) and "http" not in l:
                item.setdefault("cupom_loja", re.sub(r"^[^\w]+", "", l).strip()[:80])
            continue
        if re.search(r"\bpix\b", l, re.I) and re.search(r"\d", l):
            item["pix"] = True
        valores += [numero_br(v) for v in re.findall(r"(?:R\$|\bpor\b|\bde\b)\s*(?:R\$)?\s*(\d[\d.]*(?:,\d{1,2})?)", l, re.I)]
    valores = [v for v in valores if v >= 5]
    if valores:
        if len(valores) >= 2 and max(valores[:2]) > min(valores[:2]):
            item["de"], item["por"] = max(valores[:2]), min(valores[:2])
        else:
            item["por"] = valores[0]
    if item.get("de") and item.get("por") and item["por"] < item["de"] * 0.2:  # mais de 80% off: quase sempre leitura errada
        item["por"] = item.pop("de")
        item["aviso_preco"] = True
    if prod.get("vitrine", {}).get("foto"):
        lido = prod["vitrine"]
    else:
        try:
            lido = ler_pagina(item["link_produto"])
        except Exception:
            lido = {}
    if lido.get("foto") and not lido.get("_bloqueado"):
        item["foto"], item["foto_origem"] = lido["foto"], "loja"
    elif item["loja"] == "amazon":
        foto_amz = foto_amazon_por_asin(item["chave"].split(":", 1)[1])
        if foto_amz:
            item["foto"], item["foto_origem"] = foto_amz, "loja"
    if prod.get("shopee_api"):  # produto confirmado pela API da Shopee (o link curto do canal às vezes enganava)
        item["shopee_api"] = True
    if item["loja"] == "mercadolivre" or prod.get("shopee_api") or not item.get("nome"):
        if lido.get("nome") and not lido.get("_bloqueado"):
            item["nome"] = lido["nome"]
        if not item.get("por") and lido.get("por"):
            item["por"], item["de"] = lido["por"], lido.get("de")
            if not item["de"]:
                item.pop("de")
    if not item.get("nome"):  # último recurso: nome pelo endereço do produto
        slug = re.search(r"\.com\.br/([^/?#]+?)(?:/(?:dp|p)/|-_JM|$)", item["link_produto"])
        if slug and not slug.group(1).startswith(("dp", "MLB")):
            item["nome"] = nome_curto(re.sub(r"^MLB-\d+-", "", slug.group(1)).replace("-", " ").capitalize())
    c = re.search(r"cupom\s*[:\-]?\s*([A-Z0-9]{4,20})\b", texto, re.I)
    if c and not c.group(1).isdigit():
        item["cupom"] = c.group(1).upper()
    if re.search(r"prime", texto, re.I) and re.search(r"exclusiv|membro|assinante", texto, re.I):
        item["prime"] = True
    return item


# ---------------------------------------------------------------- Promobit
# Site de promoções da comunidade. Abre para servidor (o Pelando não: Cloudflare). A página traz as ofertas
# prontas no __NEXT_DATA__; o "Ir à loja" é promobit.com.br/Redirect/to/<id>/, que leva até a loja.
LOJAS_PROMOBIT = {"amazon.com.br", "mercadolivre.com.br", "shopee.com.br"}


def ler_promobit(paginas):
    """Ofertas das páginas do Promobit (recentes, destaques), sem repetir, da mais antiga para a mais nova."""
    ofertas = {}
    for url in paginas:
        pagina, _ = http(url, headers={"Accept-Language": "pt-BR,pt;q=0.9"})
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
        if not m:
            raise RuntimeError("a página mudou (sem __NEXT_DATA__)")
        props = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        lista = (props.get("serverOffers") or {}).get("offers", []) + (props.get("serverFeaturedOffers") or [])
        for o in lista:
            if o.get("offerId"):
                ofertas[o["offerId"]] = o
    return [ofertas[k] for k in sorted(ofertas)]


def oferta_do_promobit(o):
    """Oferta do Promobit no formato do radar. None se não for de Amazon/ML/Shopee ou do nicho."""
    if o.get("storeDomain") not in LOJAS_PROMOBIT or (o.get("storeDomain") == "shopee.com.br" and shopee_so_api()):
        return None
    texto = " ".join(str(o.get(k) or "") for k in ("offerTitle", "categoryName", "subcategoryName", "suggestionKeywords"))
    if not do_nicho(texto):
        return None
    link = f"https://www.promobit.com.br/Redirect/to/{o['offerId']}/"
    prod = produto_do_link(link)
    if not prod:
        return None
    item = {"loja": prod["loja"], "chave": prod["chave"], "origem": "promobit", "fonte": "promobit",
            "link": prod.get("meu_link") or prod["link_produto"], "link_produto": prod["link_produto"],
            "post_origem": f"https://www.promobit.com.br/oferta/{o.get('offerSlug', '')}/", "link_canal": link,
            "nome": nome_curto(o.get("offerTitle", ""))}
    if not prod.get("meu_link"):
        item["precisa_link"] = True
    por, de = o.get("offerPrice") or 0, o.get("offerOldPrice") or 0
    if por:
        item["por"] = float(por)
        if de and de > por:
            item["de"] = float(de)
    if o.get("offerCoupon"):
        item["cupom"] = str(o["offerCoupon"]).strip()[:30]
    # foto: a oficial da loja quando abre; se não, a do Promobit
    lido = prod.get("vitrine") or {}
    if not lido.get("foto"):
        try:
            lido = ler_pagina(item["link_produto"])
        except Exception:
            lido = {}
    if lido.get("foto") and not lido.get("_bloqueado"):
        item["foto"], item["foto_origem"] = lido["foto"], "loja"
    elif item["loja"] == "amazon" and foto_amazon_por_asin(item["chave"].split(":", 1)[1]):
        item["foto"], item["foto_origem"] = foto_amazon_por_asin(item["chave"].split(":", 1)[1]), "loja"
    elif o.get("offerPhoto"):
        foto = o["offerPhoto"]
        item["foto"] = foto if foto.startswith("http") else "https://i.promobit.com.br/268/" + foto.lstrip("/")
        item["foto_origem"] = "canal"
    return item


def radar_promobit(estado, r, cfg, conta, usadas, teste, acordando):
    """Lê o Promobit com limite próprio por hora (não come a cota dos canais), mas conta no limite do dia."""
    pcfg = cfg.get("promobit") or {}
    if not pcfg.get("ligado"):
        return 0
    paginas = pcfg.get("paginas") or ["https://www.promobit.com.br/promocoes/recentes/", "https://www.promobit.com.br/"]
    limite_hora, limite_dia = pcfg.get("max_por_hora", 0), cfg.get("max_por_dia", 0)  # 0 = sem limite
    hora = agora().strftime("%Y-%m-%dT%H")
    if r.get("promobit_hora") != hora:
        r["promobit_hora"], r["promobit_nesta_hora"] = hora, 0
    try:
        ofertas = ler_promobit(paginas)
    except Exception as e:
        log(f"Radar: não consegui ler o Promobit ({e}).")
        conta["falhas"].append("promobit")
        return 0
    conta["lidos"] += 1
    visto = r.get("promobit_visto")
    ultimo = max((o["offerId"] for o in ofertas), default=visto or 0)
    if (visto is None or acordando) and not teste:
        r["promobit_visto"] = ultimo
        log("Radar: comecei a acompanhar o Promobit." if visto is None else "Radar: voltei da pausa no Promobit.")
        return 0
    novas = 0
    for o in ofertas:
        if not teste and o["offerId"] <= visto:
            continue
        conta["posts_novos"] += 1
        cheia_dia = bool(limite_dia) and r["hoje"] >= limite_dia
        if not teste and ((limite_hora and r["promobit_nesta_hora"] >= limite_hora) or cheia_dia):
            conta["limite_dia"] = cheia_dia
            conta["limite_hora"] = not cheia_dia
            break
        item = oferta_do_promobit(o)
        if not item:
            continue
        conta["do_nicho"] += 1
        if item["chave"] in usadas:
            conta["repetidas"] += 1
            continue
        usadas.add(item["chave"])
        novas += 1
        if teste:
            log(f"Radar achou no Promobit: {json.dumps(item, ensure_ascii=False)}")
            continue
        r["hoje"] += 1
        r["promobit_nesta_hora"] += 1
        r["chaves"] = (r["chaves"] + [item["chave"]])[-3000:]
        if not peneirar(estado, r, item, conta):
            novas -= 1
    if not teste:
        r["promobit_visto"] = ultimo
    return novas


# ---------------------------------------------------------------- peneira do garimpo
# Em vez de mandar tudo o que acha para Revisar, o radar junta as ofertas numa peneira e, uma vez por hora,
# solta só as melhores (radar.melhores_por_hora). Nota = desconto + quanto você aprova daquela fonte + foto.
# Também barra o mesmo produto vindo de outro canal ou de outra loja (nome parecido nos últimos dias).

PALAVRAS_VAZIAS = {"de", "da", "do", "das", "dos", "para", "pra", "com", "sem", "e", "o", "a", "os", "as", "em", "no", "na",
                   "um", "uma", "kit", "novo", "nova", "original", "oferta", "promocao", "the", "and", "for", "with",
                   "sem", "sabor", "po", "masculino", "masculina", "feminino", "feminina", "preto", "preta", "branco",
                   "branca", "cor", "unidade", "unidades", "x", "ml", "g"}


def nome_tokens(nome):
    return [t for t in re.findall(r"[a-z0-9]+", sem_acento(nome or "")) if t not in PALAVRAS_VAZIAS and len(t) > 1][:8]


def nomes_parecidos(a, b):
    """Mesmo tipo de produto (1ª palavra), metade ou mais das palavras iguais e, se os dois têm números
    (tamanho, modelo), pelo menos um número em comum: 'Creatina Growth 1kg' não é 'Creatina Max 300g'."""
    if not a or not b or a[0] != b[0]:
        return False
    sa, sb = set(a), set(b)
    na, nb = {t for t in sa if any(c.isdigit() for c in t)}, {t for t in sb if any(c.isdigit() for c in t)}
    if na and nb and not na & nb:
        return False
    return len(sa & sb) / len(sa | sb) >= 0.5


def palavra_do_item(item):
    """A palavra do nicho que aparece no nome do produto (a primeira da lista), ou None."""
    texto = f"{item.get('nome', '')} {item.get('titulo', '')}"
    return next((p for p in palavras_radar() if _tem_palavra(texto, [p])), None)


def _taxa(linha):
    """Quanto você aprova: começa em 25% e vai se ajustando com as suas aprovações e descartes."""
    a, r = linha.get("aprovados", 0), linha.get("reprovados", 0)
    return (a + 1) / (a + r + 4)


def stats_palavras(estado):
    """Aprovados e descartados por palavra do nicho. Começa com o que você fez em 13/10 (arquivo de partida)."""
    if "garimpo_palavras" not in estado:
        try:
            estado["garimpo_palavras"] = json.loads((PASTA / "dados" / "garimpo_palavras_inicio.json").read_text(encoding="utf-8"))
        except Exception:
            estado["garimpo_palavras"] = {}
    return estado["garimpo_palavras"]


def nota_oferta(estado, item):
    """Desconto + quanto você aprova daquela fonte + quanto você aprova daquela palavra + foto e cupom."""
    de, por = item.get("de"), item.get("por")
    desconto = min((1 - por / de) * 100, 80) if de and por and de > por else 0
    fonte = {"aprovados": 0, "reprovados": 0}
    for dia, fontes in (estado.get("garimpo") or {}).items():
        for k in fonte:
            fonte[k] += fontes.get(item.get("origem"), {}).get(k, 0)
    palavra = stats_palavras(estado).get(item.get("palavra") or "", {})
    nota = 0.6 * desconto + 40 * _taxa(fonte) + 50 * _taxa(palavra) + (10 if item.get("foto") else 0) + (5 if item.get("cupom") else 0)
    return round(nota - (40 if not por else 0), 1)


def repetida_pelo_nome(estado, r, item):
    tk = nome_tokens(item.get("nome"))
    if len(tk) < 2:
        return False
    dias = CONFIG.get("radar", {}).get("repetido_dias", 7)
    corte = (agora() - timedelta(days=dias)).isoformat()
    r["nomes"] = [n for n in r.get("nomes", []) if n[1] >= corte][-3000:]
    outros = [n[0] for n in r["nomes"]] + [nome_tokens(x.get("nome")) for x in estado["fila"] + estado["revisar"]]
    return any(nomes_parecidos(tk, o) for o in outros)


def peneirar(estado, r, item, conta):
    """Põe a oferta na peneira. False se o nome do produto não é do nicho ou se é repetida pelo nome."""
    if item.get("nome") and CONFIG.get("radar", {}).get("nicho_no_nome", True):
        # A palavra precisa estar no nome do produto, não só em algum canto do post (rodapé, lista de outras ofertas).
        item["palavra"] = palavra_do_item(item)
        if not item["palavra"] or _tem_palavra(item["nome"], palavras_bloqueadas()):
            conta["fora_do_nicho"] = conta.get("fora_do_nicho", 0) + 1
            return False
    if repetida_pelo_nome(estado, r, item):
        conta["repetidas_nome"] = conta.get("repetidas_nome", 0) + 1
        return False
    item["garimpado_em"] = agora().isoformat(timespec="minutes")
    item["nota"] = nota_oferta(estado, item)
    peneira = r.setdefault("peneira", [])
    tk = nome_tokens(item.get("nome"))
    igual = next((x for x in peneira if nomes_parecidos(tk, nome_tokens(x.get("nome")))), None)
    if igual:  # o mesmo produto em dois canais: fica a melhor nota
        conta["repetidas_nome"] = conta.get("repetidas_nome", 0) + 1
        if igual["nota"] >= item["nota"]:
            return False
        peneira.remove(igual)
    peneira.append(item)
    if not CONFIG.get("radar", {}).get("melhores_por_hora"):  # 0 = sem peneira: vai direto, como antes
        soltar_peneira(estado, r, True)
    return True


def soltar_peneira(estado, r, forcado=False):
    """Uma vez por hora (a partir do minuto 40, para juntar bastante), manda as melhores da peneira para Revisar."""
    cfg = CONFIG.get("radar", {})
    peneira = r.setdefault("peneira", [])
    validade = (agora() - timedelta(hours=cfg.get("peneira_horas", 3))).isoformat()
    peneira[:] = [x for x in peneira if x.get("garimpado_em", "") >= validade]
    por_hora = cfg.get("melhores_por_hora", 0)
    hora = agora().strftime("%Y-%m-%dT%H")
    if r.get("soltas_hora") != hora:
        r["soltas_hora"], r["soltas"] = hora, 0
    if por_hora:
        if not forcado and agora().minute < 40:
            return 0
        vagas = por_hora - r["soltas"]
    else:
        vagas = len(peneira)
    soltas = 0
    for item in sorted(peneira, key=lambda x: -x.get("nota", 0))[:max(vagas, 0)]:
        peneira.remove(item)
        if repetida_pelo_nome(estado, r, item):  # entrou parecida em Revisar enquanto esperava
            continue
        r.setdefault("nomes", []).append([nome_tokens(item.get("nome")), agora().isoformat(timespec="minutes")])
        mandar_para_revisao(estado, item)
        contar_garimpo(estado, item, "achados")
        soltas += 1
    r["soltas"] += soltas
    if soltas:
        log(f"Peneira: {soltas} oferta(s) para Revisar; {len(peneira)} esperando.")
    return soltas


def radar_pausado(cfg):
    """Pausa do radar, em horas de Brasília. Ex.: pausa_de 0 e pausa_ate 7 = pausado da meia-noite às 6h59."""
    de, ate = cfg.get("pausa_de"), cfg.get("pausa_ate")
    if de is None or ate is None or de == ate:
        return False
    h = agora().hour
    return de <= h < ate if de < ate else (h >= de or h < ate)


def rodar_radar(estado, teste=False):
    cfg = CONFIG.get("radar", {})
    canais = cfg.get("canais", [])
    if not canais:
        return
    r = estado.setdefault("radar", {"visto": {}, "dia": "", "hoje": 0, "chaves": []})
    ajuste_radar(estado)
    hoje = agora().date().isoformat()
    if r.get("dia") != hoje:
        r["dia"], r["hoje"] = hoje, 0
    limite_dia, limite_rodada = cfg.get("max_por_dia", 0), cfg.get("max_por_rodada", 0)  # 0 = sem limite
    # Limite por hora: espalha as ofertas pelo dia em vez de gastar a cota toda logo de manhã.
    limite_hora = cfg.get("max_por_hora", 0)
    hora = agora().strftime("%Y-%m-%dT%H")
    if r.get("hora") != hora:
        r["hora"], r["nesta_hora"] = hora, 0
    # "Rodar agora" do painel: roda mesmo de madrugada e deixa o resultado para o painel mostrar.
    forcado = os.environ.get("BOT_RADAR_FORCADO") == "1"
    if forcado:  # pedido na mão: não segura pelo limite da hora, só pelo do dia
        limite_hora = 0
    conta = {"em": agora().isoformat(timespec="seconds"), "pedido": os.environ.get("BOT_PEDIDO", ""),
             "forcado": forcado, "canais": len(canais), "lidos": 0, "falhas": [], "posts_novos": 0,
             "do_nicho": 0, "repetidas": 0, "novas": 0, "limite_dia": False}
    if radar_pausado(cfg) and not teste and not forcado:
        if conta["pedido"]:  # só guarda quando foi pedido (para não gravar a fila a cada 5 minutos à toa)
            r["ultima"] = {**conta, "pausado": True}
        # Madrugada: não lê nada. Na primeira rodada depois da pausa só marca onde cada canal está,
        # para não mandar oferta da madrugada que talvez já mudou de preço.
        r["pausado"] = True
        log("Radar: pausado (madrugada).")
        return
    acordando = (False if forcado else r.pop("pausado", False)) and not teste
    if acordando:
        r["peneira"] = []  # o que sobrou de ontem pode ter mudado de preço
    usadas = set(r["chaves"]) | {x["chave"] for x in estado["fila"] + estado["revisar"] + r.get("peneira", [])}
    conta["repetidas_nome"] = 0
    novas = radar_promobit(estado, r, cfg, conta, usadas, teste, acordando)
    conta["promobit"] = novas
    novas_canais = 0
    for canal in canais:
        canal = canal.strip().replace("https://t.me/", "").replace("@", "").strip("/")
        try:
            posts = ler_canal(canal)
        except Exception as e:
            log(f"Radar: não consegui ler @{canal} ({e}).")
            conta["falhas"].append(canal)
            continue
        conta["lidos"] += 1
        visto = r["visto"].get(canal)
        ultimo = max((p["id"] for p in posts), default=visto or 0)
        if (visto is None or acordando) and not teste:  # primeira vez ou fim da madrugada: só marca onde está
            r["visto"][canal] = ultimo
            log(f"Radar: {'voltei da pausa em' if acordando else 'comecei a acompanhar'} @{canal}.")
            continue
        for post in sorted(posts, key=lambda p: p["id"]):
            if not teste and post["id"] <= visto:
                continue
            conta["posts_novos"] += 1
            cheia_hora = bool(limite_hora) and r["nesta_hora"] >= limite_hora
            cheia_dia = bool(limite_dia) and r["hoje"] >= limite_dia
            cheia_rodada = bool(limite_rodada) and novas_canais >= limite_rodada
            if cheia_rodada or ((cheia_dia or cheia_hora) and not teste):
                conta["limite_dia"] = cheia_dia
                conta["limite_hora"] = cheia_hora
                conta["limite_rodada"] = cheia_rodada
                break
            item = oferta_do_post(post, canal)
            if not item:
                continue
            conta["do_nicho"] += 1
            if item["chave"] in usadas:
                conta["repetidas"] += 1
                continue
            usadas.add(item["chave"])
            novas += 1
            novas_canais += 1
            if teste:
                log(f"Radar achou em @{canal}: {json.dumps(item, ensure_ascii=False)}")
                continue
            r["hoje"] += 1
            r["nesta_hora"] = r.get("nesta_hora", 0) + 1
            r["chaves"] = (r["chaves"] + [item["chave"]])[-3000:]
            if not peneirar(estado, r, item, conta):
                novas -= 1
                novas_canais -= 1
        if not teste:
            r["visto"][canal] = ultimo
    conta["novas"] = novas
    if not teste:
        conta["soltas"] = soltar_peneira(estado, r, forcado)
        conta["na_peneira"] = len(r.get("peneira", []))
    if not teste and (conta["pedido"] or conta["posts_novos"] or conta["falhas"]):
        r["ultima"] = conta
    log(f"Radar: {novas} oferta(s) nova(s) do nicho." + (" (teste, nada enviado)" if teste else ""))


def cmd_shopee_teste(links):
    """Diagnóstico da Open API da Shopee (workflow shopee-teste.yml): busca, link curto e links que você passar."""
    if not (segredo("SHOPEE_APP_ID") and segredo("SHOPEE_SECRET")):
        log("Faltam os secrets SHOPEE_APP_ID e SHOPEE_SECRET no GitHub.")
        return
    nos = shopee_consulta(CONSULTA_SHOPEE, {"palavra": "creatina", "limite": 3})
    log(f"Busca 'creatina': {len(nos)} resultado(s).")
    for no in nos:
        o = oferta_shopee(no)
        log(f"  {o['nome'][:60]} · R$ {o['por']} ({o['desconto']}% off, {o['vendas']} vendas) · {o['link']}")
    if nos:
        o = oferta_shopee(nos[0])
        curto = shopee_link_curto(o['link_produto'])
        log(f"Link curto gerado para {o['link_produto']}: {curto}")
        for link in (o["link"], curto):  # para onde o link leva: os parâmetros mostram de quem é a comissão
            try:
                req = urllib.request.Request(link, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    final = resp.geturl()
            except urllib.error.HTTPError as e:
                final = e.geturl()
            except Exception as e:
                final = f"erro: {e}"
            q = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
            log(f"{link} abre: {final[:300]}")
            log(f"  comissão: utm_source={q.get('utm_source')} utm_medium={q.get('utm_medium')} "
                f"utm_content={q.get('utm_content')} affiliate_id={q.get('affiliate_id')}")
    for link in links:
        prod = produto_do_link(link)
        log(f"{link} -> {json.dumps(prod, ensure_ascii=False)[:400]}")


def cmd_titulo_teste():
    """Prévia (workflow titulo-teste.yml): chamada atual x chamada nova das ofertas do Revisar e da fila. Não salva nada."""
    estado, _ = carregar_estado()
    recentes = []
    for item in (estado["revisar"] + estado["fila"])[:20]:
        novo = gerar_titulo(item, recentes) or "(IA não respondeu) " + titulo_reserva(item)
        recentes.append(novo)
        log(f"{nome_curto(item.get('nome') or '', 45)} :: antes: {item.get('titulo') or item.get('frase')} :: agora: {novo}")
        log(f"  sem IA ficaria: {titulo_reserva(item)}")


def cmd_foto_teste(links):
    """Diagnóstico (workflow diagnostico.yml): o que a loja devolve para este servidor."""
    for link in links:
        log(f"--- {link}")
        try:
            req = urllib.request.Request(link, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                pagina = resp.read().decode("utf-8", "replace")
                log(f"HTTP {resp.status} · final {resp.geturl()} · {len(pagina)} letras")
        except urllib.error.HTTPError as e:
            log(f"HTTP {e.code} · {e.geturl()}")
            pagina = e.read().decode("utf-8", "replace")
        except Exception as e:
            log(f"Falhou: {e}")
            continue
        t = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S)
        log("title: " + (t.group(1).strip()[:120] if t else "(nenhum)"))
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', pagina)
        log("og:image: " + (m.group(1) if m else "(nenhum)"))
        log("ler_pagina: " + json.dumps(ler_pagina(link), ensure_ascii=False)[:400])
        log("produto_do_link: " + json.dumps(produto_do_link(link), ensure_ascii=False)[:500])
        a = re.search(r"/dp/([A-Z0-9]{10})", link)
        if a:
            log("foto por ASIN: " + str(foto_amazon_por_asin(a.group(1))))


def cmd_radar_teste():
    """TESTAR_RADAR.bat: lê os canais agora e mostra o que mandaria, sem enviar nada."""
    os.environ["BOT_SALVAR_PAGINA"] = "1"
    CONFIG.setdefault("radar", {})["max_por_rodada"] = 8
    rodar_radar(estado_vazio(), teste=True)


def reler_bloqueadas(estado):
    """Ofertas que a loja bloqueou no servidor do Render: tenta ler de novo daqui (outro endereço)."""
    for item in [x for x in estado["revisar"] if x.get("ler_depois")]:
        lido = ler_pagina(item["link"])
        bloqueou = lido.pop("_bloqueado", False)
        for chave, valor in lido.items():
            item.setdefault(chave, valor)
        if not faltando(item):
            log(f"Consegui ler agora: {item['nome']}")
            item.pop("ler_depois", None)
            estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
            mandar_para_revisao(estado, item)
            continue
        item["tentativas"] = item.get("tentativas", 0) + 1
        log(f"Ainda não deu para ler ({'bloqueado' if bloqueou else 'faltou ' + ', '.join(faltando(item))}): {item['link']}")
        if item["tentativas"] >= 2:  # desiste e avisa, para você não ficar esperando à toa
            item.pop("ler_depois", None)
            responder_admin("Não consegui ler essa oferta nem pelo GitHub. Complete respondendo a revisão com nome: e preço:.",
                            responder_a=item.get("msg_id"))
        else:
            responder_admin("A loja bloqueou também o GitHub. Tento mais uma vez em até 30 minutos, "
                            "ou complete já respondendo a revisão com nome: e preço:.", responder_a=item.get("msg_id"))


def ciclo(estado):
    if not os.environ.get("RENDER"):
        try:
            reler_bloqueadas(estado)
        except Exception as e:
            log(f"Erro relendo ofertas bloqueadas: {e}")
    try:
        ler_mensagens(estado)
    except Exception as e:
        log(f"Erro lendo suas mensagens: {e}")
    try:
        checar_validade(estado)
    except Exception as e:
        log(f"Erro checando a validade da fila: {e}")
    try:
        vencer_revisao(estado)
    except Exception as e:
        log(f"Erro tirando ofertas vencidas do Revisar: {e}")
    if not os.environ.get("RENDER"):
        try:
            rodar_radar(estado)
        except Exception as e:
            log(f"Erro no radar: {e}")
    try:
        buscar_shopee(estado)
    except Exception as e:
        log(f"Erro na Shopee: {e}")
    try:
        refazer_titulos(estado)
    except Exception as e:
        log(f"Erro refazendo chamadas: {e}")
    try:
        postar_se_for_hora(estado)
    except Exception as e:
        log(f"Erro ao postar: {e}")
    try:
        avisar_antes_de_postar(estado)
    except Exception as e:
        log(f"Erro avisando o próximo post: {e}")
    limpar_estado(estado)
    log(f"Fila agora: {len(estado['fila'])} oferta(s). Esperando revisão: {len(estado['revisar'])}.")


def cmd_rodar():
    if SIMULAR:
        log("MODO SIMULAÇÃO: nada é enviado e o estado não é salvo.")
    log("Fila guardada no " + ("repositório do GitHub." if usar_github() else "arquivo dados/estado.json."))
    com_estado(ciclo)
    esperar_e_postar()


def cmd_ids():
    updates = telegram("getUpdates", timeout=0)
    if not updates:
        print("Nada chegou ainda. Mande um 'oi' no privado do bot e poste qualquer coisa no canal "
              "(com o bot já como administrador), depois rode de novo.")
        return
    vistos = set()
    for up in updates:
        msg = up.get("message") or up.get("channel_post") or up.get("my_chat_member") or {}
        chat = msg.get("chat", {})
        if chat.get("type") == "private" and ("eu", chat["id"]) not in vistos:
            vistos.add(("eu", chat["id"]))
            print(f"Seu ID (ADMIN_ID): {chat['id']}  ({chat.get('first_name', '')})")
        elif chat.get("type") == "channel" and ("canal", chat["id"]) not in vistos:
            vistos.add(("canal", chat["id"]))
            print(f"ID do canal (TELEGRAM_CHANNEL): {chat['id']}  ({chat.get('title', '')})")


def cmd_previa(texto):
    item, erro = interpretar_mensagem(texto)
    if erro:
        print(erro)
        return
    if item.get("nome") and not item.get("titulo"):
        item["titulo"] = gerar_frase(item)
    print("Como fica no canal (o Telegram transforma <b> em negrito e <s> em riscado):\n")
    print(montar_post(item) if item.get("nome") else f"(sem nome) {item['link']}")
    falta = faltando(item)
    print("\nO que o bot leu sozinho:", {k: v for k, v in item.items() if k not in ("chave",)})
    if falta:
        print("Faltou:", ", ".join(falta), "(você completaria respondendo a mensagem de revisão)")


def cmd_teste_canal():
    telegram("sendMessage", chat_id=segredo("TELEGRAM_CHANNEL"),
             text="✅ Teste do bot de achados: se você está lendo isso, o bot consegue postar aqui.")
    print("Mensagem de teste enviada para o canal.")


def main():
    global SIMULAR, FORCAR, CONFIG
    sys.stdout.reconfigure(encoding="utf-8")
    carregar_env()
    CONFIG = carregar_json(ARQ_CONFIG, {})
    args = sys.argv[1:]
    SIMULAR = "--simular" in args
    FORCAR = "--agora" in args
    args = [a for a in args if a not in ("--simular", "--agora")]
    cmd = args[0] if args else "rodar"
    try:
        if cmd == "rodar":
            cmd_rodar()
        elif cmd == "ids":
            cmd_ids()
        elif cmd == "previa":
            cmd_previa(" ".join(args[1:]).replace("\\n", "\n"))
        elif cmd == "radar-teste":
            cmd_radar_teste()
        elif cmd == "teste-canal":
            cmd_teste_canal()
        elif cmd == "foto-teste":
            cmd_foto_teste(args[1:])
        elif cmd == "shopee-teste":
            cmd_shopee_teste(args[1:])
        elif cmd == "titulo-teste":
            cmd_titulo_teste()
        else:
            print(__doc__)
    except RuntimeError as e:
        print(f"ERRO: {e}")
        sys.exit(1)


CONFIG = {}

if __name__ == "__main__":
    main()
