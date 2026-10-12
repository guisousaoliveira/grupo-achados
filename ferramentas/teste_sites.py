"""Testa se sites de ofertas abrem a partir do GitHub (servidor). Uso: python3 ferramentas/teste_sites.py URL..."""
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

for url in sys.argv[1:]:
    print("=" * 70)
    print(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9",
                                               "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            codigo, final, corpo = r.status, r.geturl(), r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        codigo, final, corpo = e.code, e.geturl() or url, e.read(200_000).decode("utf-8", "replace")
    except Exception as e:
        print("  FALHOU:", e)
        continue
    titulo = re.search(r"<title[^>]*>(.*?)</title>", corpo, re.S | re.I)
    print(f"  status {codigo} | final {final} | {len(corpo)} caracteres")
    print("  título:", (titulo.group(1).strip()[:100] if titulo else "-"))
    for nome, padrao in [("cloudflare/desafio", r"cf-chl|challenge-platform|Just a moment|captcha"),
                         ("__NEXT_DATA__", r"__NEXT_DATA__"), ("rss/xml", r"<rss|<feed|<item>"),
                         ("links de oferta", r'href="[^"]*/(?:d|oferta|promocao|promocoes|p)/[^"]+"'),
                         ("amazon/ML/shopee no html", r"amazon\.com\.br|mercadolivre|shopee"),
                         ("temperatura/graus", r"temperature|temperatura|°")]:
        print(f"  {nome}: {len(re.findall(padrao, corpo, re.I))}")
    exemplos = list(dict.fromkeys(re.findall(r'href="(/[^"]*(?:/d/|/oferta|/promocao)[^"]*)"', corpo)))[:5]
    if exemplos:
        print("  exemplos:", exemplos)
    print("  início:", re.sub(r"\s+", " ", corpo[:300]))
