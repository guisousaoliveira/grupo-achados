"""Legião Alpha: Odin escolhe o tema, Bragi escreve, Atena revisa, Hefesto desenha o carrossel
e a prévia vai pro Telegram. Tudo com o Claude Code na assinatura (CLAUDE_CODE_OAUTH_TOKEN)."""
import json
import os
import subprocess
import sys
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import carrossel

AQUI = Path(__file__).resolve().parent
BRT = timezone(timedelta(hours=-3))
CFG = json.loads((AQUI / "config.json").read_text(encoding="utf-8"))
HISTORICO = AQUI / "historico.json"
SAIDA = AQUI / "saida"
gastos = []


# ---------- Telegram ----------

def _tg(metodo, corpo, tipo):
    token = os.environ["TELEGRAM_TOKEN"]
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}", corpo, {"Content-Type": tipo})
    return urllib.request.urlopen(req, timeout=120).read()


def mensagem(texto):
    partes, atual = [], ""
    for linha in texto.splitlines(keepends=True):
        if len(atual) + len(linha) > 3900:
            partes.append(atual)
            atual = ""
        atual += linha
    partes.append(atual)
    for parte in partes:
        _tg("sendMessage", json.dumps({"chat_id": os.environ["ADMIN_ID"], "text": parte,
                                       "disable_web_page_preview": True}).encode(), "application/json")


def fotos(arquivos, legenda, tipo="photo"):
    limite = "----legiao" + uuid.uuid4().hex
    campos = {"chat_id": os.environ["ADMIN_ID"],
              "media": json.dumps([{"type": tipo, "media": f"attach://f{i}", **({"caption": legenda} if i == 0 else {})}
                                   for i in range(len(arquivos))])}
    corpo = b""
    for nome, valor in campos.items():
        corpo += f"--{limite}\r\nContent-Disposition: form-data; name=\"{nome}\"\r\n\r\n{valor}\r\n".encode()
    for i, arq in enumerate(arquivos):
        corpo += (f"--{limite}\r\nContent-Disposition: form-data; name=\"f{i}\"; filename=\"{arq.name}\"\r\n"
                  "Content-Type: image/jpeg\r\n\r\n").encode() + arq.read_bytes() + b"\r\n"
    corpo += f"--{limite}--\r\n".encode()
    _tg("sendMediaGroup", corpo, f"multipart/form-data; boundary={limite}")


# ---------- Claude ----------

def claude(prompt):
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", CFG.get("modelo", "sonnet"), "--max-turns", "3"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10 * 60)
    try:
        saida = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError((proc.stderr or proc.stdout)[-1500:])
    gastos.append(saida)
    texto = (saida.get("result") or "").strip()
    if saida.get("is_error") or not texto:
        raise RuntimeError(texto[:1500] or str(saida.get("subtype")))
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim < 0:
        raise RuntimeError("O Claude não devolveu JSON: " + texto[:500])
    return json.loads(texto[inicio:fim + 1])


def n(numero):
    return f"{numero:,}".replace(",", ".")


def consumo():
    soma = {k: sum((g.get("usage") or {}).get(k, 0) for g in gastos)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    total = sum(soma.values())
    custo = sum(g.get("total_cost_usd") or 0 for g in gastos)
    segundos = round(sum(g.get("duration_ms") or 0 for g in gastos) / 1000)
    return "\n".join([
        "📊 Consumo desta rodada",
        f"Tokens: {n(total)} no total ({n(soma['input_tokens'] + soma['cache_creation_input_tokens'])} lidos novos, "
        f"{n(soma['cache_read_input_tokens'])} reaproveitados do cache, {n(soma['output_tokens'])} escritos)",
        f"Chamadas ao Claude: {len(gastos)} · tempo: {segundos}s",
        f"Se fosse API paga: ~US$ {custo:.2f}. Na sua assinatura não cobra, só gasta do limite do plano.",
    ])


# ---------- Legião ----------

def preencher(modelo, **valores):
    texto = (AQUI / modelo).read_text(encoding="utf-8")
    for chave, valor in valores.items():
        texto = texto.replace("{" + chave + "}", str(valor))
    return texto


def validar_formato(post):
    slides = post.get("slides") or []
    erros = []
    if not (CFG["frames_min"] + 2 <= len(slides) <= CFG["frames_max"] + 2):
        erros.append(f"O carrossel tem {len(slides)} slides; precisa de capa + {CFG['frames_min']} a "
                     f"{CFG['frames_max']} frames + finalização.")
    if any(not isinstance(s, dict) or not s.get("titulo") for s in slides):
        erros.append("Todo slide precisa de \"titulo\".")
    if not post.get("legenda"):
        erros.append("Falta a \"legenda\".")
    for s in slides:
        if isinstance(s, dict):
            s["titulo"], s["texto"] = str(s.get("titulo", "")).strip(), str(s.get("texto", "")).strip()
    return erros


def main():
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("Falta o secret CLAUDE_CODE_OAUTH_TOKEN; a Legião não roda.")
        return
    agora = datetime.now(BRT)
    historico = json.loads(HISTORICO.read_text(encoding="utf-8")) if HISTORICO.exists() else []
    temas = "\n".join(f"- {h['tema']}" for h in historico[-60:]) or "- (nenhum ainda)"
    base = {"DATA": agora.strftime("%d/%m/%Y"), "HISTORICO": temas, "CTA": CFG["cta"],
            "FRAMES_MIN": CFG["frames_min"], "FRAMES_MAX": CFG["frames_max"]}

    correcoes, post, motivos = "", None, []
    for rodada in range(1 + CFG["rodadas_revisao"]):
        post = claude(preencher("bragi.md", CORRECOES=correcoes, **base))
        motivos = validar_formato(post)
        if not motivos:
            motivos = carrossel.problemas_de_espaco(post["slides"])
        if not motivos:
            revisao = claude(preencher("atena.md", CARROSSEL=json.dumps(post, ensure_ascii=False, indent=1), **base))
            if not revisao.get("aprovado"):
                motivos = [str(m) for m in revisao.get("motivos") or ["Reprovado sem motivo."]]
        print(f"Rodada {rodada + 1}: {'aprovado' if not motivos else motivos}")
        if not motivos:
            break
        correcoes = ("# Correções obrigatórias\nA versão anterior foi reprovada. Mantenha o que estava bom e corrija:\n"
                     + "\n".join(f"- {m}" for m in motivos)
                     + "\n\nVersão anterior:\n" + json.dumps(post, ensure_ascii=False))

    aprovado = not motivos
    SAIDA.mkdir(exist_ok=True)
    (SAIDA / "post.json").write_text(json.dumps({**post, "aprovado": aprovado, "data": base["DATA"]},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    if validar_formato(post):
        mensagem("⚠️ Legião: não consegui montar o carrossel hoje.\n\n" + "\n".join(motivos) + "\n\n" + consumo())
        sys.exit(1)

    arquivos = carrossel.gerar(post["slides"], SAIDA)
    status = "✅ Aprovado pela Atena" if aprovado else "⛔ A Atena REPROVOU (não postar):\n" + "\n".join(f"• {m}" for m in motivos)
    fotos(arquivos, f"🦁 Legião Alpha – {base['DATA']}\nTema: {post.get('tema', '')}")
    fotos(arquivos, "Arquivos em qualidade máxima, pra postar", "document")
    mensagem(f"{status}\n\n📝 Legenda:\n\n{post['legenda']}\n\n"
             "Ainda não posto sozinho: falta conectar o Instagram. Por enquanto é só baixar os arquivos acima e postar.\n\n"
             + consumo())
    if aprovado:
        historico.append({"data": agora.strftime("%Y-%m-%d"), "tema": post.get("tema", "")})
        HISTORICO.write_text(json.dumps(historico, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:  # avisa no Telegram em vez de falhar calado
        mensagem(f"⚠️ Legião falhou: {str(erro)[:1500]}" + (f"\n\n{consumo()}" if gastos else ""))
        raise
