"""Servidor do bot para o Render: responde suas mensagens e botões na hora.

O Telegram avisa este servidor (webhook) a cada mensagem que você manda para o bot.
A fila fica no repositório do GitHub (dados/estado.json), a mesma que o GitHub Actions usa
para postar nos horários. Ou seja: o Render cuida da conversa com você, o GitHub cuida de postar.

No Render:
  Start command:  python3 servidor.py
  Variáveis:      TELEGRAM_TOKEN, TELEGRAM_CHANNEL, ADMIN_ID, GITHUB_TOKEN, GITHUB_REPO
                  PAINEL_SENHA (senha do painel em /painel)
                  (e SHOPEE_APP_ID, SHOPEE_SECRET, GEMINI_API_KEY quando tiver)
"""

import hashlib
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import bot
import painel

bot.carregar_env()
bot.CONFIG = bot.carregar_json(bot.ARQ_CONFIG, {})

# /tick: um site de agendamento (cron-job.org) abre este endereço de 5 em 5 minutos.
# Isso mantém o Render acordado e pede uma rodada ao GitHub (radar, postagens, releituras).
# No máximo uma rodada a cada 4 minutos, mesmo que alguém abra o endereço várias vezes.
INTERVALO_TICK = 4 * 60
_ultimo_tick = [0.0]

SEGREDO = hashlib.sha256(("webhook" + bot.segredo("TELEGRAM_TOKEN")).encode()).hexdigest()[:32]


class Atendente(BaseHTTPRequestHandler):
    def _responder(self, codigo, texto="ok"):
        corpo = texto.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)
        self.wfile.flush()

    def do_GET(self):  # usado pelo Render (e por um "pinger", se você quiser) para ver se está vivo
        if self.path.startswith("/painel"):
            try:
                painel.tratar_get(self)
            except Exception as e:
                bot.log(f"Painel: erro em {self.path[:60]}: {e}")
            return
        if self.path.split("?")[0].rstrip("/") == "/tick":
            if time.time() - _ultimo_tick[0] < INTERVALO_TICK:
                self._responder(200, "cedo demais, rodada recente")
                return
            _ultimo_tick[0] = time.time()
            ok = bot.chamar_github_agora("Tick: pedi uma rodada ao GitHub.")
            self._responder(200, "rodada pedida" if ok else "nao consegui pedir a rodada (veja o log)")
            return
        self._responder(200, "bot de achados ligado")

    def do_HEAD(self):
        self._responder(200, "")

    def do_POST(self):
        if self.path.startswith("/painel"):
            try:
                painel.tratar_post(self)
            except Exception as e:
                bot.log(f"Painel: erro em {self.path[:60]}: {e}")
            return
        if self.path != "/telegram" or self.headers.get("X-Telegram-Bot-Api-Secret-Token") != SEGREDO:
            self._responder(403, "nao")
            return
        tamanho = int(self.headers.get("Content-Length") or 0)
        try:
            update = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            self._responder(400, "json invalido")
            return
        # Responde logo para o Telegram não reenviar, e depois trabalha (pode levar alguns segundos
        # para abrir a página da loja). Este servidor atende um pedido por vez, então a fila não se embaralha.
        self._responder(200)
        try:
            bot.com_estado(lambda estado: bot.processar_update(estado, update))
        except Exception as e:
            bot.log(f"Erro tratando mensagem: {e}")

    def log_message(self, formato, *args):  # silencia o log padrão de cada acesso
        pass


def ligar_webhook():
    base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("URL_PUBLICA")
    if not base:
        bot.log("Sem RENDER_EXTERNAL_URL: não liguei o webhook (normal fora do Render).")
        return
    bot.telegram("setWebhook", url=base.rstrip("/") + "/telegram", secret_token=SEGREDO,
                 allowed_updates=["message", "callback_query"])
    bot.log(f"Webhook ligado em {base}/telegram. Agora o bot responde na hora.")


if __name__ == "__main__":
    faltam = [n for n in ("TELEGRAM_TOKEN", "ADMIN_ID", "GITHUB_TOKEN", "GITHUB_REPO") if not bot.segredo(n)]
    if faltam:
        bot.log(f"ATENÇÃO: faltam as variáveis {', '.join(faltam)}.")
    ligar_webhook()
    porta = int(os.environ.get("PORT", "10000"))
    bot.log(f"Servidor ouvindo na porta {porta}.")
    HTTPServer(("0.0.0.0", porta), Atendente).serve_forever()
