"""Íris: lê os botões ✅/❌ que você aperta no bot da Legião e, no horário, posta o carrossel ou o Reel aprovado
no Instagram e na Página do Facebook (API oficial da Meta). Roda a cada rodada do bot-achados, sem Claude.

Os posts ficam na branch legiao-posts em posts/AAAA-MM-DD/ (carrossel) e posts/AAAA-MM-DD-reel/ (Reel),
cada um com post.json e status.json."""
import base64
import json
import os
import sys
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


def refazer(pasta, status, sha, motivo):
    """Manda a Legião fazer de novo esse post, levando em conta o motivo da recusa."""
    status["aguardando_motivo"] = False
    gravar_status(pasta, status, sha)
    http(f"https://api.github.com/repos/{REPO}/actions/workflows/legiao.yml/dispatches",
         {"ref": "main", "inputs": {"tipo": status.get("tipo", "carrossel"), "motivo": motivo[:900], "pasta": pasta}},
         cabecalhos=GH)


def ler_motivo(texto):
    """Sua mensagem depois de um ❌ vira o motivo, e a Legião refaz o post."""
    hoje = datetime.now(BRT)
    candidatos = []
    for dia in (hoje.strftime("%Y-%m-%d"), (hoje - timedelta(days=1)).strftime("%Y-%m-%d")):
        for pasta in (dia, f"{dia}-reel"):
            status, sha = ler_status(pasta)
            if status and status.get("aguardando_motivo"):
                candidatos.append((status.get("recusado_em", ""), pasta, status, sha))
    if not candidatos:
        return
    _, pasta, status, sha = max(candidatos)
    nome = "Reel" if status.get("tipo") == "reel" else "carrossel"
    if texto.strip().lower().rstrip(".!").startswith(("não refaz", "nao refaz", "não precisa", "nao precisa")):
        status["aguardando_motivo"] = False
        gravar_status(pasta, status, sha)
        avisar(f"Ok, sem {nome} hoje.")
        return
    refazer(pasta, status, sha, texto)
    avisar(f"🔁 Entendi. Refazendo o {nome} com base nisso; a nova versão chega aqui em alguns minutos.")


def ler_botoes():
    """Processa os cliques em ✅/❌ (e o motivo depois de um ❌) e confirma pro Telegram que já foram lidos."""
    updates = tg("getUpdates", {"timeout": 0, "allowed_updates": ["callback_query", "message"]}).get("result", [])
    for u in updates:
        msg = u.get("message") or {}
        if msg.get("text") and str(msg.get("from", {}).get("id")) == str(CHAT) and not msg["text"].startswith("/"):
            ler_motivo(msg["text"])
            continue
        cb = u.get("callback_query")
        if not cb or not str(cb.get("data", "")).startswith("lg:") or str(cb["from"]["id"]) != str(CHAT):
            continue
        _, acao, dia = cb["data"].split(":", 2)
        if acao == "x":
            tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
            continue
        status, sha = ler_status(dia)
        if not status:
            tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Não achei esse post."})
            continue
        pediu_motivo = False
        if status["status"] in ("pendente", "aprovado", "recusado"):
            pediu_motivo = acao == "no" and status["status"] != "recusado"
            status["status"] = "aprovado" if acao == "ok" else "recusado"
            status["aguardando_motivo"] = pediu_motivo
            if pediu_motivo:
                status["recusado_em"] = datetime.now(BRT).isoformat()
            gravar_status(dia, status, sha)
        resposta = {"aprovado": f"✅ Aprovado! Posto às {status.get('horario', 18)}h.",
                    "recusado": "❌ Recusado, não vou postar."}.get(status["status"], "Esse já foi postado.")
        tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": resposta})
        msg = cb.get("message") or {}
        if msg:
            tg("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                          "reply_markup": {"inline_keyboard": [[{"text": resposta, "callback_data": "lg:x:-"}]]}})
        if pediu_motivo:
            avisar("❌ O que não ficou bom? Me manda em uma mensagem (ex.: \"tema fraco\", \"frase muito longa\", "
                   "\"clipe não combina\") que eu refaço. Se não quiser outro, manda \"não refaz\".")
    if updates:
        tg("getUpdates", {"offset": updates[-1]["update_id"] + 1, "timeout": 0})


# ---------- Meta ----------

def meta(caminho, **params):
    params["access_token"] = os.environ["META_PAGE_TOKEN"]
    return http(f"{GRAPH}/{caminho}", urllib.parse.urlencode(params).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"})


def esperar_pronto(container, tentativas=30):
    for _ in range(tentativas):
        r = http(f"{GRAPH}/{container}?fields=status_code&access_token={os.environ['META_PAGE_TOKEN']}")
        if r.get("status_code") in ("FINISHED", None):
            return
        if r.get("status_code") == "ERROR":
            raise RuntimeError(f"O Instagram recusou a mídia {container}.")
        time.sleep(5)
    raise RuntimeError("O Instagram demorou demais pra processar a mídia.")


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


def enviar_video(url_upload, video):
    """Upload direto do arquivo (rupload da Meta), sem depender de link público do vídeo."""
    return http(url_upload, video, {"Authorization": f"OAuth {os.environ['META_PAGE_TOKEN']}", "offset": "0",
                                    "file_size": str(len(video)), "Content-Type": "application/octet-stream"})


def postar_reel_instagram(video, legenda):
    ig = os.environ["IG_USER_ID"]
    container = meta(f"{ig}/media", media_type="REELS", upload_type="resumable", caption=legenda, share_to_feed="true")
    enviar_video(container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v26.0/{container['id']}", video)
    esperar_pronto(container["id"], tentativas=60)
    publicado = meta(f"{ig}/media_publish", creation_id=container["id"])["id"]
    link = http(f"{GRAPH}/{publicado}?fields=permalink&access_token={os.environ['META_PAGE_TOKEN']}").get("permalink", "")
    return publicado, link


def postar_reel_facebook(video, legenda):
    pagina = os.environ["META_PAGE_ID"]
    inicio = meta(f"{pagina}/video_reels", upload_phase="start")
    enviar_video(inicio["upload_url"], video)
    meta(f"{pagina}/video_reels", upload_phase="finish", video_id=inicio["video_id"], video_state="PUBLISHED",
         description=legenda)
    return inicio["video_id"]


def publicar(pasta, status, sha):
    post = ler_post(pasta)
    base = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/posts/{pasta}"
    reel = status.get("tipo") == "reel"
    if reel:
        with urllib.request.urlopen(f"{base}/reel.mp4", timeout=120) as r:
            midia = r.read()
        ig, fb, nome = postar_reel_instagram, postar_reel_facebook, "Reel"
    else:
        midia = [f"{base}/{i + 1:02d}.jpg" for i in range(len(post["slides"]))]
        ig, fb, nome = postar_instagram, postar_facebook, "Carrossel"
    status["status"] = "publicando"
    gravar_status(pasta, status, sha)
    _, sha = ler_status(pasta)
    linhas = []
    try:
        ig_id, link = ig(midia, post["legenda"])
        status["instagram"] = ig_id
        linhas.append(f"✅ Instagram: {link or 'postado'}")
    except Exception as erro:
        linhas.append(f"⚠️ Instagram falhou: {str(erro)[:400]}")
    if os.environ.get("META_PAGE_ID"):
        try:
            status["facebook"] = fb(midia, post["legenda"])
            linhas.append("✅ Facebook: postado na Página")
        except Exception as erro:
            linhas.append(f"⚠️ Facebook falhou: {str(erro)[:400]}")
    status["status"] = "publicado" if status.get("instagram") or status.get("facebook") else "erro"
    gravar_status(pasta, status, sha)
    avisar(f"🦁 {nome} de {status.get('dia', pasta)}: \"{post.get('tema', '')}\"\n\n" + "\n".join(linhas))


def checar():
    """Só confere se o token da Meta enxerga o Instagram e a Página (não posta nada)."""
    token = os.environ["META_PAGE_TOKEN"]
    ig = http(f"{GRAPH}/{os.environ['IG_USER_ID']}?fields=username&access_token={token}")
    print("Instagram OK:", ig.get("username"))
    if os.environ.get("META_PAGE_ID"):
        pg = http(f"{GRAPH}/{os.environ['META_PAGE_ID']}?fields=name&access_token={token}")
        print("Página OK:", pg.get("name"))


def main():
    if not TOKEN_TG or not os.environ.get("GITHUB_TOKEN"):
        return
    ler_botoes()
    if not (os.environ.get("META_PAGE_TOKEN") and os.environ.get("IG_USER_ID")):
        return
    agora = datetime.now(BRT)
    for dia in (agora.strftime("%Y-%m-%d"), (agora - timedelta(days=1)).strftime("%Y-%m-%d")):
        for pasta in (dia, f"{dia}-reel"):
            status, sha = ler_status(pasta)
            if not status or status["status"] != "aprovado":
                continue
            hora_post = datetime.strptime(dia, "%Y-%m-%d").replace(hour=int(status.get("horario", 18)), tzinfo=BRT)
            if agora >= hora_post:
                publicar(pasta, status, sha)


if __name__ == "__main__":
    checar() if "--checar" in sys.argv else main()
