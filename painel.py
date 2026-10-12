"""Painel web do bot (no mesmo servidor do Render): https://<seu-servico>.onrender.com/painel

Tudo o que você faz no privado do bot, numa tela só: ver e reordenar a fila, revisar as ofertas do radar,
colar um link e montar a oferta, gerar story e pedir sugestões de título e frase para a IA.
Usa a mesma fila (dados/estado.json no GitHub) que o bot e o GitHub Actions.

Variáveis no Render:
  PAINEL_SENHA     senha para entrar (pode passar para os sócios)
  GEMINI_API_KEY   (opcional) liga as sugestões de IA; sem ela o painel usa o banco de frases
"""

import base64
import hashlib
import hmac
import html
import json
import re
import time
import urllib.parse
from datetime import timedelta

import bot

ARQ_HTML = bot.PASTA / "painel.html"
COOKIE = "painel"
LOJAS = {"amazon": "Amazon", "mercadolivre": "Mercado Livre", "shopee": "Shopee"}
CAMPOS = ("nome", "de", "por", "prime", "sem_prime", "cupom", "cupom_loja", "frase", "link", "link_produto",
          "loja", "origem", "foto_origem", "aprovado_em", "aviso_validade", "ler_depois", "precisa_link", "chave")

_fotos = {}  # cache das fotos do Telegram (file_id -> bytes), para a tela abrir rápido


# ---------------------------------------------------------------- senha

def senha():
    return bot.segredo("PAINEL_SENHA")


def assinatura():
    chave = (bot.segredo("TELEGRAM_TOKEN") or "painel").encode()
    return hmac.new(chave, ("painel:" + senha()).encode(), hashlib.sha256).hexdigest()


def logado(req):
    if not senha():
        return False
    for parte in (req.headers.get("Cookie") or "").split(";"):
        nome, _, valor = parte.strip().partition("=")
        if nome == COOKIE and hmac.compare_digest(valor, assinatura()):
            return True
    return False


# ---------------------------------------------------------------- respostas

def responder(req, codigo, corpo, tipo="application/json; charset=utf-8", extras=None):
    if isinstance(corpo, (dict, list)):
        corpo = json.dumps(corpo, ensure_ascii=False)
    if isinstance(corpo, str):
        corpo = corpo.encode()
    req.send_response(codigo)
    req.send_header("Content-Type", tipo)
    req.send_header("Content-Length", str(len(corpo)))
    req.send_header("Cache-Control", "no-store" if "json" in tipo or "html" in tipo else "private, max-age=86400")
    for k, v in (extras or {}).items():
        req.send_header(k, v)
    req.end_headers()
    req.wfile.write(corpo)
    req.wfile.flush()


def pagina_login(erro=""):
    aviso = ""
    if not senha():
        aviso = ('<p class="erro">O painel ainda está trancado. No Render, em Environment, crie a variável '
                 '<b>PAINEL_SENHA</b> com a senha que você quiser e salve.</p>')
    elif erro:
        aviso = f'<p class="erro">{html.escape(erro)}</p>'
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Alpha Promo · Painel</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0b0c;color:#ededed;font:15px Inter,system-ui,sans-serif}}
form{{width:min(360px,calc(100% - 32px));background:#141416;border:1px solid #26262a;border-radius:16px;padding:28px}}
.marca{{display:flex;align-items:center;gap:10px;margin-bottom:22px}}.selo{{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#e7c867,#b8932f);display:grid;place-items:center;color:#0b0b0c;font-weight:700}}
h1{{font-size:17px;margin:0}}small{{color:#8a8a90}}label{{display:block;font-size:13px;color:#a1a1a8;margin-bottom:6px}}
input{{width:100%;padding:12px 14px;border-radius:10px;border:1px solid #2c2c31;background:#0e0e10;color:#fff;font:inherit}}input:focus{{outline:none;border-color:#d4af37}}
button{{width:100%;margin-top:14px;padding:12px;border:0;border-radius:10px;background:#d4af37;color:#0b0b0c;font:600 15px Inter,sans-serif;cursor:pointer}}
.erro{{background:#2a1515;border:1px solid #5a2323;color:#ffb4b4;padding:10px 12px;border-radius:10px;font-size:13px}}
</style></head><body><form method="post" action="/painel/entrar">
<div class="marca"><div class="selo">A</div><div><h1>Alpha Promo</h1><small>Painel do grupo de achados</small></div></div>
{aviso}<label for="s">Senha</label><input id="s" name="senha" type="password" autofocus autocomplete="current-password">
<button>Entrar</button></form></body></html>"""


# ---------------------------------------------------------------- leitura

def url_foto(foto):
    if not foto:
        return ""
    if foto.startswith("http"):
        return foto
    return "/painel/foto?f=" + urllib.parse.quote(foto)


def item_json(x, ident):
    d = {k: x.get(k) for k in CAMPOS if x.get(k) not in (None, "", False)}
    d["id"] = ident
    d["foto"] = url_foto(x.get("foto"))
    d["foto_raw"] = x.get("foto") or ""
    d["falta"] = bot.faltando(x)
    return d


def proximos_horarios(estado, n):
    """Quando cada item da fila deve sair, seguindo os horários do config e os já usados hoje."""
    dt = bot.agora()
    horarios = sorted(bot.CONFIG.get("horarios", []))
    saida = []
    for dia in range(4):
        data = (dt + timedelta(days=dia)).date()
        usados = estado["slots_usados"].get(data.isoformat(), [])
        for h in horarios:
            if dia == 0 and (h < dt.hour or h in usados):
                continue
            rotulo = "hoje" if dia == 0 else "amanhã" if dia == 1 else data.strftime("%d/%m")
            saida.append(f"{rotulo} {h}h")
            if len(saida) >= n:
                return saida
    return saida


def resumo(estado):
    cfg_radar = bot.CONFIG.get("radar", {})
    r = estado.get("radar", {})
    dt = bot.agora()
    hoje = dt.date().isoformat()
    previsao = proximos_horarios(estado, len(estado["fila"]))
    fila = []
    for i, x in enumerate(estado["fila"]):
        j = item_json(x, bot.id_story(x))
        j["previsao"] = previsao[i] if i < len(previsao) else ""
        fila.append(j)
    return {
        "agora": dt.isoformat(timespec="minutes"),
        "hora": dt.hour,
        "horarios": bot.CONFIG.get("horarios", []),
        "usados_hoje": estado["slots_usados"].get(hoje, []),
        "validade_horas": bot.CONFIG.get("validade_horas", 12),
        "canal": bot.CONFIG.get("canal_publico", ""),
        "fila": fila,
        "revisar": [item_json(x, str(x["id"])) for x in estado["revisar"]],
        "ultimos": [item_json(x, bot.id_story(x)) for x in estado.get("ultimos", [])],
        "postados": list(reversed(estado["postados"][-300:])),
        "radar": {
            "canais": len(cfg_radar.get("canais", [])),
            "hoje": r.get("hoje", 0) if r.get("dia") == hoje else 0,
            "max_dia": cfg_radar.get("max_por_dia", 10),
            "pausado": bot.radar_pausado(cfg_radar),
            "pausa_de": cfg_radar.get("pausa_de"),
            "pausa_ate": cfg_radar.get("pausa_ate"),
        },
        "ia": bool(bot.segredo("GEMINI_API_KEY")),
    }


# ---------------------------------------------------------------- edição

def aplicar_campos(item, d):
    """Campos vindos do formulário do painel. Texto vazio apaga o campo."""
    for campo in ("nome", "frase", "cupom"):
        if campo in d:
            valor = (d[campo] or "").strip()
            if valor:
                item[campo] = valor
            else:
                item.pop(campo, None)
                if campo == "cupom":
                    item.pop("cupom_loja", None)
    for campo in ("de", "por", "sem_prime"):
        if campo in d:
            valor = bot._preco(d[campo]) if str(d[campo] or "").strip() else None
            if valor:
                item[campo] = valor
            else:
                item.pop(campo, None)
    if "prime" in d:
        if d["prime"]:
            item["prime"] = True
        else:
            item.pop("prime", None)
            item.pop("sem_prime", None)
    if d.get("foto"):
        item["foto"] = d["foto"]
        item.pop("foto_origem", None)
    novo = (d.get("link") or "").strip()
    if novo and novo != item.get("link"):
        bot.aplicar_correcao(item, "link: " + novo)  # também troca a foto do canal pela da loja, se der
    if item.get("de") and item.get("por") and item["de"] <= item["por"]:
        item.pop("de", None)


def ler_link(texto):
    """Cola o link (e, se quiser, linhas com nome/preço) e devolve a oferta montada, sem salvar nada."""
    item, erro = bot.interpretar_mensagem(texto)
    if erro:
        return {"erro": erro}
    if item["loja"] == "amazon" and not item.get("foto"):  # página bloqueada: a foto pelo código do produto costuma abrir
        final, _ = bot.endereco_final(item["link"])
        m = bot.ASIN.search(final)
        if m:
            foto = bot.foto_amazon_por_asin(m.group(1))
            if foto:
                item["foto"] = foto
    if not item.get("frase") and item.get("nome"):
        item["frase"] = bot.gerar_frase(item)
    estado, _ = bot.carregar_estado()
    aviso = ""
    if any(x["chave"] == item["chave"] for x in estado["fila"]):
        aviso = "Esse link já está na fila."
    elif any(x["chave"] == item["chave"] for x in estado["revisar"]):
        aviso = "Esse link já está esperando revisão."
    j = item_json(item, "")
    j["bloqueado"] = bool(item.get("ler_depois"))
    return {"item": j, "aviso": aviso}


def subir_foto(data_url):
    """Foto enviada pelo painel: manda para o seu privado, guarda o código do Telegram e apaga a mensagem."""
    cabeca, _, b64 = data_url.partition(",")
    dados = base64.b64decode(b64)
    tipo = re.search(r"data:([^;]+)", cabeca)
    msg = bot.enviar_multipart("sendPhoto", {"chat_id": bot.segredo("ADMIN_ID"), "disable_notification": "true"},
                               "photo", dados, "foto.jpg", tipo.group(1) if tipo else "image/jpeg")
    try:
        bot.telegram("deleteMessage", chat_id=bot.segredo("ADMIN_ID"), message_id=msg["message_id"])
    except Exception:
        pass
    file_id = msg["photo"][-1]["file_id"]
    _fotos[file_id] = dados
    return {"foto": url_foto(file_id), "foto_raw": file_id}


def tirar_botoes(msg_id):
    """Aprovou ou descartou pelo painel: some com os botões da revisão no Telegram."""
    if not msg_id:
        return
    try:
        bot.telegram("editMessageReplyMarkup", chat_id=bot.segredo("ADMIN_ID"), message_id=msg_id,
                     reply_markup={"inline_keyboard": []})
    except Exception:
        pass


# ---------------------------------------------------------------- IA (Gemini)

PEDIDO_IA = """Você escreve ofertas para o Alpha Promo, um canal de achados no Telegram com público masculino
de 18 a 35 anos (academia, estilo, cuidados, tecnologia, games, ferramentas, casa, cozinha, churrasco e carro).
Olhe a foto (se tiver) e os dados abaixo e devolva só um JSON assim:
{"titulos": [3 opções], "frases": [3 opções], "nota": "..."}
- titulos: nome curto do produto para o post, até 60 caracteres. Tipo de produto + marca + o detalhe que importa
  (tamanho, capacidade, voltagem, cor). Sem emoji, sem caixa alta, sem preço, sem palavras como "oferta".
- frases: frase chamativa de até 90 caracteres, português do Brasil, tom direto e masculino, sem exagero e sem
  inventar o que não está nos dados. Pode citar o desconto se houver. No máximo 1 emoji.
- nota: uma frase curta de opinião sobre a oferta (se o desconto é forte, se vale destacar o cupom, ou um alerta se
  algo parece estranho nos dados, como preço fora do normal para o produto).

Dados:
"""


def ia(d):
    item = {"nome": d.get("nome", ""), "por": bot._preco(d.get("por")), "de": bot._preco(d.get("de")),
            "loja": d.get("loja", "")}
    off = round((1 - item["por"] / item["de"]) * 100) if item["de"] and item["por"] and item["de"] > item["por"] else 0
    chave = bot.segredo("GEMINI_API_KEY")
    if not chave:
        frases = list(dict.fromkeys(bot.gerar_frase(item) for _ in range(12)))[:3]
        return {"sem_chave": True, "titulos": [bot.nome_curto(item["nome"], 60)] if item["nome"] else [],
                "frases": [f for f in frases if f], "nota": ""}
    dados = (f"Nome atual: {item['nome']}\nLoja: {LOJAS.get(item['loja'], item['loja'])}\n"
             f"Preço: {bot.reais(item['por']) if item['por'] else '?'}"
             + (f" (antes {bot.reais(item['de'])}, {off}% off)" if off else "")
             + (f"\nCupom: {d['cupom']}" if d.get("cupom") else "")
             + ("\nPreço exclusivo Prime" if d.get("prime") else "")
             + (f"\nFrase atual: {d['frase']}" if d.get("frase") else ""))
    partes = [{"text": PEDIDO_IA + dados}]
    foto = bot.baixar_foto(d.get("foto_raw")) if d.get("foto_raw") else None
    if foto and len(foto) < 3_500_000:
        tipo = "image/png" if foto[:4] == b"\x89PNG" else "image/webp" if foto[8:12] == b"WEBP" else "image/jpeg"
        partes.append({"inline_data": {"mime_type": tipo, "data": base64.b64encode(foto).decode()}})
    modelo = bot.CONFIG.get("ia", {}).get("modelo", "gemini-2.5-flash")
    corpo = {"contents": [{"parts": partes}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    try:
        texto, _ = bot.http(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}",
                            json.dumps(corpo).encode(), {"Content-Type": "application/json"}, timeout=40)
        bruto = json.loads(texto)["candidates"][0]["content"]["parts"][0]["text"]
        r = json.loads(bruto[bruto.find("{"):bruto.rfind("}") + 1])
    except Exception as e:
        bot.log(f"Painel: IA falhou ({e}).")
        return {"erro": "A IA não respondeu agora. Tente de novo em alguns segundos."}
    limpa = lambda lista, n: [str(t).strip().strip('"') for t in (lista or []) if str(t).strip()][:n]
    return {"titulos": limpa(r.get("titulos"), 3), "frases": limpa(r.get("frases"), 3), "nota": str(r.get("nota") or "")}


# ---------------------------------------------------------------- ações que mudam a fila

def acao(corpo):
    a = corpo.get("acao", "")
    ident = str(corpo.get("id", ""))
    d = corpo.get("dados") or {}

    if a == "ler_link":
        return ler_link(d.get("texto", ""))
    if a == "ia":
        return ia(d)
    if a == "foto":
        return subir_foto(d.get("imagem", ""))
    if a == "radar":
        if bot.chamar_github_agora("Painel: pedi uma rodada ao GitHub."):
            return {"ok": "Pedi uma rodada ao GitHub. As ofertas novas aparecem em Revisar em 1 ou 2 minutos."}
        return {"erro": "Não consegui chamar o GitHub agora."}
    if a == "story_telegram":
        return story_telegram(d)

    feito = {}      # o que já saiu para o Telegram (se a fila mudar no meio, a ação roda de novo sem repetir o post)
    saida = {}
    depois = []     # coisas para fazer só depois de salvar

    def mexer(estado):
        saida.clear()
        depois.clear()
        fila, revisar = estado["fila"], estado["revisar"]
        na_fila = next((x for x in fila if bot.id_story(x) == ident), None)
        em_revisao = next((x for x in revisar if str(x.get("id")) == ident), None)

        if a in ("rev_salvar", "rev_aprovar", "rev_descartar"):
            if not em_revisao:
                saida["erro"] = "Essa oferta já foi resolvida (talvez pelo Telegram)."
                return
            if a == "rev_descartar":
                estado["revisar"] = [x for x in revisar if x is not em_revisao]
                depois.append(lambda m=em_revisao.get("msg_id"): tirar_botoes(m))
                saida["ok"] = "Descartada."
                return
            aplicar_campos(em_revisao, d)
            if a == "rev_salvar":
                saida["ok"] = "Salvo."
                return
            falta = bot.faltando(em_revisao)
            if falta:
                saida["erro"] = "Ainda falta: " + ", ".join(falta) + "."
                return
            depois.append(lambda m=em_revisao.get("msg_id"): tirar_botoes(m))
            saida["ok"] = bot.aprovar(estado, em_revisao).replace("✅ ", "")
        elif a == "rev_aprovar_tudo":
            prontos = [x for x in revisar if not bot.faltando(x)]
            for x in prontos:
                bot.aprovar(estado, x)
                depois.append(lambda m=x.get("msg_id"): tirar_botoes(m))
            saida["ok"] = f"Aprovei {len(prontos)} oferta(s)."
        elif a.startswith("fila_"):
            if not na_fila:
                saida["erro"] = "Essa oferta já saiu da fila."
                return
            if a == "fila_salvar":
                aplicar_campos(na_fila, d)
                saida["ok"] = "Salvo."
            elif a == "fila_remover":
                estado["fila"] = [x for x in fila if x is not na_fila]
                saida["ok"] = "Removida da fila."
            elif a == "fila_manter":
                na_fila["aprovado_em"] = bot.agora().isoformat(timespec="minutes")
                na_fila.pop("aviso_validade", None)
                saida["ok"] = "Prazo renovado."
            elif a == "fila_mover":
                fila.remove(na_fila)
                fila.insert(max(0, min(int(d.get("para", 0)), len(fila))), na_fila)
                saida["ok"] = "Ordem salva."
            elif a == "fila_postar":
                aplicar_campos(na_fila, d)
                postar(estado, na_fila, feito)
                saida["ok"] = "Postado no canal."
        elif a == "nova":
            link = (d.get("link") or "").strip()
            if not link.startswith("http"):
                saida["erro"] = "Falta o link."
                return
            item = {"loja": bot.loja_do_link(link), "link": link, "chave": d.get("chave") or f"{bot.loja_do_link(link)}:{link}"}
            for k in ("link_produto", "cupom_loja"):
                if d.get(k):
                    item[k] = d[k]
            if d.get("foto_raw"):
                item["foto"] = d["foto_raw"]
            aplicar_campos(item, {k: v for k, v in d.items() if k != "link"})
            if any(x["chave"] == item["chave"] for x in fila):
                saida["erro"] = "Esse link já está na fila."
                return
            destino = d.get("destino", "fila")
            if destino == "telegram":  # manda para revisar no privado (e o GitHub tenta ler se a loja bloqueou)
                if d.get("bloqueado") and bot.faltando(item):
                    item["ler_depois"] = True
                if not feito.get("revisao"):
                    bot.mandar_para_revisao(estado, item)
                    feito["revisao"] = item
                else:
                    estado["revisar"].append(feito["revisao"])
                    estado["proximo_id"] = max(estado["proximo_id"], feito["revisao"]["id"] + 1)
                saida["ok"] = "Mandei para revisão no Telegram."
                return
            falta = bot.faltando(item)
            if falta:
                saida["erro"] = "Ainda falta: " + ", ".join(falta) + "."
                return
            item["aprovado_em"] = bot.agora().isoformat(timespec="minutes")
            fila.append(item)
            if destino == "postar":
                postar(estado, item, feito)
                saida["ok"] = "Postado no canal."
            else:
                saida["ok"] = f"Na fila, posição {len(fila)}."
        else:
            saida["erro"] = "Ação desconhecida."

    bot.com_estado(mexer)
    for f in depois:
        f()
    if feito.get("postado"):
        bot.responder_admin(f"📢 Postado pelo painel: {html.escape(feito['postado'])}.")
    return dict(saida) or {"ok": "Nada mudou."}


def postar(estado, item, feito):
    if not feito.get("postado"):
        bot.publicar(item)
        feito["postado"] = item.get("nome", "")
    estado["fila"] = [x for x in estado["fila"] if x is not item]
    bot.guardar_ultimo(estado, item)
    estado["postados"].append({"chave": item["chave"], "loja": item["loja"], "nome": item["nome"],
                               "data": bot.agora().isoformat(timespec="minutes")})


# ---------------------------------------------------------------- story

def achar_para_story(tipo, ident):
    if tipo == "fixo":
        return None
    estado, _ = bot.carregar_estado()
    lista = estado.get("ultimos", []) if tipo == "ultimo" else estado["fila"]
    return next((x for x in lista if bot.id_story(x) == ident), None)


def gerar_story_png(tipo, ident):
    import story
    canal = bot.CONFIG.get("canal_publico", "").replace("@", "").replace("https://t.me/", "")
    if tipo == "fixo":
        return story.story_fixo(canal), "story_alpha_promo.png"
    item = achar_para_story(tipo, ident)
    if not item:
        return None, ""
    return story.story_oferta(item, bot.baixar_foto(item.get("foto")), canal), "story_oferta.png"


def story_telegram(d):
    png, nome = gerar_story_png(d.get("tipo", "ultimo"), str(d.get("id", "")))
    if not png:
        return {"erro": "Não achei mais essa oferta."}
    bot.enviar_arquivo(png, nome, "Story gerado pelo painel.")
    return {"ok": "Mandei o story no seu privado do Telegram."}


# ---------------------------------------------------------------- rotas

def tratar_get(req):
    url = urllib.parse.urlparse(req.path)
    q = dict(urllib.parse.parse_qsl(url.query))
    caminho = url.path.rstrip("/")
    if caminho == "/painel/sair":
        responder(req, 303, "", "text/plain", {"Location": "/painel", "Set-Cookie": f"{COOKIE}=; Path=/painel; Max-Age=0"})
        return
    if not logado(req):
        if caminho == "/painel":
            responder(req, 200, pagina_login(), "text/html; charset=utf-8")
        else:
            responder(req, 401, {"erro": "entre de novo"})
        return
    if caminho == "/painel":
        responder(req, 200, ARQ_HTML.read_bytes(), "text/html; charset=utf-8")
    elif caminho == "/painel/api/estado":
        estado, _ = bot.carregar_estado()
        responder(req, 200, resumo(estado))
    elif caminho == "/painel/foto":
        f = q.get("f", "")
        if f not in _fotos:
            dados = bot.baixar_foto(f)
            if not dados:
                responder(req, 404, "", "text/plain")
                return
            if len(_fotos) > 80:
                _fotos.pop(next(iter(_fotos)))
            _fotos[f] = dados
        responder(req, 200, _fotos[f], "image/jpeg")
    elif caminho == "/painel/story":
        try:
            png, nome = gerar_story_png(q.get("tipo", "ultimo"), q.get("id", ""))
        except ImportError:
            responder(req, 500, "O servidor precisa do Pillow: Build Command = pip install -r requirements.txt", "text/plain")
            return
        if not png:
            responder(req, 404, "Não achei mais essa oferta.", "text/plain; charset=utf-8")
            return
        responder(req, 200, png, "image/png", {"Content-Disposition": f'inline; filename="{nome}"', "Cache-Control": "no-store"})
    else:
        responder(req, 404, {"erro": "não existe"})


def tratar_post(req):
    caminho = urllib.parse.urlparse(req.path).path.rstrip("/")
    tamanho = int(req.headers.get("Content-Length") or 0)
    if tamanho > 12_000_000:
        responder(req, 413, {"erro": "arquivo grande demais"})
        return
    bruto = req.rfile.read(tamanho)
    if caminho == "/painel/entrar":
        tentativa = dict(urllib.parse.parse_qsl(bruto.decode("utf-8", "replace"))).get("senha", "")
        if senha() and hmac.compare_digest(tentativa.encode(), senha().encode()):
            cookie = f"{COOKIE}={assinatura()}; Path=/painel; Max-Age={60 * 60 * 24 * 60}; HttpOnly; SameSite=Lax"
            if req.headers.get("X-Forwarded-Proto") == "https":
                cookie += "; Secure"
            responder(req, 303, "", "text/plain", {"Location": "/painel", "Set-Cookie": cookie})
        else:
            time.sleep(1.5)  # freia quem tentar adivinhar a senha
            responder(req, 200, pagina_login("Senha errada." if senha() else ""), "text/html; charset=utf-8")
        return
    if not logado(req):
        responder(req, 401, {"erro": "entre de novo"})
        return
    if caminho != "/painel/api/acao":
        responder(req, 404, {"erro": "não existe"})
        return
    try:
        corpo = json.loads(bruto or b"{}")
        responder(req, 200, acao(corpo))
    except Exception as e:
        bot.log(f"Painel: erro na ação {str(bruto[:80])}: {e}")
        responder(req, 200, {"erro": f"Deu erro: {e}"})
