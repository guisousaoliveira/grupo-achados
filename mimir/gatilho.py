"""Chamado a cada rodada do bot de achados: dispara o Mímir às 8h e às 16h (Brasília), uma vez por horário."""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

HORARIOS = (8, 16)
BRT = timezone(timedelta(hours=-3))


def api(caminho, dados=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{caminho}",
        json.dumps(dados).encode() if dados is not None else None,
        {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
        method="POST" if dados is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        corpo = r.read()
        return json.loads(corpo) if corpo else {}


def main():
    agora = datetime.now(BRT)
    if agora.hour not in HORARIOS:  # janela de 1 hora: 8:00-8:59 e 16:00-16:59
        return
    inicio = agora.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    runs = api(f"/actions/workflows/mimir.yml/runs?created=>={inicio.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if runs.get("total_count", 0) >= 1:
        return
    api("/actions/workflows/mimir.yml/dispatches", {"ref": "main"})
    print("Mímir disparado")


if __name__ == "__main__":
    main()
