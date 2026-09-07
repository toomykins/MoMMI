from __future__ import annotations

import random
import re

import discord
from discord import app_commands

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

MAX_DICE = 100
MAX_SIDES = 1_000_000


DICE_CAP_MSG = f"Too many dice. Max is {MAX_DICE}."

EIGHTBALL = [
    "It is certain",
    "It is decidedly so",
    "Without a doubt",
    "Yes, definitely",
    "You may rely on it",
    "As I see it, yes",
    "Most likely",
    "Outlook: Positive",
    "Yes",
    "Signs point to: Yes",
    "Reply hazy, try again",
    "Ask again later",
    "Better to not tell you right now",
    "Cannot predict now",
    "Concentrate, then ask again",
    "Do not count on it",
    "My reply is: no",
    "My sources say: no",
    "Outlook: Negative",
    "Very doubtful",
]


def roll_dice(count: int, sides: int, modifier: int = 0) -> tuple[list[int], int]:
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls) + modifier


def format_roll(rolls: list[int], total: int, modifier: int) -> str:
    body = ", ".join(str(r) for r in rolls)
    if modifier:
        body += f" + {modifier}"
    return f"Results: {body} = {total}"


def pick_from(raw: str) -> str | None:
    choices = [c.strip() for c in raw.split(",") if c.strip()]
    if len(choices) < 2:
        return None
    return random.choice(choices)


class Chance(MoMMICog):
    @regex_command("pick", r"(?:pick|choose)\s*\((.*?)\)", help_topic="pick")
    async def pick(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        choice = pick_from(match.group(1))
        if choice is None:
            await ctx.send("You gotta provide at least 2 options.")
            return
        await ctx.send(f"**{choice}**")

    @regex_command("roll", r"(\d+)d(\d+)(?:\s*\+\s*(\d+))?", help_topic="dice")
    async def roll(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        count, sides = int(match.group(1)), int(match.group(2))
        modifier = int(match.group(3)) if match.group(3) else 0

        if count < 1 or sides < 1:
            await ctx.send("You need at least one die with at least one side.")
            return
        if count > MAX_DICE:
            await ctx.send(DICE_CAP_MSG)
            return
        if sides > MAX_SIDES:
            await ctx.send(f"Max die is d{MAX_SIDES}.")
            return

        rolls, total = roll_dice(count, sides, modifier)
        await ctx.send(format_roll(rolls, total, modifier))

    @regex_command("rand", r"rand\s*(-?\d+)\s*(-?\d+)")
    async def rand(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        await ctx.send(str(random.randint(low, high)))

    @regex_command("magic8ball", r"(?:magic8ball|magic)\b", help_topic="magic8ball")
    async def magic8ball(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await ctx.send(random.choice(EIGHTBALL))


    @app_commands.command(name="roll", description="Roll some dice, e.g. 2d20+3.")
    @app_commands.describe(dice="Dice in NdN(+N) form, such as 1d20 or 2d6+3")
    async def slash_roll(self, interaction: discord.Interaction, dice: str) -> None:
        match = re.fullmatch(r"\s*(\d+)d(\d+)(?:\s*\+\s*(\d+))?\s*", dice, re.IGNORECASE)
        if match is None:
            await interaction.response.send_message(
                "That's not dice. Try something like `2d6+1`.", ephemeral=True
            )
            return
        count, sides = int(match.group(1)), int(match.group(2))
        modifier = int(match.group(3)) if match.group(3) else 0
        if count > MAX_DICE:
            await interaction.response.send_message(DICE_CAP_MSG, ephemeral=True)
            return
        if not (1 <= count) or not (1 <= sides <= MAX_SIDES):
            await interaction.response.send_message(
                f"One to {MAX_DICE} dice, up to d{MAX_SIDES}.", ephemeral=True
            )
            return
        rolls, total = roll_dice(count, sides, modifier)
        await interaction.response.send_message(format_roll(rolls, total, modifier))

    @app_commands.command(name="pick", description="Pick one of several comma-separated options.")
    @app_commands.describe(options="Comma-separated options, e.g. 'yes, no, maybe'")
    async def slash_pick(self, interaction: discord.Interaction, options: str) -> None:
        choice = pick_from(options)
        if choice is None:
            await interaction.response.send_message(
                "You gotta provide at least 2 comma-separated options.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"**{choice}**")

    @app_commands.command(name="magic8ball", description="Consult the magic 8-ball.")
    @app_commands.describe(question="What you'd like to know")
    async def slash_magic8ball(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.send_message(f"> {question}\n{random.choice(EIGHTBALL)}")

    def help_articles(self) -> dict[str, str]:
        return {
            "dice": (
                "The party enters the AI upload.\n"
                "The room's power systems are completely off. At the back of the room is a hole "
                "into the core, molten out of the reinforced wall.\n\n"
                "*I walk up to the room's APC and see if the APC still works.*\n\n"
                "Everybody roll a dexterity saving throw.\n\n"
                "*@MoMMI 1d20+0*\n\n"
                "*Results: 1 = 1*"
            ),
            "magic8ball": (
                "Unable to make important project decisions responsibly?\n"
                "Need some reliable help from our lord and saviour RNGesus?\n\n"
                "Simple, just run `@MoMMI magic 'Do I delete the Discord server?'` and let NT's "
                "latest proven MoMMI Random Number Generator Technology™ decide for you.\n\n"
                "*Nanotrasen is not liable for any damages caused - material, bodily or "
                "psychologically - as a result of poor decision making as a result of the "
                "responses from this feature.*"
            ),
            "pick": (
                "Man can you believe this? People actually want to do *fair* 50/50 picks between "
                "things? Kids these days.\n\n"
                "Fine, just run `@MoMMI pick(a,b,c)` with as many comma separated values as you "
                "need. Normies."
            ),
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Chance(bot))
