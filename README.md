# Bot de Matemática Diária para Discord

Bot que posta automaticamente um problema de matemática todo dia no seu servidor de estudos, com dificuldade e assunto variados (aritmética, álgebra, funções quadráticas, e questões estilo OBMEP).

## Comandos

- `!problema` — mostra como usar o comando e os temas já disponíveis no banco
- `!problema <dificuldade> <conteúdo>` — busca um problema no banco com essa dificuldade (fácil, médio ou difícil) e conteúdo; se não achar, gera um novo na hora com IA (Groq) e já salva no banco (ex: `!problema difícil geometria espacial`)
- `!resposta` — revela a resposta do último problema postado no canal
- `!resposta <id>` — revela a resposta de um problema específico pelo número (ex: `!resposta 3`)
- `!ajuda` — mostra a lista de comandos

## Passo a passo para colocar no ar

### 1. Criar o bot no Discord
1. Acesse https://discord.com/developers/applications e clique em **New Application**.
2. Dê um nome (ex: "Bot Matemática") e clique em **Create**.
3. No menu lateral, vá em **Bot** → **Add Bot**.
4. Em **Privileged Gateway Intents**, ative **Message Content Intent** (necessário para os comandos `!problema` e `!resposta`).
5. Clique em **Reset Token** e copie o token gerado — ele vai no arquivo `.env`.

### 2. Convidar o bot para o servidor
1. No menu lateral, vá em **OAuth2 → URL Generator**.
2. Em **Scopes**, marque `bot`.
3. Em **Bot Permissions**, marque **Send Messages** e **Embed Links**.
4. Copie o link gerado, abra no navegador e escolha seu servidor de estudos.

### 3. Pegar o ID do canal
1. No Discord, ative o **Modo Desenvolvedor**: Configurações → Avançado → Modo Desenvolvedor.
2. Clique com o botão direito no canal onde os problemas devem aparecer → **Copiar ID do Canal**.

### 4. Pegar a chave da API da Groq (para gerar problemas novos com `!problema`)
1. Acesse https://console.groq.com/keys.
2. Faça login (dá pra usar sua conta Google) e clique em **Create API Key**.
3. Copie a chave gerada — ela vai no `.env` como `GROQ_API_KEY`.
   (Essa chave só é usada quando `!problema <dificuldade> <conteúdo>` não encontra nada no banco; os outros comandos funcionam mesmo sem ela. É grátis, sem cartão de crédito e sem crédito limitado, só com um limite de pedidos por minuto/dia.)

### 5. Configurar o salvamento automático dos problemas gerados (GitHub)

Quando o bot gera um problema novo (porque não achou nada no banco com a dificuldade/conteúdo pedidos), ele salva no `problems.json` e sincroniza direto com o GitHub, pra essa adição não se perder quando o Railway reiniciar o bot. O mesmo vale pra numeração dos problemas (`#1`, `#2`...), que fica salva num arquivo separado (`problem_history.json`) e é recarregada sempre que o bot reinicia — não volta mais pro zero à toa.

1. Acesse https://github.com/settings/tokens?type=beta e clique em **Generate new token** (token do tipo "Fine-grained").
2. Em **Repository access**, escolha **Only select repositories** e marque o repositório do bot (ex: `bot-matematica`).
3. Em **Permissions → Repository permissions**, encontre **Contents** e mude para **Read and write**.
4. Gere o token e copie ele — ele vai no `.env` como `GITHUB_TOKEN`. **Trate esse token como uma senha: nunca cole ele no problems.json ou em qualquer arquivo que vá pro repositório.**
5. Defina também `GITHUB_REPO` no formato `seu_usuario/nome_do_repositorio` (ex: `joaosilva/bot-matematica`).

> ⚠️ Como o Railway fica de olho em mudanças no GitHub, cada vez que o bot salvar algo automaticamente (um problema novo ou a numeração atualizada), isso vai disparar um redeploy e reiniciar o bot. Isso não reseta mais a numeração — ela é recarregada do GitHub assim que o bot volta —, mas se você quiser evitar os redeploys em si, configure em Railway → Settings → **Watch Paths** um padrão tipo `*.py` (ou `bot.py`), assim só mudanças de código disparam redeploy, não os arquivos de dados (`problems.json`, `problem_history.json`).

### 6. Configurar o projeto
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Copie `.env.example` para `.env`:
   ```bash
   cp .env.example .env
   ```
3. Edite o `.env` com seus dados:
   ```
   DISCORD_TOKEN=seu_token_aqui
   CHANNEL_ID=123456789012345678
   POST_HOUR=8
   POST_MINUTE=0
   GROQ_API_KEY=sua_chave_da_groq_aqui
   GITHUB_TOKEN=seu_token_do_github_aqui
   GITHUB_REPO=seu_usuario/bot-matematica
   GITHUB_BRANCH=main
   ```
   (`POST_HOUR`/`POST_MINUTE` estão no horário de Brasília. Existe também `GITHUB_HISTORY_FILE_PATH`, opcional — por padrão o bot usa `problem_history.json` pra guardar a numeração; só precisa mudar se quiser um nome diferente.)

### 7. Rodar o bot
```bash
python bot.py
```

O bot precisa ficar rodando continuamente para postar todo dia no horário configurado. Para isso, você tem algumas opções:
- Deixar rodando num computador/servidor seu com `pm2`, `screen` ou como serviço `systemd`.
- Hospedar gratuitamente em algo como **Railway**, **Render** ou um VPS pequeno.

## Adicionando mais problemas

Edite o arquivo `problems.json` — cada problema segue este formato:

```json
{
  "difficulty": "médio",
  "topic": "Funções Quadráticas",
  "question": "Enunciado do problema aqui.",
  "answer": "Resposta e explicação aqui."
}
```

Quanto mais problemas você adicionar, menor a chance de repetição.
