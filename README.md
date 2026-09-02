# Bot de Matemática Diária para Discord

Bot que posta automaticamente um problema de matemática todo dia no seu servidor de estudos, com dificuldade e assunto variados (aritmética, álgebra, funções quadráticas, e questões estilo OBMEP).

## Comandos

- `!problema` — posta um problema novo na hora (fora do horário automático)
- `!resposta` — revela a resposta do último problema postado naquele canal

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

### 4. Configurar o projeto
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Copie `.env.example` para `.env`:
   ```bash
   cp .env.example .env
   ```
3. Edite o `.env` com seu token e o ID do canal:
   ```
   DISCORD_TOKEN=seu_token_aqui
   CHANNEL_ID=123456789012345678
   POST_HOUR=8
   POST_MINUTE=0
   ```
   (`POST_HOUR`/`POST_MINUTE` estão no horário de Brasília.)

### 5. Rodar o bot
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
