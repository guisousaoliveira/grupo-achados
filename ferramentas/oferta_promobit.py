"""Mapeia a página de uma oferta do Promobit (link da loja, foto). Uso: python3 ferramentas/oferta_promobit.py URL_DA_LISTA"""
import json
import re
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def abrir(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def proximo(corpo):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', corpo, re.S)
    return json.loads(m.group(1)) if m else {}


_, lista = abrir(sys.argv[1])
ofertas = proximo(lista)["props"]["pageProps"]["serverOffers"]["offers"]
alvo = next((o for o in ofertas if o["storeDomain"] in ("mercadolivre.com.br", "amazon.com.br", "shopee.com.br")), ofertas[0])
print("oferta:", alvo["offerTitle"], alvo["storeDomain"], alvo["offerSlug"], "foto:", alvo["offerPhoto"])
for url in (f"https://www.promobit.com.br/oferta/{alvo['offerSlug']}/",):
    final, corpo = abrir(url)
    print("página:", final, len(corpo))
    d = proximo(corpo)
    pp = d.get("props", {}).get("pageProps", {})
    print("pageProps:", list(pp.keys()))
    texto = json.dumps(pp, ensure_ascii=False)
    for padrao in [r'"[^"]*(?:[Uu]rl|[Ll]ink|[Rr]edirect)[^"]*"\s*:\s*"[^"]{5,300}"', r'https?://[^"\s]*(?:amazon|mercadoli|meli\.la|shopee|redirect|/r/)[^"\s]*']:
        for x in list(dict.fromkeys(re.findall(padrao, texto)))[:25]:
            print("  ", x[:300])
    for x in list(dict.fromkeys(re.findall(r'"[^"]*[Pp]hoto[^"]*"\s*:\s*"[^"]+"', texto)))[:8]:
        print("  foto:", x[:200])
    for x in list(dict.fromkeys(re.findall(r'href="([^"]*(?:redirect|Redirect|/out/|/r/|ir-a-loja|vai)[^"]*)"', corpo)))[:10]:
        print("  href:", x)
    imgs = list(dict.fromkeys(re.findall(r'(https?://[^"\s]*' + re.escape(alvo["offerPhoto"].strip("/")) + ')', corpo)))[:3]
    print("  img completas:", imgs)
