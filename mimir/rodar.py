"""Mímir: resumo de IA feito pelo Claude Code (assinatura) e enviado no Telegram."""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
BRT = timezone(timedelta(hours=-3))
ULTIMO = os.path.join(AQUI, "ultimo.md")


def telegram(texto):
    token = os.environ.get("MIMIR_TELEGRAM_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat = os.environ.get("MIMIR_CHAT_ID") or os.environ["ADMIN_ID"]
    partes, atual = [], ""
    for linha in texto.splitlines(keepends=True):
        if len(atual) + len(linha) > 3900:
            partes.append(atual)
            atual = ""
        atual += linha
    partes.append(atual)
    for parte in partes:
        dados = json.dumps({"chat_id": chat, "text": parte, "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", dados,
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()


def montar_prompt(agora):
    desde = agora - timedelta(hours=16 if agora.hour < 12 else 8)
    anterior = open(ULTIMO, encoding="utf-8").read() if os.path.exists(ULTIMO) else "(nenhum)"
    modelo = open(os.path.join(AQUI, "prompt.md"), encoding="utf-8").read()
    return (modelo.replace("{AGORA}", agora.strftime("%d/%m/%Y %H:%M"))
            .replace("{DESDE}", desde.strftime("%d/%m %H:%M"))
            .replace("{ANTERIOR}", anterior)
            .replace("{DATA}", agora.strftime("%d/%m"))
            .replace("{HORA}", str(agora.hour)))


def n(numero):
    return f"{numero:,}".replace(",", ".")


def consumo(saida):
    u = saida.get("usage") or {}
    entrada = u.get("input_tokens", 0)
    cache_criado = u.get("cache_creation_input_tokens", 0)
    cache_lido = u.get("cache_read_input_tokens", 0)
    gerado = u.get("output_tokens", 0)
    total = entrada + cache_criado + cache_lido + gerado
    linhas = [
        "📊 Consumo desta rodada",
        f"Tokens: {n(total)} no total ({n(entrada + cache_criado)} lidos novos, {n(cache_lido)} reaproveitados do cache, {n(gerado)} escritos)",
        f"Passos do agente: {saida.get('num_turns', '?')} · tempo: {round((saida.get('duration_ms') or 0) / 1000)}s",
    ]
    custo = saida.get("total_cost_usd")
    if custo is not None:
        linhas.append(f"Se fosse API paga: ~US$ {custo:.2f}. Na sua assinatura não cobra, só gasta do limite do plano.")
    return "\n".join(linhas)


def main():
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("Falta o secret CLAUDE_CODE_OAUTH_TOKEN; Mímir não roda.")
        return
    agora = datetime.now(BRT)
    cmd = ["claude", "-p", montar_prompt(agora), "--output-format", "json",
           "--model", os.environ.get("MIMIR_MODELO", "sonnet"),
           "--allowedTools", "WebSearch,WebFetch", "--max-turns", "40"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20 * 60)
    try:
        saida = json.loads(proc.stdout)
    except ValueError:
        telegram(f"⚠️ Mímir falhou ao rodar o Claude.\n{(proc.stderr or proc.stdout)[-1500:]}")
        sys.exit(1)
    texto = (saida.get("result") or "").strip()
    if saida.get("is_error") or not texto:
        telegram(f"⚠️ Mímir não conseguiu fazer o resumo: {texto[:1500] or saida.get('subtype')}\n\n{consumo(saida)}")
        sys.exit(1)
    telegram(f"{texto}\n\n{consumo(saida)}")
    with open(ULTIMO, "w", encoding="utf-8") as f:
        f.write(texto)


if __name__ == "__main__":
    main()
