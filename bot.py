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

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "problems.json")

POST_HOUR = int(os.getenv("POST_HOUR", "8"))
POST_MINUTE = int(os.getenv("POST_MINUTE", "0"))
BRASILIA_TZ = timezone(timedelta(hours=-3))
POST_TIME = time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=BRASILIA_TZ)

PROBLEMS_FILE = os.path.join(os.path.dirname(__file__), "problems.json")
CHANNEL_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "channel_history.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("math-bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_problems() -> list[dict]:
    if not os.path.exists(PROBLEMS_FILE):
        return []
    with open(PROBLEMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_channel_history() -> dict:
    if not os.path.exists(CHANNEL_HISTORY_FILE):
        return {}
    try:
        with open(CHANNEL_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_channel_history(data: dict) -> None:
    try:
        with open(CHANNEL_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("Erro ao persistir histórico dos canais: %s", exc)


def register_problem(channel_id: int, problem: dict) -> int:
    channel_key = str(channel_id)
    history = load_channel_history()
    channel_data = history.setdefault(channel_key, {"last_id": 0, "problems": {}})
    
    existing_ids = [int(k) for k in channel_data.get("problems", {}).keys()]
    current_max = max(existing_ids) if existing_ids else channel_data.get("last_id", 0)
    next_id = current_max + 1

    channel_data["last_id"] = next_id
    channel_data["problems"][str(next_id)] = problem
    save_channel_history(history)
    return next_id


def normalizar_dificuldade(valor) -> float:
    try:
        num = float(str(valor).replace(",", ".").split("/")[0].strip())
        return max(0.0, min(10.0, round(num, 1)))
    except (ValueError, TypeError):
        return 5.0


def generate_ai_problem(tema: str, nivel_descricao: str | None = None) -> dict:
    if not GROQ_API_KEY:
        raise RuntimeError("A geração por IA não está configurada. Defina GROQ_API_KEY no .env do bot.")

    instrucao_nivel = (
        f'\n- O nível de dificuldade deve ser: {nivel_descricao}'
        if nivel_descricao
        else "\n- Atribua uma dificuldade realista de 0.0 a 10.0 adequada ao tema."
    )

    prompt = f"""Crie UM problema de matemática original em português sobre o tema "{tema}".{instrucao_nivel}

IMPORTANTE sobre a formatação matemática:
- NUNCA use comandos LaTeX crus (como \\ge, \\le, \\frac, \\cdot, \\times, chaves {{}}, cifrões $, etc.).
- Para expoentes use potências legíveis como x^2 ou 2^10.
- Para índices use sublinhado simples como a_n ou a_1.
- Use símbolos normais: ≥ ≤ ≠ × ÷ π √ ±.
- DESTAQUE TODAS AS EQUAÇÕES, EXPRESSÕES E FÓRMULAS PRINCIPAIS: coloque-as em linhas separadas e em negrito (exemplo: **x² - 6x + 8 = 0** ou **f(x) = 2x + 1**) para destacá-las do texto descritivo.
- Separe o enunciado em parágrafos claros: contexto, equações/dados e a pergunta.

Responda APENAS com um JSON válido, sem markdown ou crases, no formato exato:
{{"question": "enunciado com equações destacadas em negrito e em linhas separadas", "answer": "resposta final e explicação do passo a passo com fórmulas destacadas", "difficulty": 7.5, "topic": "{tema}"}}"""

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
    except Exception as exc:
        logger.exception("Erro ao chamar a API da Groq")
        raise RuntimeError(f"Não consegui falar com a IA agora ({exc}).") from exc

    texto = re.sub(r"^```(json)?|```$", "", texto, flags=re.MULTILINE).strip()

    try:
        problem = json.loads(texto)
    except json.JSONDecodeError as exc:
        logger.error("Resposta da IA não é um JSON válido: %s", texto)
        raise RuntimeError("A IA respondeu em um formato inesperado. Tente de novo.") from exc

    for campo in ("question", "answer", "difficulty", "topic"):
        if campo not in problem:
            raise RuntimeError("A IA não retornou todos os campos esperados. Tente de novo.")

    for campo in ("question", "answer"):
        problem[campo] = str(problem[campo]).replace("\\n", "\n").strip()

    problem["difficulty"] = normalizar_dificuldade(problem["difficulty"])
    problem["topic"] = str(problem.get("topic", tema)).strip().title()

    return problem


def github_configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def push_problems_to_github(problems: list[dict]) -> None:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    conteudo = json.dumps(problems, ensure_ascii=False, indent=2)
    conteudo_b64 = base64.b64encode(conteudo.encode("utf-8")).decode("utf-8")

    tentativas_maximas = 2
    for tentativa in range(1, tentativas_maximas + 1):
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
            time_module.sleep(1)
            continue

        put_resp.raise_for_status()
        return


def save_generated_problem(problem: dict) -> tuple[bool, str]:
    problems = load_problems()
    problems.append(problem)
    with open(PROBLEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(problems, f, ensure_ascii=False, indent=2)

    if not github_configured():
        return False, (
            "⚠️ Salvei no banco local, mas a sincronização com o GitHub não está "
            "configurada (faltam GITHUB_TOKEN/GITHUB_REPO)."
        )

    try:
        push_problems_to_github(problems)
        return True, "✅ Problema salvo no banco e sincronizado com o GitHub."
    except Exception as exc:
        logger.exception("Erro ao sincronizar problems.json com o GitHub")
        return False, f"⚠️ Salvei no banco local, mas não consegui sincronizar com o GitHub ({exc})."


def _normalizar(texto: str) -> str:
    substituicoes = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeiooouc" + "AAAAEEIOOOUC".lower())
    return texto.translate(substituicoes).lower()


def get_topico_emoji(topico: str) -> str:
    t = _normalizar(topico)
    if any(k in t for k in ["geometr", "triang", "angulo", "circulo"]):
        return "📐"
    if any(k in t for k in ["probab", "combinat", "arranjo", "dado"]):
        return "🎲"
    if any(k in t for k in ["algebra", "equac", "polinom", "funcao", "matriz"]):
        return "📊"
    if any(k in t for k in ["aritmet", "numero", "primo", "divis"]):
        return "🔢"
    return "🧮"


def get_dificuldade_estilo(dificuldade) -> dict:
    nota = normalizar_dificuldade(dificuldade)
    if nota < 4.0:
        return {"emoji": "🟢", "cor": discord.Color.green(), "rotulo": f"{nota:.1f}/10.0 (Fácil)"}
    elif nota < 7.0:
        return {"emoji": "🟡", "cor": discord.Color.gold(), "rotulo": f"{nota:.1f}/10.0 (Médio)"}
    elif nota < 8.5:
        return {"emoji": "🟠", "cor": discord.Color.orange(), "rotulo": f"{nota:.1f}/10.0 (Difícil)"}
    else:
        return {"emoji": "🔴", "cor": discord.Color.red(), "rotulo": f"{nota:.1f}/10.0 (Muito Difícil / Olímpico)"}


_SUPERSCRITO = str.maketrans({
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "n": "ⁿ", "i": "ⁱ",
})

_SUBSCRITO = str.maketrans({
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "n": "ₙ", "i": "ᵢ", "a": "ₐ",
    "k": "ₖ", "m": "ₘ", "j": "ⱼ", "x": "ₓ",
})

_LATEX_COMANDOS = [
    (r"\\ge\b", "≥"), (r"\\geq\b", "≥"),
    (r"\\le\b", "≤"), (r"\\leq\b", "≤"),
    (r"\\neq\b", "≠"), (r"\\ne\b", "≠"),
    (r"\\times\b", "×"), (r"\\cdot\b", "·"),
    (r"\\pm\b", "±"), (r"\\infty\b", "∞"),
    (r"\\pi\b", "π"), (r"\\sqrt", "√"),
]


def formatar_matematica(texto: str) -> str:
    for padrao, substituto in _LATEX_COMANDOS:
        texto = re.sub(padrao, substituto, texto)

    texto = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1 / \2)", texto)
    texto = re.sub(r"\^\{([\d+\-ni]+)\}", lambda m: m.group(1).translate(_SUPERSCRITO), texto)
    texto = re.sub(r"\^\(([\d+\-ni]+)\)", lambda m: m.group(1).translate(_SUPERSCRITO), texto)
    texto = re.sub(r"\^([\d+\-ni]+)", lambda m: m.group(1).translate(_SUPERSCRITO), texto)

    texto = re.sub(r"_\{([\w+\-]+)\}", lambda m: m.group(1).translate(_SUBSCRITO), texto)
    texto = re.sub(r"_\(([\w+\-]+)\)", lambda m: m.group(1).translate(_SUBSCRITO), texto)
    texto = re.sub(r"_([a-zA-Z0-9]+)", lambda m: m.group(1).translate(_SUBSCRITO), texto)
    texto = re.sub(r"sqrt\(([^()]+)\)", r"√(\1)", texto, flags=re.IGNORECASE)

    linhas = []
    for linha in texto.splitlines():
        linha_limpa = linha.strip()
        if not linha_limpa:
            linhas.append("")
            continue

        eh_equacao_isolada = (
            any(op in linha_limpa for op in ["=", "≥", "≤", "<", ">", "≠"])
            and len(linha_limpa) <= 80
            and not linha_limpa.endswith(".")
        )

        if eh_equacao_isolada and not (linha_limpa.startswith("**") and linha_limpa.endswith("**")):
            linha_limpa = f"**{linha_limpa}**"

        linhas.append(linha_limpa)

    return "\n".join(linhas)


def formatar_passos(explicacao: str) -> str:
    partes = re.split(r"(?<=[.;])\s+(?=[A-ZÀ-Ú0-9])", explicacao.strip())
    partes = [p.strip().rstrip(".;").strip() for p in partes if p.strip()]

    if len(partes) <= 1:
        return explicacao

    return "\n\n".join(f"**{i}.** {parte}." for i, parte in enumerate(partes, start=1))


def build_problem_embed(problem: dict, numero: int | None = None) -> discord.Embed:
    topico = str(problem.get("topic", "Matemática"))
    topico_emoji = get_topico_emoji(topico)
    estilo = get_dificuldade_estilo(problem["difficulty"])

    titulo = f"{topico_emoji} Problema #{numero}" if numero is not None else f"{topico_emoji} Desafio de Matemática"

    enunciado = formatar_matematica(problem["question"])
    linhas_formatadas = []
    for linha in enunciado.splitlines():
        if linha.strip():
            linhas_formatadas.append(f"> {linha}")
        else:
            linhas_formatadas.append(">")
    enunciado_formatado = "\n".join(linhas_formatadas)

    embed = discord.Embed(
        title=titulo,
        description=enunciado_formatado,
        color=estilo["cor"],
    )
    embed.add_field(
        name="Dificuldade",
        value=f"{estilo['emoji']} **{estilo['rotulo']}**",
        inline=True,
    )
    embed.add_field(name="Assunto", value=f"**{topico}**", inline=True)
    embed.set_footer(text="Use !resposta para conferir a resolução.")
    image_url = problem.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


TEMAS_SUGERIDOS = [
    "Geometria Plana",
    "Álgebra e Polinômios",
    "Teoria dos Números",
    "Análise Combinatória",
    "Probabilidade",
    "Trigonometria",
    "Geometria Espacial",
    "Sequências e Progressões",
    "Equações Diofantinas",
    "Funções e Gráficos"
]


def escolher_problema_diario() -> dict:
    if GROQ_API_KEY:
        tema = random.choice(TEMAS_SUGERIDOS)
        try:
            problem = generate_ai_problem(
                tema,
                nivel_descricao="Nível muito avançado (OBMEP Fase 3 ou vestibular do ITA), nota de dificuldade entre 8.5 e 10.0"
            )
            sincronizado, status_msg = save_generated_problem(problem)
            if not sincronizado:
                logger.warning("Problema diário não sincronizado: %s", status_msg)
            return problem
        except RuntimeError:
            logger.exception("Falha ao gerar problema diário via IA, recorrendo ao banco local.")

    problems = load_problems()
    if problems:
        dificeis = [p for p in problems if normalizar_dificuldade(p.get("difficulty", 0)) >= 7.0]
        return random.choice(dificeis or problems)

    return {
        "question": "Resolva a equação nos reais:\n\n**x² - 5x + 6 = 0**",
        "answer": "x = 2 ou x = 3 (Fatorando: (x - 2)(x - 3) = 0).",
        "difficulty": 3.0,
        "topic": "Álgebra"
    }


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
        logger.warning("CHANNEL_ID inválido ou inacessível.")
        return
    await post_daily_problem(channel)


@bot.command(name="problema")
async def problema_manual(ctx: commands.Context, *, tema: str = None):
    tema_escolhido = tema.strip() if tema else random.choice(TEMAS_SUGERIDOS)

    async with ctx.typing():
        try:
            problem = await asyncio.to_thread(generate_ai_problem, tema_escolhido)
            await asyncio.to_thread(save_generated_problem, problem)
        except RuntimeError:
            problems = load_problems()
            if tema:
                filtrados = [p for p in problems if _normalizar(tema) in _normalizar(str(p.get("topic", "")))]
            else:
                filtrados = problems

            if not filtrados:
                await ctx.send(f"Não consegui gerar uma questão com a IA e não há problemas salvos sobre '{tema_escolhido}'.")
                return
            problem = random.choice(filtrados)

    numero = register_problem(ctx.channel.id, problem)
    await ctx.send(embed=build_problem_embed(problem, numero))


@bot.command(name="gerar")
async def gerar_problema_ia(ctx: commands.Context, *, tema: str = None):
    if not tema:
        await ctx.send("Informe o tema desejado. Exemplo: `!gerar trigonometria avançada` ou `!gerar matrizes`")
        return

    async with ctx.typing():
        try:
            problem = await asyncio.to_thread(generate_ai_problem, tema.strip())
        except RuntimeError as exc:
            await ctx.send(f"⚠️ {exc}")
            return

        sincronizado, status_msg = await asyncio.to_thread(save_generated_problem, problem)

    numero = register_problem(ctx.channel.id, problem)
    embed = build_problem_embed(problem, numero)
    embed.set_footer(text="Gerado sob demanda • Use !resposta para conferir a resolução.")
    await ctx.send(embed=embed)

    if not sincronizado:
        await ctx.send(status_msg)


@bot.command(name="resposta")
async def resposta(ctx: commands.Context, numero: int = None):
    history = load_channel_history()
    channel_data = history.get(str(ctx.channel.id), {})
    problems_map = channel_data.get("problems", {})

    if numero is None:
        target_id = channel_data.get("last_id")
        if not target_id:
            await ctx.send("Nenhum problema foi registrado neste canal ainda. Use `!problema` ou `!gerar <tema>`.")
            return
        problem = problems_map.get(str(target_id))
        numero_exibicao = target_id
    else:
        problem = problems_map.get(str(numero))
        numero_exibicao = numero

    if not problem:
        await ctx.send(f"Não encontrei o problema #{numero} neste canal.")
        return

    resposta_formatada = formatar_matematica(str(problem["answer"])).strip()
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
        descricao = f"**🎯 Resposta:**\n{valor}\n\n**📝 Como chegar lá:**\n{passos}"
    else:
        descricao = f"**🎯 Resposta:**\n{resposta_formatada}"

    estilo = get_dificuldade_estilo(problem["difficulty"])
    embed = discord.Embed(
        title=f"✅ Resposta do Problema #{numero_exibicao}",
        description=descricao,
        color=estilo["cor"]
    )
    embed.set_footer(text=f"Tema: {problem.get('topic', 'Matemática')} • Dificuldade: {estilo['rotulo']}")
    await ctx.send(embed=embed)


@bot.command(name="ajuda")
async def ajuda(ctx: commands.Context):
    embed = discord.Embed(
        title="📖 Comandos do Bot de Matemática",
        description="Comandos disponíveis para estudo e resolução de desafios:",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🧮 !problema",
        value="Gera um problema sobre um tema aleatório.",
        inline=False,
    )
    embed.add_field(
        name="🎯 !problema <tema>",
        value="Gera um problema com IA sobre qualquer assunto informado (ex: `!problema matrizes`).",
        inline=False,
    )
    embed.add_field(
        name="🤖 !gerar <tema>",
        value="Cria um desafio específico sob demanda (ex: `!gerar geometria analítica`).",
        inline=False,
    )
    embed.add_field(
        name="✅ !resposta",
        value="Mostra a resposta e a resolução do último problema postado neste canal.",
        inline=False,
    )
    embed.add_field(
        name="🔢 !resposta <id>",
        value="Mostra a resolução de um problema anterior pelo ID contínuo do canal (ex: `!resposta 4`).",
        inline=False,
    )
    embed.set_footer(text=f"Desafio diário postado automaticamente às {POST_HOUR:02d}:{POST_MINUTE:02d}.")
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina DISCORD_TOKEN no arquivo .env antes de rodar o bot.")
    if not CHANNEL_ID:
        raise SystemExit("Defina CHANNEL_ID no arquivo .env antes de rodar o bot.")
    bot.run(TOKEN)