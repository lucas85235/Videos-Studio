# Guia de uso do Vulto

## 1. Instalar (uma vez)

Pré-requisitos já presentes no seu notebook: Node.js, Python 3.12 com `uv`, FFmpeg e Git.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

O script instala o backend (venv em `%USERPROFILE%\VideosStudioData\.venv-backend`, fora do OneDrive), as dependências do renderer e da interface, e compila a interface.

O instalador inclui o **Whisper local**, que ouve cada narração e refaz a que sair errada (ver §7, *Conferência da narração*). Na primeira narração ele baixa o modelo (~460 MB). Roda na CPU: uns 5 s por trecho curto.

Ele também baixa a **voz local da prévia** (Kokoro, ~350 MB, licença Apache-2.0). Se faltar, o app baixa na primeira prévia, ou rode `uv run --project backend videos-studio voz-local`.

## 2. Abrir o app

Dê um duplo clique em `scripts\iniciar.cmd`. O navegador abre em http://127.0.0.1:8765. Para encerrar, feche a janela do terminal.

Depois de atualizar o app (ex.: `git pull`), o `iniciar.cmd` instala sozinho as dependências novas do backend quando elas mudam (uma vez, leva alguns segundos). Se ele avisar que não conseguiu, rode o `scripts\setup.ps1` de novo.

Onde ficam os arquivos: `%USERPROFILE%\VideosStudioData` (banco, cache, vídeos finais em `outputs\<projeto>\<idioma>\`).

## 3. Primeiro vídeo (5 passos)

1. **Novo projeto**: escreva um tema específico, escolha o estilo do seu nicho (o sugerido é *Curiosidades e Explicativo (Short)*; veja a lista na seção 7) e os idiomas, e deixe marcado "Gerar o roteiro agora".
2. **Roteiro** (aba 1): leia, edite o que quiser, trave os segmentos que estão perfeitos e clique em **Aprovar e criar as cenas**. O app cria as cenas e para para você conferir na aba Cenas, antes de gastar a cota de imagens: a direção de arte do vídeo, quantas imagens são novas e quantas reaproveitam outra, os cartões de número e os avisos da revisão. Clique em **Aprovar cenas e gerar imagens** para gerar as imagens e a prévia. Para seguir direto, desmarque *Parar para eu conferir as cenas* no menu Automação (projetos criados antes dessa parada já seguem direto).
3. **Prévia e edição** (aba 3): assista no player. A prévia é narrada pela **voz local grátis**, só para você conferir o conteúdo. Para mudar algo, escreva no chat, por exemplo "deixa a cena 5 mais clara" ou "legenda maior". Veja o que o agente propõe e quanto custa, e clique em **Aplicar**. Só o que mudou é refeito.
4. **Renderizar vídeo** (botão no topo ou aba 4): grava a narração com a **voz do vídeo final** (a de API que você escolheu), gera o MP4 em blocos, a thumbnail e as legendas SRT.
5. **Publicar** (aba 4): envie ao YouTube pela API oficial, ou prepare o pacote manual para TikTok, Instagram e outras redes.

## 4. Modo prévia × conteúdo real

O roteiro, a tradução e o agente de edição usam **só o Claude da sua conta**. Sem o Claude Code logado, gerar o roteiro falha com o motivo (não há roteiro de exemplo no lugar).

**A voz tem duas etapas:**
- **Prévia: voz local grátis** (Kokoro). Roda no seu computador, sem cota e sem rede, cerca de 2,5× mais rápido que a fala. Fala português, inglês, espanhol, francês, italiano e hindi; nos outros idiomas a prévia já usa a voz final. A qualidade é menor que a das vozes de API: serve para conferir roteiro, ritmo e cenas antes de gastar créditos.
- **Vídeo final: a voz de API que você escolher** (Gemini, Chirp 3 HD ou ElevenLabs). Ela só é gravada quando você renderiza. Se ela estiver sem cota ou sem chave, a renderização para logo no começo e diz o motivo: o vídeo final nunca sai com voz de teste.

Para a prévia já sair com a voz final (gastando a cota dela), desmarque *Usar a voz local grátis na prévia* no cartão *Voz* da prévia ou no estilo.

Sem a chave de imagem, o app usa imagens procedurais abstratas, marcadas como "só prévia" (assim como a voz do Windows, último recurso da prévia se a voz local falhar). O app **bloqueia** a publicação pública de vídeos feitos com elas (você ainda pode enviar como privado para testar).

Para conteúdo real **a custo zero**:

| O quê | Onde conseguir | Onde colocar |
|---|---|---|
| **Claude pela sua conta** (roteiro, tradução, agente) | Plano Pro/Max do Claude. Instale o Claude Code e rode `claude auth login` com a sua conta (não use chave de API: seria cobrado à parte). | Detectado sozinho quando o comando `claude` existir e estiver logado. Conta nos limites de uso do seu plano; ao bater o limite, espere ele voltar e gere de novo. |
| **Gemini** (voz) | Google AI Studio → "Get API key". Use um projeto **sem** cobrança ativada para ficar na camada grátis. | Configurações › Voz › Gemini |
| **Codex** (imagens pela cota do ChatGPT Plus) | Instale o Codex CLI (`npm install -g @openai/codex`) e rode `codex login` com a sua conta do ChatGPT. | Detectado sozinho quando o comando `codex` existir |
| **Pexels** (fotos de banco, opcional) | pexels.com/api | Configurações › Imagens › Pexels |

Voz e imagens seguem a cadeia de fallback de cada estilo: se um provedor falhar ou esgotar a cota, o próximo assume. As APIs pagas (Claude API, Gemini para texto, FLUX, fal.ai e OpenAI Images) foram retiradas do app.

### Escolher a voz

- **Ouvir antes**: Configurações › Voz › *Ouvir vozes* (com o idioma). Clique em ▶: a amostra é gerada na primeira vez (um pedido da cota, exceto na voz local) e fica guardada.
- **No novo projeto**: o campo *Voz do vídeo final* lista as vozes de API e a local, com ▶ para ouvir; clique no nome para escolher. Sem escolha, vale a voz do estilo.
- **Num projeto existente**: aba *Prévia e edição* › cartão *Voz*, com duas abas:
  - *Prévia*: a voz local (Dora, Alex ou Santa em português). Sem escolha, o app usa uma do mesmo gênero da voz final. Depois, **Atualizar prévia**.
  - *Vídeo final*: a voz que grava o vídeo final. A aba *Vídeo final* do projeto também mostra essa voz, quantos trechos ainda vão ser gravados e o botão *Trocar voz*.
- As 30 vozes do Gemini falam todos os idiomas; a descrição (firme, macia, rouca…) segue a documentação do Google. Com a chave da ElevenLabs configurada, as vozes da sua conta aparecem na lista (cada amostra gasta créditos).

O Claude da conta usa o Opus mais recente com esforço médio. Para trocar, defina `VS_CLAUDE_MODEL` (ex.: `sonnet`) ou `VS_CLAUDE_EFFORT` (`low`, `high`…) antes de iniciar o app. É uso pessoal do seu plano: não distribua o app configurado com a sua conta para outras pessoas.

## 5. Conectar o YouTube (uma vez)

1. Em https://console.cloud.google.com crie um projeto (ex.: "Vulto").
2. Em "APIs e serviços › Biblioteca", ative a **YouTube Data API v3**.
3. Em "Tela de permissão OAuth", escolha **Externo**, preencha o básico e **publique o app ("In production")**. Em "Testing" o acesso expira a cada 7 dias. Para uso pessoal não é preciso verificação: na hora de conectar, clique em "Avançado › Acessar" no aviso do Google.
4. Em "Credenciais › Criar credenciais › ID do cliente OAuth", escolha **App para computador**.
5. Copie o client ID e o client secret para **Configurações › Publicação › YouTube**.
6. Em **Canais**, clique em **Vincular canal do YouTube › Entrar com o Google** e escolha a conta e o canal (veja abaixo). Ou, na aba 4 de um projeto, clique em **Conectar canal do YouTube** (conta única, como antes).
7. No YouTube Studio, **verifique o canal por telefone**. Sem isso, uploads acima de 15 min e thumbnails personalizadas falham.

Envio: escolha privado, não listado, público ou **agendado**. O agendamento fica no próprio YouTube, então o notebook pode estar desligado na hora. O rótulo de conteúdo sintético é marcado sozinho quando o estilo é fotorrealista ou usa vídeo IA.

### Vincular um canal que você já tem

Em **Canais › Vincular canal do YouTube** há dois caminhos:

- **Entrar com o Google** (precisa do cliente OAuth acima): o login abre numa nova aba. Escolha a conta e o canal (contas de marca aparecem na tela do Google). O app cria o canal, ou atualiza o que você escolheu, com o nome, o @, a descrição, a foto, o país e o idioma, as palavras-chave, os inscritos e os títulos dos últimos 15 vídeos. Cada canal vinculado guarda o próprio acesso: os envios ao YouTube de um projeto vão para o canal dele. Projeto sem canal vinculado usa a conta conectada na aba 4, como antes.
- **Colar o link do canal** (sem login): aceita `youtube.com/@seucanal`, `youtube.com/channel/UC…` ou só o `@seucanal`. O app lê a página pública do canal uma vez e o feed oficial (nome, foto e títulos recentes). Por esse caminho o app **não publica** no canal; para publicar, use depois **Vincular com login** no cartão.

No cartão do canal:
- **Preencher com IA**: o Claude lê a descrição e os títulos recentes e sugere nicho, persona, idiomas, estilo padrão e identidade visual. Gasta um pouco do limite do seu plano. Nada é salvo até você revisar e clicar em **Salvar canal**.
- **Atualizar dados**: lê de novo nome, foto, números e vídeos recentes (o nome do canal no app volta a ser o do YouTube).
- **Desvincular**: apaga só o acesso daquele canal; os dados trazidos ficam. **Remover link** tira também os dados do YouTube do canal.
- Selo **Acesso expirado**: o Google recusou o acesso (tela de consentimento em "Testing" expira em 7 dias). Clique em **Vincular de novo**.

### Agenda e horários de postagem

- Em **Canais**, defina os **horários de postagem** de cada canal (dias da semana, hora e fuso; ex.: seg/qua/sex 18:00, America/Sao_Paulo).
- No cartão do YouTube aparecem os **próximos horários livres** do canal do projeto: um clique preenche o agendamento. Horários a menos de 45 min de outro vídeo agendado do mesmo canal não são oferecidos.
- O pacote manual (TikTok, Instagram etc.) aceita um horário **planejado**. Ele fica na **Agenda** até você marcar como postado (com o link do post, se quiser).
- A página **Agenda** mostra o que está agendado, os pacotes por postar (com atalho para a pasta, a página de upload e a legenda) e o que saiu nas últimas duas semanas.
- **Fila do YouTube** (na Agenda): marque vários vídeos já renderizados e clique em *Agendar nos próximos horários livres*: cada um vai, na ordem, para o próximo horário livre do seu canal, e o envio roda em segundo plano.

## 6. Editar por instrução

Exemplos que funcionam bem:
- "deixa a cena 5 mais clara" ou "mais sombria" (só ajusta a cor, sem gerar imagem nova);
- "outra imagem na cena atual" (marque "Só a cena que está na tela agora");
- "na cena 3, mostra um farol na tempestade" (novo prompt de imagem);
- "legenda maior", "legenda em amarelo", "música mais baixa", "música mais animada", "sem música";
- "zoom de impacto na cena 2", "transição glitch na cena 7";
- "reescreve o segmento 2 com um exemplo do dia a dia" ou "com mais suspense".

Toda mudança vira uma versão (aba Versões). Voltar a uma versão não paga de novo, porque os arquivos ficam no cache.

### Mídia real (fotos, notícias e material oficial)

Ao criar as cenas, o Claude marca algumas batidas em que uma imagem real vale mais que a de IA: pessoa ou lugar real, produto, personagem oficial, notícia. Na aba Cenas, o cartão da cena mostra **Mídia real sugerida** e o que procurar; o painel da curadoria conta as sugestões (o **Ver** leva à primeira). Nada é anexado sozinho. Em qualquer outra cena, o botão **Mídia real** abre as mesmas opções.

- **Foto real**: o app busca fotos de licença livre no Wikimedia Commons e no Openverse (grátis, sem chave; o Pexels entra se a chave estiver configurada). Escolha uma e clique em **Usar esta**. Para buscar outra coisa, escreva em inglês (ex.: "Miami Beach").
- **Notícia**: **Usar a fonte** (uma fonte do roteiro) ou cole o link da matéria. O app lê só o título, o veículo, a data e a imagem da página e desenha um cartão de notícia (só em cena do tipo Imagem).
- **Material oficial** (arte, trailer, foto de imprensa): não há fonte livre. Cole o link da página ou da imagem, ou envie o arquivo (JPG, PNG ou WEBP, até 25 MB), e escreva o crédito (ex.: Rockstar Games). Material de terceiros pode gerar reivindicação de direitos (Content ID).
- Depois de anexar: **Crédito na tela**, **Enquadramento** (Automático, Preencher a tela ou Imagem inteira com fundo desfocado), **Sujeito na foto** e, na notícia, veículo, data e manchete.
- A cena com mídia real **não gera imagem de IA** (não gasta cota). O prompt fica de reserva: **Tirar a mídia real** volta para a imagem de IA, e o arquivo fica guardado. Cenas que reaproveitam a imagem dela mostram a mesma mídia.
- **Créditos**: um selo pequeno no canto da cena e a lista *Créditos das imagens* no fim da descrição do YouTube e dos pacotes (`creditos.txt`). A lista aparece na aba 4, abaixo da descrição.
- Vídeo real (b-roll em movimento) ainda não: por enquanto só imagem e cartão de notícia.

## 7. Estilos

"Canal dark" aqui quer dizer canal sem rosto, de qualquer nicho. O app vem com estilos prontos para vários nichos; escolha o mais próximo do seu e ajuste:

| Nicho | Curto (9:16) | Longo (16:9) |
|---|---|---|
| Curiosidades, ciência, explicações | Curiosidades e Explicativo (Short) | Curiosidades e Explicativo (longo), Explicador com Arte Vetorial |
| Tecnologia e IA | Tecnologia e IA (Short) | (use Curiosidades longo ou Explicador) |
| História e biografias | | História Documental, Biografias |
| Finanças e negócios | | Finanças e Negócios |
| Contos, relatos, histórias de vida | | Histórias Narradas |
| Listas e rankings | | Top 10 / Listas |
| Motivacional e estoicismo | Estoico / Motivacional (Short) | |
| Mistério, terror e true crime | Mistérios Sombrios, Terror Analógico (VHS) | Documentário Sombrio, True Crime |

O novo projeto já vem com *Curiosidades e Explicativo (Short)* marcado. Se o projeto for de um canal com estilo padrão (em Canais), vale o estilo do canal.

- **Duplicar e ajustar**: abra um preset, dê um id para a cópia e edite no formulário (ou no JSON completo).
- **Criar por descrição**: em Estilos, descreva (ex.: "documentário de jornal antigo, lento, sépia, máquina de escrever") e escolha um estilo base. O app mostra as mudanças aplicadas e as rejeitadas.
- Famílias prontas na v1: imagens paradas (Ken Burns), motion graphics (cartões, tipografia cinética, documentos, contagem) e analógico (VHS, glitch, scanlines). As demais estão no backlog.

### Conferência da narração

Cada trecho narrado é ouvido de volta pelo Whisper e comparado com o roteiro. O app refaz o trecho (até 3 tentativas, a última sem nenhuma instrução de estilo) quando a voz:

- lê a instrução de estilo em voz alta ("Narre como um documentário, voz grave…");
- repete o texto ou fala palavras que não estão no roteiro;
- erra mais palavras do que o limite do estilo.

Se ainda sobrar problema, a prévia mostra um aviso com o trecho. Sem o Whisper, só a duração é conferida (pega repetição e instrução lida em trechos curtos).

A voz do Gemini recebe **só o texto**: o tom vem da voz escolhida no estilo, e a velocidade do estilo é aplicada no áudio. Quando a instrução de estilo ia junto, o Gemini a lia em voz alta ou repetia o texto em 5 de 6 testes. Para experimentar com instrução mesmo assim, defina `VS_GEMINI_TTS_STYLE=1` antes de iniciar (a conferência continua ativa).

### Música

- **Procedural** (padrão): o app gera a trilha. Climas leves: neutro (o padrão), animado, inspirador, lo-fi, corporativo leve, curioso / divertido, calmo, épico e pulso. Climas sombrios: mistério, tensão, terror e sombrio (drone). O andamento e os acordes variam de vídeo para vídeo. Sem direitos autorais, mas simples.
- **Suas faixas**: em Configurações → *Biblioteca de músicas*, clique em **Abrir pasta** e coloque os arquivos (mp3, wav, m4a, ogg, flac). Use subpastas com o clima no nome (`animado`, `inspirador`, `lofi`, `corporativo`, `divertido`, `calmo`, `epico`, `pulso`, `misterio`, `tensao`, `terror`, `sombrio`); o resto conta como neutro. No estilo, escolha **Fonte da trilha = biblioteca**.
- O app escolhe uma faixa do mesmo clima pela semente do projeto, repete ou corta para caber no vídeo, aplica fade e ajusta o volume (com ducking sob a narração).
- Para trocar a faixa de um vídeo: aba **Prévia e edição** → cartão *Trilha* → escolha a faixa e gere a prévia de novo. Só o áudio é refeito.
- Fonte grátis: a Biblioteca de áudio do YouTube Studio, filtrando por "Atribuição não necessária". Use só faixas cuja licença permita o seu uso: música de terceiros sem licença gera reivindicação de Content ID.

## 8. Vários idiomas

Marque os idiomas no novo projeto (o primeiro é o principal). Depois do roteiro aprovado, o piloto automático traduz por segmento e narra cada idioma. Cada idioma gera o seu próprio vídeo (modo A). O modo de várias faixas de áudio no mesmo vídeo (modo B) está no backlog.

Ao enviar ao YouTube, a opção **Incluir título e descrição traduzidos** coloca os títulos dos outros idiomas no mesmo vídeo (o YouTube mostra a versão no idioma de quem assiste). Deixe desmarcada se cada idioma vai ganhar o próprio vídeo ou canal, para os vídeos não competirem entre si.

## 9. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Render lento | VHS, desfoque de bordas e aberração cromática custam caro (o grão é leve). Teste com menos efeitos no estilo. Deixe o notebook na tomada e no modo de desempenho. |
| Disco enchendo | Configurações → *Armazenamento* → **Limpar cache** (ou `uv run videos-studio clean`). Apaga renders e mixagens sem uso; narrações, imagens e os vídeos em *outputs* ficam. Marque *Incluir narrações e imagens de versões antigas* (ou `clean --versoes-antigas`) para apagar também as que nenhuma versão atual nem publicação usa. Com limite de espaço, narrações e imagens também podem sair pelo limite (a confirmação avisa). `clean --limite-gb 0` limpa sem limite. Para não se preocupar: defina um *Limite de espaço* e ligue *Limpar sozinho* (roda depois de cada build e ao abrir o app; para agendar no Windows, `uv run videos-studio clean --auto`). |
| "provedores de teste" na prévia | Configure o Gemini (voz) e o Codex (imagens) e gere a prévia de novo: o app troca sozinho os itens de prévia. |
| A voz lê instruções ou repete frases | Atualize o app (versão de 14/11/2026 em diante) e gere a prévia de novo: as narrações antigas não são reaproveitadas. Se o aviso continuar em algum trecho, ajuste uma palavra dele na aba Roteiro: só aquela narração é refeita. |
| "nenhum provedor de llm funcionou: claude_code…" | O Claude Code não está logado ou o limite do plano acabou. Rode `claude auth login` (ou espere o limite voltar) e gere de novo. |
| Cota do Gemini esgotada | O app segue para o próximo provedor da cadeia e tenta o Gemini de novo no dia seguinte. |
| Porta 8765 ocupada | Defina outra porta: `$env:VS_PORT=8766` antes de iniciar. |
| Erro ao conectar o YouTube | Confira se o cliente OAuth é do tipo "App para computador" e se a tela de consentimento está publicada. |
| "já está ligado ao canal…" ao vincular | Esse canal do YouTube já pertence a outro canal do app. Use **Atualizar dados** nele, ou **Remover link** lá antes de vincular aqui. |
| Colar o link do canal falha | Cole no formato `youtube.com/channel/UC…` (o id fica em YouTube Studio › Personalização › Informações básicas). |

## 10. Para desenvolver

```powershell
scripts\start.ps1 -Dev        # backend com recarga + Vite em http://127.0.0.1:5173
cd backend; $env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"; uv run pytest
$env:VS_TEST_RENDER="1"; uv run pytest tests/test_pipeline.py::test_full_render_short   # render real
npm run renderer:studio       # Remotion Studio para ajustar as composições
```
