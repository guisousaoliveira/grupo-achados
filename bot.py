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


def chamar_github_agora(motivo="Pedi ao GitHub para reler a página agora."):
    """No Render: pede ao GitHub Actions para rodar já (a Amazon bloqueia o Render, mas costuma abrir para o GitHub;
    e o /tick do servidor usa isto para rodar o radar de 5 em 5 minutos).
    Precisa que o token tenha também a permissão Actions: Read and write."""
    if not (os.environ.get("RENDER") and usar_github()):
        return False
    url = f"https://api.github.com/repos/{_repo()}/actions/workflows/bot.yml/dispatches"
    corpo = {"ref": "main"}
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
    blocos = [f"🔥 <b>{e(item['nome'])}</b>"]
    preco = []
    de, por = item.get("de"), item.get("por")
    if por and de and de > por:
        off = round((1 - por / de) * 100)
        preco.append(f"De <s>{reais(de)}</s> por <b>{reais(por)}</b> ({off}% off)")
    elif por:
        preco.append(f"Por <b>{reais(por)}</b>")
    if item.get("prime"):
        extra = f" (sem Prime: {reais(item['sem_prime'])})" if item.get("sem_prime") else ""
        preco.append(f"👑 Preço exclusivo para membros Prime{extra}")
    if preco:
        blocos.append("\n".join(preco))
    if item.get("cupom"):
        blocos.append(f"🎟️ Cupom: <code>{e(item['cupom'])}</code>")
    elif item.get("cupom_loja"):
        blocos.append(f"🎟️ {e(item['cupom_loja'])}")
    if item.get("frase"):
        blocos.append(f"✅ {e(item['frase'])}")
    loja = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}.get(item.get("loja"), "")
    blocos.append(f"🛒 {e(item['link'])}" + (f"  ({loja})" if loja else ""))
    rodape = CONFIG.get("rodape", "")
    if rodape:
        blocos.append(e(rodape))
    return "\n\n".join(blocos)


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
        achado.update(shopee_por_link(final))
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
    return {k: o[k] for k in ("nome", "por", "de", "foto") if o.get(k)}


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
    rotulo = r"(nome|t[ií]tulo|pre[cç]o|frase|cupom|prime)\s*[:=]"
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
        elif "frase" not in item:
            item["frase"] = linha

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


BOTOES_REVISAO = lambda i: {"inline_keyboard": [[
    {"text": "✅ Aprovar", "callback_data": f"ok:{i}"},
    {"text": "❌ Descartar", "callback_data": f"no:{i}"},
]]}
SO_DESCARTAR = lambda i: {"inline_keyboard": [[{"text": "❌ Descartar", "callback_data": f"no:{i}"}]]}


def gerar_frase(item):
    """Frase chamativa para a oferta. Usa IA (Gemini, grátis) se tiver a chave; se não, o banco de frases do config."""
    off = round((1 - item["por"] / item["de"]) * 100) if item.get("de") and item.get("por") else 0
    chave = segredo("GEMINI_API_KEY")
    if chave and not SIMULAR:
        modelo = CONFIG.get("ia", {}).get("modelo", "gemini-2.5-flash")
        pedido = (CONFIG.get("ia", {}).get("instrucao", "") +
                  f"\nProduto: {item.get('nome')}\nPreço: {item.get('por')}\nDesconto: {off}%\nFrase:")
        try:
            texto, _ = http(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}",
                            json.dumps({"contents": [{"parts": [{"text": pedido}]}]}).encode(),
                            {"Content-Type": "application/json"})
            frase = json.loads(texto)["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')
            if frase:
                return frase.splitlines()[0][:140]
        except Exception as e:
            log(f"IA não respondeu ({e}), usando o banco de frases.")
    frases = CONFIG.get("frases", {})
    nome = (item.get("nome") or "").lower()
    opcoes = []
    for tema, palavras in CONFIG.get("palavras", {}).items():
        if any(re.search(rf"\b{re.escape(p)}\b", nome) for p in palavras):
            opcoes = frases.get(tema, [])
            break
    if off >= 40:
        opcoes = opcoes + frases.get("desconto_alto", [])
    opcoes = opcoes or frases.get("geral", [])
    if not opcoes:
        return ""
    return random.choice(opcoes).replace("{off}", str(off))


def mandar_para_revisao(estado, item):
    if not item.get("frase") and item.get("nome"):
        item["frase"] = gerar_frase(item)
    item["id"] = estado["proximo_id"]
    estado["proximo_id"] += 1
    falta = faltando(item)
    texto = "📝 <b>Revisar oferta</b>\n\n" + (montar_post(item) if item.get("nome") else html.escape(item["link"]))
    if item.get("origem"):
        texto += f"\n\n📡 Achei no @{html.escape(item['origem'])}. Confira o preço antes de aprovar: {html.escape(item.get('link_produto', ''))}"
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
              "nome: Creatina Growth 250g\npreço: 119,90 69,90\nfrase: A mais vendida\ncupom: NOITEPRIME10\nprime: sim 189,37 (preço sem Prime)\n(frase: - ou cupom: - para tirar)</i>")
    msg = responder_admin(texto, botoes=SO_DESCARTAR(item["id"]) if faltando(item) else BOTOES_REVISAO(item["id"]),
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
        m = re.match(r"(nome|t[ií]tulo|pre[cç]o|frase|link|de|por|cupom|prime)\s*[:=]\s*(.+)", linha, re.I)
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
        elif campo in ("nome", "título", "titulo") or (not campo and not item.get("nome")):
            item["nome"] = valor
        elif campo == "frase" or not campo:
            item["frase"] = "" if valor == "-" else valor
        elif campo == "link":
            item["link"] = valor
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
            if valor == "-":
                item.pop("cupom_loja", None)


def aprovar(estado, item):
    estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
    if any(x["chave"] == item["chave"] for x in estado["fila"]):
        return "Esse link já estava na fila."
    limpo = {k: v for k, v in item.items() if k not in ("id", "msg_id")}
    limpo["aprovado_em"] = agora().isoformat(timespec="minutes")
    estado["fila"].append(limpo)
    return f"✅ Aprovada. Posição {len(estado['fila'])} na fila."


def postar_agora(estado, item):
    publicar(item)
    estado["fila"] = [x for x in estado["fila"] if x is not item]
    guardar_ultimo(estado, item)
    estado["postados"].append({"chave": item["chave"], "loja": item["loja"], "nome": item["nome"],
                               "data": agora().isoformat(timespec="minutes")})
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
    return f"🗑️ Removida da fila. Fila: {len(estado['fila'])}."


def tratar_botao(estado, cb):
    try:
        telegram("answerCallbackQuery", callback_query_id=cb["id"])
    except Exception:
        pass  # clique antigo: o Telegram já não aceita resposta, mas a ação vale
    acao, _, ident = (cb.get("data") or "").partition(":")
    if acao in ("st", "sf"):
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
    if not item:
        resultado = "Essa oferta já foi resolvida."
    elif acao == "ok":
        falta = faltando(item)
        resultado = f"Ainda falta: {', '.join(falta)}." if falta else aprovar(estado, item)
    else:
        estado["revisar"] = [x for x in estado["revisar"] if x["id"] != item["id"]]
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
    """Guarda as últimas ofertas postadas (completas) para o /story."""
    limpo = {k: v for k, v in item.items() if k in ("nome", "de", "por", "foto", "loja", "cupom", "prime")}
    estado["ultimos"] = ([limpo] + estado.get("ultimos", []))[:15]


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
        enviar_arquivo(story.story_fixo(canal), "story_alpha_promo.png",
                       "Story fixo do canal. Poste e coloque o sticker de link do canal na parte de baixo.")
        return
    ultimos = estado.get("ultimos", [])
    if not ultimos:
        responder_admin("Ainda não tenho oferta postada guardada. Depois que a próxima oferta sair no canal, "
                        "mande /story de novo.")
        return
    if len(partes) == 1:  # sem número: mostra as 3 últimas postadas para você escolher
        botoes = [[{"text": f"{i}. {x.get('nome', '')[:40]}", "callback_data": "st:" + id_story(x)}]
                  for i, x in enumerate(ultimos[:3], 1)]
        botoes.append([{"text": "Story fixo do canal", "callback_data": "sf:"}])
        responder_admin("De qual oferta você quer o story?", {"inline_keyboard": botoes})
        return
    try:
        item = ultimos[int(partes[1]) - 1]
    except (IndexError, ValueError):
        responder_admin(f"Tenho as {len(ultimos)} últimas postadas guardadas. Use /story para escolher, ou /story 2.")
        return
    gerar_story(story, item, canal)


def id_story(item):
    return hashlib.md5(item.get("chave", item.get("nome", "")).encode()).hexdigest()[:12]


def gerar_story(story, item, canal):
    enviar_arquivo(story.story_oferta(item, baixar_foto(item.get("foto")), canal), "story_oferta.png",
                   f"Story de prova: {item.get('nome', '')}. Coloque o sticker de link do canal embaixo.")


def botao_story(estado, acao, ident):
    """Clique num botão da lista do /story."""
    if acao == "sf":
        cmd_story(estado, "/storyfixo")
        return
    item = next((x for x in estado.get("ultimos", []) if id_story(x) == ident), None)
    if not item:
        responder_admin("Não achei mais essa oferta. Mande /story de novo.")
        return
    try:
        import story
    except ImportError:
        cmd_story(estado, "/story 1")  # mostra o aviso do Pillow
        return
    gerar_story(story, item, CONFIG.get("canal_publico", "").replace("@", "").replace("https://t.me/", ""))


def tratar_comando(estado, texto, foto=None):
    cmd = texto.split()[0].lower() if texto else ""
    if cmd in ("/start", "/ajuda"):
        responder_admin(
            "Me manda só o link de afiliado (Amazon, Mercado Livre ou Shopee). Eu leio nome, preço e foto "
            "e te mando para revisar. Se quiser, escreva nas linhas de baixo o nome, o preço (De 199 por 129) "
            "e uma frase, e isso vale mais que o que eu ler.\n\n"
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
            sales ratingStar imageUrl offerLink shopName }
  }
}"""


CONSULTA_SHOPEE_ITEM = """query ($item: Int64, $loja: Int64) {
  productOfferV2(itemId: $item, shopId: $loja, page: 1, limit: 1) {
    nodes { itemId productName price priceMin priceMax priceDiscountRate
            sales ratingStar imageUrl offerLink shopName }
  }
}"""


def shopee_buscar(palavra, limite):
    if not (segredo("SHOPEE_APP_ID") and segredo("SHOPEE_SECRET")):
        exemplos = carregar_json(ARQ_MOCK, {})
        return exemplos.get(palavra, [])
    return shopee_consulta(CONSULTA_SHOPEE, {"palavra": palavra, "limite": limite})


def shopee_consulta(consulta, variaveis):
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
    return resp["data"]["productOfferV2"]["nodes"]


def oferta_shopee(no):
    preco = float(no.get("priceMin") or no.get("price") or 0)
    desconto = float(no.get("priceDiscountRate") or 0)
    de = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
    return {
        "loja": "shopee",
        "chave": f"shopee:{no.get('itemId')}",
        "nome": (no.get("productName") or "").strip()[:120],
        "por": preco or None,
        "de": de,
        "desconto": desconto,
        "nota": float(no.get("ratingStar") or 0),
        "vendas": int(no.get("sales") or 0),
        "foto": no.get("imageUrl"),
        "link": no.get("offerLink"),
    }


def buscar_shopee(estado):
    regras = CONFIG["shopee"]
    hoje = agora().date().isoformat()
    if not regras.get("ativo", True):
        return
    if not SIMULAR and not segredo("SHOPEE_APP_ID"):
        return  # sem a chave da API, ofertas de exemplo só aparecem na simulação
    if estado.get("shopee_buscado_em") == hoje:
        return
    if agora().hour < regras.get("buscar_a_partir_das", 8):
        return
    modo = "API" if segredo("SHOPEE_APP_ID") else "exemplo (sem chave da API ainda)"
    log(f"Buscando ofertas da Shopee, modo {modo}...")

    limite_dias = agora() - timedelta(days=regras["nao_repetir_dias"])
    recentes = {p["chave"] for p in estado["postados"] if datetime.fromisoformat(p["data"]) >= limite_dias}
    na_fila = {x["chave"] for x in estado["fila"]}

    candidatos = {}
    for palavras in CONFIG["palavras"].values():
        for palavra in palavras:
            try:
                nos = shopee_buscar(palavra, regras.get("resultados_por_palavra", 20))
            except Exception as e:
                log(f"Shopee falhou em '{palavra}': {e}")
                continue
            for no in nos:
                o = oferta_shopee(no)
                if not o["link"] or not o["nome"] or o["chave"] in recentes or o["chave"] in na_fila:
                    continue
                if o["desconto"] < regras["desconto_minimo"] or o["nota"] < regras["nota_minima"] \
                        or o["vendas"] < regras["vendas_minimas"]:
                    continue
                candidatos[o["chave"]] = o

    escolhidos = sorted(candidatos.values(), key=lambda o: (o["desconto"], o["vendas"]), reverse=True)
    escolhidos = escolhidos[: regras["maximo_por_dia"]]
    estado["fila"].extend(escolhidos)
    estado["shopee_buscado_em"] = hoje
    log(f"Shopee: {len(candidatos)} passaram no filtro, {len(escolhidos)} entraram na fila.")


# ---------------------------------------------------------------- postagem

def postar_se_for_hora(estado):
    dt = agora()
    hoje = dt.date().isoformat()
    usados = estado["slots_usados"].setdefault(hoje, [])
    # Horários que já chegaram e ainda não foram usados. Com o tick de 5 em 5 minutos, um horário vale só
    # dentro da própria hora (ex.: o das 15h até 15h59), para não sair uma rajada de posts atrasados.
    atraso = CONFIG.get("atraso_max_horas", 0)
    vencidos = [h for h in CONFIG["horarios"] if 0 <= dt.hour - h <= atraso and h not in usados]
    if FORCAR:
        vencidos = ["teste"]
    if not vencidos:
        log("Nenhum horário de postagem agora.")
        return
    if not estado["fila"]:
        log("Chegou um horário de postagem, mas a fila está vazia.")
        return
    item = estado["fila"][0]
    publicar(item)
    estado["fila"].pop(0)
    guardar_ultimo(estado, item)
    # Um post por execução: se o GitHub atrasar e passar mais de um horário,
    # o próximo sai na execução seguinte, sem rajada.
    if not FORCAR:
        usados.append(vencidos[0])
    estado["postados"].append({"chave": item["chave"], "loja": item["loja"],
                               "nome": item["nome"], "data": dt.isoformat(timespec="minutes")})
    log(f"Postado: {item['nome']}")


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


def palavras_radar():
    cfg = CONFIG.get("radar", {})
    lista = [p for ps in CONFIG.get("palavras", {}).values() for p in ps] + cfg.get("palavras_extra", [])
    return [sem_acento(p) for p in lista if p.strip()]


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
    for alvo in [final] + LINK_LOJA.findall(pagina)[:20]:
        alvo = html.unescape(alvo)
        m = ASIN.search(alvo)
        if m and "amazon" in alvo:
            tag = CONFIG.get("radar", {}).get("tag_amazon", "")
            meu = f"https://www.amazon.com.br/dp/{m.group(1)}" + (f"?tag={tag}" if tag else "")
            return {"loja": "amazon", "chave": f"amazon:{m.group(1)}", "link_produto": meu, "meu_link": meu}
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
        return {"loja": "shopee", "chave": f"shopee:{m.group(1)}.{m.group(2)}",
                "link_produto": f"https://shopee.com.br/product/{m.group(1)}/{m.group(2)}"}
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


def oferta_do_post(post, canal):
    """Monta a oferta a partir do post. None se não for do nicho ou não tiver link de loja."""
    texto = post["texto"]
    if not texto or not any(p in sem_acento(texto) for p in palavras_radar()):
        return None
    candidatos = [l for l in post["links"] if l.startswith("http") and "t.me/" not in l]
    if not candidatos:
        return None
    prod = None
    for link in candidatos[:3]:
        prod = produto_do_link(link)
        if prod:
            break
    if not prod:
        return None
    item = {"loja": prod["loja"], "chave": prod["chave"], "origem": canal,
            "link": prod.get("meu_link") or prod["link_produto"], "link_produto": prod["link_produto"]}
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
    valores = [numero_br(v) for v in re.findall(r"(?:R\$|\bpor\b|\bde\b)\s*(?:R\$)?\s*(\d[\d.]*(?:,\d{1,2})?)", texto, re.I)]
    valores = [v for v in valores if v >= 5]
    if valores:
        if len(valores) >= 2 and max(valores[:2]) > min(valores[:2]):
            item["de"], item["por"] = max(valores[:2]), min(valores[:2])
        else:
            item["por"] = valores[0]
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
    if item["loja"] == "mercadolivre" or not item.get("nome"):
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
    hoje = agora().date().isoformat()
    if r.get("dia") != hoje:
        r["dia"], r["hoje"] = hoje, 0
    limite_dia, limite_rodada = cfg.get("max_por_dia", 10), cfg.get("max_por_rodada", 3)
    if radar_pausado(cfg) and not teste:
        # Madrugada: não lê nada. Na primeira rodada depois da pausa só marca onde cada canal está,
        # para não mandar oferta da madrugada que talvez já mudou de preço.
        r["pausado"] = True
        log("Radar: pausado (madrugada).")
        return
    acordando = r.pop("pausado", False) and not teste
    usadas = set(r["chaves"]) | {x["chave"] for x in estado["fila"] + estado["revisar"]}
    novas = 0
    for canal in canais:
        canal = canal.strip().replace("https://t.me/", "").replace("@", "").strip("/")
        try:
            posts = ler_canal(canal)
        except Exception as e:
            log(f"Radar: não consegui ler @{canal} ({e}).")
            continue
        visto = r["visto"].get(canal)
        ultimo = max((p["id"] for p in posts), default=visto or 0)
        if (visto is None or acordando) and not teste:  # primeira vez ou fim da madrugada: só marca onde está
            r["visto"][canal] = ultimo
            log(f"Radar: {'voltei da pausa em' if acordando else 'comecei a acompanhar'} @{canal}.")
            continue
        for post in sorted(posts, key=lambda p: p["id"]):
            if not teste and post["id"] <= visto:
                continue
            if novas >= limite_rodada or (r["hoje"] >= limite_dia and not teste):
                break
            item = oferta_do_post(post, canal)
            if not item or item["chave"] in usadas:
                continue
            usadas.add(item["chave"])
            novas += 1
            if teste:
                log(f"Radar achou em @{canal}: {json.dumps(item, ensure_ascii=False)}")
                continue
            r["hoje"] += 1
            r["chaves"] = (r["chaves"] + [item["chave"]])[-400:]
            mandar_para_revisao(estado, item)
        if not teste:
            r["visto"][canal] = ultimo
    log(f"Radar: {novas} oferta(s) nova(s) do nicho." + (" (teste, nada enviado)" if teste else ""))


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
        postar_se_for_hora(estado)
    except Exception as e:
        log(f"Erro ao postar: {e}")
    limpar_estado(estado)
    log(f"Fila agora: {len(estado['fila'])} oferta(s). Esperando revisão: {len(estado['revisar'])}.")


def cmd_rodar():
    if SIMULAR:
        log("MODO SIMULAÇÃO: nada é enviado e o estado não é salvo.")
    log("Fila guardada no " + ("repositório do GitHub." if usar_github() else "arquivo dados/estado.json."))
    com_estado(ciclo)


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
    if item.get("nome") and not item.get("frase"):
        item["frase"] = gerar_frase(item)
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
        else:
            print(__doc__)
    except RuntimeError as e:
        print(f"ERRO: {e}")
        sys.exit(1)


CONFIG = {}

if __name__ == "__main__":
    main()
