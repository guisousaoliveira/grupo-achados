"""Íris: lê os botões ✅/❌ que você aperta no bot da Legião e, no horário, posta o carrossel aprovado
no Instagram e na Página do Facebook (API oficial da Meta). Roda a cada rodada do bot-achados, sem Claude.

Os posts ficam na branch legiao-posts em posts/AAAA-MM-DD/ (imagens, post.json, status.json)."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
BRANCH = "legiao-posts"
GRAPH = "https://graph.facebook.com/v26.0"
TOKEN_TG = os.environ.get("LEGIAO_TELEGRAM_TOKEN") or ""
CHAT = os.environ.get("LEGIAO_CHAT_ID") or os.environ.get("ADMIN_ID", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "guisousaoliveira/grupo-achados")


def http(url, dados=None, cabecalhos=None, metodo=None):
    corpo = dados if isinstance(dados, (bytes, type(None))) else json.dumps(dados).encode()
    req = urllib.request.Request(url, corpo, {"Content-Type": "application/json", **(cabecalhos or {})},
                                 method=metodo or ("POST" if corpo is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            texto = r.read()
            return json.loads(texto) if texto else {}
    except urllib.error.HTTPError as erro:
        raise RuntimeError(f"{erro.code} em {url.split('?')[0]}: {erro.read().decode(errors='replace')[:400]}") from None


# ---------- GitHub (branch legiao-posts) ----------

def _gh(caminho):
    return f"https://api.github.com/repos/{REPO}/contents/{caminho}"


GH = {"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json"}


def ler_status(dia):
    try:
        r = http(_gh(f"posts/{dia}/status.json") + f"?ref={BRANCH}", cabecalhos=GH)
    except RuntimeError as erro:
        if str(erro).startswith("404"):
            return None, None
        raise
    return json.loads(base64.b64decode(r["content"])), r["sha"]


def gravar_status(dia, status, sha):
    http(_gh(f"posts/{dia}/status.json"), {
        "message": f"legião: {dia} {status['status']}", "branch": BRANCH, "sha": sha,
        "content": base64.b64encode(json.dumps(status, ensure_ascii=False, indent=1).encode()).decode()},
        cabecalhos=GH, metodo="PUT")


def ler_post(dia):
    r = http(_gh(f"posts/{dia}/post.json") + f"?ref={BRANCH}", cabecalhos=GH)
    return json.loads(base64.b64decode(r["content"]))


# ---------- Telegram ----------

def tg(metodo, dados):
    return http(f"https://api.telegram.org/bot{TOKEN_TG}/{metodo}", dados)


def avisar(texto):
    tg("sendMessage", {"chat_id": CHAT, "text": texto, "disable_web_page_preview": True})


def ler_botoes():
    """Processa os cliques em ✅/❌ e confirma pro Telegram que já foram lidos."""
    updates = tg("getUpdates", {"timeout": 0, "allowed_updates": ["callback_query"]}).get("result", [])
    for u in updates:
        cb = u.get("callback_query")
        if not cb or not str(cb.get("data", "")).startswith("lg:") or str(cb["from"]["id"]) != str(CHAT):
            continue
        _, acao, dia = cb["data"].split(":", 2)
        status, sha = ler_status(dia)
        if not status:
            tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Não achei esse post."})
            continue
        if status["status"] in ("pendente", "aprovado", "recusado"):
            status["status"] = "aprovado" if acao == "ok" else "recusado"
            gravar_status(dia, status, sha)
        resposta = {"aprovado": f"✅ Aprovado! Posto às {status.get('horario', 18)}h.",
                    "recusado": "❌ Recusado, não vou postar."}.get(status["status"], "Esse já foi postado.")
        tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": resposta})
        msg = cb.get("message") or {}
        if msg:
            tg("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                          "reply_markup": {"inline_keyboard": [[{"text": resposta, "callback_data": "lg:x:-"}]]}})
    if updates:
        tg("getUpdates", {"offset": updates[-1]["update_id"] + 1, "timeout": 0})


# ---------- Meta ----------

def meta(caminho, **params):
    params["access_token"] = os.environ["META_PAGE_TOKEN"]
    return http(f"{GRAPH}/{caminho}", urllib.parse.urlencode(params).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"})


def esperar_pronto(container):
    for _ in range(30):
        r = http(f"{GRAPH}/{container}?fields=status_code&access_token={os.environ['META_PAGE_TOKEN']}")
        if r.get("status_code") in ("FINISHED", None):
            return
        if r.get("status_code") == "ERROR":
            raise RuntimeError(f"O Instagram recusou a mídia {container}.")
        time.sleep(5)
    raise RuntimeError("O Instagram demorou demais pra processar as imagens.")


def postar_instagram(urls, legenda):
    ig = os.environ["IG_USER_ID"]
    filhos = [meta(f"{ig}/media", image_url=u, is_carousel_item="true")["id"] for u in urls]
    for f in filhos:
        esperar_pronto(f)
    carrossel = meta(f"{ig}/media", media_type="CAROUSEL", children=",".join(filhos), caption=legenda)["id"]
    esperar_pronto(carrossel)
    publicado = meta(f"{ig}/media_publish", creation_id=carrossel)["id"]
    link = http(f"{GRAPH}/{publicado}?fields=permalink&access_token={os.environ['META_PAGE_TOKEN']}").get("permalink", "")
    return publicado, link


def postar_facebook(urls, legenda):
    pagina = os.environ["META_PAGE_ID"]
    fotos = [meta(f"{pagina}/photos", url=u, published="false")["id"] for u in urls]
    params = {"message": legenda}
    for i, f in enumerate(fotos):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": f})
    return meta(f"{pagina}/feed", **params)["id"]


def publicar(dia, status, sha):
    post = ler_post(dia)
    base = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/posts/{dia}"
    urls = [f"{base}/{i + 1:02d}.jpg" for i in range(len(post["slides"]))]
    status["status"] = "publicando"
    gravar_status(dia, status, sha)
    _, sha = ler_status(dia)
    linhas = []
    try:
        ig_id, link = postar_instagram(urls, post["legenda"])
        status["instagram"] = ig_id
        linhas.append(f"✅ Instagram: {link or 'postado'}")
    except Exception as erro:
        linhas.append(f"⚠️ Instagram falhou: {str(erro)[:400]}")
    if os.environ.get("META_PAGE_ID"):
        try:
            status["facebook"] = postar_facebook(urls, post["legenda"])
            linhas.append("✅ Facebook: postado na Página")
        except Exception as erro:
            linhas.append(f"⚠️ Facebook falhou: {str(erro)[:400]}")
    status["status"] = "publicado" if status.get("instagram") or status.get("facebook") else "erro"
    gravar_status(dia, status, sha)
    avisar(f"🦁 Carrossel de {dia}: \"{post.get('tema', '')}\"\n\n" + "\n".join(linhas))


def main():
    if not TOKEN_TG or not os.environ.get("GITHUB_TOKEN"):
        return
    ler_botoes()
    if not (os.environ.get("META_PAGE_TOKEN") and os.environ.get("IG_USER_ID")):
        return
    agora = datetime.now(BRT)
    for dia in (agora.strftime("%Y-%m-%d"), (agora - timedelta(days=1)).strftime("%Y-%m-%d")):
        status, sha = ler_status(dia)
        if not status or status["status"] != "aprovado":
            continue
        hora_post = datetime.strptime(dia, "%Y-%m-%d").replace(hour=int(status.get("horario", 18)), tzinfo=BRT)
        if agora >= hora_post:
            publicar(dia, status, sha)


if __name__ == "__main__":
    main()
