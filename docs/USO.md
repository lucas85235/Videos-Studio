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

- **Playlist**: no cartão do YouTube, escolha a playlist do vídeo, **Nenhuma** ou **+ Nova playlist…** (cria no canal). Em **Canais**, *Playlist dos envios* define a playlist padrão de cada canal; ela vale também na Fila do YouTube. Se a playlist falhar (apagada, cheia), o vídeo continua enviado e o aviso aparece no resultado.
- **Várias contas**: cada canal vinculado em Canais publica só no próprio canal do YouTube. Projeto sem canal vinculado mostra *Conta do YouTube* para escolher em qual das contas conectadas o vídeo entra.
- **Cota**: o cartão e **Configurações › Publicação › Cota e contas do YouTube** mostram a cota de hoje: 10.000 unidades por dia para tudo (capa 50, legendas 400, playlist 50, listar 1) e 100 envios de vídeo por dia, à parte. Ela é do projeto do Google Cloud (todas as contas dividem) e zera à meia-noite do horário do Pacífico. A partir de 80% aparece um aviso; quando acaba (ou o Google avisa que acabou), o envio fica bloqueado até zerar. Se o Google aumentou a cota do seu projeto, ajuste o limite ali.

### Vincular um canal que você já tem

Em **Canais › Vincular canal do YouTube** há dois caminhos:

- **Entrar com o Google** (precisa do cliente OAuth acima): o login abre numa nova aba. Escolha a conta e o canal (contas de marca aparecem na tela do Google). O app cria o canal, ou atualiza o que você escolheu, com o nome, o @, a descrição, a foto, o país e o idioma, as palavras-chave, os inscritos e os títulos dos últimos 15 vídeos. Cada canal vinculado guarda o próprio acesso: os envios ao YouTube de um projeto vão para o canal dele. Projeto sem canal vinculado usa a conta conectada na aba 4, como antes.
- **Colar o link do canal** (sem login): aceita `youtube.com/@seucanal`, `youtube.com/channel/UC…` ou só o `@seucanal`. O app lê a página pública do canal uma vez e o feed oficial (nome, foto e títulos recentes). Por esse caminho o app **não publica** no canal; para publicar, use depois **Vincular com login** no cartão.

No cartão do canal:
- **Preencher com IA**: o Claude lê a descrição e os títulos recentes e sugere nicho, persona, idiomas, estilo padrão e identidade visual. Gasta um pouco do limite do seu plano. Nada é salvo até você revisar e clicar em **Salvar canal**.
- **Atualizar dados**: lê de novo nome, foto, números e vídeos recentes (o nome do canal no app volta a ser o do YouTube).
- **Desvincular**: apaga só o acesso daquele canal; os dados trazidos ficam. **Remover link** tira também os dados do YouTube do canal.
- Selo **Acesso expirado**: o Google recusou o acesso (tela de consentimento em "Testing" expira em 7 dias). Clique em **Vincular de novo**.

### TikTok e Instagram pelo Buffer

O Buffer publica no TikTok e no Instagram (Reels) pelo plano grátis. Como ele baixa o vídeo **na hora de publicar**, o Vulto guarda o vídeo numa URL pública do **Cloudflare R2** e apaga depois que o post sai.

1. No **Buffer**, conecte o TikTok e o Instagram (conta Business ou Creator). Em *Settings → API*, crie uma chave e cole em **Configurações → Publicação → Buffer** (ou use a variável `BUFFER_API_KEY`).
2. Na **Cloudflare**, crie um bucket no R2 e ative o acesso público (domínio próprio ou o endereço `r2.dev`, que tem limite de uso e serve para testes). Crie um token de API do R2 com leitura e escrita de objetos no bucket.
3. Em **Configurações → Publicação → Cloudflare R2**, cole o ID da conta, o *Access Key ID*, a *Secret Access Key*, o nome do bucket e o endereço público (ex.: `https://pub-xxxx.r2.dev`).
4. Na aba de publicação do projeto, no cartão **TikTok e Instagram pelo Buffer**, escolha as redes, o perfil (o Vulto lembra o perfil de cada canal) e quando: fila do Buffer, primeiro da fila, agora ou um horário marcado (os horários livres do canal também preenchem esse campo).
5. Em **Publicações deste projeto**, o post aparece como *Agendado* (ou *Na fila do Buffer*, quando ainda não tem horário). Com o app aberto, o Vulto confere no Buffer sem gastar a cota da API (600 pedidos por dia e 1.000 por canal a cada 30 dias no plano grátis): um post agendado só é conferido alguns minutos depois do horário; sem horário, o intervalo cresce (10 min, 30 min, 1 h, 3 h...); com o saldo de pedidos baixo, a conferência espera. **Conferir no Buffer** confere tudo na hora. Quando o post sai, vira *Postado* com o link e o vídeo é apagado do R2. Se o post for apagado no Buffer, o vídeo também é.
6. Se o Buffer **falhar** (perfil desconectado, por exemplo), o vídeo fica no R2 por 3 dias: dá para tentar de novo pelo próprio Buffer até lá. Depois, o vídeo é apagado e é preciso enviar outra vez pelo Vulto. Se a criação do post ficar sem resposta (queda de rede), o Vulto não apaga o vídeo (o post pode ter sido criado): confira a fila do Buffer antes de enviar de novo; o vídeo fica 7 dias no R2.
7. **Cancelar post** confere o post no Buffer antes: se ele já saiu ou está saindo, o cancelamento é recusado; senão, apaga o post e o vídeo. Se você trocar o bucket ou a conta do R2 com posts pendentes, os vídeos antigos ficam no bucket antigo e a tela avisa para apagá-los pelo painel da Cloudflare.
8. O cartão avisa (e pede confirmação) quando o vídeo não é vertical ou passa do limite das redes (TikTok até 10 min; Reels até 15 min), porque o Buffer só confere o vídeo na hora de publicar.

A legenda é a mesma do pacote manual (texto, créditos das imagens e até 5 hashtags) e o rótulo de IA segue o estilo (como no YouTube). Renders com voz ou imagens de teste não podem ir ao Buffer, porque ele publica como público.

### Instagram direto pela API oficial (Reels)

Opcional: sem isso, o Instagram vai pelo pacote manual. Precisa de conta **profissional** (Empresa ou Criador de conteúdo).

1. Em https://developers.facebook.com crie um app do tipo **Empresa** e adicione o produto **Instagram** (API com login do Instagram). Em "Instagram › API setup with Instagram business login", copie o **ID** e o **segredo do app do Instagram** para **Configurações › Publicação › Instagram (API oficial)**.
2. Conecte a conta em **Configurações › Contas do Instagram**, de um destes jeitos:
   - **Entrar com o Instagram**: cadastre o endereço de retorno em "Valid OAuth Redirect URIs" do app (o padrão é `http://127.0.0.1:8765/api/instagram/callback`). Se a Meta exigir https, cadastre um endereço seu (ex.: `https://localhost/`), salve-o nas opções e, depois do login, **cole o endereço final** da página no campo que aparece.
   - **Token do painel**: em "Generate token", entre com a conta e cole o token. Ele vale 60 dias; o app renova sozinho ao publicar (a Meta só renova tokens com mais de 24 h). A validade mostrada é uma estimativa até a primeira renovação, que o app faz no primeiro uso depois de 24 h.
3. O Instagram **baixa o vídeo de uma URL pública https** (o app roda só neste computador). Suba o MP4 final para um lugar público e cole o **link direto do arquivo** na hora de publicar (link de compartilhamento do Drive/Dropbox abre uma página, não o arquivo; URL pré-assinada de S3/R2 funciona), ou configure o **endereço público dos vídeos**: se você espelha **só a pasta `outputs`** do Vulto num endereço https (ex.: um bucket), cole o endereço que corresponde a ela; o app junta o caminho do vídeo dentro de `outputs` (`<projeto>/<idioma>/…`). A capa (JPEG) vai junto só nesse caso, e fica de fora se não responder.
   > **Nunca publique a pasta de dados inteira.** Ela guarda `studio.db` (o cofre, com o segredo do app e os tokens do Instagram e do YouTube) e, quando o sistema não tem keyring, `.master.key`, a chave que abre esse cofre. Suba só os MP4/JPG finais ou só a pasta `outputs`.
4. Na aba 4 do projeto, cartão **Instagram (Reels)**: escolha **API oficial** ou **Pacote manual** (com canal, a escolha fica salva no canal, assim como a conta, quando há mais de uma) e clique em **Publicar Reel**.

O que saber:
- Publica **na hora, como público**: a API não agenda nem faz post privado. Render com voz ou imagens de teste fica bloqueado.
- Com o app da Meta em **modo de desenvolvimento**, o post só aparece para quem tem papel no app (teste num navegador deslogado).
- Limite da Meta: **50 posts por conta a cada 24 h** (o app confere antes de enviar e mostra o uso em Configurações). Vídeo de 3 s a 15 min, até 300 MB; legenda até 2.200 caracteres e 30 hashtags.
- O app consulta o processamento uma vez por minuto por até 5 min (como pede a Meta). Se passar disso, ou se algo falhar depois do envio (limite, rede, app fechado, cancelamento), a publicação fica **Processando** e **Retomar envio** (no cartão) ou **Tentar publicar de novo** (em Publicações) publica o mesmo envio, com a mesma conta (vale por 24 h). Enquanto ele existe, o app não deixa publicar outro igual.
- **Rótulo de IA**: vai pela API (`is_ai_generated`) quando o estilo é fotorrealista ou usa vídeo IA. Se a API recusar, o app publica sem ele e avisa para marcar no celular.

### Agenda e horários de postagem

- Em **Canais**, defina os **horários de postagem** de cada canal (dias da semana, hora e fuso; ex.: seg/qua/sex 18:00, America/Sao_Paulo).
- No cartão do YouTube aparecem os **próximos horários livres** do canal do projeto: um clique preenche o agendamento. Horários a menos de 45 min de outro vídeo agendado do mesmo canal não são oferecidos.
- O pacote manual (TikTok, Instagram etc.) aceita um horário **planejado**. Ele fica na **Agenda** até você marcar como postado (com o link do post, se quiser).
- A página **Agenda** mostra o que está agendado, os pacotes por postar (com atalho para a pasta, a página de upload e a legenda) e o que saiu nas últimas duas semanas.
- **Fila do YouTube** (na Agenda): marque vários vídeos já renderizados e clique em *Agendar nos próximos horários livres*: cada um vai, na ordem, para o próximo horário livre do seu canal, e o envio roda em segundo plano.

### Comentários

A página **Comentários** (grupo Gestão) lê os comentários de todos os vídeos dos canais vinculados com o login do Google (o link público não lê comentários).

- **Ler comentários novos** traz até 300 comentários por canal, os mais novos primeiro (1 unidade de cota a cada 100). Depois da primeira leitura, a rotina da manhã lê sozinha os novos.
- Filtros: **Não respondidos** (o canal ainda não respondeu), **Perguntas novas** (perguntas sem resposta, ainda não lidas e que aceitam resposta: tem "?" ou começa com pergunta clara, como "por que", "qual", "como faz"), **Não lidos** e **Todos**; dá para filtrar por vídeo. A lista mostra 100 por vez (**Carregar mais** traz o resto, até 500).
- **Sugerir resposta**: o Claude escreve uma resposta curta no tom da persona do canal (Canais › Persona), no idioma do comentário, sem inventar fatos. Você edita à vontade.
- **Responder no YouTube** pede confirmação e só então publica a resposta em nome do canal (50 unidades de cota). A resposta é pública.
- **Marcar como lido** fica só no Vulto (o YouTube não tem isso). As mesmas **Perguntas novas** aparecem em **Hoje › Esperando você**; ler ou responder tira de lá. **Marcar todos como lidos** vale só para o canal, filtro e vídeo escolhidos.
- A linha de cota mostra quanto os comentários gastaram hoje e o total que o Vulto contou (a cota de 10.000 unidades por dia é do projeto do Google Cloud, dividida entre todos os canais).
- Se aparecer "sem permissão de comentários", vincule o canal de novo em Canais (a permissão `youtube.force-ssl` já vem no login do Vulto; o aviso aparece se ela foi desmarcada na tela do Google). A leitura continua; só a resposta fica bloqueada, sem gastar cota. Acesso vencido ou revogado aparece como "sem acesso ao YouTube".
- Se a resposta der "o YouTube não respondeu a tempo", ela pode ter sido publicada: confira em **Abrir no YouTube** antes de enviar de novo.
- Em fios com muitas respostas o YouTube devolve só parte delas: se a resposta do canal não vier entre elas, o comentário continua em **Não respondidos** (marcar como lido tira de **Perguntas novas**, **Não lidos** e Hoje).

### Séries com piloto automático

Em **Séries**, cadastre uma série por canal: nome, tema, instruções fixas (bordão, estrutura), formato, estilo (vazio = o do canal), **cadência** (todo dia, toda semana...) e quantos **episódios em estoque** ela deve manter à frente.

- **De onde vêm os temas**: uma **fila de temas** (um por linha; o primeiro vira o próximo episódio e sai da fila) ou as **ideias aprovadas** do canal (da nota mais alta para a mais baixa, no formato da série e, se quiser, só as que têm uma palavra).
- **Rodar agora** cria os episódios que faltam no estoque (até o máximo por rodada); **+1 episódio** cria um a mais. Com o **piloto automático** ligado, a rotina da manhã faz isso sozinha.
- Cada episódio vira um projeto com o briefing da série e roda o piloto do projeto **até o portão de revisão**: para em *Revisar o roteiro* (depois, nas cenas). **Nada é publicado sozinho**; o envio continua pela Agenda.
- O estoque da série conta os episódios em produção, esperando revisão, prontos ou com falha; publicado ou apagado sai da conta. **Hoje** mostra o estoque de cada série e as revisões esperando você. Se a fila de temas acabar (ou não houver ideia aprovada), aparece uma pendência em Hoje.
- **Pausar** a série impede a rotina e os botões de criar episódios. Apagar a série não apaga os projetos.

### Rotinas com o Vulto fechado

As rotinas do dia (cópia do banco e, depois do horário da manhã, coleta de métricas, comentários novos, pendências de estoque, receita e marcos, radar semanal e episódios das séries com piloto ligado) rodam sozinhas com o app aberto. Para rodarem com ele fechado, agende uma tarefa no Windows (uma vez só), no PowerShell, na pasta do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1                 # ao entrar no Windows e todo dia às 08:00
powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1 -Horario 07:30  # outro horário
powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1 -Remover        # tira a tarefa
```

- A tarefa **Vulto Rotinas** chama `scripts\rodar-rotinas.ps1`, que roda `python -m studio rotinas` com o uv do projeto (como o `start.ps1`). Se o computador estiver desligado no horário, ela roda assim que der.
- Use o mesmo horário da manhã da página **Hoje**: antes dele, só a cópia do banco roda (a manhã fica para a execução seguinte).
- Nada roda duas vezes no mesmo dia, seja pelo app ou pela tarefa. Com o app aberto, a tarefa não faz nada (uma trava em `<pasta de dados>\rotinas.lock` cuida disso; trava de processo que já morreu ou vencida é ignorada).
- Em **Hoje → Rotinas** aparece se a tarefa existe, o comando para instalar (já com o horário da manhã) ou tirar e a última execução com o app fechado. Se o horário da manhã mudar depois de instalar, a página avisa e mostra o comando para agendar de novo. A saída de cada execução fica em `<pasta de dados>\logs\rotinas.log`.
- Para rodar à mão: `uv run --project backend python -m studio rotinas` (ou `videos-studio rotinas`).

## 6. Editar por instrução

Exemplos que funcionam bem:
- "deixa a cena 5 mais clara" ou "mais sombria" (só ajusta a cor, sem gerar imagem nova);
- "outra imagem na cena atual" (marque "Só a cena que está na tela agora");
- "na cena 3, mostra um farol na tempestade" (novo prompt de imagem);
- "legenda maior", "legenda em amarelo", "música mais baixa", "música mais animada", "sem música";
- "zoom de impacto na cena 2", "transição glitch na cena 7";
- "reescreve o segmento 2 com um exemplo do dia a dia" ou "com mais suspense".

Toda mudança vira uma versão (aba Versões). Voltar a uma versão não paga de novo, porque os arquivos ficam no cache.

### Versão B (teste A/B)

Na aba Versões, **Criar versão B** (ou o ícone de ramo numa linha do histórico) cria uma cópia que você edita por instrução sem mexer na principal (a versão A). **Comparar A × B** mostra as duas prévias lado a lado e o que mudou trecho a trecho. **Gerar prévia de A e B** e **Renderizar A e B** fazem as duas num trabalho só (o que é igual é reaproveitado). Para terminar: marque trechos e **Mesclar escolhidos na A**, **B vence: virar a principal** ou **A vence: descartar a B**. Trechos que só a A mudou depois de a B nascer aparecem como "Mudou só na A" e não dá para mesclá-los (a cópia da B desfaria a mudança). Para medir, publique as duas no YouTube como **públicas** (ou não listadas): a A pela aba Vídeo e publicação, a B pela aba Versões (escolha "Público" e confirme que assistiu ao vídeo; ou ligue o link de um vídeo já publicado). Vídeo privado não ganha views, então o experimento em Desempenho (variável "versão") só abre quando as duas estão no ar; se enviar a B como privada, deixe-a pública no YouTube Studio depois. A leitura compara as views no mesmo idioma, uma contra uma, depois da idade mínima do formato (3 dias para Shorts, 7 para longos), pede 1,3x de vantagem para apontar a vencedora e fecha o experimento sozinha.

### Mídia real (fotos, notícias e material oficial)

Ao criar as cenas, o Claude marca algumas batidas em que uma imagem real vale mais que a de IA: pessoa ou lugar real, produto, personagem oficial, notícia. Na aba Cenas, o cartão da cena mostra **Mídia real sugerida** e o que procurar; o painel da curadoria conta as sugestões (o **Ver** leva à primeira). Nada é anexado sozinho. Em qualquer outra cena, o botão **Mídia real** abre as mesmas opções.

- **Foto real**: o app busca fotos de licença livre no Wikimedia Commons e no Openverse (grátis, sem chave; o Pexels entra se a chave estiver configurada). Escolha uma e clique em **Usar esta**. Para buscar outra coisa, escreva em inglês (ex.: "Miami Beach").
- **Notícia**: **Usar a fonte** (uma fonte do roteiro) ou cole o link da matéria. O app lê só o título, o veículo, a data e a imagem da página e desenha um cartão de notícia (só em cena do tipo Imagem).
- **Material oficial** (arte, trailer, foto de imprensa): não há fonte livre. Cole o link da página ou da imagem, ou envie o arquivo (JPG, PNG ou WEBP, até 25 MB), e escreva o crédito (ex.: Rockstar Games). Material de terceiros pode gerar reivindicação de direitos (Content ID).
- **Busca automática de arte oficial e notícias**: ao fim das cenas, se o Claude Code com busca na web for o LLM em uso, o app procura sozinho, numa única chamada para o vídeo inteiro (até 8 sugestões por vez), a imagem oficial ou a da notícia de cada sugestão pendente (e das fotos que a busca livre não achou). Cada candidato é conferido sem IA (só https, imagem de verdade, lado maior de pelo menos 600 px) e aparece na cena com o veículo e o motivo; nada é anexado sem você escolher. A busca roda como uma tarefa à parte (**Busca de mídia oficial**, no painel *Mídia oficial e notícias* da aba Cenas): as cenas já ficam prontas para editar enquanto ela roda, e dá para cancelar. No painel, **Buscar de novo** repete a busca e **Usar as primeiras** anexa o primeiro achado de cada cena. Sem o Claude Code com busca web (ou no modo offline), nada é chamado.
- Depois de anexar: **Crédito (na descrição)**, **Enquadramento** (Automático, Preencher a tela ou Imagem inteira com fundo desfocado), **Sujeito na foto** e, na notícia, veículo, data e manchete.
- A cena com mídia real **não gera imagem de IA** (não gasta cota). O prompt fica de reserva: **Tirar a mídia real** volta para a imagem de IA, e o arquivo fica guardado. Cenas que reaproveitam a imagem dela mostram a mesma mídia.
- **Créditos**: nada aparece na tela (o vídeo fica limpo); o crédito vai na lista *Créditos das imagens* no fim da descrição do YouTube e dos pacotes (`creditos.txt`). A lista aparece na aba 4, abaixo da descrição.
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
- **LUT de cor (.cube)**: no editor, em *Efeitos de imagem*, ligue **LUT de cor** e escolha um dos prontos (azul-petróleo e laranja, filme desbotado, noite fria, âmbar quente, processo cruzado, preto e branco contrastado) ou clique em **Enviar .cube** para usar o seu (fica em `data/library/luts`). A intensidade mistura o LUT com a cor original. O LUT é aplicado nas imagens no build (fica em cache) e aparece na prévia do vídeo; vídeos de cena (mídia real em vídeo) ficam sem LUT por enquanto (o build avisa). Um .cube com nome repetido é guardado como `nome-2.cube`, sem mudar a cor dos estilos que usam o antigo.
- Famílias prontas na v1: imagens paradas (Ken Burns), motion graphics (cartões, tipografia cinética, documentos, contagem) e analógico (VHS, glitch, scanlines). As demais estão no backlog.

### Conferência da narração

Cada trecho narrado é ouvido de volta pelo Whisper e comparado com o roteiro. O app refaz o trecho (até 3 tentativas, a última sem nenhuma instrução de estilo) quando a voz:

- lê a instrução de estilo em voz alta ("Narre como um documentário, voz grave…");
- repete o texto ou fala palavras que não estão no roteiro;
- erra mais palavras do que o limite do estilo.

Se ainda sobrar problema, a prévia mostra um aviso com o trecho. Sem o Whisper, só a duração é conferida (pega repetição e instrução lida em trechos curtos).

A voz do Gemini recebe **só o texto**: o tom vem da voz escolhida no estilo, e a velocidade do estilo é aplicada no áudio. Quando a instrução de estilo ia junto, o Gemini a lia em voz alta ou repetia o texto em 5 de 6 testes. Para experimentar com instrução mesmo assim, defina `VS_GEMINI_TTS_STYLE=1` antes de iniciar (a conferência continua ativa).

### Pronúncia

A voz local da prévia (Kokoro) e a voz do Windows leem mal o texto cru. Para elas, o app escreve por extenso só no texto que vai para a voz: números ("2025", "R$ 2 milhões", "30%", "24h", "1º"), datas e horas ("15/11/2026", "14h30"), graus ("30°C"), siglas que não dá para ler ("FBI" → "éfe bê í"; "ONU" e "NASA" ficam), símbolos ("&", "@perfil", "#tag") e palavras comuns em inglês ("YouTube", "streaming", "app"). A legenda e os cartões continuam exatamente como no roteiro. As vozes de API (Gemini, ElevenLabs, Chirp) recebem o texto como está, porque já fazem isso bem.

Para nomes e termos que a voz erra, use **Canais › editar canal › Pronúncia**: "termo → como falar" (ex.: "Nvidia" → "envídia"), para todos os idiomas ou só um. Essa lista vale para **todas** as vozes do canal e passa na frente das trocas automáticas. Sigla (termo todo em maiúsculas, como "PIB") só troca quando aparece em maiúsculas. Ao mudar a lista, só os trechos que têm o termo são gravados de novo.

### Música

- **Procedural** (padrão): o app gera a trilha. Climas leves: neutro (o padrão), animado, inspirador, lo-fi, corporativo leve, curioso / divertido, calmo, épico e pulso. Climas sombrios: mistério, tensão, terror e sombrio (drone). O andamento e os acordes variam de vídeo para vídeo. Sem direitos autorais, mas simples.
- **Suas faixas**: em Configurações → *Biblioteca de músicas*, clique em **Abrir pasta** e coloque os arquivos (mp3, wav, m4a, ogg, flac). Use subpastas com o clima no nome (`animado`, `inspirador`, `lofi`, `corporativo`, `divertido`, `calmo`, `epico`, `pulso`, `misterio`, `tensao`, `terror`, `sombrio`); o resto conta como neutro. No estilo, escolha **Fonte da trilha = biblioteca**.
- O app escolhe uma faixa do mesmo clima pela semente do projeto, repete ou corta para caber no vídeo, aplica fade e ajusta o volume (com ducking sob a narração).
- Para trocar a faixa de um vídeo: aba **Prévia e edição** → cartão *Trilha* → escolha a faixa e gere a prévia de novo. Só o áudio é refeito.
- Fonte grátis: a Biblioteca de áudio do YouTube Studio, filtrando por "Atribuição não necessária". Use só faixas cuja licença permita o seu uso: música de terceiros sem licença gera reivindicação de Content ID.

## 8. Vários idiomas

Marque os idiomas no novo projeto (o primeiro é o principal). Depois do roteiro aprovado, o piloto automático traduz por segmento e narra cada idioma. Por padrão cada idioma gera o seu próprio vídeo (modo A).

**Faixas no mesmo vídeo (modo B, só YouTube).** Na aba Saída, em *Idiomas no YouTube*, escolha **Faixas no mesmo vídeo**. Aí:

- a tradução recebe o tempo de cada trecho (quantas palavras cabem), e uma tradução longa demais ganha uma segunda rodada pedindo uma versão mais curta. Se o projeto já estava traduzido quando você trocou para o modo B, o painel mostra quantos trechos passam do tempo e o botão **Encurtar traduções longas**, que reescreve só esses trechos (suas correções e os trechos travados ficam);
- o render faz **um vídeo só** (na imagem do idioma principal, sem legenda queimada) e uma **faixa de áudio por idioma** com a mesma duração: a narração que passa do tempo é acelerada até +10% e a que fica curta é desacelerada até -8%, sem mudar o tom; passou disso, o trecho ganha uma pausa no vídeo e o app avisa qual segmento encurtar;
- o app monta a pasta `outputs\<projeto>\faixas-youtube\` com, por idioma, a faixa WAV, a legenda `.srt`, o título, a descrição e as tags, e um `CHECKLIST.md`;
- ao publicar pelo app (no idioma principal: os outros idiomas são faixas e não têm vídeo próprio), títulos, descrições e legendas de todos os idiomas vão junto com o vídeo. As **faixas de áudio não têm API**: envie cada uma em YouTube Studio → Idiomas → Adicionar idioma → Dublagem. O canal precisa ter acesso aos recursos avançados.

**Glossário do canal** (Canais › editar canal › *Glossário da tradução*): termos que não se traduzem (nome do canal, marcas) ou que têm tradução fixa por idioma (ex.: "canal dark" → "faceless channel" em inglês). Os termos que aparecem no roteiro vão no pedido de tradução. Depois de traduzir, a aba **Roteiro** do idioma mostra um aviso quando um termo não foi respeitado; corrija o texto ali (o aviso some ao salvar) ou ajuste o glossário.

Ao enviar ao YouTube, a opção **Incluir título e descrição traduzidos** coloca os títulos dos outros idiomas no mesmo vídeo (o YouTube mostra a versão no idioma de quem assiste). Deixe desmarcada se cada idioma vai ganhar o próprio vídeo ou canal, para os vídeos não competirem entre si.

## 9. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Render lento | VHS, desfoque de bordas e aberração cromática custam caro (o grão e o LUT são leves; a aberração já desenha a imagem uma vez só). Teste com menos efeitos no estilo. Deixe o notebook na tomada e no modo de desempenho. |
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

### Testes de interface (Playwright)

```powershell
npm run web:test:ui           # compila a web e roda os testes de apps/web/e2e
cd apps/web; npx playwright test e2e/fluxo.spec.ts --headed   # um arquivo só, vendo o navegador
```

- Os testes sobem o próprio backend em **modo offline** (`VS_OFFLINE=1`: roteiro simulado, voz de teste, imagens procedurais), com uma **pasta de dados temporária** (`%TEMP%\vulto-e2e-8786`, apagada e recriada a cada rodada) na **porta 8786**. Nada vai para a internet e o seu banco real não é tocado: o backend dos testes ignora os `VS_*` do terminal e força `VS_DATABASE_URL` vazio (sem banco externo, mesmo que o `.env` tenha um), além da pasta, da porta, da interface compilada e dos workers. `VS_E2E_PORT` e `VS_E2E_DATA_DIR` trocam a porta e a pasta. **A pasta de `VS_E2E_DATA_DIR` é apagada a cada rodada**, por isso o script só aceita uma pasta dentro de `%TEMP%`, com nome começando por `vulto-e2e` ou já marcada pelos testes (arquivo `.vulto-e2e`), e recusa a pasta de dados real, o repositório e a sua pasta pessoal.
- Não é preciso baixar navegador: o config usa o **Edge** ou o **Chrome** já instalados (canais `msedge`/`chrome` do Playwright). `VS_E2E_CHANNEL=chrome` força um deles. Sem nenhum dos dois, instale o Chromium do Playwright você mesmo (`npx playwright install chromium`) e use `VS_E2E_CHANNEL=chromium`.
- Cobrem as páginas principais (Projetos, Novo projeto, Estilos, editor de estilo, Agenda, Canais, Configurações, página não encontrada, menu no celular) e o fluxo criar projeto → roteiro → cenas → prévia. Qualquer erro de JavaScript, erro no console ou resposta 5xx da API reprova o teste.
- O FFmpeg precisa estar no PATH (a prévia usa; `VS_FFMPEG`/`VS_FFPROBE` também valem). O backend usa o ambiente Python que já existir, nesta ordem: o de `UV_PROJECT_ENVIRONMENT`, `backend/.venv` (o do CI) ou `%USERPROFILE%\VideosStudioData\.venv-backend` (o criado pelo `scripts\setup.ps1`). Sem nenhum deles, o script para e explica: rode o `scripts\setup.ps1`.

### CI (GitHub Actions)

`.github/workflows/ci.yml` roda em push para `develop`/`main` e em pull requests, em três jobs:

- **Lint, tipos e build** (Linux): Ruff, `npm run typecheck` (inclui os testes de interface) e `npm run web:build`.
- **Pytest** (Windows, como o notebook; FFmpeg instalado pelo Chocolatey). O render real (`VS_TEST_RENDER=1`) fica de fora.
- **Testes de interface** (Windows, com o Edge do próprio runner). O relatório do Playwright fica como artefato da execução.
