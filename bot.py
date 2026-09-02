"""
Bot de Discord que posta um problema de matemática diariamente
no canal configurado, e permite ver a resposta e pedir um problema extra.
"""

import os
import json
import random
import logging
from datetime import time, timezone, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ------------------------------------------------------------------
# Configuração
# ------------------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

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

# Guarda o último problema postado (por canal) para o comando !resposta funcionar
last_problem_by_channel: dict[int, dict] = {}


def load_problems() -> list[dict]:
    with open(PROBLEMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_problem_embed(problem: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🧮 Problema de Matemática do Dia",
        description=problem["question"],
        color=discord.Color.blue(),
    )
    embed.add_field(name="Dificuldade", value=problem["difficulty"].capitalize(), inline=True)
    embed.add_field(name="Assunto", value=problem["topic"], inline=True)
    embed.set_footer(text="Use !resposta para revelar a solução quando quiser tentar depois de pensar.")
    image_url = problem.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


async def post_daily_problem(channel: discord.abc.Messageable):
    problems = load_problems()
    problem = random.choice(problems)
    last_problem_by_channel[channel.id] = problem
    await channel.send(embed=build_problem_embed(problem))


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
async def problema_manual(ctx: commands.Context):
    """Posta um problema novo na hora, sob demanda (!problema)."""
    await post_daily_problem(ctx.channel)


@bot.command(name="resposta")
async def resposta(ctx: commands.Context):
    """Revela a resposta do último problema postado neste canal (!resposta)."""
    problem = last_problem_by_channel.get(ctx.channel.id)
    if not problem:
        await ctx.send("Ainda não há nenhum problema postado neste canal. Use `!problema` para gerar um.")
        return
    embed = discord.Embed(
        title="✅ Resposta",
        description=problem["answer"],
        color=discord.Color.green(),
    )
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina DISCORD_TOKEN no arquivo .env antes de rodar o bot.")
    if not CHANNEL_ID:
        raise SystemExit("Defina CHANNEL_ID no arquivo .env antes de rodar o bot.")
    bot.run(TOKEN)
