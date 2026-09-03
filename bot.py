"""
Bot de Discord que posta um problema de matemática diariamente
no canal configurado, e permite ver a resposta e pedir um problema extra.
"""

import os
import json
import random
import logging
import re
import base64
import asyncio
import time as time_module
from datetime import time, timezone, timedelta

import discord
import requests
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ------------------------------------------------------------------
# Configuração
# ------------------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# openai/gpt-oss-120b é o modelo "de produção" recomendado atualmente pela Groq
# para tarefas de propósito geral. Pode ser trocado via variável de ambiente
# se a Groq aposentar esse modelo no futuro (veja console.groq.com/docs/models).
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Sincronização do banco de problemas com o GitHub (para persistir problemas
# gerados por IA além do reinício/redeploy do bot).
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # formato "usuario/repositorio"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "problems.json")

# Horário (fuso de Brasília, UTC-3) em que o problema é postado todo dia.
# Exemplo: 8h da manhã em Brasília.
POST_HOUR = int(os.getenv("POST_HOUR", "8"))
POST_MINUTE = int(os.getenv("POST_MINUTE", "0"))
BRASILIA_TZ = timezone(timedelta(hours=-3))
POST_TIME = time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=BRASILIA_TZ)

PROBLEMS_FILE = os.path.join(os.path.dirname(__file__), "problems.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("math-bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Histórico de problemas por canal: canal_id -> {numero: problema}
problem_history_by_channel: dict[int, dict[int, dict]] = {}
# Próximo número a usar em cada canal
next_id_by_channel: dict[int, int] = {}
# Guarda o último problema postado (por canal), pra !resposta sem número continuar funcionando
last_problem_by_channel: dict[int, dict] = {}


def load_problems() -> list[dict]:
    with open(PROBLEMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_topics() -> list[str]:
    problems = load_problems()
    return sorted({padronizar_assunto(p["topic"]) for p in problems})


def register_problem(channel_id: int, problem: dict) -> int:
    """Registra o problema no histórico do canal e devolve o número (#ID) atribuído a ele."""
    numero = next_id_by_channel.get(channel_id, 1)
    problem_history_by_channel.setdefault(channel_id, {})[numero] = problem
    next_id_by_channel[channel_id] = numero + 1
    last_problem_by_channel[channel_id] = problem
    return numero


def generate_ai_problem(tema: str, nivel: str | None = None) -> dict:
    """Gera um problema de matemática novo usando a API da Groq.
    'nivel', se informado, força um grau de dificuldade específico no prompt
    (ex: nível OBMEP 3 / ITA, pra questões bem mais avançadas que o padrão).
    Levanta RuntimeError com uma mensagem amigável se algo der errado."""
    if not GROQ_API_KEY:
        raise RuntimeError(
            "A geração por IA não está configurada. Defina GROQ_API_KEY no .env do bot."
        )

    instrucao_nivel = (
        f'\n\nO problema DEVE ter o nível de dificuldade de "{nivel}" — ou seja, '
        "bem avançado e desafiador, nada trivial ou de nível básico."
        if nivel
        else ""
    )

    prompt = f"""Crie UM problema de matemática original em português sobre o tema "{tema}",
adequado para um estudante de ensino médio se preparando para olimpíadas (nível OBMEP).{instrucao_nivel}

Responda APENAS com um JSON válido, sem markdown, sem crases, no formato exato:
{{"question": "enunciado completo do problema", "answer": "resposta final com uma explicação breve de como chegar nela", "difficulty": "fácil, médio ou difícil", "topic": "{tema}"}}"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        texto = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # erro de rede, chave inválida, etc.
        logger.exception("Erro ao chamar a API da Groq")
        raise RuntimeError(f"Não consegui falar com a IA agora ({exc}).") from exc

    # Remove blocos de código markdown, caso a IA responda com ```json ... ```
    texto = re.sub(r"^```(json)?|```$", "", texto, flags=re.MULTILINE).strip()

    try:
        problem = json.loads(texto)
    except json.JSONDecodeError as exc:
        logger.error("Resposta da IA não é um JSON válido: %s", texto)
        raise RuntimeError("A IA respondeu em um formato inesperado. Tente de novo.") from exc

    for campo in ("question", "answer", "difficulty", "topic"):
        if campo not in problem:
            raise RuntimeError("A IA não retornou todos os campos esperados. Tente de novo.")

    # A IA às vezes devolve "\n" como texto literal (duas letras: barra e "n")
    # em vez de uma quebra de linha de verdade. Troca isso por uma quebra real.
    for campo in ("question", "answer"):
        problem[campo] = problem[campo].replace("\\n", "\n").strip()

    # Padroniza o assunto pra uma das categorias fixas do bot, em vez de deixar
    # a IA inventar um nome novo a cada vez.
    problem["topic"] = padronizar_assunto(problem["topic"])

    return problem


def github_configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def push_problems_to_github(problems: list[dict]) -> None:
    """Sobrescreve o problems.json no GitHub com a lista de problemas atual.
    Tenta de novo automaticamente uma vez se der conflito (409) — geralmente
    causado por uma pequena inconsistência passageira da API do GitHub.
    Levanta uma exceção se algo der errado (chave inválida, repo errado, etc.)."""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    conteudo = json.dumps(problems, ensure_ascii=False, indent=2)
    conteudo_b64 = base64.b64encode(conteudo.encode("utf-8")).decode("utf-8")

    tentativas_maximas = 2
    for tentativa in range(1, tentativas_maximas + 1):
        # Precisa do sha atual do arquivo pra poder atualizá-lo.
        resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=20)
        resp.raise_for_status()
        sha_atual = resp.json()["sha"]

        payload = {
            "message": "Adiciona problema gerado por IA via bot do Discord",
            "content": conteudo_b64,
            "sha": sha_atual,
            "branch": GITHUB_BRANCH,
        }
        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=20)

        if put_resp.status_code == 409 and tentativa < tentativas_maximas:
            logger.warning("Conflito (409) ao salvar no GitHub, tentando de novo...")
            time_module.sleep(1)
            continue

        put_resp.raise_for_status()
        return


def save_generated_problem(problem: dict) -> tuple[bool, str]:
    """Adiciona o problema ao banco local (problems.json) e tenta sincronizar
    com o GitHub, para que a mudança sobreviva ao próximo redeploy.
    Retorna (sincronizado_com_github, mensagem_de_status)."""
    problems = load_problems()
    problems.append(problem)
    with open(PROBLEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(problems, f, ensure_ascii=False, indent=2)

    if not github_configured():
        return False, (
            "⚠️ Salvei no banco local, mas a sincronização com o GitHub não está "
            "configurada (faltam GITHUB_TOKEN/GITHUB_REPO) — essa adição pode se "
            "perder no próximo deploy."
        )

    try:
        push_problems_to_github(problems)
        return True, "✅ Problema salvo no banco e sincronizado com o GitHub."
    except Exception as exc:
        logger.exception("Erro ao sincronizar problems.json com o GitHub")
        return False, (
            f"⚠️ Salvei no banco local, mas não consegui sincronizar com o GitHub "
            f"agora ({exc}). Essa adição pode se perder no próximo deploy."
        )


def _normalizar(texto: str) -> str:
    """Remove acentos simples pra facilitar comparação de strings (fácil -> facil)."""
    substituicoes = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeiooouc" + "AAAAEEIOOOUC".lower())
    return texto.translate(substituicoes).lower()


# Categorias fixas de assunto. Qualquer tópico (inclusive os gerados por IA,
# que variam muito: "geometria espacial", "função quadrática", "adição"...)
# é padronizado pra uma dessas, tanto na exibição quanto ao salvar problemas
# novos. O que não se encaixa em nenhuma vira "MATEMÁTICA" (categoria genérica) —
# isso também cobre temas fora do escopo de matemática que a IA às vezes
# inventa (física, respostas sem sentido, etc.).
CATEGORIAS_PADRAO = [
    ("probabilidade", "PROBABILIDADE"),
    ("geometria", "GEOMETRIA"),
    ("obmep", "OBMEP"),
    ("funcao quadratica", "FUNÇÕES QUADRÁTICAS"),
    ("funcoes quadraticas", "FUNÇÕES QUADRÁTICAS"),
    ("quadratica", "FUNÇÕES QUADRÁTICAS"),
    ("adicao", "ARITMÉTICA"),
    ("subtracao", "ARITMÉTICA"),
    ("multiplicacao", "ARITMÉTICA"),
    ("divisao", "ARITMÉTICA"),
    ("aritmetica", "ARITMÉTICA"),
    ("algebra", "ÁLGEBRA"),
]

# Temas que contêm uma palavra-chave acima mas devem ficar de fora mesmo assim
# (ex: "álgebra booleana" não é o assunto de matemática que interessa aqui).
ASSUNTOS_EXCLUIDOS = ["booleana", "boolean"]

TOPICO_EMOJI = {
    "PROBABILIDADE": "🎲",
    "GEOMETRIA": "📐",
    "ARITMÉTICA": "➗",
    "ÁLGEBRA": "🧮",
    "FUNÇÕES QUADRÁTICAS": "📊",
    "OBMEP": "🏆",
    "MATEMÁTICA": "🧮",
}

DIFICULDADE_ESTILO = {
    "facil": {"emoji": "🟢", "cor": discord.Color.green()},
    "medio": {"emoji": "🟡", "cor": discord.Color.orange()},
    "dificil": {"emoji": "🔴", "cor": discord.Color.red()},
}


def padronizar_assunto(topico: str) -> str:
    """Mapeia qualquer texto de assunto pra uma das categorias fixas."""
    normalizado = _normalizar(topico)
    if any(excluido in normalizado for excluido in ASSUNTOS_EXCLUIDOS):
        return "MATEMÁTICA"
    for chave, categoria in CATEGORIAS_PADRAO:
        if chave in normalizado:
            return categoria
    return "MATEMÁTICA"


def get_topico_emoji(topico: str) -> str:
    return TOPICO_EMOJI.get(padronizar_assunto(topico), "🧮")


def get_dificuldade_estilo(dificuldade: str) -> dict:
    return DIFICULDADE_ESTILO.get(_normalizar(dificuldade), {"emoji": "⚪", "cor": discord.Color.blue()})


# Mapa pra converter dígitos/sinais/letras comuns de expoente em Unicode sobrescrito.
# Cobre os casos mais comuns em problemas de matemática (10^99, x^2, 2^n, a^-1).
_SUPERSCRITO = str.maketrans({
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "n": "ⁿ", "i": "ⁱ",
})


def formatar_matematica(texto: str) -> str:
    """Deixa a notação matemática mais legível no Discord (que não renderiza LaTeX):
    converte expoentes escritos com "^" em expoente Unicode de verdade
    (2^10 -> 2¹⁰, x^2 -> x², 10^-3 -> 10⁻³) e sqrt(x) em √x."""
    # Expoente entre parênteses: base^(expr)
    texto = re.sub(
        r"\^\(([\d+\-ni]+)\)",
        lambda m: m.group(1).translate(_SUPERSCRITO),
        texto,
    )
    # Expoente simples: base^expr
    texto = re.sub(
        r"\^([\d+\-ni]+)",
        lambda m: m.group(1).translate(_SUPERSCRITO),
        texto,
    )
    # Raiz quadrada: sqrt(x) -> √x
    texto = re.sub(r"sqrt\(([^()]+)\)", r"√(\1)", texto, flags=re.IGNORECASE)
    return texto


def formatar_passos(explicacao: str) -> str:
    """Quebra uma explicação corrida em passos numerados e espaçados, em vez de
    um parágrafo único difícil de acompanhar."""
    # Separa em frases sempre que um "." ou ";" é seguido de espaço e uma letra
    # maiúscula/dígito — tenta não quebrar no meio de números ou fórmulas.
    partes = re.split(r"(?<=[.;])\s+(?=[A-ZÀ-Ú0-9])", explicacao.strip())
    partes = [p.strip().rstrip(".;").strip() for p in partes if p.strip()]

    if len(partes) <= 1:
        return explicacao

    return "\n\n".join(f"**{i}.** {parte}." for i, parte in enumerate(partes, start=1))


def build_problem_embed(problem: dict, numero: int | None = None) -> discord.Embed:
    topico_emoji = get_topico_emoji(problem["topic"])
    estilo = get_dificuldade_estilo(problem["difficulty"])

    titulo = f"{topico_emoji} Problema de Matemática do Dia"
    if numero is not None:
        titulo = f"{topico_emoji} Problema #{numero}"

    # Bloco de citação deixa o enunciado visualmente destacado do resto do card,
    # o que ajuda a distinguir a fórmula/pergunta do texto de apoio.
    enunciado = formatar_matematica(problem["question"])
    enunciado_formatado = "\n".join(f"> {linha}" for linha in enunciado.splitlines())

    embed = discord.Embed(
        title=titulo,
        description=enunciado_formatado,
        color=estilo["cor"],
    )
    embed.add_field(
        name="Dificuldade",
        value=f"{estilo['emoji']} {problem['difficulty'].capitalize()}",
        inline=True,
    )
    embed.add_field(name="Assunto", value=padronizar_assunto(problem["topic"]), inline=True)
    embed.set_footer(text="Use !resposta para revelar a solução quando quiser tentar depois de pensar.")
    image_url = problem.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


# Temas sorteados pro problema diário, e o nível de dificuldade exigido —
# o objetivo aqui é sempre nível avançado (OBMEP Fase 3 / vestibular do ITA),
# bem mais puxado que o padrão dos outros comandos.
TEMAS_DIARIOS = ["geometria", "álgebra", "aritmética", "funções quadráticas", "probabilidade", "combinatória"]
NIVEL_DIARIO = "OBMEP Nível 3 (fase avançada) ou de vestibular do ITA"


def escolher_problema_diario() -> dict:
    """Escolhe o problema do dia: tenta gerar um novo em nível avançado via IA;
    se a IA não estiver configurada ou falhar, cai pra um problema difícil já
    existente no banco local (ou qualquer um, se não houver nenhum difícil)."""
    if GROQ_API_KEY:
        tema = random.choice(TEMAS_DIARIOS)
        try:
            problem = generate_ai_problem(tema, nivel=NIVEL_DIARIO)
            problem["difficulty"] = "difícil"  # garante a tag certa independente do que a IA disser
            sincronizado, status_msg = save_generated_problem(problem)
            if not sincronizado:
                logger.warning("Problema diário gerado mas não sincronizado com o GitHub: %s", status_msg)
            return problem
        except RuntimeError:
            logger.exception("Falha ao gerar problema diário via IA, usando o banco local como alternativa.")

    problems = load_problems()
    dificeis = [p for p in problems if _normalizar(p["difficulty"]) == "dificil"]
    return random.choice(dificeis or problems)


async def post_daily_problem(channel: discord.abc.Messageable):
    problem = await asyncio.to_thread(escolher_problema_diario)
    numero = register_problem(channel.id, problem)
    await channel.send(embed=build_problem_embed(problem, numero))


@bot.event
async def on_ready():
    logger.info(f"Bot conectado como {bot.user}")
    if not daily_problem_task.is_running():
        daily_problem_task.start()


@tasks.loop(time=POST_TIME)
async def daily_problem_task():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logger.warning("CHANNEL_ID inválido ou bot sem acesso ao canal.")
        return
    await post_daily_problem(channel)


@bot.command(name="problema")
async def problema_manual(ctx: commands.Context, *, tema: str = None):
    """Posta um problema novo (!problema) ou filtrado por tema (!problema <tema>).
    Sem tema, mostra a lista de temas disponíveis."""
    if tema is None:
        topicos = get_topics()
        lista = "\n".join(f"• {t}" for t in topicos)
        embed = discord.Embed(
            title="📚 Temas disponíveis",
            description=(
                "Use `!problema <tema>` para pedir um problema de um tema específico.\n\n"
                f"{lista}"
            ),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)
        return

    problems = load_problems()
    categoria_pedida = padronizar_assunto(tema)
    filtrados = [p for p in problems if padronizar_assunto(p["topic"]) == categoria_pedida]
    if not filtrados:
        await ctx.send(
            f"Não encontrei nenhum problema com o tema '{tema}'. "
            "Use `!problema` sem nada para ver os temas disponíveis."
        )
        return

    problem = random.choice(filtrados)
    numero = register_problem(ctx.channel.id, problem)
    await ctx.send(embed=build_problem_embed(problem, numero))


@bot.command(name="gerar")
async def gerar_problema_ia(ctx: commands.Context, *, tema: str = None):
    """Gera um problema NOVO com IA sobre o tema pedido (!gerar <tema>) e o
    adiciona automaticamente ao banco de problemas."""
    if tema is None:
        await ctx.send("Me diga sobre qual tema gerar o problema. Exemplo: `!gerar geometria espacial`")
        return

    async with ctx.typing():
        try:
            problem = generate_ai_problem(tema)
        except RuntimeError as exc:
            await ctx.send(f"⚠️ {exc}")
            return

        # Salvar em disco e sincronizar com o GitHub são operações bloqueantes,
        # então rodam numa thread separada pra não travar o bot.
        sincronizado, status_msg = await asyncio.to_thread(save_generated_problem, problem)

    numero = register_problem(ctx.channel.id, problem)
    embed = build_problem_embed(problem, numero)
    embed.set_footer(text="Gerado por IA (Groq) • Use !resposta para revelar a solução.")
    await ctx.send(embed=embed)

    if not sincronizado:
        # Só avisa explicitamente quando algo deu errado — quando funciona,
        # não precisa poluir o canal com mais uma mensagem de confirmação.
        await ctx.send(status_msg)


@bot.command(name="resposta")
async def resposta(ctx: commands.Context, numero: int = None):
    """Revela a resposta do último problema (!resposta) ou de um problema específico (!resposta <id>)."""
    if numero is None:
        problem = last_problem_by_channel.get(ctx.channel.id)
    else:
        problem = problem_history_by_channel.get(ctx.channel.id, {}).get(numero)

    if not problem:
        await ctx.send(
            "Não encontrei esse problema neste canal. "
            "Use `!problema` para gerar um novo, ou confira se o número está certo "
            "(a numeração reinicia sempre que o bot é reiniciado)."
        )
        return

    # Tenta separar "valor final" de "explicação de como chegar nele" de duas formas:
    # 1) formato "valor (explicação)" — comum nos problemas do banco fixo
    # 2) primeira frase como conclusão + o resto como explicação — usado quando
    #    a resposta não vem entre parênteses (ex: respostas geradas por IA)
    resposta_formatada = formatar_matematica(problem["answer"]).strip()
    match = re.match(r"^(.*?)\s*\((.*)\)$", resposta_formatada, re.DOTALL)
    if match:
        valor, explicacao = match.group(1).strip(), match.group(2).strip()
    else:
        partes = re.split(r"(?<=[.;])\s+(?=[A-ZÀ-Ú0-9])", resposta_formatada, maxsplit=1)
        if len(partes) == 2:
            valor, explicacao = partes[0].strip().rstrip(".;"), partes[1].strip()
        else:
            valor, explicacao = resposta_formatada, ""

    if explicacao:
        passos = formatar_passos(explicacao)
        descricao = f"**🎯 Resposta:** {valor}\n\n**📝 Como chegar lá:**\n{passos}"
    else:
        descricao = resposta_formatada

    estilo = get_dificuldade_estilo(problem["difficulty"])
    titulo = f"✅ Resposta do Problema #{numero}" if numero is not None else "✅ Resposta"
    embed = discord.Embed(title=titulo, description=descricao, color=estilo["cor"])
    embed.set_footer(text=padronizar_assunto(problem["topic"]))
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina DISCORD_TOKEN no arquivo .env antes de rodar o bot.")
    if not CHANNEL_ID:
        raise SystemExit("Defina CHANNEL_ID no arquivo .env antes de rodar o bot.")
    bot.run(TOKEN)
