#!/usr/bin/env python3
import json
import math
import os
import random
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFilter

OUTPUT_GIF = Path("assets/snake/appex-cobra.gif")
OUTPUT_PREVIEW = Path("assets/snake/appex-cobra-preview.png")
USERNAME = os.getenv("GITHUB_USERNAME", "RianDuarte")
TOKEN = os.getenv("GITHUB_TOKEN")

# Render in higher resolution and downscale for smoother anti-aliasing.
SUPER_SAMPLE = 2
FINAL_WIDTH = 1320
FINAL_HEIGHT = 360
W = FINAL_WIDTH * SUPER_SAMPLE
H = FINAL_HEIGHT * SUPER_SAMPLE
FPS = 22
TOTAL_FRAMES = 210

MAP_X = 170 * SUPER_SAMPLE
MAP_Y = 82 * SUPER_SAMPLE
CELL = 12 * SUPER_SAMPLE
GAP = 5 * SUPER_SAMPLE
COLS = 53
ROWS = 7

BG_TOP = (8, 10, 14)
BG_BOTTOM = (3, 5, 8)
MAP_LOW = (20, 32, 24)
MAP_MED = (28, 64, 34)
MAP_HIGH = (44, 130, 64)
MAP_MAX = (88, 236, 120)
MAP_EMPTY = (13, 18, 15)

SNAKE_BASE = (62, 200, 92)
SNAKE_DARK = (24, 102, 48)
SNAKE_OUTLINE = (7, 36, 19)
SNAKE_HIGHLIGHT = (152, 255, 188)
SNAKE_ACCENT = (215, 36, 54)

random.seed(42)


@dataclass
class Food:
    x: float
    y: float
    level: int
    value: int
    alive: bool = True
    pulse_shift: float = 0.0


@dataclass
class ContributionCell:
    x: float
    y: float
    count: int
    level: int


def clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp(t, 0.0, 1.0))


def color_lerp(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def github_graphql(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "contribution-cobra-generator",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fallback_calendar() -> List[List[dict]]:
    # Generate a deterministic synthetic calendar when API is unavailable.
    start = date.today() - timedelta(days=370)
    weeks = []
    for c in range(COLS):
        days = []
        for r in range(ROWS):
            d = start + timedelta(days=(c * ROWS + r))
            base = (math.sin(c * 0.31) + 1) * 2.1 + (math.cos(r * 1.2) + 1)
            noise = random.random() * 2.2
            count = int(max(0, base + noise - 2.2))
            level = 0 if count == 0 else (1 if count < 2 else 2 if count < 4 else 3 if count < 7 else 4)
            days.append({"date": d.isoformat(), "contributionCount": count, "contributionLevel": level})
        weeks.append(days)
    return weeks


def fetch_calendar(username: str) -> List[List[dict]]:
    if not TOKEN:
        return fallback_calendar()

    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
                contributionLevel
              }
            }
          }
        }
      }
    }
    """

    try:
        data = github_graphql(query, {"login": username})
        weeks = (
            data.get("data", {})
            .get("user", {})
            .get("contributionsCollection", {})
            .get("contributionCalendar", {})
            .get("weeks", [])
        )
        if not weeks:
            return fallback_calendar()

        parsed = []
        for wk in weeks[-COLS:]:
            days = []
            for d in wk.get("contributionDays", [])[:ROWS]:
                level_str = d.get("contributionLevel", "NONE")
                level = {
                    "NONE": 0,
                    "FIRST_QUARTILE": 1,
                    "SECOND_QUARTILE": 2,
                    "THIRD_QUARTILE": 3,
                    "FOURTH_QUARTILE": 4,
                }.get(level_str, 0)
                days.append(
                    {
                        "date": d.get("date", ""),
                        "contributionCount": int(d.get("contributionCount", 0)),
                        "contributionLevel": level,
                    }
                )
            while len(days) < ROWS:
                days.append({"date": "", "contributionCount": 0, "contributionLevel": 0})
            parsed.append(days)

        while len(parsed) < COLS:
            parsed.insert(0, [{"date": "", "contributionCount": 0, "contributionLevel": 0} for _ in range(ROWS)])

        return parsed[-COLS:]
    except Exception:
        return fallback_calendar()


def build_cells_and_food(weeks: List[List[dict]]) -> Tuple[List[ContributionCell], List[Food]]:
    cells: List[ContributionCell] = []
    foods: List[Food] = []

    for c in range(min(COLS, len(weeks))):
        days = weeks[c]
        for r in range(min(ROWS, len(days))):
            d = days[r]
            x = MAP_X + c * (CELL + GAP) + CELL * 0.5
            y = MAP_Y + r * (CELL + GAP) + CELL * 0.5
            count = int(d.get("contributionCount", 0))
            level = int(d.get("contributionLevel", 0))
            cells.append(ContributionCell(x, y, count, level))

            if count > 0 and random.random() < (0.20 + level * 0.07):
                foods.append(
                    Food(
                        x=x,
                        y=y,
                        level=level,
                        value=count,
                        pulse_shift=random.random() * math.tau,
                    )
                )

    if len(foods) < 24:
        hotspots = [c for c in cells if c.count > 0]
        hotspots.sort(key=lambda c: c.count, reverse=True)
        for c in hotspots[: 30 - len(foods)]:
            foods.append(Food(x=c.x, y=c.y, level=max(2, c.level), value=c.count, pulse_shift=random.random() * math.tau))

    if len(foods) < 16:
        # fallback seeds
        for _ in range(16 - len(foods)):
            c = random.choice(cells)
            foods.append(Food(c.x, c.y, 2, 1, pulse_shift=random.random() * math.tau))

    # Spread targets across map for better motion.
    foods.sort(key=lambda f: (f.x, (f.y if int(f.x) % 2 == 0 else -f.y)))
    return cells, foods[:42]


def nearest_food(current: Tuple[float, float], foods: List[Food], consumed: set) -> int:
    best_idx = -1
    best_dist = float("inf")
    for i, food in enumerate(foods):
        if i in consumed:
            continue
        dx = food.x - current[0]
        dy = food.y - current[1]
        d = dx * dx + dy * dy
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def draw_glow_circle(img: Image.Image, x: float, y: float, radius: float, color: Tuple[int, int, int], alpha: int = 255):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay, "RGBA")
    for i in range(5, 0, -1):
        rr = radius * (1 + i * 0.42)
        a = int(alpha * (0.07 * i))
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(*color, a))
    d.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, alpha))
    img.alpha_composite(overlay)


def draw_background(canvas: Image.Image):
    draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(H):
        t = y / max(1, H - 1)
        c = color_lerp(BG_TOP, BG_BOTTOM, t)
        draw.line((0, y, W, y), fill=(*c, 255))

    # Vignette glow center
    light = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(light, "RGBA")
    cx, cy = W * 0.5, H * 0.55
    for i in range(10, 0, -1):
        r = min(W, H) * (0.2 + 0.1 * i)
        ld.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(18, 48, 24, int(11 * i)))
    canvas.alpha_composite(light)


def cell_color(level: int, count: int) -> Tuple[int, int, int]:
    if level <= 0 or count <= 0:
        return MAP_EMPTY
    if level == 1:
        return MAP_LOW
    if level == 2:
        return MAP_MED
    if level == 3:
        return MAP_HIGH
    return MAP_MAX


def draw_contribution_map(canvas: Image.Image, cells: List[ContributionCell], frame: int):
    draw = ImageDraw.Draw(canvas, "RGBA")
    pulse = (math.sin(frame * 0.12) + 1.0) * 0.5

    map_w = COLS * CELL + (COLS - 1) * GAP
    map_h = ROWS * CELL + (ROWS - 1) * GAP

    # frame panel
    panel_pad = 28 * SUPER_SAMPLE
    panel = (
        MAP_X - panel_pad,
        MAP_Y - panel_pad,
        MAP_X + map_w + panel_pad,
        MAP_Y + map_h + panel_pad,
    )
    draw.rounded_rectangle(panel, radius=18 * SUPER_SAMPLE, fill=(8, 12, 14, 150), outline=(65, 135, 90, 150), width=2)

    for cell in cells:
        x0 = cell.x - CELL * 0.5
        y0 = cell.y - CELL * 0.5
        x1 = x0 + CELL
        y1 = y0 + CELL
        base = cell_color(cell.level, cell.count)
        glow = color_lerp(base, (170, 255, 186), pulse * 0.22)
        draw.rounded_rectangle((x0, y0, x1, y1), radius=3 * SUPER_SAMPLE, fill=(*base, 235), outline=(*glow, 180), width=1)


def food_radius(food: Food) -> float:
    return 5.5 * SUPER_SAMPLE + food.level * 1.35 * SUPER_SAMPLE + min(food.value, 8) * 0.15 * SUPER_SAMPLE


def draw_foods(canvas: Image.Image, foods: List[Food], frame: int, bite_events: List[Tuple[float, float, float]]):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")

    for idx, food in enumerate(foods):
        if not food.alive:
            continue
        pulse = 0.5 + 0.5 * math.sin(frame * 0.21 + food.pulse_shift)
        bob = math.sin(frame * 0.15 + idx * 0.7) * 2.2 * SUPER_SAMPLE
        cx, cy = food.x, food.y + bob
        r = food_radius(food) * (0.9 + 0.14 * pulse)

        core = color_lerp((65, 242, 135), (228, 55, 84), clamp(food.level / 4, 0.0, 1.0) * 0.32)
        draw_glow_circle(layer, cx, cy, r, core, alpha=230)
        d.ellipse((cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45), fill=(230, 255, 242, 140))

        # tiny ambient particles
        for p in range(3):
            ang = frame * 0.09 + idx + p * 2.1
            pr = r * (1.3 + p * 0.25)
            px = cx + math.cos(ang) * pr
            py = cy + math.sin(ang * 1.6) * pr * 0.65
            d.ellipse((px - 1.8, py - 1.8, px + 1.8, py + 1.8), fill=(145, 255, 180, 120))

    for x, y, power in bite_events:
        for i in range(6):
            ang = i * (math.tau / 6) + frame * 0.18
            pr = (8 + i * 2 + power * 6) * SUPER_SAMPLE
            px = x + math.cos(ang) * pr
            py = y + math.sin(ang) * pr
            d.ellipse((px - 2.8, py - 2.8, px + 2.8, py + 2.8), fill=(255, 78, 88, 180))

    canvas.alpha_composite(layer)


def sample_trail(trail: List[Tuple[float, float]], spacing: float, count: int) -> List[Tuple[float, float]]:
    if not trail:
        return [(0.0, 0.0)] * count

    points = [trail[-1]]
    dist_target = spacing
    total = 0.0

    for i in range(len(trail) - 2, -1, -1):
        x1, y1 = trail[i + 1]
        x0, y0 = trail[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-4:
            continue
        while total + seg >= dist_target and len(points) < count:
            t = (dist_target - total) / seg
            px = x1 + (x0 - x1) * t
            py = y1 + (y0 - y1) * t
            points.append((px, py))
            dist_target += spacing
        total += seg
        if len(points) >= count:
            break

    while len(points) < count:
        points.append(points[-1])

    return points


def draw_snake(canvas: Image.Image, body_points: List[Tuple[float, float]], heading: float, frame: int, state: str, blink: float, tongue_t: float):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")

    n = len(body_points)

    # shadow under body
    for i in range(n - 1, -1, -1):
        t = i / max(1, n - 1)
        x, y = body_points[i]
        rr = (7.5 + (1 - t) * 8.0) * SUPER_SAMPLE
        d.ellipse((x - rr * 1.2, y - rr * 0.58 + 8 * SUPER_SAMPLE, x + rr * 1.2, y + rr * 0.58 + 8 * SUPER_SAMPLE), fill=(0, 0, 0, int(54 * (1 - t))))

    # body segments with color gradient, highlights and outline
    for i in range(n - 1, -1, -1):
        t = i / max(1, n - 1)
        x, y = body_points[i]
        rr = (8.0 + (1 - t) * 10.0) * SUPER_SAMPLE

        # organic width modulation to avoid identical segments
        rr *= 0.92 + 0.08 * math.sin(frame * 0.14 + i * 0.6)

        base = color_lerp(SNAKE_DARK, SNAKE_BASE, 1 - t * 0.82)
        outline = color_lerp((8, 28, 16), SNAKE_OUTLINE, t * 0.6)

        d.ellipse((x - rr - 1, y - rr - 1, x + rr + 1, y + rr + 1), fill=(*outline, 215))
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(*base, 245))

        # underside shading
        d.ellipse((x - rr * 0.82, y - rr * 0.1, x + rr * 0.82, y + rr * 0.9), fill=(18, 58, 30, 80))

        # highlight strip
        d.ellipse((x - rr * 0.64, y - rr * 0.88, x + rr * 0.64, y - rr * 0.08), fill=(*SNAKE_HIGHLIGHT, int(66 * (1 - t))))

    # subtle accent scales
    for i in range(2, n, 3):
        x, y = body_points[i]
        s = (3.0 + (1 - i / max(1, n - 1)) * 2.4) * SUPER_SAMPLE
        d.ellipse((x - s, y - s, x + s, y + s), outline=(*SNAKE_ACCENT, 110), width=1)

    hx, hy = body_points[0]

    # Head (custom character-style)
    def rot(local_x: float, local_y: float) -> Tuple[float, float]:
        c = math.cos(heading)
        s = math.sin(heading)
        return hx + local_x * c - local_y * s, hy + local_x * s + local_y * c

    head_poly_local = [
        (-28, -22),
        (-7, -30),
        (26, -20),
        (34, 0),
        (26, 20),
        (-7, 30),
        (-28, 22),
        (-36, 0),
    ]
    head_poly = [rot(x * SUPER_SAMPLE, y * SUPER_SAMPLE) for x, y in head_poly_local]

    d.polygon(head_poly, fill=(*SNAKE_OUTLINE, 235))

    inner = [rot(x * 0.92 * SUPER_SAMPLE, y * 0.92 * SUPER_SAMPLE) for x, y in head_poly_local]
    d.polygon(inner, fill=(*SNAKE_BASE, 248))

    # top highlight and shading for volume
    hl = [rot(x * SUPER_SAMPLE, y * SUPER_SAMPLE) for x, y in [(-20, -16), (2, -21), (21, -13), (8, -5), (-13, -7)]]
    d.polygon(hl, fill=(*SNAKE_HIGHLIGHT, 125))

    shade = [rot(x * SUPER_SAMPLE, y * SUPER_SAMPLE) for x, y in [(-18, 9), (8, 6), (25, 15), (1, 24), (-16, 19)]]
    d.polygon(shade, fill=(18, 62, 34, 95))

    # Eyes
    eye_open = 1.0 - blink
    left_eye = rot(7 * SUPER_SAMPLE, -10 * SUPER_SAMPLE)
    right_eye = rot(7 * SUPER_SAMPLE, 10 * SUPER_SAMPLE)

    ew = 7.5 * SUPER_SAMPLE
    eh = (5.5 * eye_open + 0.4) * SUPER_SAMPLE

    for ex, ey in (left_eye, right_eye):
        d.ellipse((ex - ew, ey - eh, ex + ew, ey + eh), fill=(244, 255, 247, 255), outline=(30, 48, 35, 200), width=1)

    pup_shift = math.sin(frame * 0.1) * 1.2 * SUPER_SAMPLE
    for ex, ey in (left_eye, right_eye):
        d.ellipse(
            (
                ex - 2.2 * SUPER_SAMPLE + pup_shift,
                ey - 2.2 * SUPER_SAMPLE,
                ex + 2.2 * SUPER_SAMPLE + pup_shift,
                ey + 2.2 * SUPER_SAMPLE,
            ),
            fill=(10, 20, 14, 255),
        )
        d.ellipse(
            (
                ex - 1.2 * SUPER_SAMPLE + pup_shift,
                ey - 2.2 * SUPER_SAMPLE,
                ex + 0.2 * SUPER_SAMPLE + pup_shift,
                ey - 0.9 * SUPER_SAMPLE,
            ),
            fill=(255, 255, 255, 220),
        )

    # Mouth / expression
    mouth_curve = [
        rot(18 * SUPER_SAMPLE, -6 * SUPER_SAMPLE),
        rot(27 * SUPER_SAMPLE, 0),
        rot(18 * SUPER_SAMPLE, 6 * SUPER_SAMPLE),
    ]
    d.line(mouth_curve, fill=(18, 22, 18, 255), width=2 * SUPER_SAMPLE, joint="curve")

    if state in {"searching", "eating"}:
        tongue_len = (10 + 20 * tongue_t) * SUPER_SAMPLE
        fork = (4 + 4 * tongue_t) * SUPER_SAMPLE
        tx1, ty1 = rot(31 * SUPER_SAMPLE, 0)
        tx2, ty2 = rot((31 + tongue_len) * SUPER_SAMPLE, -fork)
        tx3, ty3 = rot((31 + tongue_len) * SUPER_SAMPLE, fork)
        d.line((tx1, ty1, tx2, ty2), fill=(242, 56, 96, 230), width=max(1, 2 * SUPER_SAMPLE))
        d.line((tx1, ty1, tx3, ty3), fill=(242, 56, 96, 230), width=max(1, 2 * SUPER_SAMPLE))

    # cheek accent
    accent = [rot(x * SUPER_SAMPLE, y * SUPER_SAMPLE) for x, y in [(0, 13), (10, 9), (19, 13), (7, 18)]]
    d.polygon(accent, fill=(*SNAKE_ACCENT, 88))

    canvas.alpha_composite(layer)


def draw_ui(canvas: Image.Image, state: str):
    d = ImageDraw.Draw(canvas, "RGBA")
    text = f"Contribution Cobra • {state.upper()}"
    d.rounded_rectangle((24 * SUPER_SAMPLE, 20 * SUPER_SAMPLE, 390 * SUPER_SAMPLE, 56 * SUPER_SAMPLE), radius=10 * SUPER_SAMPLE, fill=(6, 10, 9, 155), outline=(93, 214, 134, 120), width=1)
    d.text((36 * SUPER_SAMPLE, 29 * SUPER_SAMPLE), text, fill=(196, 255, 214, 230))


def generate_frames(cells: List[ContributionCell], foods: List[Food]) -> List[Image.Image]:
    frames = []

    head_x = MAP_X - 34 * SUPER_SAMPLE
    head_y = MAP_Y + (ROWS * (CELL + GAP)) * 0.6
    heading = 0.0
    trail: List[Tuple[float, float]] = [(head_x, head_y)]

    consumed = set()
    target_idx = nearest_food((head_x, head_y), foods, consumed)

    state = "idle"
    state_timer = 18
    eat_food_idx = -1
    bite_events: List[Tuple[float, float, float]] = []

    for frame in range(TOTAL_FRAMES):
        bite_events = [(x, y, p * 0.84) for x, y, p in bite_events if p > 0.06]

        if target_idx < 0:
            state = "idle"

        tx, ty = (head_x + math.cos(frame * 0.05), head_y)
        if target_idx >= 0:
            target = foods[target_idx]
            tx, ty = target.x, target.y

        dx, dy = tx - head_x, ty - head_y
        dist = max(1e-5, math.hypot(dx, dy))
        dir_x, dir_y = dx / dist, dy / dist

        # state transitions
        if state == "idle":
            wobble = math.sin(frame * 0.18) * 0.9 * SUPER_SAMPLE
            head_x += 0.55 * SUPER_SAMPLE
            head_y += wobble
            state_timer -= 1
            if state_timer <= 0 and target_idx >= 0:
                state = "moving"
        elif state == "moving":
            speed = (2.6 + 0.9 * math.sin(frame * 0.35)) * SUPER_SAMPLE
            head_x += dir_x * speed
            head_y += dir_y * speed
            if dist < 22 * SUPER_SAMPLE:
                state = "searching"
                state_timer = 10
        elif state == "searching":
            angle = math.atan2(dir_y, dir_x)
            orbit = math.sin((10 - state_timer) * 0.9) * 6.0 * SUPER_SAMPLE
            head_x = tx - math.cos(angle) * 16 * SUPER_SAMPLE + math.cos(angle + math.pi / 2) * orbit
            head_y = ty - math.sin(angle) * 16 * SUPER_SAMPLE + math.sin(angle + math.pi / 2) * orbit
            state_timer -= 1
            if state_timer <= 0:
                state = "eating"
                state_timer = 12
                eat_food_idx = target_idx
        elif state == "eating":
            t = 1.0 - state_timer / 12.0
            approach = ease_in_out(t)
            head_x = lerp(head_x, tx, 0.36 * approach)
            head_y = lerp(head_y, ty, 0.36 * approach)
            if state_timer == 7 and eat_food_idx >= 0 and foods[eat_food_idx].alive:
                foods[eat_food_idx].alive = False
                consumed.add(eat_food_idx)
                bite_events.append((tx, ty, 1.4))
            state_timer -= 1
            if state_timer <= 0:
                state = "boost"
                state_timer = 9
                target_idx = nearest_food((head_x, head_y), foods, consumed)
        elif state == "boost":
            speed = (5.0 + 1.4 * math.sin(frame * 0.6)) * SUPER_SAMPLE
            if target_idx >= 0:
                dx, dy = foods[target_idx].x - head_x, foods[target_idx].y - head_y
                dist = max(1e-5, math.hypot(dx, dy))
                dir_x, dir_y = dx / dist, dy / dist
            head_x += dir_x * speed
            head_y += dir_y * speed + math.sin(frame * 0.45) * 1.0 * SUPER_SAMPLE
            state_timer -= 1
            if state_timer <= 0:
                state = "moving"

        # Keep snake in scene bounds
        min_x = MAP_X - 56 * SUPER_SAMPLE
        max_x = MAP_X + COLS * (CELL + GAP) + 26 * SUPER_SAMPLE
        min_y = MAP_Y - 28 * SUPER_SAMPLE
        max_y = MAP_Y + ROWS * (CELL + GAP) + 26 * SUPER_SAMPLE
        head_x = clamp(head_x, min_x, max_x)
        head_y = clamp(head_y, min_y, max_y)

        if trail:
            prev_x, prev_y = trail[-1]
            heading = math.atan2(head_y - prev_y, head_x - prev_x)

        trail.append((head_x, head_y))
        if len(trail) > 900:
            trail = trail[-900:]

        body = sample_trail(trail, spacing=7.7 * SUPER_SAMPLE, count=34)

        # organic wave along body
        waved = []
        for i, (bx, by) in enumerate(body):
            if i == 0:
                waved.append((bx, by))
                continue
            phase = frame * 0.22 - i * 0.55
            sway = math.sin(phase) * (2.3 + i * 0.04) * SUPER_SAMPLE
            perp = heading + math.pi / 2
            waved.append((bx + math.cos(perp) * sway * 0.35, by + math.sin(perp) * sway * 0.35))

        blink = 1.0 if (frame % 74 in (0, 1, 2)) else 0.0
        tongue_t = 0.0
        if state in {"searching", "eating"}:
            tongue_t = 0.5 + 0.5 * math.sin(frame * 0.8)

        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        draw_background(canvas)
        draw_contribution_map(canvas, cells, frame)
        draw_foods(canvas, foods, frame, bite_events)
        draw_snake(canvas, waved, heading, frame, state, blink, tongue_t)

        if state == "boost":
            boost_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            bd = ImageDraw.Draw(boost_layer, "RGBA")
            hx, hy = waved[0]
            for k in range(7):
                spread = (10 + k * 6) * SUPER_SAMPLE
                alpha = int(120 - k * 15)
                bx = hx - math.cos(heading) * spread
                by = hy - math.sin(heading) * spread
                bd.ellipse((bx - 5, by - 3, bx + 5, by + 3), fill=(255, 58, 78, max(0, alpha)))
            canvas.alpha_composite(boost_layer)

        draw_ui(canvas, state)

        final = canvas.resize((FINAL_WIDTH, FINAL_HEIGHT), Image.Resampling.LANCZOS).convert("P", palette=Image.Palette.ADAPTIVE)
        frames.append(final)

    return frames


def main():
    weeks = fetch_calendar(USERNAME)
    cells, foods = build_cells_and_food(weeks)

    OUTPUT_GIF.parent.mkdir(parents=True, exist_ok=True)
    frames = generate_frames(cells, foods)
    frames[0].save(
        OUTPUT_GIF,
        save_all=True,
        append_images=frames[1:],
        optimize=False,
        duration=int(1000 / FPS),
        loop=0,
        disposal=2,
    )

    preview = frames[min(len(frames) - 1, 66)].convert("RGBA")
    preview.save(OUTPUT_PREVIEW)

    print(f"Generated {OUTPUT_GIF} ({len(frames)} frames)")
    print(f"Generated {OUTPUT_PREVIEW}")


if __name__ == "__main__":
    main()
