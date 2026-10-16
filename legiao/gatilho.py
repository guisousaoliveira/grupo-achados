"""Chamado a cada rodada do bot de achados: dispara a Legião Alpha (carrossel e Reel) nos horários do
config.json (Brasília), uma vez por horário."""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

CFG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")))
HORARIOS = {"carrossel": tuple(CFG["horarios"]), "reel": tuple(CFG.get("reel", {}).get("horarios", ()))}
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
    for tipo, horas in HORARIOS.items():
        if agora.hour not in horas:  # janela de 1 hora
            continue
        inicio = agora.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        runs = api(f"/actions/workflows/legiao.yml/runs?created=>={inicio.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        if any(r.get("display_title") == f"legiao {tipo}" for r in runs.get("workflow_runs", [])):
            continue
        api("/actions/workflows/legiao.yml/dispatches", {"ref": "main", "inputs": {"tipo": tipo}})
        print("Legião disparada:", tipo)


if __name__ == "__main__":
    main()
