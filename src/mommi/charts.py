from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

LOGGER = logging.getLogger(__name__)


SERIES_COLORS = [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
]

SURFACE = "#1a1a19"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
GRID = "#383835"


MAX_SERIES = len(SERIES_COLORS)


TARGET_POINTS = 240


def downsample(
    points: list[tuple[float, int]], target: int = TARGET_POINTS
) -> list[tuple[float, float]]:
    if len(points) <= target:
        return [(ts, float(value)) for ts, value in points]

    start, end = points[0][0], points[-1][0]
    span = end - start
    if span <= 0:
        return [(start, float(points[-1][1]))]

    width = span / target
    buckets: dict[int, list[float]] = {}
    for ts, value in points:
        index = min(int((ts - start) / width), target - 1)
        buckets.setdefault(index, []).append(float(value))

    return [
        (start + (index + 0.5) * width, sum(values) / len(values))
        for index, values in sorted(buckets.items())
    ]


class ChartUnavailable(RuntimeError):
    pass


def render_player_chart(
    series: dict[str, list[tuple[float, int]]],
    title: str,
) -> bytes:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError as e:  # pragma: no cover
        raise ChartUnavailable(
            "matplotlib isn't installed. Install MoMMI with the `charts` extra."
        ) from e

    figure, axes = plt.subplots(figsize=(10, 4.5), dpi=140)
    figure.patch.set_facecolor(SURFACE)
    axes.set_facecolor(SURFACE)

    plotted = 0
    for index, (label, points) in enumerate(sorted(series.items())):
        if not points or plotted >= MAX_SERIES:
            continue
        colour = SERIES_COLORS[index % len(SERIES_COLORS)]
        reduced = downsample(points)
        times = [datetime.fromtimestamp(ts, timezone.utc) for ts, _ in reduced]
        values = [value for _, value in reduced]

        latest = points[-1][1]


        axes.plot(times, values, color=colour, linewidth=2, label=label, solid_capstyle="round")
        axes.fill_between(times, values, color=colour, alpha=0.12)


        axes.annotate(
            f" {label}: {latest}",
            xy=(times[-1], values[-1]),
            color=colour,
            fontsize=9,
            va="center",
            fontweight="bold",
        )
        plotted += 1

    if plotted == 0:
        plt.close(figure)
        raise ValueError("no data to plot")

    axes.set_title(title, color=TEXT_PRIMARY, fontsize=13, loc="left", pad=12)
    axes.set_ylabel("Players", color=TEXT_SECONDARY, fontsize=10)
    axes.set_ylim(bottom=0)

    axes.margins(x=0.02)


    axes.grid(True, axis="y", color=GRID, linewidth=0.8, alpha=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID)
    axes.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)


    locator = mdates.AutoDateLocator(tz=timezone.utc, minticks=4, maxticks=9)
    formatter = mdates.ConciseDateFormatter(locator, tz=timezone.utc)
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(formatter)
    axes.xaxis.get_offset_text().set_color(TEXT_SECONDARY)
    axes.xaxis.get_offset_text().set_fontsize(9)


    if plotted > 1:


        legend = axes.legend(
            loc="upper left",
            bbox_to_anchor=(0, -0.12),
            frameon=False,
            fontsize=9,
            ncol=min(plotted, 4),
            handlelength=1.6,
        )
        for text in legend.get_texts():
            text.set_color(TEXT_SECONDARY)

    figure.text(
        0.01,
        0.02,
        "times UTC",
        color=TEXT_SECONDARY,
        fontsize=8,
        ha="left",
        alpha=0.7,
    )
    figure.tight_layout()

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(figure)
    return buffer.getvalue()


def render_hourly_chart(by_hour: dict[int, float], title: str) -> bytes:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:  # pragma: no cover
        raise ChartUnavailable(
            "matplotlib isn't installed. Install MoMMI with the `charts` extra."
        ) from e

    if not by_hour:
        raise ValueError("no data to plot")

    hours = list(range(24))
    values = [by_hour.get(hour, 0.0) for hour in hours]

    figure, axes = plt.subplots(figsize=(10, 3.6), dpi=140)
    figure.patch.set_facecolor(SURFACE)
    axes.set_facecolor(SURFACE)


    axes.bar(hours, values, color=SERIES_COLORS[0], width=0.78, zorder=3)

    peak_hour = max(by_hour, key=lambda h: by_hour[h])
    axes.annotate(
        f"busiest: {peak_hour:02d}:00 ({by_hour[peak_hour]:.0f})",
        xy=(peak_hour, by_hour[peak_hour]),
        xytext=(0, 6),
        textcoords="offset points",
        ha="center",
        color=TEXT_PRIMARY,
        fontsize=9,
        fontweight="bold",
    )

    axes.set_title(title, color=TEXT_PRIMARY, fontsize=12, loc="left", pad=10)
    axes.set_ylabel("Players", color=TEXT_SECONDARY, fontsize=9)
    axes.set_ylim(bottom=0, top=max(values) * 1.18 if max(values) else 1)
    axes.set_xticks(range(0, 24, 3))
    axes.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 3)])
    axes.set_xlim(-0.7, 23.7)

    axes.grid(True, axis="y", color=GRID, linewidth=0.8, alpha=0.8, zorder=0)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID)
    axes.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)

    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(figure)
    return buffer.getvalue()
