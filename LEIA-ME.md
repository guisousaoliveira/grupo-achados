# Bot do grupo de achados

Bot do canal de ofertas no Telegram. Roda de graça no GitHub Actions (posta nos horários) e, se quiser, no Render (responde na hora).

## O que ele faz

1. **Seus links (Amazon, Mercado Livre e Shopee):** você manda no privado do bot só o link de afiliado. Ele abre a página, lê nome, preço e foto e te devolve a oferta pronta com os botões **✅ Aprovar** e **❌ Descartar**. Para corrigir, responda a mensagem de revisão (arraste para o lado) com `nome: ...`, `preço: 199 129` ou `frase: ...`. Só entra na fila o que você aprovar.
2. **Shopee automático:** uma vez por dia busca as palavras do `config.json` na Affiliate Open API, filtra (20% off, nota 4,7, 100 vendas, sem repetir em 7 dias) e coloca no máximo 2 na fila.
3. **Postagem espalhada:** nos horários do `config.json` (9h, 12h, 15h, 18h, 20h e 22h) posta a próxima oferta da fila no canal.

No GitHub ele roda a cada 30 minutos, então a revisão chega em até meia hora. Para ver na hora, clique em "Run workflow" no GitHub.

**Depois que estiver no GitHub, não rode mais `RODAR_AGORA.bat` nem `POSTAR_AGORA_TESTE.bat` no PC.** Os dois leem as mesmas mensagens do Telegram, e o que o PC ler o GitHub não vê. `TESTAR_LINK.bat` e `TESTAR_SIMULACAO.bat` podem ser usados à vontade.

Algumas lojas bloqueiam robô (a Amazon é a mais chata). Quando isso acontece, a revisão avisa o que faltou e você completa respondendo.

## Formato da mensagem que você manda para o bot

```
https://amzn.to/seu-link
Creatina Growth 250g
De 119,90 por 69,90
A mais vendida da Amazon
```

Só o link também funciona quando a página da loja deixa o bot ler nome e preço (costuma funcionar no Mercado Livre, quase nunca na Amazon). Pode mandar uma foto com esse texto na legenda, e a foto vai junto no post.

Comandos no privado do bot: `/fila`, `/remover N`, `/pendentes`, `/aprovartudo`, `/ajuda`.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `bot.py` | O bot inteiro. Só usa Python puro, nada para instalar |
| `config.json` | Palavras do nicho, filtros da Shopee, horários e rodapé do post |
| `PREPARAR.bat` | Cria o `.env` a partir do `modelo_env.txt` e coloca o workflow em `.github/workflows/` |
| `modelo_env.txt` | Modelo do `.env`, onde ficam os segredos no seu PC |
| `dados/estado.json` | Fila e histórico (o bot cria e atualiza sozinho) |
| `dados/shopee_exemplo.json` | Ofertas falsas para testar enquanto a Shopee não libera a API |
| `TESTAR_SIMULACAO.bat` | Mostra o que o bot faria, sem precisar de token |
| `DESCOBRIR_IDS.bat` | Mostra o seu ID e o ID do canal |
| `TESTAR_CANAL.bat` | Manda uma mensagem de teste no canal |
| `TESTAR_LINK.bat` | Cola um link e mostra o que o bot consegue ler da página |
| `RODAR_AGORA.bat` | Roda o bot de verdade uma vez no seu PC |
| `workflow_bot.yml` | Faz o GitHub rodar o bot a cada hora (o `PREPARAR.bat` copia para `.github/workflows/bot.yml`) |

## Segredos

`TELEGRAM_TOKEN`, `TELEGRAM_CHANNEL`, `ADMIN_ID`, `SHOPEE_APP_ID` e `SHOPEE_SECRET`. No PC ficam no `.env` (que não sobe para o GitHub). No GitHub ficam em Settings > Secrets and variables > Actions. Nunca no chat, nunca no código.

## Shopee

Enquanto `SHOPEE_APP_ID` estiver vazio, o bot usa `dados/shopee_exemplo.json`. A consulta da API real (`productOfferV2`) foi escrita pela documentação pública da Shopee e ainda não foi testada com uma chave de verdade: no primeiro teste com a chave, confira o log.

## Colocar no GitHub (rodar sozinho)

1. Crie uma conta em github.com e clique em **New repository**. Nome `grupo-achados`, marque **Private** e crie.
2. Na página do repositório, clique em **uploading an existing file** e arraste **o conteúdo da pasta `para-github`** (não a pasta de fora: lá tem o `.env`). Clique em **Commit changes**.
3. Vá em **Settings > Secrets and variables > Actions > New repository secret** e crie um por um, copiando os valores do seu `.env`: `TELEGRAM_TOKEN`, `TELEGRAM_CHANNEL`, `ADMIN_ID`. (Os da Shopee e da Gemini só quando tiver.)
4. Clique em **Add file > Create new file**, digite o nome `.github/workflows/bot.yml`, cole o conteúdo do `workflow_bot.yml` e clique em **Commit changes**.
5. Em **Actions**, abra "bot-achados" e clique em **Run workflow** para o primeiro teste. Se ficar verde, está rodando sozinho a cada 30 minutos.

Depois disso, pare de usar `RODAR_AGORA.bat` e `POSTAR_AGORA_TESTE.bat` no PC.

## Resposta na hora (servidor no Render)

Sem o Render, o bot só lê suas mensagens e botões a cada 30 minutos. Com o `servidor.py` rodando no Render (grátis), o Telegram avisa o servidor na hora e a revisão chega em segundos. O GitHub Actions continua postando nos horários. Os dois usam a mesma fila, guardada em `dados/estado.json` dentro do repositório.

1. **Token do GitHub:** em github.com > foto > **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**. Nome `bot-render`, validade 1 ano, **Only select repositories** > `grupo-achados`, em **Repository permissions** coloque **Contents: Read and write** e **Actions: Read and write** (o Actions deixa o Render chamar o GitHub na hora quando a Amazon bloqueia). Gere e copie o token (começa com `github_pat_`). Ele aparece uma vez só.
2. **Render:** crie a conta em render.com entrando com o GitHub. **New > Web Service**, escolha o repositório `grupo-achados`.
   - Language: **Python 3**
   - Build Command: `echo ok`
   - Start Command: `python3 servidor.py`
   - Instance Type: **Free**
3. Em **Environment Variables**, adicione `TELEGRAM_TOKEN`, `TELEGRAM_CHANNEL`, `ADMIN_ID` (os mesmos do `.env`), `GITHUB_TOKEN` (o `github_pat_...`) e `GITHUB_REPO` = `seu-usuario/grupo-achados`.
4. Clique em **Deploy**. No log deve aparecer "Webhook ligado". Pronto.

O plano grátis do Render "dorme" depois de 15 minutos sem uso. A primeira mensagem depois disso demora cerca de 1 minuto, e as seguintes saem na hora. Para ele não dormir, cadastre o endereço do serviço num "pinger" grátis (ex.: UptimeRobot, a cada 5 minutos).

Para desligar o Render e voltar ao modo de 30 em 30 minutos, mande para o Telegram: `https://api.telegram.org/bot<TOKEN>/deleteWebhook` (abra no navegador com o seu token).
