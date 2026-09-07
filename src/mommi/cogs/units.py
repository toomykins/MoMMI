from __future__ import annotations

import logging
import math
import re
from typing import Any

import discord
from discord import app_commands

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

LOGGER = logging.getLogger(__name__)


SAFE_EXPR_RE = re.compile(r"^[0-9a-zA-Z_.,\s*/^+\-°Ωµ%()]+$")
MAX_INPUT = 100


TEMPERATURE_ALIASES = {
    "c": "degC",
    "celsius": "degC",
    "centigrade": "degC",
    "f": "degF",
    "fahrenheit": "degF",
    "k": "kelvin",
    "kelvin": "kelvin",
    "r": "degR",
    "rankine": "degR",
}


QUANTITY_RE = re.compile(r"^\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*(.*)$")


SIGNIFICANT_DIGITS = 6

ZERO_EPSILON = 1e-10

_registry: Any = None


def format_magnitude(value: float, digits: int = SIGNIFICANT_DIGITS) -> str:
    if not math.isfinite(value):
        return str(value)

    if value == 0:
        rounded = 0.0
    else:
        exponent = math.floor(math.log10(abs(value)))
        rounded = round(value, -exponent + (digits - 1))
        if abs(rounded) < ZERO_EPSILON:
            rounded = 0.0


    if rounded == int(rounded) and abs(rounded) < 1e15:
        return f"{int(rounded):,}"
    return f"{rounded:,}"


def registry() -> Any:
    global _registry
    if _registry is None:
        import pint


        _registry = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    return _registry


def _variants(text: str, is_quantity: bool) -> list[str]:
    if is_quantity:
        match = QUANTITY_RE.match(text)
        if match is None:
            return [text]
        magnitude, unit = match.group(1), match.group(2).strip()
        return [f"{magnitude} {u}" for u in _unit_variants(unit)]
    return _unit_variants(text)


def _unit_variants(unit: str) -> list[str]:
    seen = [unit]
    for candidate in (TEMPERATURE_ALIASES.get(unit.lower()), unit.lower()):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def convert(source: str, target: str) -> str:
    source, target = source.strip(), target.strip()
    if len(source) > MAX_INPUT or len(target) > MAX_INPUT:
        raise ValueError("That's an awfully long unit.")
    if not SAFE_EXPR_RE.match(source) or not SAFE_EXPR_RE.match(target):
        raise ValueError("That doesn't look like a unit.")

    import pint

    ureg = registry()
    parse_error: Exception | None = None
    convert_error: Exception | None = None

    for source_try in _variants(source, is_quantity=True):
        try:
            quantity = ureg.Quantity(source_try)
        except (pint.PintError, ValueError, TypeError, AttributeError) as e:
            parse_error = e
            continue

        for target_try in _variants(target, is_quantity=False):
            try:
                result = quantity.to(target_try)
            except (pint.PintError, ValueError, TypeError, AttributeError) as e:
                convert_error = e
                continue

            return f"{format_magnitude(float(result.magnitude))} {result.units:~P}"

    if convert_error is not None:
        if isinstance(convert_error, pint.DimensionalityError):


            raise ValueError(f"{convert_error}. Those aren't the same kind of thing.")
        raise ValueError(f"I don't understand `{target}`.")
    raise ValueError(f"I don't understand `{source}`.") from parse_error


class Units(MoMMICog):
    @regex_command(
        "unit",
        r"(?:unit\s+)?`?([\d.,]+\s*[^`]+?)`?\s+(?:as|to|in)\s+`?([^`\s]+)`?\s*$",
        help_topic="units",
    )
    async def unit(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        try:
            await ctx.send(convert(match.group(1), match.group(2)))
        except ValueError as e:
            await ctx.send(str(e).replace("*", "\\*"))
        except Exception:
            LOGGER.exception("Unit conversion blew up on %r -> %r.", match.group(1), match.group(2))
            await ctx.send("That broke something. Congratulations.")

    @app_commands.command(name="convert", description="Convert between units.")
    @app_commands.describe(quantity="What to convert, e.g. '10 mph'", to="Target unit, e.g. 'km/h'")
    async def slash_convert(self, interaction: discord.Interaction, quantity: str, to: str) -> None:
        try:
            await interaction.response.send_message(convert(quantity, to))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    def help_articles(self) -> dict[str, str]:
        return {
            "units": (
                "`@MoMMI 10 mph as km/h`. Also accepts `to` and `in` instead of `as`.\n\n"
                "Anything Pint knows works, which is essentially everything: "
                "`2.5 kWh to joule`, `98.6 degF as degC`, `1 parsec in km`.\n"
                "Compound units are fine too: `9.81 m/s^2 as ft/s^2`."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Units(bot))
