"""Chamado a cada rodada do bot de achados: dispara a Legião Alpha (carrossel e Reel) nos horários do
config.json (Brasília), uma vez por horário."""
import base64
import json
import os
import urllib.error
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


def ja_tem_post(pasta):
    """Se já gerou (na mão) o post de hoje e você não recusou, não gera outro por cima."""
    try:
        r = api(f"/contents/posts/{pasta}/status.json?ref=legiao-posts")
    except urllib.error.HTTPError:
        return False
    return json.loads(base64.b64decode(r["content"])).get("status") != "recusado"


def main():
    agora = datetime.now(BRT)
    for tipo, horas in HORARIOS.items():
        if agora.hour not in horas:  # janela de 1 hora
            continue
        if ja_tem_post(agora.strftime("%Y-%m-%d") + ("-reel" if tipo == "reel" else "")):
            continue
        inicio = agora.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        runs = api(f"/actions/workflows/legiao.yml/runs?created=>={inicio.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        if any(r.get("display_title") == f"legiao {tipo}" for r in runs.get("workflow_runs", [])):
            continue
        api("/actions/workflows/legiao.yml/dispatches", {"ref": "main", "inputs": {"tipo": tipo}})
        print("Legião disparada:", tipo)


if __name__ == "__main__":
    main()
