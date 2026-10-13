"""Diagnóstico: mostra o card do produto principal numa vitrine de afiliado do ML (roda no GitHub)."""
import re, sys
import bot

for link in sys.argv[1:]:
    final, pagina = bot.endereco_final(link)
    img = re.search(r'property="og:image" content="[^"]*?/D_[A-Z_]*?(\d+-MLB\d+)_', pagina)
    print("LINK", link, "| foto principal", img and img.group(1))
    if not img:
        continue
    for m in re.finditer(re.escape('"id":"' + img.group(1)), pagina):
        ini = pagina.rfind('"metadata":{"id":"', 0, m.start())
        print("  CARD", ini, m.start(), "|", pagina[ini:m.start() + 80].replace("\n", " ")[:1500])
