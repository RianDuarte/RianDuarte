#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter

OWNER = os.getenv("GITHUB_USERNAME", "RianDuarte")
OUTPUT = Path("assets/snake/appex-cobra.gif")
API_URL = "https://api.github.com/graphql"


@dataclass(frozen=True)
class Cell:
    week: int
    weekday: int
    date: str
    count: int


def fetch_graphql_calendar(username: str, token: str) -> list[Cell]:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
                weekday
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"login": username}}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "cobra-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "errors" in data:
        raise RuntimeError(f"GraphQL returned errors: {data['errors']}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    cells: list[Cell] = []
    for w_index, week in enumerate(weeks):
        for day in week["contributionDays"]:
            cells.append(Cell(w_index, int(day["weekday"]), day["date"], int(day["contributionCount"])))
    return cells


def fetch_public_calendar(username: str) -> list[Cell]:
    today = dt.date.today()
    one_year_ago = today - dt.timedelta(days=365)
    query = urllib.parse.urlencode({"from": one_year_ago.isoformat(), "to": today.isoformat()})
    url = f"https://github.com/users/{username}/contributions?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "cobra-generator"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        svg = resp.read().decode("utf-8", errors="ignore")

    pattern = re.compile(r"<td[^>]*class=\"ContributionCalendar-day\"[^>]*>", re.IGNORECASE)
    id_pattern = re.compile(r'id="contribution-day-component-(\d+)-(\d+)"', re.IGNORECASE)
    date_pattern = re.compile(r'data-date="(\d{4}-\d{2}-\d{2})"', re.IGNORECASE)
    level_pattern = re.compile(r'data-level="(\d+)"', re.IGNORECASE)
    level_to_count = {0: 0, 1: 1, 2: 3, 3: 6, 4: 10}
    cells: list[Cell] = []
    for match in pattern.finditer(svg):
        tag = match.group(0)
        id_match = id_pattern.search(tag)
        date_match = date_pattern.search(tag)
        level_match = level_pattern.search(tag)
        if not (id_match and date_match and level_match):
            continue
        weekday = int(id_match.group(1))
        week = int(id_match.group(2))
        level = int(level_match.group(1))
        count = level_to_count.get(level, max(0, level))
        cells.append(Cell(week, weekday, date_match.group(1), count))

    if not cells:
        raise RuntimeError("Unable to parse public contribution calendar.")
    return sorted(cells, key=lambda c: c.date)


def load_contributions(username: str, token: str | None) -> list[Cell]:
    if token:
        try:
            return fetch_graphql_calendar(username, token)
        except Exception as exc:
            print(f"GraphQL fetch failed, falling back to public calendar: {exc}")
    print("Using public contribution calendar fallback")
    return fetch_public_calendar(username)


def normalize_counts(cells: list[Cell]) -> dict[tuple[int, int], int]:
    grid: dict[tuple[int, int], int] = {}
    for c in cells:
        grid[(c.week, c.weekday)] = c.count
    return grid


def dense_path(cells: list[Cell]) -> list[tuple[int, int]]:
    ordered = sorted(cells, key=lambda c: c.date)
    active = [(c.week, c.weekday) for c in ordered if c.count > 0]
    if not active:
        active = [(c.week, c.weekday) for c in ordered]
    deduped: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pos in active:
        if pos not in seen:
            deduped.append(pos)
            seen.add(pos)
    return deduped


def interpolate_points(points: list[tuple[float, float]], steps: int) -> list[tuple[float, float]]:
    if len(points) == 1:
        return [points[0]] * steps
    interpolated: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        for t in range(steps):
            k = t / steps
            interpolated.append((x1 + (x2 - x1) * k, y1 + (y2 - y1) * k))
    interpolated.append(points[-1])
    return interpolated


def circle(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, int, int, int], outline=None, width=1):
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=outline, width=width)


def render_frames(cells: list[Cell], grid: dict[tuple[int, int], int], output: Path) -> list[Image.Image]:
    max_week = max(c.week for c in cells)
    width_cells = max_week + 1
    height_cells = 7

    cell = 20
    pad = 28
    board_w = width_cells * cell
    board_h = height_cells * cell
    width = board_w + pad * 2
    height = board_h + pad * 2

    path = dense_path(cells)
    pixel_path = [(pad + (w + 0.5) * cell, pad + (d + 0.5) * cell) for (w, d) in path]
    sampled = interpolate_points(pixel_path, steps=4)

    body_len = 18
    frame_count = min(max(120, len(sampled)), 260)
    if len(sampled) > frame_count:
        stride = len(sampled) / frame_count
        sampled = [sampled[int(i * stride)] for i in range(frame_count)]
    else:
        while len(sampled) < frame_count:
            sampled.extend(sampled)
        sampled = sampled[:frame_count]

    frames: list[Image.Image] = []
    heat = [0, 1, 2, 4, 7, 11, 16]
    food_points = [pos for pos, cnt in grid.items() if cnt > 0]

    history: list[tuple[float, float]] = []
    for i, (hx, hy) in enumerate(sampled):
        history.insert(0, (hx, hy))
        history = history[: body_len * 6]

        img = Image.new("RGBA", (width, height), (8, 6, 10, 255))
        draw = ImageDraw.Draw(img)

        for gy in range(height):
            v = gy / max(1, height - 1)
            color = (12 + int(12 * v), 8, 14 + int(20 * v), 255)
            draw.line((0, gy, width, gy), fill=color)

        for w in range(width_cells):
            for d in range(height_cells):
                x0 = pad + w * cell
                y0 = pad + d * cell
                count = grid.get((w, d), 0)
                lvl = 0
                for threshold in heat:
                    if count >= threshold:
                        lvl += 1
                tone = min(255, 30 + lvl * 26)
                fill = (30 + tone // 6, 8, 14 + tone // 3, 255)
                border = (60 + tone // 4, 20, 30 + tone // 3, 255)
                draw.rounded_rectangle((x0 + 2, y0 + 2, x0 + cell - 3, y0 + cell - 3), radius=5, fill=fill, outline=border, width=1)

        if food_points:
            for fw, fd in food_points[:: max(1, len(food_points) // 20)]:
                fx = pad + (fw + 0.5) * cell
                fy = pad + (fd + 0.5) * cell
                pulse = 0.5 + 0.5 * math.sin((i + fw * 2 + fd) * 0.25)
                r = 2.5 + pulse * 1.5
                circle(draw, fx, fy, r + 2, (255, 80, 40, 70))
                circle(draw, fx, fy, r, (255, 120, 70, 220), outline=(255, 200, 140, 255), width=1)

        for seg in range(body_len, 0, -1):
            idx = min(len(history) - 1, seg * 3)
            sx, sy = history[idx]
            n = seg / body_len
            r = 9.5 - n * 4.0
            shade = int(200 - n * 110)
            draw.ellipse((sx - r + 2, sy - r + 3, sx + r + 2, sy + r + 3), fill=(0, 0, 0, 80))
            circle(
                draw,
                sx,
                sy,
                r,
                fill=(30 + shade // 5, 80 + shade // 2, 35 + shade // 5, 255),
                outline=(14, 28, 16, 255),
                width=2,
            )
            circle(draw, sx - r * 0.25, sy - r * 0.3, r * 0.35, (140, 220, 120, 130))

        if len(sampled) > 1:
            nx, ny = sampled[(i + 1) % len(sampled)]
        else:
            nx, ny = hx + 1, hy
        angle = math.atan2(ny - hy, nx - hx)

        head_r = 11
        draw.ellipse((hx - head_r + 2, hy - head_r + 3, hx + head_r + 2, hy + head_r + 3), fill=(0, 0, 0, 90))
        circle(draw, hx, hy, head_r, (48, 170, 70, 255), outline=(8, 30, 12, 255), width=3)
        circle(draw, hx - head_r * 0.28, hy - head_r * 0.4, 4, (170, 245, 150, 180))

        eye_dx = math.cos(angle + math.pi / 2) * 4
        eye_dy = math.sin(angle + math.pi / 2) * 4
        forward_x = math.cos(angle) * 2
        forward_y = math.sin(angle) * 2
        ex1, ey1 = hx + eye_dx + forward_x, hy + eye_dy + forward_y
        ex2, ey2 = hx - eye_dx + forward_x, hy - eye_dy + forward_y
        circle(draw, ex1, ey1, 2.2, (255, 255, 255, 255), outline=(0, 0, 0, 255), width=1)
        circle(draw, ex2, ey2, 2.2, (255, 255, 255, 255), outline=(0, 0, 0, 255), width=1)
        circle(draw, ex1 + math.cos(angle) * 0.8, ey1 + math.sin(angle) * 0.8, 0.9, (0, 0, 0, 255))
        circle(draw, ex2 + math.cos(angle) * 0.8, ey2 + math.sin(angle) * 0.8, 0.9, (0, 0, 0, 255))

        tongue_len = 8 + 2 * math.sin(i * 0.4)
        tx = hx + math.cos(angle) * (head_r - 1)
        ty = hy + math.sin(angle) * (head_r - 1)
        tongue_tip_x = tx + math.cos(angle) * tongue_len
        tongue_tip_y = ty + math.sin(angle) * tongue_len
        draw.line((tx, ty, tongue_tip_x, tongue_tip_y), fill=(255, 70, 90, 255), width=2)
        fork_angle = angle + math.pi / 7
        draw.line(
            (tongue_tip_x, tongue_tip_y, tongue_tip_x + math.cos(fork_angle) * 4, tongue_tip_y + math.sin(fork_angle) * 4),
            fill=(255, 70, 90, 255),
            width=2,
        )
        draw.line(
            (tongue_tip_x, tongue_tip_y, tongue_tip_x + math.cos(angle - math.pi / 7) * 4, tongue_tip_y + math.sin(angle - math.pi / 7) * 4),
            fill=(255, 70, 90, 255),
            width=2,
        )

        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        circle(gd, hx, hy, head_r + 5, (120, 255, 160, 70))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=5))
        img = Image.alpha_composite(img, glow)

        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    output.parent.mkdir(parents=True, exist_ok=True)
    return frames


def save_gif(frames: Iterable[Image.Image], output: Path) -> None:
    frame_list = list(frames)
    if len(frame_list) < 2:
        raise RuntimeError("Animation requires multiple frames.")
    frame_list[0].save(
        output,
        save_all=True,
        append_images=frame_list[1:],
        duration=70,
        loop=0,
        optimize=False,
        disposal=2,
    )


def main() -> None:
    token = os.getenv("GITHUB_TOKEN")
    cells = load_contributions(OWNER, token)
    grid = normalize_counts(cells)
    frames = render_frames(cells, grid, OUTPUT)
    save_gif(frames, OUTPUT)
    print(f"Generated {OUTPUT} with {len(frames)} frames")


if __name__ == "__main__":
    main()
