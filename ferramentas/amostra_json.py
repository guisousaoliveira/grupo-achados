"""Mostra a estrutura do __NEXT_DATA__ de uma página (para mapear os dados das ofertas). Uso: python3 ferramentas/amostra_json.py URL"""
import json
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
for url in sys.argv[1:]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    corpo = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', corpo, re.S)
    if not m:
        print(url, "sem __NEXT_DATA__")
        continue
    dados = json.loads(m.group(1))
    print("=" * 60, url)

    def andar(o, caminho, prof=0):
        if prof > 9:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                andar(v, f"{caminho}.{k}", prof + 1)
        elif isinstance(o, list) and o:
            if isinstance(o[0], dict) and len(o) >= 3:
                print(f"LISTA {caminho} ({len(o)} itens) chaves: {sorted(o[0].keys())[:40]}")
            andar(o[0], caminho + "[0]", prof + 1)

    andar(dados, "")
    # primeira lista grande que parece de ofertas: mostra 2 itens inteiros
    achou = []

    def procurar(o):
        if isinstance(o, list) and len(o) >= 5 and all(isinstance(x, dict) for x in o[:5]):
            texto = json.dumps(o[0])
            if re.search(r"price|preco|offer", texto, re.I):
                achou.append(o)
        if isinstance(o, dict):
            for v in o.values():
                procurar(v)
        elif isinstance(o, list):
            for v in o[:3]:
                procurar(v)
    procurar(dados)
    for lista in achou[:2]:
        print("-" * 60)
        for item in lista[:2]:
            print(json.dumps(item, ensure_ascii=False)[:2500])
