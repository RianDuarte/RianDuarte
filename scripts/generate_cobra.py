#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import urllib.error
import urllib.request
from collections import defaultdict

from PIL import Image, ImageDraw

USERNAME = os.getenv("GITHUB_USERNAME", "RianDuarte")
TOKEN = os.getenv("GITHUB_TOKEN", "")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "assets" / "snake" / "appex-cobra.gif"

WEEKS = 52
DAYS = 7
CELL = 22
PAD = 26
HEADER = 52
FPS = 16
FRAMES = 144

BG = (9, 11, 17)
BG_ACCENT = (20, 8, 8)
GRID_EMPTY = (24, 27, 35)
GRID_STROKE = (40, 45, 58)
FOOD = (255, 48, 48)
FOOD_GLOW = (150, 20, 20)
SNAKE_BODY = (72, 225, 102)
SNAKE_BODY_DARK = (25, 120, 48)
SNAKE_OUTLINE = (4, 45, 12)
SNAKE_HEAD = (105, 245, 132)
EYE_WHITE = (245, 245, 245)
EYE_PUPIL = (16, 20, 20)
TONGUE = (255, 88, 88)


def post_graphql(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "rianduarte-cobra-generator",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_calendar_contributions() -> list[list[int]]:
    if TOKEN:
        query = """
        query($login: String!) {
          user(login: $login) {
            contributionsCollection {
              contributionCalendar {
                weeks {
                  contributionDays {
                    contributionCount
                  }
                }
              }
            }
          }
        }
        """
        try:
            data = post_graphql(query, {"login": USERNAME})
            weeks = (
                data.get("data", {})
                .get("user", {})
                .get("contributionsCollection", {})
                .get("contributionCalendar", {})
                .get("weeks", [])
            )
            if weeks:
                matrix = []
                for week in weeks[-WEEKS:]:
                    days = [d.get("contributionCount", 0) for d in week.get("contributionDays", [])]
                    if len(days) < DAYS:
                        days += [0] * (DAYS - len(days))
                    matrix.append(days[:DAYS])
                if len(matrix) < WEEKS:
                    matrix = [[0] * DAYS for _ in range(WEEKS - len(matrix))] + matrix
                return matrix
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            pass

    end = dt.date.today()
    start = end - dt.timedelta(days=WEEKS * DAYS - 1)
    by_day = defaultdict(int)
    page = 1
    while page <= 6:
        url = f"https://api.github.com/users/{USERNAME}/events/public?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"User-Agent": "rianduarte-cobra-generator"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                events = json.loads(resp.read().decode("utf-8"))
        except Exception:
            break
        if not events:
            break
        for event in events:
            created = event.get("created_at")
            if not created:
                continue
            day = dt.datetime.fromisoformat(created.replace("Z", "+00:00")).date()
            if start <= day <= end:
                by_day[day] += 1
        page += 1

    all_days = [start + dt.timedelta(days=i) for i in range(WEEKS * DAYS)]
    matrix = []
    for w in range(WEEKS):
        week_days = all_days[w * DAYS : (w + 1) * DAYS]
        matrix.append([by_day[d] for d in week_days])
    return matrix


def cell_center(col: int, row: int) -> tuple[float, float]:
    return (
        PAD + col * CELL + CELL / 2,
        HEADER + PAD + row * CELL + CELL / 2,
    )


def build_path(counts: list[list[int]]) -> list[tuple[float, float]]:
    targets = []
    for col in range(WEEKS):
        rows = range(DAYS) if col % 2 == 0 else range(DAYS - 1, -1, -1)
        for row in rows:
            if counts[col][row] > 0:
                targets.append(cell_center(col, row))
    if len(targets) < 8:
        for col in range(2, WEEKS, 2):
            targets.append(cell_center(col, (col // 2) % DAYS))
    return targets


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def draw_grid(draw: ImageDraw.ImageDraw, counts: list[list[int]]) -> None:
    max_count = max((v for week in counts for v in week), default=1)
    for col in range(WEEKS):
        for row in range(DAYS):
            x0 = PAD + col * CELL
            y0 = HEADER + PAD + row * CELL
            x1 = x0 + CELL - 3
            y1 = y0 + CELL - 3
            value = counts[col][row]
            if value <= 0:
                fill = GRID_EMPTY
            else:
                intensity = value / max_count
                fill = (
                    int(38 + intensity * 80),
                    int(40 + intensity * 160),
                    int(42 + intensity * 60),
                )
            draw.rounded_rectangle((x0, y0, x1, y1), radius=5, fill=fill, outline=GRID_STROKE, width=1)


def draw_food(draw: ImageDraw.ImageDraw, food_cells: list[tuple[int, int]], progress: float, eaten: int) -> None:
    blink = 0.5 + 0.5 * math.sin(progress * 2 * math.pi * 2)
    for i, (col, row) in enumerate(food_cells):
        if i < eaten:
            continue
        cx, cy = cell_center(col, row)
        r = 3.8 + blink * 1.4
        draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=FOOD_GLOW)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=FOOD, outline=(130, 10, 10), width=1)


def snake_positions(path: list[tuple[float, float]], frame_index: int) -> tuple[list[tuple[float, float]], float]:
    travel = len(path)
    if travel == 0:
        return [(PAD, HEADER + PAD)] * 10, 0.0
    total_progress = frame_index / FRAMES * travel
    idx0 = int(total_progress) % travel
    idx1 = (idx0 + 1) % travel
    local_t = total_progress - int(total_progress)

    hx = lerp(path[idx0][0], path[idx1][0], local_t)
    hy = lerp(path[idx0][1], path[idx1][1], local_t)

    heading = math.atan2(path[idx1][1] - path[idx0][1], path[idx1][0] - path[idx0][0] + 1e-9)

    segments = []
    segment_gap = 11.0
    for i in range(11):
        back = total_progress - (i * segment_gap / max(CELL, 1))
        b0 = int(math.floor(back)) % travel
        b1 = (b0 + 1) % travel
        bt = back - math.floor(back)
        sx = lerp(path[b0][0], path[b1][0], bt)
        sy = lerp(path[b0][1], path[b1][1], bt)
        sy += math.sin((frame_index / 8.0) - i * 0.65) * 1.6
        segments.append((sx, sy))

    segments[0] = (hx, hy)
    return segments, heading


def draw_snake(draw: ImageDraw.ImageDraw, segments: list[tuple[float, float]], heading: float, frame_index: int) -> None:
    for i in range(len(segments) - 1, 0, -1):
        x, y = segments[i]
        scale = max(0.58, 1.0 - i * 0.042)
        r = 8.2 * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=SNAKE_BODY_DARK)
        draw.ellipse((x - r + 1.3, y - r + 1.3, x + r - 1.3, y + r - 1.3), fill=SNAKE_BODY, outline=SNAKE_OUTLINE, width=1)

    hx, hy = segments[0]
    hrx, hry = 12.5, 10.5
    draw.ellipse((hx - hrx, hy - hry, hx + hrx, hy + hry), fill=SNAKE_HEAD, outline=SNAKE_OUTLINE, width=2)

    eye_sep = 4.9
    eye_forward = 3.4
    blink = 1 if (frame_index % 48 in (0, 1, 2)) else 0
    for side in (-1, 1):
        ex = hx + math.cos(heading) * eye_forward + math.cos(heading + math.pi / 2) * side * eye_sep
        ey = hy + math.sin(heading) * eye_forward + math.sin(heading + math.pi / 2) * side * eye_sep
        if blink:
            draw.line((ex - 2.2, ey, ex + 2.2, ey), fill=SNAKE_OUTLINE, width=2)
        else:
            draw.ellipse((ex - 2.6, ey - 2.6, ex + 2.6, ey + 2.6), fill=EYE_WHITE, outline=SNAKE_OUTLINE)
            px = ex + math.cos(heading) * 0.8
            py = ey + math.sin(heading) * 0.8
            draw.ellipse((px - 1.0, py - 1.0, px + 1.0, py + 1.0), fill=EYE_PUPIL)

    tongue_out = frame_index % 9 < 5
    if tongue_out:
        mouth_x = hx + math.cos(heading) * 11.8
        mouth_y = hy + math.sin(heading) * 11.8
        fork_len = 7.0
        spread = 1.6
        t1x = mouth_x + math.cos(heading + 0.18) * fork_len
        t1y = mouth_y + math.sin(heading + 0.18) * fork_len
        t2x = mouth_x + math.cos(heading - 0.18) * fork_len
        t2y = mouth_y + math.sin(heading - 0.18) * fork_len
        bx = mouth_x + math.cos(heading) * spread
        by = mouth_y + math.sin(heading) * spread
        draw.line((mouth_x, mouth_y, bx, by), fill=TONGUE, width=2)
        draw.line((bx, by, t1x, t1y), fill=TONGUE, width=2)
        draw.line((bx, by, t2x, t2y), fill=TONGUE, width=2)


def generate() -> None:
    counts = get_calendar_contributions()
    width = PAD * 2 + WEEKS * CELL
    height = HEADER + PAD * 2 + DAYS * CELL

    path = build_path(counts)
    food_cells = [(c, r) for c in range(WEEKS) for r in range(DAYS) if counts[c][r] > 0]
    serp_food = []
    used = set()
    for px, py in path:
        for c, r in food_cells:
            if (c, r) in used:
                continue
            cx, cy = cell_center(c, r)
            if abs(cx - px) < 0.1 and abs(cy - py) < 0.1:
                serp_food.append((c, r))
                used.add((c, r))
                break
    food_cells = serp_food or food_cells

    frames = []
    for i in range(FRAMES):
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, width, HEADER), fill=BG_ACCENT)
        draw.text((PAD, 17), f"{USERNAME} Contribution Cobra", fill=(255, 255, 255))

        draw_grid(draw, counts)
        eaten = min(len(food_cells), int((i / FRAMES) * max(len(food_cells), 1)))
        draw_food(draw, food_cells, i / FRAMES, eaten)

        segments, heading = snake_positions(path, i)
        draw_snake(draw, segments, heading, i)
        frames.append(img)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=False,
        disposal=2,
    )
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    generate()
