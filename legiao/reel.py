"""Legião Alpha, Reel do dia: Odin escolhe o tema, Bragi escreve a frase e a legenda, Atena revisa,
Hefesto pega um clipe e uma música do Drive (conta de serviço GOOGLE_SA_JSON) e monta o vídeo no formato
do gerador do PC (5s de clipe escurecido com a frase + 2s de "Leia a Legenda >>>"). A prévia vai pro Telegram."""
import json
import os
import random
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import rodar
from rodar import AQUI, BRT, CFG, SAIDA, claude, consumo, gastos, mensagem, pedido_de_refazer, preencher

R = CFG["reel"]
HISTORICO = AQUI / "historico_reel.json"
FONTE = AQUI / "fonts" / "Tinos-Regular.ttf"
VIDEOS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
MUSICAS = (".mp3", ".m4a", ".wav", ".aac", ".ogg")
W, H, FPS = 1080, 1920, 30


# ---------- Google Drive (conta de serviço, só leitura) ----------

def drive():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    info = json.loads(os.environ["GOOGLE_SA_JSON"])
    return AuthorizedSession(service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]))


def listar(sessao, pasta, extensoes, nivel=0):
    """Arquivos com essas extensões na pasta (e em subpastas, até 2 níveis)."""
    achados, pagina = [], None
    while True:
        r = sessao.get("https://www.googleapis.com/drive/v3/files", params={
            "q": f"'{pasta}' in parents and trashed=false", "pageSize": 1000, "pageToken": pagina,
            "fields": "nextPageToken,files(id,name,mimeType)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Drive recusou ({r.status_code}): {r.text[:300]}")
        dados = r.json()
        for f in dados.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                if nivel < 2:
                    achados += listar(sessao, f["id"], extensoes, nivel + 1)
            elif f["name"].lower().endswith(extensoes):
                achados.append(f)
        pagina = dados.get("nextPageToken")
        if not pagina:
            return achados


def baixar(sessao, arquivo, destino):
    with sessao.get(f"https://www.googleapis.com/drive/v3/files/{arquivo['id']}",
                    params={"alt": "media", "supportsAllDrives": "true"}, stream=True, timeout=300) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Não consegui baixar {arquivo['name']} do Drive ({r.status_code}).")
        with open(destino, "wb") as saida:
            for bloco in r.iter_content(1 << 20):
                saida.write(bloco)
    return destino


def escolher(opcoes, usados, guardar):
    """Sorteia evitando os últimos usados (se ainda sobrar opção)."""
    recentes = set(usados[-guardar:])
    livres = [o for o in opcoes if o["name"] not in recentes] or opcoes
    return random.choice(livres)


# ---------- Vídeo (mesmo visual do gerar_videos.py do PC) ----------

def quebrar(texto, largura=20):
    import textwrap
    linhas = []
    for par in texto.strip().splitlines() or [texto.strip()]:
        linhas += textwrap.wrap(par, width=largura, break_long_words=False, break_on_hyphens=False) or [""]
    return linhas


def duracao(arquivo):
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arquivo)],
                          capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def inicio_da_musica(musica, precisa):
    """Ponto aleatório no meio da música (entre 25% e 65%), pra fugir do silêncio/intro do começo."""
    total = duracao(musica)
    if total < precisa + 20:
        return 0.0
    return round(random.uniform(total * 0.25, min(total * 0.65, total - precisa - 1)), 2)


def montar(video, musica, frase, saida):
    dur, fim = float(R["duracao_video"]), float(R["duracao_final"])
    corte = inicio_da_musica(musica, dur + fim)
    print(f"Música começa em {corte}s")
    fonte = str(FONTE).replace(":", r"\:")
    sombra = "shadowcolor=black@0.70:shadowx=2:shadowy=2"
    linhas = quebrar(frase)
    tam = 64
    topo = f"(h-{tam * len(linhas)})/2-h*0.06"
    pasta = Path(tempfile.mkdtemp())
    filtros = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
               f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,drawbox=x=0:y=0:w=iw:h=ih:color=black@{R.get('escurecer', 0.45)}:t=fill[base]"]
    entrada = "base"
    for i, linha in enumerate(linhas):
        arq = pasta / f"linha{i}.txt"
        arq.write_text(linha, encoding="utf-8")
        filtros.append(f"[{entrada}]drawtext=fontfile='{fonte}':textfile='{arq}':fontcolor=white:fontsize={tam}:"
                       f"borderw=1:bordercolor=black:{sombra}:x=(w-text_w)/2:y={topo}+{i * tam}[t{i}]")
        entrada = f"t{i}"
    filtros += [
        f"[{entrada}]drawtext=fontfile='{fonte}':text='@alphadominus.tv':fontcolor=white:fontsize=44:"
        f"borderw=1:bordercolor=black:{sombra}:x=(w-text_w)/2:y=h*0.74[principal]",
        f"color=c=black:s={W}x{H}:r={FPS}:d={fim:.3f},drawtext=fontfile='{fonte}':text='Leia a Legenda':"
        f"fontcolor=white:fontsize=68:{sombra}:x=(w-text_w)/2:y=h*0.46,"
        f"drawtext=fontfile='{fonte}':text='>>>':fontcolor=white:fontsize=56:{sombra}:x=(w-text_w)/2:y=h*0.51[final]",
        "[principal][final]concat=n=2:v=1:a=0[video]",
        f"[1:a]atrim=duration={dur + fim:.3f},asetpts=PTS-STARTPTS[audio]",
    ]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(video), "-ss", str(corte), "-stream_loop", "-1",
           "-i", str(musica), "-filter_complex", ";".join(filtros), "-map", "[video]", "-map", "[audio]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(saida)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("O FFmpeg falhou: " + proc.stderr[-800:])
    return saida


def enviar_video(arquivo, legenda):
    limite = "----legiao" + uuid.uuid4().hex
    corpo = b""
    for nome, valor in {"chat_id": rodar.CHAT, "caption": legenda, "supports_streaming": "true"}.items():
        corpo += f"--{limite}\r\nContent-Disposition: form-data; name=\"{nome}\"\r\n\r\n{valor}\r\n".encode()
    corpo += (f"--{limite}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{arquivo.name}\"\r\n"
              "Content-Type: video/mp4\r\n\r\n").encode() + arquivo.read_bytes() + b"\r\n"
    corpo += f"--{limite}--\r\n".encode()
    rodar._tg("sendVideo", corpo, f"multipart/form-data; boundary={limite}")


# ---------- Legião ----------

def validar(post):
    erros = []
    frase, legenda = str(post.get("frase", "")).strip(), str(post.get("legenda", "")).strip()
    if not frase:
        erros.append("Falta a \"frase\".")
    elif len(frase) > R["max_caracteres_frase"] + 30:  # o pedido é 110; só trava se passar muito (não cabe na tela)
        erros.append(f"A frase tem {len(frase)} caracteres; o máximo é {R['max_caracteres_frase']}. Encurte.")
    if not legenda:
        erros.append("Falta a \"legenda\".")
    post["frase"], post["legenda"] = frase, legenda
    return erros


def checar():
    """Só confere se a conta de serviço enxerga as pastas do Drive."""
    sessao = drive()
    v = listar(sessao, R["pasta_videos_drive"], VIDEOS)
    m = listar(sessao, R["pasta_musicas_drive"], MUSICAS)
    print(f"Drive OK: {len(v)} vídeos e {len(m)} músicas")
    if not v or not m:
        sys.exit("A conta de serviço não está vendo os vídeos/músicas: compartilhe a pasta AlphaDominus com ela.")


def main():
    if not (os.environ.get("GEMINI_LEGIAO_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        print("Falta o secret GEMINI_LEGIAO_KEY (ou CLAUDE_CODE_OAUTH_TOKEN); a Legião não roda.")
        return
    if not os.environ.get("GOOGLE_SA_JSON"):
        raise RuntimeError("Falta o secret GOOGLE_SA_JSON (conta de serviço do Google) pra pegar os clipes do Drive.")
    agora = rodar.data_alvo()
    historico = json.loads(HISTORICO.read_text(encoding="utf-8")) if HISTORICO.exists() else []
    usados = list(historico)  # clipes e músicas já usados (inclusive o do post recusado)
    if os.environ.get("MOTIVO"):  # refazendo: o tema recusado de hoje não conta como "já postado"
        historico = [h for h in historico if h.get("data") != agora.strftime("%Y-%m-%d")]
    temas = "\n".join(f"- {t}" for t in rodar.temas_postados(historico, reel=True)[-60:]) or "- (nenhum ainda)"
    base = {"DATA": agora.strftime("%d/%m/%Y"), "HISTORICO": temas, "CTA": CFG["cta"],
            "NICHO": (AQUI / "nicho.md").read_text(encoding="utf-8"), "MAX_FRASE": R["max_caracteres_frase"]}

    # Primeiro o Drive: se a conta de serviço não enxergar as pastas, nem gasta Claude
    sessao = drive()
    videos = listar(sessao, R["pasta_videos_drive"], VIDEOS)
    musicas = listar(sessao, R["pasta_musicas_drive"], MUSICAS)
    if not videos or not musicas:
        raise RuntimeError(f"Achei {len(videos)} vídeos e {len(musicas)} músicas no Drive. "
                           "Compartilhe a pasta AlphaDominus com o e-mail da conta de serviço (como Leitor).")

    pedido = pedido_de_refazer()
    correcoes, post, motivos = pedido, None, []
    for rodada in range(1 + CFG["rodadas_revisao"]):
        post = claude(preencher("bragi_reel.md", CORRECOES=correcoes, **base))
        motivos = validar(post)
        if not motivos:
            revisao = claude(preencher("atena_reel.md", REEL=json.dumps(post, ensure_ascii=False, indent=1), **base))
            if not revisao.get("aprovado"):
                motivos = [str(m) for m in revisao.get("motivos") or ["Reprovado sem motivo."]]
        print(f"Rodada {rodada + 1}: {'aprovado' if not motivos else motivos}")
        if not motivos:
            break
        correcoes = pedido + ("\n\n# Correções obrigatórias\nA versão anterior foi reprovada. Mantenha o que estava bom e corrija:\n"
                     + "\n".join(f"- {m}" for m in motivos)
                     + "\n\nVersão anterior:\n" + json.dumps(post, ensure_ascii=False))

    aprovado = not motivos
    if validar(post):
        mensagem("⚠️ Legião: não consegui montar o Reel hoje.\n\n" + "\n".join(motivos) + "\n\n" + consumo())
        sys.exit(1)

    video = escolher(videos, [h.get("video") for h in usados], 30)
    musica = escolher(musicas, [h.get("musica") for h in usados], 10)
    tmp = Path(tempfile.mkdtemp())
    SAIDA.mkdir(exist_ok=True)
    arquivo = montar(baixar(sessao, video, tmp / ("clipe" + Path(video["name"]).suffix)),
                     baixar(sessao, musica, tmp / ("musica" + Path(musica["name"]).suffix)),
                     post["frase"], SAIDA / "reel.mp4")
    dia = agora.strftime("%Y-%m-%d")
    pasta = f"{dia}-reel"
    (SAIDA / "post.json").write_text(json.dumps({**post, "tipo": "reel", "aprovado": aprovado, "data": base["DATA"],
                                                "video": video["name"], "musica": musica["name"]},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    teste = bool(os.environ.get("TESTE"))  # teste: só manda a prévia, não guarda nem pede aprovação
    if not teste:
      (SAIDA / "status.json").write_text(json.dumps({"status": "pendente" if aprovado else "recusado", "tipo": "reel", "dia": dia, "pasta": pasta,
                                                   "horario": R["horario_post"]}), encoding="utf-8")

    enviar_video(arquivo, ("🧪 TESTE (não vai ser postado)\n" if teste else "") + f"🎬 Legião Alpha – Reel {base['DATA']}\nTema: {post.get('tema', '')}\n"
                          f"Clipe: {video['name']} · Música: {musica['name']}")
    status = "✅ Aprovado pela Atena" if aprovado else "⛔ A Atena REPROVOU (não postar):\n" + "\n".join(f"• {m}" for m in motivos)
    botoes = [{"text": "✅ Aprovar", "callback_data": f"lg:ok:{pasta}"},
              {"text": "❌ Recusar", "callback_data": f"lg:no:{pasta}"}] if aprovado and not teste else None
    fim = ("🧪 Era só um teste: não vai ser postado." if teste else
           f"Aprova pra postar o Reel no Instagram e no Facebook às {R['horario_post']}h?" if aprovado else
           "Esse não vai ser postado.")
    mensagem(f"{status}\n\n📝 Legenda:\n\n{post['legenda']}\n\n{consumo()}\n\n{fim}", botoes)
    if aprovado and not teste:
        historico.append({"data": dia, "tema": post.get("tema", ""), "video": video["name"], "musica": musica["name"]})
        HISTORICO.write_text(json.dumps(historico, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if "--checar" in sys.argv:
        checar()
        sys.exit()
    try:
        main()
    except Exception as erro:  # avisa no Telegram em vez de falhar calado
        aviso = f"⚠️ Legião (Reel) falhou: {str(erro)[:1500]}" + (f"\n\n{consumo()}" if gastos else "")
        try:
            mensagem(aviso)
        except Exception:
            os.environ.pop("LEGIAO_TELEGRAM_TOKEN", None)
            rodar.CHAT = os.environ.get("ADMIN_ID", "")
            mensagem(aviso + "\n\n(Mandei pelo bot de promo porque o bot da Legião não conseguiu te mandar mensagem.)")
        raise
