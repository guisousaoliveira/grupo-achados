"""Diagnóstico: mostra onde aparecem os códigos MLB numa vitrine de afiliado do ML (roda no GitHub)."""
import re, sys, html
import bot

for link in sys.argv[1:]:
    final, pagina = bot.endereco_final(link)
    print("LINK", link, "->", final[:200], "tamanho", len(pagina))
    for m in re.finditer(r'<meta[^>]+property="og:[^"]+"[^>]*>', pagina):
        print("  META", m.group(0)[:250])
    vistos = {}
    for m in re.finditer(r"MLB-?\d{6,}", pagina):
        vistos.setdefault(m.group(0).replace("-", ""), []).append(m.start())
    for k, pos in sorted(vistos.items(), key=lambda kv: kv[1][0])[:25]:
        print("  MLB", k, "vezes", len(pos), "primeira pos", pos[0], "|", html.unescape(pagina[max(0, pos[0]-150):pos[0]+60]).replace("\n", " ")[:260])
    t = re.search(r'property="og:title" content="([^"]+)"', pagina)
    if t:
        i = pagina.find(html.unescape(t.group(1))[:40])
        print("  TITULO pos", i, "|", pagina[i:i+2500].replace("\n", " ")[:2500])
