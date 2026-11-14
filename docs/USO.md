# Guia de uso do Videos Studio

## 1. Instalar (uma vez)

Pré-requisitos já presentes no seu notebook: Node.js, Python 3.12 com `uv`, FFmpeg e Git.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

O script instala o backend (venv em `%USERPROFILE%\VideosStudioData\.venv-backend`, fora do OneDrive), as dependências do renderer e da interface, e compila a interface.

Opcional, para legendas mais precisas (alinhamento por Whisper local):

```powershell
$env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"; uv sync --project backend --extra asr
```

## 2. Abrir o app

Dê um duplo clique em `scripts\iniciar.cmd`. O navegador abre em http://127.0.0.1:8765. Para encerrar, feche a janela do terminal.

Onde ficam os arquivos: `%USERPROFILE%\VideosStudioData` (banco, cache, vídeos finais em `outputs\<projeto>\<idioma>\`).

## 3. Primeiro vídeo (5 passos)

1. **Novo projeto**: escreva um tema específico, escolha o estilo e os idiomas, e deixe marcado "Gerar o roteiro agora".
2. **Roteiro** (aba 1): leia, edite o que quiser, trave os segmentos que estão perfeitos e clique em **Aprovar roteiro e continuar**. O app gera o storyboard e a prévia.
3. **Prévia e edição** (aba 3): assista no player. Para mudar algo, escreva no chat, por exemplo "deixa a cena 5 mais sombria" ou "legenda maior". Veja o que o agente propõe e quanto custa, e clique em **Aplicar**. Só o que mudou é refeito.
4. **Renderizar vídeo** (botão no topo ou aba 4): gera o MP4 final em blocos, a thumbnail e as legendas SRT.
5. **Publicar** (aba 4): envie ao YouTube pela API oficial, ou prepare o pacote manual para TikTok, Instagram e outras redes.

## 4. Modo prévia × conteúdo real

Sem chaves, o app usa provedores de **prévia**:
- roteiro de exemplo (regras fixas);
- voz do Windows (Maria);
- imagens procedurais abstratas.

Esses provedores aparecem marcados como "só prévia". O app **bloqueia** a publicação pública de vídeos feitos com eles (você ainda pode enviar como privado para testar).

Para conteúdo real **a custo zero**:

| O quê | Onde conseguir | Onde colocar |
|---|---|---|
| **Gemini** (roteiro, agente, voz) | Google AI Studio → "Get API key". Use um projeto **sem** cobrança ativada para ficar na camada grátis. | Configurações › Roteiro e agente › Gemini (a mesma chave serve para a voz) |
| **Codex** (imagens pela cota do ChatGPT Plus) | Instale o Codex CLI (`npm install -g @openai/codex`) e rode `codex login` com a sua conta do ChatGPT. | Detectado sozinho quando o comando `codex` existir |
| **Pexels** (fotos de banco, opcional) | pexels.com/api | Configurações › Imagens › Pexels |
| **Claude** (opcional, pago) | console.anthropic.com | Configurações › Roteiro e agente › Claude |

A ordem de uso é sempre a da cadeia de fallback de cada estilo. Se um provedor falhar ou esgotar a cota, o próximo assume.

## 5. Conectar o YouTube (uma vez)

1. Em https://console.cloud.google.com crie um projeto (ex.: "Videos Studio").
2. Em "APIs e serviços › Biblioteca", ative a **YouTube Data API v3**.
3. Em "Tela de permissão OAuth", escolha **Externo**, preencha o básico e **publique o app ("In production")**. Em "Testing" o acesso expira a cada 7 dias. Para uso pessoal não é preciso verificação: na hora de conectar, clique em "Avançado › Acessar" no aviso do Google.
4. Em "Credenciais › Criar credenciais › ID do cliente OAuth", escolha **App para computador**.
5. Copie o client ID e o client secret para **Configurações › Publicação › YouTube**.
6. Na aba 4 de um projeto, clique em **Conectar canal do YouTube** e autorize.
7. No YouTube Studio, **verifique o canal por telefone**. Sem isso, uploads acima de 15 min e thumbnails personalizadas falham.

Envio: escolha privado, não listado, público ou **agendado**. O agendamento fica no próprio YouTube, então o notebook pode estar desligado na hora. O rótulo de conteúdo sintético é marcado sozinho quando o estilo é fotorrealista ou usa vídeo IA.

### Agenda e horários de postagem

- Em **Canais**, defina os **horários de postagem** de cada canal (dias da semana, hora e fuso; ex.: seg/qua/sex 18:00, America/Sao_Paulo).
- No cartão do YouTube aparecem os **próximos horários livres** do canal do projeto: um clique preenche o agendamento. Horários a menos de 45 min de outro vídeo agendado do mesmo canal não são oferecidos.
- O pacote manual (TikTok, Instagram etc.) aceita um horário **planejado**. Ele fica na **Agenda** até você marcar como postado (com o link do post, se quiser).
- A página **Agenda** mostra o que está agendado, os pacotes por postar (com atalho para a pasta, a página de upload e a legenda) e o que saiu nas últimas duas semanas.

## 6. Editar por instrução

Exemplos que funcionam bem:
- "deixa a cena 5 mais sombria" (só ajusta a cor, sem gerar imagem nova);
- "outra imagem na cena atual" (marque "Só a cena que está na tela agora");
- "na cena 3, mostra um farol na tempestade" (novo prompt de imagem);
- "legenda maior", "legenda em amarelo", "música mais baixa", "sem música";
- "zoom de impacto na cena 2", "transição glitch na cena 7";
- "reescreve o segmento 2 com mais suspense".

Toda mudança vira uma versão (aba Versões). Voltar a uma versão não paga de novo, porque os arquivos ficam no cache.

## 7. Estilos

- **Duplicar e ajustar**: abra um preset, dê um id para a cópia e edite no formulário (ou no JSON completo).
- **Criar por descrição**: em Estilos, descreva (ex.: "documentário de jornal antigo, lento, sépia, máquina de escrever") e escolha um estilo base. O app mostra as mudanças aplicadas e as rejeitadas.
- Famílias prontas na v1: imagens paradas (Ken Burns), motion graphics (cartões, tipografia cinética, documentos, contagem) e analógico (VHS, glitch, scanlines). As demais estão no backlog.

### Música

- **Procedural** (padrão): o app gera a trilha (drone, tensão, terror, mistério, épico, pulso, calmo). Sem direitos autorais, mas simples.
- **Suas faixas**: em Configurações → *Biblioteca de músicas*, clique em **Abrir pasta** e coloque os arquivos (mp3, wav, m4a, ogg, flac). Use subpastas com o clima no nome (`terror`, `misterio`, `tensao`, `epico`, `calmo`, `pulso`); o resto conta como ambiente. No estilo, escolha **Fonte da trilha = biblioteca**.
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
| Disco enchendo | Configurações → *Armazenamento* → **Limpar agora** (ou `uv run videos-studio clean`). Apaga renders e mixagens sem uso; narrações, imagens e os vídeos em *outputs* ficam. |
| "provedores de teste" na prévia | Configure o Gemini (voz e roteiro) e o Codex ou outra fonte de imagens, e gere a prévia de novo: o app troca sozinho os itens de prévia. |
| Cota do Gemini esgotada | O app segue para o próximo provedor da cadeia e tenta o Gemini de novo no dia seguinte. |
| Porta 8765 ocupada | Defina outra porta: `$env:VS_PORT=8766` antes de iniciar. |
| Erro ao conectar o YouTube | Confira se o cliente OAuth é do tipo "App para computador" e se a tela de consentimento está publicada. |

## 10. Para desenvolver

```powershell
scripts\start.ps1 -Dev        # backend com recarga + Vite em http://127.0.0.1:5173
cd backend; $env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"; uv run pytest
$env:VS_TEST_RENDER="1"; uv run pytest tests/test_pipeline.py::test_full_render_short   # render real
npm run renderer:studio       # Remotion Studio para ajustar as composições
```
