# Passagem: vídeo "A Ilha dos Mortos" (canal Aster) para o Claude Code do PC

A partir de agora este vídeo é feito inteiro no PC do Guilherme, sem a nuvem no meio. Ele não quer copiar e colar nada entre sessões: faça tudo aqui e só peça a ele o que só ele pode fazer (gerar o áudio no Gemini e aprovar o resultado). Responda sempre em PT-BR, curto.

## Onde está tudo
- Estúdio: `F:\projects\dinheiro-online\web-documentario\ilha-dos-mortos\estudio` (alinhar.py, motor.py, montar.py, LEIA-ME.md, venv .venv).
- Blocos prontos: `projetos\ilha-b1` (Gancho) e `projetos\ilha-b2` (O pintor). Use-os como modelo de planos.json.
- Narrações: `ilha-dos-mortos\narracao\` (o Guilherme salva ali um wav por bloco).
- Vídeo juntado: `ilha-dos-mortos\ilha-dos-mortos_blocos_1-2.mp4` (junte os próximos do mesmo jeito, sem recomprimir).
- Pendente: o Bloco 1 dentro do juntado é um render antigo. Re-renderize `projetos\ilha-b1` com o planos.json atual (abertura que cresce na tela) e refaça o juntado.

## Fluxo de cada bloco (3 a 6)
1. Mande ao Guilherme o texto do bloco (abaixo) já com as tags do Gemini. Ele gera o áudio e salva em `narracao`.
2. Crie `projetos\ilha-bN` copiando `media` e `musica` de um bloco anterior. Coloque o áudio como `voz\bloco1.wav` e um `roteiro.md` com `# A Ilha dos Mortos`, uma linha `## Bloco N — Título` e o texto do bloco SEM as tags.
3. Alinhe: `python alinhar.py projetos\ilha-bN`. No PC não há whisper, então ele usa proporção de letras, que pode errar. Instalar o whisper melhora muito: `pip install sherpa-onnx` e baixar o modelo whisper small int8 do sherpa-onnx (releases do GitHub k2-fsa/sherpa-onnx, arquivo sherpa-onnx-whisper-small.tar.bz2). Depois aponte a variável WHISPER_DIR para a pasta do modelo, terminando em barra.
4. Busque as imagens no Wikimedia Commons em 3840 px (`https://commons.wikimedia.org/wiki/Special:Redirect/file/NOME?width=3840`, com User-Agent). Registre título, página e licença em `media\creditos.json`. Obras de antes de 1900 são domínio público. Fotos modernas: confira a licença.
5. Escreva `planos.json` no estilo dos blocos 1 e 2. Use `"trilha": [{"arquivo": "musica/isle1929.mp3", "inicio": 0, "corte": X}]`, continuando de onde o bloco anterior parou (bloco 2 começou em 86,5 s).
6. `python montar.py projetos\ilha-bN`, leia `saida\conferencia.txt`, corrija os avisos (zoom borrado: imagem maior ou zoom mais aberto; etiqueta curta: tire ou mova) e renderize de novo.
7. Junte com os blocos anteriores e avise o Guilherme com o caminho do mp4.

## Regras de estilo (aprovadas por ele)
- Abertura cinematográfica só no Bloco 1. Estilo Velum: obras em tela cheia com zoom lento, 3 a 5 s por imagem.
- Texto na tela só nome + data (etiqueta com placa escura), anos gigantes no modo `year`, retratos no modo `framed` com `nome`.
- Clique de projetor automático quando muda de imagem. Não há clique entre vistas do mesmo quadro (`sem_clique` desliga).
- Gemini TTS: voz Enzo, texto CORRIDO num parágrafo só, poucas tags em inglês entre `< >` (`<short pause>`, `<long pause>`, no máximo uns 4 por bloco). NUNCA `<whispers>` (fica parecendo ASMR). Estilo no campo de expressão: "Narração de documentário de mistério. Calma, grave e firme, ritmo lento, sem pressa. Voz projetada e clara, nunca sussurrada."
- Música: Rachmaninoff 1929 (risco de Content ID; para o vídeo final procurar uma gravação livre).
- O Guilherme lê no celular: ao entregar, diga em uma ou duas frases o que mudou e onde está o arquivo.

## Roteiro completo (aprovado)
# A Ilha dos Mortos — roteiro v1 (YouTube, ~10 min)

> Narração em 6 blocos. Gere um áudio por bloco no ElevenLabs (bloco1.mp3 ... bloco6.mp3), texto corrido, sem marcações. Eu estico as pausas depois.
> Título sugerido: "O quadro que obcecou Hitler, Freud e Lênin" · alternativa: "Ninguém sabe o que este quadro mostra"
> Miniatura: o barco com a figura branca, bem fechado, com a água escura. Teste A sem texto; teste B com "NINGUÉM SABE" em vermelho.

## Bloco 1 — Gancho

Em 1933, dois acontecimentos sem nenhuma ligação aparente aconteceram no mesmo ano. Em Hollywood, os cenógrafos de King Kong desenharam a Ilha da Caveira, o lugar mais sombrio que o cinema já tinha mostrado. E na Alemanha, um homem que tinha acabado de chegar ao poder pagou uma fortuna por uma pintura. O nome dele era Adolf Hitler. As duas coisas nasceram da mesma imagem. Essa mesma imagem estava pendurada acima da cama de Lênin. Estava no consultório de Sigmund Freud. Fez Rachmaninoff compor uma de suas peças mais sombrias. Inspirou Salvador Dalí, e anos depois inspirou o artista que criou o monstro de Alien. O escritor Vladimir Nabokov dizia que ela estava em todas as casas de Berlim. Mesmo assim, o homem que a pintou nunca explicou o que ela mostra. Nunca escreveu uma linha sobre o significado. E nem o nome pelo qual o mundo inteiro a conhece foi ele quem escolheu. Esta é a história da Ilha dos Mortos.

## Bloco 2 — O pintor

Arnold Böcklin nasceu em Basileia, na Suíça, em 1827. Enquanto os pintores da época retratavam a burguesia e paisagens tranquilas, ele pintava outra coisa. Tritões saindo do mar. Ninfas em ruínas. Deuses antigos que ninguém mais adorava. Böcklin passou boa parte da vida na Itália, cercado de templos caídos e cemitérios antigos, e isso aparece em quase tudo o que ele fez. Mas existe um motivo mais pessoal para a morte estar tão presente nos quadros dele. Böcklin teve catorze filhos. Oito morreram. Em 1872, ele pintou um autorretrato que diz muito sobre isso. Ele aparece com o pincel na mão, olhando para frente, e logo atrás dele, quase encostado no ouvido, um esqueleto toca violino. Böcklin não pinta a morte como um susto. Ela está ali do lado, tocando, como uma companhia de trabalho. Em Florença, o ateliê dele ficava a poucos passos de um pequeno cemitério protestante, cheio de ciprestes, cercado de muros. Era o Cemitério Inglês. E foi ali que ele enterrou uma de suas filhas, Maria, ainda bebê.

## Bloco 3 — A origem

Em abril de 1880, uma mulher bateu à porta desse ateliê. O nome dela era Marie Berna. Ela era viúva. O marido tinha morrido de difteria anos antes, e ela nunca tinha superado. No cavalete, Böcklin tinha um quadro pela metade. Uma ilha de pedra no meio de um mar parado, com ciprestes escuros no centro. Marie Berna ficou parada diante daquela imagem. E pediu ao pintor uma versão só para ela. Mas com uma diferença. Ela pediu que ele colocasse um caixão no barco. E, ao lado do caixão, uma figura de branco. Böcklin aceitou. Pintou para ela uma versão menor, sobre madeira, e acrescentou os dois elementos. Depois, voltou ao quadro original e acrescentou também. Ele mesmo descreveu a pintura numa carta. Disse que era um quadro de sonho, que precisava produzir um silêncio tão grande que uma simples batida na porta fosse capaz de assustar. Ele não chamou o quadro de Ilha dos Mortos. Chamou de Ilha dos Túmulos. E é aqui que a história começa a ficar estranha.

## Bloco 4 — O que tem no quadro

Olhe com atenção. A ilha sobe da água como uma fortaleza. As paredes de pedra são quase verticais, e nelas existem portas e janelas escavadas na rocha. São túmulos. No centro, um bosque de ciprestes, a árvore que desde a Antiguidade se planta em cemitérios, tão alto e tão fechado que nenhuma luz entra ali dentro. A água em volta não tem uma onda sequer. Não existe vento. Não existe som. Um pequeno barco chega até a escada da ilha. Atrás, um remador. Na frente, de pé, uma figura toda coberta de branco, de costas para nós. Ninguém sabe quem ela é. Durante mais de um século, a leitura mais comum foi a da mitologia grega. O remador seria Caronte, o barqueiro que levava as almas para o mundo dos mortos, e a figura de branco seria uma alma recém chegada. Mas lembre de onde essa figura veio. Ela foi um pedido de uma viúva. Então ela também pode ser a própria Marie Berna, levando o marido até o lugar onde ele finalmente vai descansar. E tem um detalhe que quase ninguém percebe. A partir da terceira versão, em um dos túmulos escavados na rocha, do lado direito, aparecem duas letras. A. B. Arnold Böcklin. O pintor reservou um túmulo para si mesmo dentro da ilha.

## Bloco 5 — O destino das cinco ilhas

Böcklin pintou a ilha cinco vezes entre 1880 e 1886. Como se não conseguisse sair dela. A terceira versão foi feita em 1883 para o marchand dele, Fritz Gurlitt. E foi Gurlitt quem deu ao quadro o nome que ficou para sempre. Die Toteninsel. A Ilha dos Mortos. Um nome de vendedor, e funcionou. Reproduções se espalharam pela Europa, em gravuras baratas, em preto e branco. Em 1907, em Paris, o compositor Sergei Rachmaninoff viu uma dessas cópias cinzentas. Dois anos depois, ele lançou o poema sinfônico A Ilha dos Mortos, com um compasso de cinco tempos que lembra o movimento de um remo na água. Anos mais tarde, quando finalmente viu o original colorido, Rachmaninoff disse que, se tivesse visto aquela versão primeiro, provavelmente nunca teria escrito a música. Em 1933, a terceira versão foi colocada à venda. E foi comprada por Adolf Hitler. Ele a pendurou na sua casa nos Alpes, e depois na nova Chancelaria do Reich, em Berlim. Existe uma fotografia de 1940 em que Hitler conversa com o ministro soviético Molotov, e atrás deles, na parede, está a ilha. A quarta versão teve outro destino. Foi parar num banco em Berlim e, durante a guerra, um bombardeio a destruiu. Dela, sobrou apenas uma foto em preto e branco. Como a cópia que Rachmaninoff viu.

## Bloco 6 — A virada e o fim

E a história poderia terminar aqui, com cinco quadros. Mas em 2022, uma sexta Ilha dos Mortos apareceu. Estava numa coleção particular e foi parar no museu Hermitage, em São Petersburgo. Ela tem duas assinaturas. Uma diz que a ideia é de Arnold Böcklin. A outra diz que quem terminou de pintar foi Carlo Böcklin, o filho dele, em 1901. O mesmo ano em que o pai morreu. Böcklin começou a última ilha e não chegou a terminar. Quem levou o barco até o fim foi o filho. Böcklin está enterrado em Florença, a poucos quilômetros do cemitério que o inspirou. A ilha nunca existiu em mapa nenhum. Os ciprestes, as portas na pedra e a água parada só existem naquele quadro. Talvez seja por isso que gente tão diferente olhou para ela e viu a mesma coisa. Um ditador, um revolucionário, um psicanalista, um compositor. Cada um projetou ali o seu próprio fim. Böcklin pediu um silêncio tão grande que uma batida na porta assustasse. Mais de cento e quarenta anos depois, o quadro continua fazendo esse silêncio. A pergunta é o que cada um de nós escuta dentro dele.
