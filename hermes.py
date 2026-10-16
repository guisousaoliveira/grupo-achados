"""Hermes: robô de alertas do Alpha Promo (@HermesAlphaPromoBot).

Cada pessoa monta a lista dela (seções e produtos) na tela do Hermes, que abre dentro do Telegram
(alphadominustv.com/hermes). Quando o canal posta uma oferta que bate com a lista, o Hermes manda
no privado dela.

- Listas: dados/hermes.json no repositório, cifrado com a chave do HERMES_TOKEN (o repositório é público,
  então ninguém de fora consegue ler quem pediu o quê). Guardamos só o ID do Telegram e a lista.
- Render (servidor.py): /hermes (webhook do bot) e /hermes/lista (a tela lê e salva a lista).
- GitHub Actions e Render: avisar(item) logo depois de cada post no canal.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import bot

ARQUIVO = "dados/hermes.json"
URL_TELA = "https://alphadominustv.com/hermes"
ORIGENS = ("https://alphadominustv.com", "https://www.alphadominustv.com", "https://alpha-dominus.web.app")
SECOES = {"academia": "Academia (Ares)", "relogios": "Relógios (Cronos)", "barba": "Barba (Apolo)",
          "casa": "Casa (Héstia)", "tech": "Tech (Atena)", "carro": "Carro (Hefesto)", "estilo": "Estilo (Perseu)"}
MAX_PALAVRAS, MAX_LETRAS = 15, 40


def token():
    return bot.segredo("HERMES_TOKEN")


def ligado():
    return bool(token())


# ---------------------------------------------------------------- Telegram do Hermes

def api(metodo, **params):
    dados = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    try:
        texto, _ = bot.http(f"https://api.telegram.org/bot{token()}/{metodo}", dados, timeout=30)
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", errors="replace")
    resp = json.loads(texto)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram recusou {metodo}: {resp.get('description')}")
    return resp["result"]


def botao_lista(texto="📋 Montar minha lista"):
    return {"inline_keyboard": [[{"text": texto, "web_app": {"url": URL_TELA}}]]}


# ---------------------------------------------------------------- listas (arquivo cifrado)

def _fluxo(chave, nonce, tamanho):
    saida, n = b"", 0
    while len(saida) < tamanho:
        saida += hashlib.sha256(chave + nonce + n.to_bytes(4, "big")).digest()
        n += 1
    return saida[:tamanho]


def _chave():
    return hashlib.sha256(("hermes-listas" + token()).encode()).digest()


def cifrar(dados):
    texto = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(16)
    corpo = bytes(a ^ b for a, b in zip(texto, _fluxo(_chave(), nonce, len(texto))))
    selo = hmac.new(_chave(), nonce + corpo, hashlib.sha256).digest()[:16]
    return {"v": 1, "dados": base64.b64encode(nonce + selo + corpo).decode()}


def decifrar(guardado):
    bruto = base64.b64decode(guardado["dados"])
    nonce, selo, corpo = bruto[:16], bruto[16:32], bruto[32:]
    if not hmac.compare_digest(selo, hmac.new(_chave(), nonce + corpo, hashlib.sha256).digest()[:16]):
        raise ValueError("lista do Hermes com chave diferente (o HERMES_TOKEN mudou?)")
    texto = bytes(a ^ b for a, b in zip(corpo, _fluxo(_chave(), nonce, len(corpo))))
    return json.loads(texto.decode("utf-8"))


def carregar():
    """Devolve (listas, sha). listas = {"usuarios": {id: {...}}}"""
    vazio = {"usuarios": {}}
    if not bot.usar_github():
        caminho = bot.ARQ_ESTADO.parent / "hermes.json"
        return (decifrar(json.loads(caminho.read_text())) if caminho.exists() else vazio), None
    try:
        r = bot._github("GET", arquivo=ARQUIVO)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return vazio, None
        raise
    return decifrar(json.loads(base64.b64decode(r["content"]).decode())), r["sha"]


def salvar(listas, sha):
    texto = json.dumps(cifrar(listas))
    if not bot.usar_github():
        (bot.ARQ_ESTADO.parent / "hermes.json").write_text(texto)
        return
    corpo = {"message": "listas do Hermes", "content": base64.b64encode(texto.encode()).decode()}
    if sha:
        corpo["sha"] = sha
    try:
        bot._github("PUT", corpo, arquivo=ARQUIVO)
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            raise bot.Conflito() from e
        raise


def com_listas(acao, tentativas=3):
    for _ in range(tentativas):
        listas, sha = carregar()
        resultado = acao(listas)
        try:
            salvar(listas, sha)
            return resultado
        except bot.Conflito:
            continue
    raise RuntimeError("não consegui salvar a lista do Hermes")


def limpar_lista(corpo):
    secoes = [s for s in corpo.get("secoes", []) if s in SECOES]
    palavras = []
    for p in corpo.get("palavras", []):
        p = re.sub(r"\s+", " ", str(p)).strip().lower()[:MAX_LETRAS]
        if p and p not in palavras:
            palavras.append(p)
    maximo = corpo.get("max")
    try:
        maximo = round(float(maximo), 2) if maximo not in (None, "", 0) else None
    except (TypeError, ValueError):
        maximo = None
    return {"secoes": list(dict.fromkeys(secoes)), "palavras": palavras[:MAX_PALAVRAS], "max": maximo}


def resumo(u):
    partes = [SECOES[s] for s in u.get("secoes", [])] + [f"“{p}”" for p in u.get("palavras", [])]
    if not partes:
        return "Sua lista está vazia."
    texto = "📋 <b>Sua lista</b>\n" + "\n".join(f"• {html.escape(p)}" for p in partes)
    if u.get("max"):
        texto += f"\n💰 Até {bot.reais(u['max'])}"
    if u.get("pausado"):
        texto += "\n\n⏸️ Alertas pausados. Mande /voltar para ligar de novo."
    return texto


# ---------------------------------------------------------------- tela (Mini App)

def validar_init_data(init_data, max_idade=24 * 3600):
    """Confere a assinatura que o Telegram põe na tela (ninguém mexe na lista de outra pessoa)."""
    if not init_data or not ligado():
        return None
    campos = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    recebido = campos.pop("hash", "")
    verificar = "\n".join(f"{k}={campos[k]}" for k in sorted(campos))
    segredo = hmac.new(b"WebAppData", token().encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(segredo, verificar.encode(), hashlib.sha256).hexdigest(), recebido):
        return None
    if time.time() - int(campos.get("auth_date", 0) or 0) > max_idade:
        return None
    try:
        return json.loads(campos.get("user", "{}"))
    except ValueError:
        return None


def _cors(handler):
    origem = handler.headers.get("Origin", "")
    handler.send_header("Access-Control-Allow-Origin", origem if origem in ORIGENS else ORIGENS[0])
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Telegram-Init-Data")
    handler.send_header("Access-Control-Max-Age", "86400")


def _json(handler, codigo, dados):
    corpo = json.dumps(dados, ensure_ascii=False).encode()
    handler.send_response(codigo)
    _cors(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.end_headers()
    handler.wfile.write(corpo)


def tratar_opcoes(handler):
    handler.send_response(204)
    _cors(handler)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def tratar_lista(handler, metodo):
    if not ligado():
        return _json(handler, 503, {"ok": False, "erro": "O Hermes ainda não está ligado."})
    usuario = validar_init_data(handler.headers.get("X-Telegram-Init-Data", ""))
    if not usuario or not usuario.get("id"):
        return _json(handler, 401, {"ok": False, "erro": "Abra a lista pelo @HermesAlphaPromoBot no Telegram."})
    uid = str(usuario["id"])
    try:
        if metodo == "GET":
            listas, _ = carregar()
            u = listas["usuarios"].get(uid, {})
            return _json(handler, 200, {"secoes": u.get("secoes", []), "palavras": u.get("palavras", []),
                                        "max": u.get("max"), "pausado": bool(u.get("pausado"))})
        tamanho = int(handler.headers.get("Content-Length") or 0)
        try:
            corpo = json.loads(handler.rfile.read(min(tamanho, 20000)) or b"{}")
        except ValueError:
            return _json(handler, 400, {"ok": False, "erro": "pedido inválido"})
        nova = limpar_lista(corpo)

        def gravar(listas):
            u = listas["usuarios"].setdefault(uid, {"desde": bot.agora().isoformat(timespec="minutes")})
            u.update(nova)
            u["pausado"] = False
            return u

        u = com_listas(gravar)
        total = len(u["secoes"]) + len(u["palavras"])
        bot.log(f"Hermes: lista salva ({total} itens).")
        try:
            api("sendMessage", chat_id=uid, parse_mode="HTML",
                text="✅ <b>Alertas ativados!</b>\n\n" + resumo(u) +
                     "\n\nQuando sair uma oferta assim no Alpha Promo, eu te aviso aqui.",
                reply_markup=botao_lista("✏️ Editar lista"))
        except Exception as e:
            bot.log(f"Hermes: não consegui confirmar no privado ({e}).")
        return _json(handler, 200, {"ok": True, "total": total})
    except Exception as e:
        bot.log(f"Hermes: erro na lista ({e}).")
        return _json(handler, 500, {"ok": False, "erro": "Não consegui salvar agora. Tente de novo em instantes."})


# ---------------------------------------------------------------- conversa com o bot

BOAS_VINDAS = ("Sou o <b>Hermes</b>, o mensageiro do Alpha Promo. ⚡\n\n"
               "Escolha seções (academia, relógios, barba, tech…) ou produtos (whey, Casio, air fryer…) "
               "e eu te aviso aqui quando sair uma promoção que combina com você.\n\n"
               "Se preferir, mande o nome do produto por mensagem.")


def tratar_update(update):
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private" or not msg.get("text"):
        return
    uid, texto = str(chat["id"]), msg["text"].strip()
    comando = texto.split()[0].split("@")[0].lower() if texto.startswith("/") else ""

    def enviar(t, botoes=None):
        api("sendMessage", chat_id=uid, text=t, parse_mode="HTML", reply_markup=botoes,
            disable_web_page_preview=True)

    if comando in ("/start", "/ajuda", "/help"):
        return enviar(BOAS_VINDAS, botao_lista())
    if comando in ("/lista", "/minhalista"):
        listas, _ = carregar()
        return enviar(resumo(listas["usuarios"].get(uid, {})), botao_lista("✏️ Editar lista"))
    if comando in ("/parar", "/pausar", "/voltar"):
        pausar = comando != "/voltar"

        def mudar(listas):
            if uid in listas["usuarios"]:
                listas["usuarios"][uid]["pausado"] = pausar
        com_listas(mudar)
        return enviar("⏸️ Pausei seus alertas. Mande /voltar quando quiser de novo." if pausar
                      else "▶️ Alertas ligados de novo.")
    if comando in ("/apagar", "/excluir"):
        com_listas(lambda listas: listas["usuarios"].pop(uid, None))
        return enviar("🗑️ Apaguei sua lista e seus dados. Se quiser voltar, é só mandar /start.")
    if comando:
        return enviar("Comandos: /lista, /parar, /voltar, /apagar.", botao_lista())
    # texto solto: vira produto na lista
    novas = [p for p in re.split(r"[,\n;]+", texto) if p.strip()][:5]

    def somar(listas):
        u = listas["usuarios"].setdefault(uid, {"desde": bot.agora().isoformat(timespec="minutes"),
                                                "secoes": [], "palavras": [], "max": None})
        u.update(limpar_lista({**u, "palavras": u.get("palavras", []) + novas}))
        u["pausado"] = False
        return u
    u = com_listas(somar)
    enviar("➕ Anotei. " + resumo(u), botao_lista("✏️ Editar lista"))


def configurar(base, segredo_webhook):
    """No Render, ao ligar: webhook, botão de menu e comandos do Hermes."""
    if not ligado():
        return
    try:
        api("setWebhook", url=base.rstrip("/") + "/hermes", secret_token=segredo_webhook,
            allowed_updates=["message"])
        api("setChatMenuButton", menu_button={"type": "web_app", "text": "Minha lista", "web_app": {"url": URL_TELA}})
        api("setMyCommands", commands=[{"command": "start", "description": "Montar minha lista"},
                                       {"command": "lista", "description": "Ver minha lista"},
                                       {"command": "parar", "description": "Pausar alertas"},
                                       {"command": "voltar", "description": "Ligar alertas de novo"},
                                       {"command": "apagar", "description": "Apagar minha lista"}])
        api("setMyDescription", description="Monte sua lista de produtos e seções e receba no privado as "
                                            "promoções do Alpha Promo que combinam com você. Links de afiliado.")
        api("setMyShortDescription", short_description="Alertas de promoção do Alpha Promo, do seu jeito.")
        bot.log("Hermes ligado: webhook, botão 'Minha lista' e comandos.")
    except Exception as e:
        bot.log(f"Hermes: não consegui configurar ({e}).")


# ---------------------------------------------------------------- avisos

def _normal(texto):
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))


def combina(u, item, secao):
    if u.get("pausado"):
        return False
    por = item.get("por")
    if u.get("max") and por and por > u["max"]:
        return False
    if secao in u.get("secoes", []):
        return True
    texto = " " + _normal(f"{item.get('nome', '')} {item.get('titulo', '')}") + " "
    return any(re.search(r"(?<![a-z0-9])" + re.escape(_normal(p)), texto) for p in u.get("palavras", []))


def texto_aviso(item):
    linhas = [f"🔔 <b>{html.escape(item.get('nome', ''))}</b>", ""]
    if item.get("por"):
        preco = f"💰 <b>{bot.reais(item['por'])}</b>"
        if item.get("de") and item["de"] > item["por"]:
            preco += f"  <s>{bot.reais(item['de'])}</s> (−{round((1 - item['por'] / item['de']) * 100)}%)"
        linhas.append(preco)
    if item.get("cupom"):
        linhas.append(f"🎟️ Cupom: <code>{html.escape(str(item['cupom']))}</code>")
    loja = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}.get(item.get("loja"), "")
    if loja:
        linhas.append(f"🏪 {loja}")
    linhas += ["", "Preço pode mudar a qualquer momento. Link de afiliado."]
    return "\n".join(linhas)


def avisar(item):
    """Depois de postar no canal: manda no privado de quem tem essa oferta na lista."""
    if not ligado() or bot.SIMULAR or not item.get("link"):
        return 0
    listas, _ = carregar()
    secao = bot.secao_site(item)
    alvos = [uid for uid, u in listas["usuarios"].items() if combina(u, item, secao)]
    if not alvos:
        return 0
    loja = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}.get(item.get("loja"), "loja")
    botoes = {"inline_keyboard": [[{"text": f"🛒 Ver na {loja}", "url": item["link"]}],
                                  [{"text": "✏️ Editar lista", "web_app": {"url": URL_TELA}}]]}
    texto, foto, enviados, saiu = texto_aviso(item), bot.foto_publica(item), 0, []
    for uid in alvos:
        try:
            if foto:
                try:
                    api("sendPhoto", chat_id=uid, photo=foto, caption=texto, parse_mode="HTML", reply_markup=botoes)
                except RuntimeError as e:
                    if "blocked" in str(e) or "deactivated" in str(e) or "chat not found" in str(e):
                        raise
                    api("sendMessage", chat_id=uid, text=texto, parse_mode="HTML", reply_markup=botoes)
            else:
                api("sendMessage", chat_id=uid, text=texto, parse_mode="HTML", reply_markup=botoes)
            enviados += 1
            time.sleep(0.05)
        except Exception as e:
            if "blocked" in str(e) or "deactivated" in str(e) or "chat not found" in str(e):
                saiu.append(uid)
            else:
                bot.log(f"Hermes: aviso não saiu ({e}).")
    if saiu:  # bloqueou o bot: tira da lista
        try:
            com_listas(lambda ls: [ls["usuarios"].pop(u, None) for u in saiu])
        except Exception as e:
            bot.log(f"Hermes: não tirei quem bloqueou ({e}).")
    bot.log(f"Hermes: avisei {enviados} pessoa(s) sobre {item.get('nome', '')[:50]}.")
    return enviados
