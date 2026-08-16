#!/usr/bin/env python3
import json
import math
import os
import random
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


USERNAME = os.getenv("GITHUB_USERNAME", "RianDuarte")
TOKEN = os.getenv("GITHUB_TOKEN", "")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "snake"
GIF_PATH = OUT_DIR / "appex-cobra.gif"
PREVIEW_PATH = OUT_DIR / "appex-cobra-preview.png"

FINAL_W, FINAL_H = 1200, 420
SCALE = 2
W, H = FINAL_W * SCALE, FINAL_H * SCALE
FPS = 18
SEED = 2026

random.seed(SEED)


@dataclass
class Cell:
    col: int
    row: int
    count: int
    date: str


@dataclass
class Food:
    x: float
    y: float
    power: float
    date: str
    count: int


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ease_out_back(t):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def normalize(vx, vy):
    l = math.hypot(vx, vy)
    if l < 1e-6:
        return 1.0, 0.0
    return vx / l, vy / l


def fetch_contributions(username: str, token: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=371)
    if not token:
        return []

    query = """
    query($username:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$username) {
        contributionsCollection(from:$from, to:$to) {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps(
        {
            "query": query,
            "variables": {
                "username": username,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"******",
            "Content-Type": "application/json",
            "User-Agent": "contribution-cobra-generator",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            response = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []

    if "errors" in response:
        return []

    weeks = (
        response.get("data", {})
        .get("user", {})
        .get("contributionsCollection", {})
        .get("contributionCalendar", {})
        .get("weeks", [])
    )

    cells = []
    for col, week in enumerate(weeks):
        for row, day in enumerate(week.get("contributionDays", [])):
            cells.append(
                Cell(
                    col=col,
                    row=row,
                    count=int(day.get("contributionCount", 0)),
                    date=str(day.get("date", "")),
                )
            )
    return cells


def fallback_cells():
    cells = []
    cols = 53
    for c in range(cols):
        for r in range(7):
            wave = (math.sin(c * 0.23 + r * 0.67) + 1) * 0.5
            noise = random.random() * 0.35
            count = int((wave * 0.75 + noise) * 22)
            if random.random() < 0.28:
                count = 0
            cells.append(Cell(c, r, count, ""))
    return cells


def build_layout(cells):
    cols = max((c.col for c in cells), default=52) + 1
    cell_size = 19 * SCALE
    gap = 7 * SCALE
    map_w = cols * cell_size + (cols - 1) * gap
    map_h = 7 * cell_size + 6 * gap

    ox = (W - map_w) // 2
    oy = int(H * 0.19)

    def center(col, row):
        x = ox + col * (cell_size + gap) + cell_size / 2
        y = oy + row * (cell_size + gap) + cell_size / 2
        return x, y

    return {
        "cols": cols,
        "cell": cell_size,
        "gap": gap,
        "map_w": map_w,
        "map_h": map_h,
        "ox": ox,
        "oy": oy,
        "center": center,
    }


def select_foods(cells, layout):
    max_count = max((c.count for c in cells), default=1)
    active = [c for c in cells if c.count > 0]
    if not active:
        active = random.sample(cells, min(24, len(cells)))

    active.sort(key=lambda c: (-c.count, c.col, c.row))
    limit = min(28, max(12, len(active) // 9))
    top = active[: limit * 3]
    top.sort(key=lambda c: (c.col, c.row))

    stride = max(1, len(top) // limit)
    picked = top[::stride][:limit]
    foods = []
    for c in picked:
        x, y = layout["center"](c.col, c.row)
        power = clamp(c.count / max_count, 0.15, 1.0)
        foods.append(Food(x, y, power, c.date, c.count))

    if len(foods) < 12:
        extra = sorted(active, key=lambda c: (c.col, c.row))
        for c in extra:
            if len(foods) >= 12:
                break
            x, y = layout["center"](c.col, c.row)
            foods.append(Food(x, y, clamp(c.count / max_count, 0.15, 0.8), c.date, c.count))

    foods.sort(key=lambda f: f.x)
    return foods


def build_frames(foods):
    if not foods:
        return []

    frames = []
    hx, hy = foods[0].x - 140 * SCALE, foods[0].y + 60 * SCALE
    heading = 0.0
    eaten_count = 0

    for i, food in enumerate(foods):
        target = (food.x, food.y)
        dx, dy = target[0] - hx, target[1] - hy
        dist = max(1.0, math.hypot(dx, dy))
        move_frames = int(clamp(dist / (16 * SCALE), 14, 34))

        for f in range(move_frames):
            t = smoothstep(f / max(1, move_frames - 1))
            x = lerp(hx, target[0], t)
            y = lerp(hy, target[1], t)
            nx = x + math.sin((len(frames) + f) * 0.19) * 4.0 * SCALE
            ny = y + math.cos((len(frames) + f) * 0.23) * 3.0 * SCALE
            vx, vy = x - hx, y - hy
            heading = math.atan2(vy, vx) if abs(vx) + abs(vy) > 0.01 else heading
            frames.append(
                {
                    "x": nx,
                    "y": ny,
                    "heading": heading,
                    "state": "moving",
                    "target": i,
                    "eat_progress": 0.0,
                    "boost": 0.0,
                    "eaten": eaten_count,
                }
            )

        for f in range(8):
            t = f / 7.0
            sway = math.sin(t * math.pi * 2.0) * 9 * SCALE
            look = math.sin(t * math.pi * 2.0) * 0.55
            frames.append(
                {
                    "x": food.x + math.cos(heading + math.pi / 2) * sway,
                    "y": food.y + math.sin(heading + math.pi / 2) * sway,
                    "heading": heading + look,
                    "state": "searching",
                    "target": i,
                    "eat_progress": 0.0,
                    "boost": 0.0,
                    "eaten": eaten_count,
                }
            )

        for f in range(11):
            t = f / 10.0
            bite = smoothstep(t)
            jitter = math.sin(f * 1.7) * 1.8 * SCALE
            frames.append(
                {
                    "x": food.x + jitter,
                    "y": food.y,
                    "heading": heading,
                    "state": "eating",
                    "target": i,
                    "eat_progress": bite,
                    "boost": 0.0,
                    "eaten": eaten_count,
                }
            )

        eaten_count += 1

        next_food = foods[i + 1] if i + 1 < len(foods) else None
        if next_food:
            ndx, ndy = next_food.x - food.x, next_food.y - food.y
            nvx, nvy = normalize(ndx, ndy)
            new_heading = math.atan2(ndy, ndx)
        else:
            nvx, nvy = math.cos(heading), math.sin(heading)
            new_heading = heading

        for f in range(10):
            t = f / 9.0
            jump = ease_out_back(t)
            frames.append(
                {
                    "x": food.x + nvx * jump * 42 * SCALE,
                    "y": food.y + nvy * jump * 24 * SCALE,
                    "heading": lerp(heading, new_heading, t),
                    "state": "boost",
                    "target": i,
                    "eat_progress": 0.0,
                    "boost": 1.0 - t * 0.3,
                    "eaten": eaten_count,
                }
            )

        hx, hy = frames[-1]["x"], frames[-1]["y"]
        heading = frames[-1]["heading"]

    for f in range(28):
        t = f / 27.0
        breathing = math.sin(t * math.pi * 2) * 4.5 * SCALE
        frames.append(
            {
                "x": hx + math.cos(heading + math.pi / 2) * breathing,
                "y": hy + math.sin(heading + math.pi / 2) * breathing,
                "heading": heading + math.sin(t * math.pi * 2) * 0.08,
                "state": "idle",
                "target": len(foods) - 1,
                "eat_progress": 0.0,
                "boost": 0.0,
                "eaten": eaten_count,
            }
        )

    return frames


def map_color(level):
    palette = [
        (18, 23, 22),
        (26, 58, 44),
        (42, 99, 62),
        (67, 160, 91),
        (112, 216, 132),
    ]
    return palette[clamp(level, 0, 4)]


def contribution_level(count, max_count):
    if count <= 0:
        return 0
    ratio = count / max(1, max_count)
    if ratio < 0.15:
        return 1
    if ratio < 0.35:
        return 2
    if ratio < 0.62:
        return 3
    return 4


def draw_rounded(draw, bbox, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=width)


def draw_background(img):
    draw = ImageDraw.Draw(img, "RGBA")
    for y in range(H):
        t = y / max(1, H - 1)
        r = int(8 + 18 * (1 - t))
        g = int(9 + 13 * (1 - t))
        b = int(12 + 20 * (1 - t))
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255), width=1)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    gd.ellipse([W * 0.08, H * 0.02, W * 0.52, H * 0.54], fill=(19, 255, 109, 36))
    gd.ellipse([W * 0.46, H * 0.25, W * 0.98, H * 0.95], fill=(255, 44, 56, 26))
    glow = glow.filter(ImageFilter.GaussianBlur(68))
    img.alpha_composite(glow)


def draw_map_layer(img, cells, layout):
    draw = ImageDraw.Draw(img, "RGBA")
    max_count = max((c.count for c in cells), default=1)

    panel = [
        layout["ox"] - 24 * SCALE,
        layout["oy"] - 22 * SCALE,
        layout["ox"] + layout["map_w"] + 24 * SCALE,
        layout["oy"] + layout["map_h"] + 24 * SCALE,
    ]
    draw_rounded(
        draw,
        panel,
        radius=16 * SCALE,
        fill=(8, 14, 15, 158),
        outline=(20, 150, 96, 95),
        width=2 * SCALE,
    )

    for c in cells:
        x = layout["ox"] + c.col * (layout["cell"] + layout["gap"])
        y = layout["oy"] + c.row * (layout["cell"] + layout["gap"])
        lvl = contribution_level(c.count, max_count)
        base = map_color(lvl)
        glow = tuple(min(255, int(v * 1.35)) for v in base)

        rect = [x, y, x + layout["cell"], y + layout["cell"]]
        draw_rounded(draw, rect, radius=6 * SCALE, fill=base, outline=(6, 12, 10, 180), width=1 * SCALE)
        if lvl > 0:
            hi = [x + 2 * SCALE, y + 2 * SCALE, x + layout["cell"] - 2 * SCALE, y + layout["cell"] * 0.45]
            draw_rounded(draw, hi, radius=4 * SCALE, fill=(glow[0], glow[1], glow[2], 60))


def draw_food_orb(layer, x, y, power, alive_factor, pulse, flicker):
    d = ImageDraw.Draw(layer, "RGBA")
    r = (8 + 10 * power) * SCALE * (0.9 + 0.08 * pulse)
    alpha = int(220 * alive_factor)

    outer = (255, int(80 + 90 * power), int(110 + 70 * power), int(alpha * 0.4))
    core = (255, int(130 + 90 * power), int(120 + 90 * power), alpha)

    d.ellipse([x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2], fill=outer)
    d.ellipse([x - r, y - r, x + r, y + r], fill=core)
    d.ellipse([x - r * 0.35, y - r * 0.48, x + r * 0.1, y - r * 0.05], fill=(255, 255, 255, int(alpha * 0.55)))

    for p in range(5):
        a = pulse * 2 * math.pi + p * 1.3
        pr = r * (1.8 + (p % 2) * 0.6)
        px = x + math.cos(a) * pr
        py = y + math.sin(a * 1.13) * pr
        pa = int((90 + 60 * math.sin(a + flicker)) * alive_factor)
        d.ellipse([px - 2 * SCALE, py - 2 * SCALE, px + 2 * SCALE, py + 2 * SCALE], fill=(255, 190, 80, pa))


def body_points(head_x, head_y, heading, frame_index):
    points = []
    segs = 30
    step = 14 * SCALE
    for i in range(segs):
        t = i / max(1, segs - 1)
        phase = frame_index * 0.22 - i * 0.6
        sway = math.sin(phase) * (17 * SCALE) * (1 - t * 0.5)
        bx = head_x - math.cos(heading) * step * i + math.cos(heading + math.pi / 2) * sway
        by = head_y - math.sin(heading) * step * i + math.sin(heading + math.pi / 2) * sway
        points.append((bx, by))
    return points


def draw_segmented_body(layer, points, state_boost):
    draw = ImageDraw.Draw(layer, "RGBA")
    n = len(points)
    for i in range(n - 1):
        t = i / max(1, n - 1)
        p0 = points[i]
        p1 = points[i + 1]
        base_w = (24 - t * 16) * SCALE * (1.0 + state_boost * 0.08)
        ow = int(max(2, base_w + 6 * SCALE))
        bw = int(max(1, base_w))

        shade = int(80 + (1 - t) * 85)
        fill = (26, 142 + shade // 4, 66 + shade // 6, 255)

        draw.line([p0, p1], fill=(7, 18, 14, 255), width=ow)
        draw.line([p0, p1], fill=fill, width=bw)

        hx0 = (p0[0] - 2.4 * SCALE, p0[1] - 4.0 * SCALE)
        hx1 = (p1[0] - 2.4 * SCALE, p1[1] - 4.0 * SCALE)
        draw.line([hx0, hx1], fill=(175, 255, 168, int(120 * (1 - t))), width=max(1, int(bw * 0.24)))

        sx0 = (p0[0] + 3.8 * SCALE, p0[1] + 4.8 * SCALE)
        sx1 = (p1[0] + 3.8 * SCALE, p1[1] + 4.8 * SCALE)
        draw.line([sx0, sx1], fill=(8, 32, 20, int(110 * (1 - t * 0.6))), width=max(1, int(bw * 0.4)))


def draw_head(layer, x, y, heading, state, eat_progress, boost, frame_index):
    d = ImageDraw.Draw(layer, "RGBA")
    forward = (math.cos(heading), math.sin(heading))
    right = (math.cos(heading + math.pi / 2), math.sin(heading + math.pi / 2))

    def pt(fx, ry):
        return (x + forward[0] * fx + right[0] * ry, y + forward[1] * fx + right[1] * ry)

    s = SCALE
    head_w = 42 * s
    head_l = 56 * s

    top = [pt(-18 * s, -head_w), pt(32 * s, -28 * s), pt(42 * s, 0), pt(32 * s, 28 * s), pt(-18 * s, head_w), pt(-36 * s, 0)]
    d.polygon(top, fill=(23, 166, 71, 255), outline=(6, 20, 14, 255), width=3 * s)

    d.polygon([pt(-8 * s, -24 * s), pt(30 * s, -12 * s), pt(30 * s, 12 * s), pt(-8 * s, 24 * s), pt(-26 * s, 0)], fill=(18, 121, 53, 180))
    d.polygon([pt(-20 * s, -20 * s), pt(20 * s, -8 * s), pt(18 * s, 0), pt(-16 * s, 12 * s)], fill=(121, 255, 138, 65))

    jaw_open = 0.0
    if state == "eating":
        jaw_open = 8 * s * math.sin(eat_progress * math.pi)
    elif state == "searching":
        jaw_open = 2.5 * s * (0.5 + 0.5 * math.sin(frame_index * 0.5))

    upper_jaw = [pt(12 * s, -14 * s), pt(44 * s, -6 * s), pt(44 * s, 0), pt(12 * s, -4 * s)]
    lower_jaw = [pt(12 * s, 4 * s + jaw_open), pt(44 * s, 0 + jaw_open), pt(44 * s, 6 * s + jaw_open), pt(12 * s, 14 * s + jaw_open)]
    d.polygon(upper_jaw, fill=(20, 151, 64, 255), outline=(6, 20, 14, 255))
    d.polygon(lower_jaw, fill=(17, 137, 58, 255), outline=(6, 20, 14, 255))

    blink = 1.0
    if (frame_index + 23) % 51 in (0, 1, 2):
        blink = 0.1

    eye_expr = 0.0
    if state == "boost":
        eye_expr = 0.75
    elif state == "searching":
        eye_expr = -0.35

    for sign in (-1, 1):
        ex, ey = pt(10 * s, sign * 18 * s)
        ew = 9.5 * s
        eh = (10 - 5 * (1 - blink)) * s
        d.ellipse([ex - ew, ey - eh, ex + ew, ey + eh], fill=(250, 255, 250, 245), outline=(13, 20, 13, 220), width=2 * s)

        pupil_off_f = 2.4 * s + eye_expr * 1.8 * s
        pupil_off_r = sign * (1.2 * s + eye_expr * -1.8 * s)
        px, py = pt(13 * s + pupil_off_f, sign * 18 * s + pupil_off_r)
        d.ellipse([px - 3.8 * s, py - 3.8 * s, px + 3.8 * s, py + 3.8 * s], fill=(14, 20, 14, 255))
        d.ellipse([px - 1.1 * s, py - 1.1 * s, px + 1.1 * s, py + 1.1 * s], fill=(255, 255, 255, 210))

    d.polygon([pt(8 * s, -4 * s), pt(30 * s, 0), pt(8 * s, 4 * s)], fill=(11, 35, 20, 140))

    if state in ("searching", "eating"):
        tongue_l = (10 + 18 * (0.5 + 0.5 * math.sin(frame_index * 0.9 + eat_progress * 3))) * s
        fork = 5 * s
        base = pt(42 * s, 0 + jaw_open * 0.4)
        tip = pt(42 * s + tongue_l, 0 + jaw_open * 0.3)
        fork1 = pt(42 * s + tongue_l + 7 * s, -fork)
        fork2 = pt(42 * s + tongue_l + 7 * s, fork)
        d.line([base, tip], fill=(255, 52, 82, 230), width=2 * s)
        d.line([tip, fork1], fill=(255, 52, 82, 230), width=2 * s)
        d.line([tip, fork2], fill=(255, 52, 82, 230), width=2 * s)

    if boost > 0.02:
        aura = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ad = ImageDraw.Draw(aura, "RGBA")
        ar = (90 + 20 * boost) * s
        ad.ellipse([x - ar, y - ar, x + ar, y + ar], fill=(120, 255, 130, int(34 * boost)))
        aura = aura.filter(ImageFilter.GaussianBlur(12 * s))
        layer.alpha_composite(aura)


def draw_particles(layer, head_x, head_y, heading, state, eat_progress, frame_index):
    d = ImageDraw.Draw(layer, "RGBA")
    amount = 6 if state in ("boost", "eating") else 2
    for i in range(amount):
        t = frame_index * 0.28 + i * 1.8
        dist = (20 + i * 7) * SCALE
        px = head_x - math.cos(heading) * dist + math.sin(heading) * math.sin(t) * 10 * SCALE
        py = head_y - math.sin(heading) * dist - math.cos(heading) * math.sin(t) * 8 * SCALE

        if state == "eating":
            spread = 34 * eat_progress * SCALE
            px += (random.random() - 0.5) * spread
            py += (random.random() - 0.5) * spread
            color = (255, 185, 88, int(130 + 100 * eat_progress))
        elif state == "boost":
            color = (122, 255, 144, 170)
        else:
            color = (96, 205, 120, 90)

        rr = (1.5 + (i % 3)) * SCALE
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=color)


def compose_frame(cells, foods, layout, frame_data, frame_index):
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw_background(frame)
    draw_map_layer(frame, cells, layout)

    fx_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))

    for idx, food in enumerate(foods):
        eaten = idx < frame_data["eaten"]
        current_target = idx == frame_data["target"] and frame_data["state"] == "eating"
        alive = 0.0 if eaten else 1.0
        if current_target:
            alive = 1.0 - frame_data["eat_progress"]
        if alive > 0.01:
            pulse = frame_index * 0.04 + idx * 0.43
            yy = food.y + math.sin(pulse * 1.7) * 4.0 * SCALE
            draw_food_orb(fx_layer, food.x, yy, food.power, alive, pulse, idx * 0.8)

    points = body_points(frame_data["x"], frame_data["y"], frame_data["heading"], frame_index)
    draw_segmented_body(fx_layer, points, frame_data["boost"])
    draw_head(
        fx_layer,
        frame_data["x"],
        frame_data["y"],
        frame_data["heading"],
        frame_data["state"],
        frame_data["eat_progress"],
        frame_data["boost"],
        frame_index,
    )
    draw_particles(
        fx_layer,
        frame_data["x"],
        frame_data["y"],
        frame_data["heading"],
        frame_data["state"],
        frame_data["eat_progress"],
        frame_index,
    )

    glow = fx_layer.filter(ImageFilter.GaussianBlur(7 * SCALE))
    frame = ImageChops.screen(frame, glow)
    frame.alpha_composite(fx_layer)

    ui = ImageDraw.Draw(frame, "RGBA")
    ui.rounded_rectangle(
        [18 * SCALE, 18 * SCALE, 290 * SCALE, 76 * SCALE],
        radius=12 * SCALE,
        fill=(5, 12, 12, 155),
        outline=(70, 230, 120, 90),
        width=2 * SCALE,
    )
    ui.text((32 * SCALE, 32 * SCALE), f"COBRA MODE · {frame_data['state'].upper()}", fill=(170, 255, 190, 220))

    down = frame.resize((FINAL_W, FINAL_H), Image.Resampling.LANCZOS)
    return down.convert("P", palette=Image.Palette.ADAPTIVE)


def generate():
    cells = fetch_contributions(USERNAME, TOKEN)
    if not cells:
        cells = fallback_cells()

    layout = build_layout(cells)
    foods = select_foods(cells, layout)
    frames_data = build_frames(foods)
    if not frames_data:
        raise RuntimeError("No frames generated for cobra animation")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    frames = [compose_frame(cells, foods, layout, fd, idx) for idx, fd in enumerate(frames_data)]

    durations = [int(1000 / FPS)] * len(frames)
    durations[-24:] = [90] * min(24, len(durations))

    frames[0].save(
        GIF_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )

    preview = frames[len(frames) // 2].convert("RGBA")
    preview.save(PREVIEW_PATH, format="PNG")

    print(f"Generated {GIF_PATH} ({len(frames)} frames)")
    print(f"Generated {PREVIEW_PATH}")


if __name__ == "__main__":
    generate()
