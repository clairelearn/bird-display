"""
Dawn chorus report, woodblock style.

Fetches this morning's birds from a BirdWeather station and draws the
report as index.html (800x480) for the e-paper display.

  python3 build_dawn.py                 today's real data
  python3 build_dawn.py --date 2026-09-30
  python3 build_dawn.py --demo          made-up busy May morning, for testing layout
"""
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, date, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

# ---------------- Settings ----------------
STATION_ID = "12114"            # BirdNET-Pi - PaulBris01
PLACE = "Bedminster"
LAT, LON = 51.44, -2.60
MORNING_ENDS = time(8, 0)
MIN_CONFIDENCE = 0.7
MAX_CHORUS = 8                  # birds on the arc after first light
MAX_NIGHT = 4                   # birds in the night sky
OUTPUT = "index.html"
UK = ZoneInfo("Europe/London")

# Inks (swap for pure #000000 etc. if those print crisper on the panel)
INK, PAPER, RED, YELLOW, BLUE, GREEN = "#1b1b1b", "#f2efe6", "#b5302a", "#e6b92e", "#24508f", "#3e6e3a"

DROP_PREFIXES = ("Eurasian ", "Common ", "European ", "Northern ")


def short_name(name):
    for p in DROP_PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


# ---------------- Sun ----------------
def sun_time(day, zenith):
    rad, deg = math.radians, math.degrees
    n = day.timetuple().tm_yday
    lng_hour = LON / 15
    t = n + ((6 - lng_hour) / 24)
    m = 0.9856 * t - 3.289
    l = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m)) + 282.634) % 360
    ra = deg(math.atan(0.91764 * math.tan(rad(l)))) % 360
    ra += (math.floor(l / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15
    sin_dec = 0.39782 * math.sin(rad(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(rad(zenith)) - sin_dec * math.sin(rad(LAT))) / (cos_dec * math.cos(rad(LAT)))
    h = (360 - deg(math.acos(cos_h))) / 15
    ut = (h + ra - 0.06571 * t - 6.622 - lng_hour) % 24
    return (datetime.combine(day, time(0), tzinfo=timezone.utc) + timedelta(hours=ut)).astimezone(UK)


# ---------------- BirdWeather ----------------
def ask(query):
    req = urllib.request.Request("https://app.birdweather.com/graphql",
                                 data=json.dumps({"query": query}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        result = json.load(resp)
    if "errors" in result:
        raise SystemExit("BirdWeather said no:\n" + json.dumps(result["errors"], indent=2))
    return result["data"]


def detections_between(start, end):
    found, cursor = [], None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        page = ask("""{ station(id: "%s") { detections(first: 100, confidenceGte: %s%s) {
            nodes { timestamp species { commonName } }
            pageInfo { hasNextPage endCursor } } } }""" % (STATION_ID, MIN_CONFIDENCE, after))["station"]["detections"]
        for d in page["nodes"]:
            when = datetime.fromisoformat(d["timestamp"]).astimezone(UK)
            if when < start:
                return sorted(found)
            if when < end:
                found.append((when, d["species"]["commonName"]))
        if not page["pageInfo"]["hasNextPage"]:
            return sorted(found)
        cursor = page["pageInfo"]["endCursor"]


def month_counts():
    data = ask("""{ station(id: "%s") { topSpecies(period: {count: 30, unit: "day"}) {
        count species { commonName } } } }""" % STATION_ID)
    return {e["species"]["commonName"]: e["count"] for e in data["station"]["topSpecies"]}


def real_data(day):
    midnight = datetime.combine(day, time(0), tzinfo=UK)
    end = datetime.combine(day, MORNING_ENDS, tzinfo=UK)
    dets = detections_between(midnight, end)
    month = month_counts()
    today = Counter(s for _, s in dets)
    new = {s for s in today if month.get(s, 0) <= today[s]}
    return {"day": day, "detections": dets, "new": new}


def demo_data():
    day = date(2026, 5, 12)
    def at(hm): return datetime.combine(day, time(*hm), tzinfo=UK)
    plan = [
        ("Tawny Owl", (1, 10), 6), ("Mallard", (0, 40), 3), ("Common Moorhen", (2, 30), 2),
        ("Eurasian Curlew", (3, 40), 1), ("Canada Goose", (3, 55), 1),
        ("European Robin", (4, 45), 40), ("Eurasian Blackbird", (4, 47), 55), ("Song Thrush", (4, 55), 22),
        ("Eurasian Wren", (5, 2), 30), ("Great Tit", (5, 5), 18), ("Eurasian Blue Tit", (5, 7), 12),
        ("Common Chiffchaff", (5, 30), 15), ("Eurasian Blackcap", (5, 41), 9),
        ("Common Wood-Pigeon", (5, 44), 25), ("Eurasian Collared-Dove", (6, 10), 7),
    ]
    dets = []
    for name, hm, n in plan:
        t0 = at(hm)
        for i in range(n):
            dets.append((t0 + timedelta(minutes=i * 3), name))
    dets = [d for d in dets if d[0] < at((8, 0))]
    return {"day": day, "detections": sorted(dets), "new": {"Common Chiffchaff", "Eurasian Blackcap"}}


# ---------------- Work out the report ----------------
def summarise(data):
    day = data["day"]
    first_light, sunrise = sun_time(day, 96), sun_time(day, 90.833)
    dets = data["detections"]
    night = [(t, s) for t, s in dets if t < first_light]
    chorus = [(t, s) for t, s in dets if t >= first_light]

    night_by = defaultdict(list)
    for t, s in night:
        night_by[s].append(t)
    night_list = sorted(night_by.items(), key=lambda kv: -len(kv[1]))[:MAX_NIGHT]
    night_list = sorted([(ts[0], s, len(ts)) for s, ts in night_list])

    firsts = {}
    for t, s in chorus:
        firsts.setdefault(s, t)
    first_songs = sorted((t, s) for s, t in firsts.items())

    return {
        "day": day, "first_light": first_light, "sunrise": sunrise,
        "night": night_list,
        "chorus": first_songs[:MAX_CHORUS], "chorus_extra": max(0, len(first_songs) - MAX_CHORUS),
        "early": first_songs[0] if first_songs else None,
        "top": Counter(s for _, s in chorus).most_common(3),
        "new": sorted(s for s in data["new"] if s in {x for _, x in dets}),
        "species": len({s for _, s in dets}), "calls": len(dets),
    }


# ---------------- Draw ----------------
CX, CY, R = 360, 330, 250
RS = R + 90


def pt(a, rr):
    return CX + rr * math.cos(math.radians(a)), CY - rr * math.sin(math.radians(a))


def arc(a1, a2, rr):
    x1, y1 = pt(a1, rr); x2, y2 = pt(a2, rr)
    return f"M{x1:.1f} {y1:.1f} A{rr} {rr} 0 0 1 {x2:.1f} {y2:.1f}"


def sector(a1, a2, rr):
    x1, y1 = pt(a1, rr); x2, y2 = pt(a2, rr)
    return f"M{CX} {CY} L{x1:.1f} {y1:.1f} A{rr} {rr} 0 0 1 {x2:.1f} {y2:.1f} Z"


def bird_shape(x, y, col):
    return (f'<path d="M{x-16:.1f} {y-6:.1f} Q{x-6:.1f} {y-12:.1f} {x:.1f} {y:.1f} Q{x+6:.1f} {y-12:.1f} {x+16:.1f} {y-6:.1f} '
            f'Q{x+6:.1f} {y-6:.1f} {x:.1f} {y+3:.1f} Q{x-6:.1f} {y-6:.1f} {x-16:.1f} {y-6:.1f} Z" fill="{col}"/>')


def text_width(s, size=15):
    return len(s) * size * 0.56


def overlaps(b, boxes):
    return any(not (b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3]) for o in boxes)


def in_night_sky(b):
    for x, y in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])):
        dx, dy = x - CX, CY - y
        if dy <= 4 or math.hypot(dx, dy) > RS - 6:
            return False
        if math.degrees(math.atan2(dy, dx)) < 101:
            return False
    return True


def render(rep):
    fl, sr = rep["first_light"], rep["sunrise"]
    fl_min = fl.hour * 60 + fl.minute
    end_min = MORNING_ENDS.hour * 60

    def angle(t):
        m = t.hour * 60 + t.minute + t.second / 60
        if m <= fl_min:
            return 180 - (m / fl_min) * 80
        return 100 - ((m - fl_min) / (end_min - fl_min)) * 100

    svg, labels = [], []
    placed = [(582, 18, 790, 122),        # title box
              (0, 318, 800, 480),         # waves and stats
              (CX - 96, CY - 96, CX + 96, CY)]  # sun

    # night sky with dotted stripes
    svg.append(f'<path d="{sector(180, 100, RS)}" fill="{BLUE}"/>')
    for rr in range(40, RS, 22):
        svg.append(f'<path d="{arc(180, 100, rr)}" fill="none" stroke="{PAPER}" stroke-width="1" stroke-dasharray="2 8"/>')
    svg.append(f'<circle cx="{CX}" cy="{CY}" r="92" fill="{RED}"/>')
    svg.append(f'<path d="{arc(180, 0, R)}" fill="none" stroke="{INK}" stroke-width="2" stroke-dasharray="1 7" stroke-linecap="round"/>')

    new = set(rep["new"])
    early_name = rep["early"][1] if rep["early"] else None

    def place_label(bx, by, name, when, note, candidates, fits, fg, notecol, backing=None):
        full, short = f"{name} {when}", name
        for text in (full, short):
            for (tx, ty, anchor, lead) in candidates:
                w = text_width(text) + 4
                h = 34 if note else 19
                x0 = tx if anchor == "start" else tx - w
                box = (x0 - 4, ty - 15, x0 + w + 4, ty - 15 + h)
                if fits(box) and not overlaps(box, placed):
                    placed.append(box)
                    if lead:
                        labels.append(f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{tx:.1f}" y2="{ty-5:.1f}" stroke="{fg}" stroke-width="1"/>')
                    if backing:
                        labels.append(f'<rect x="{box[0]:.1f}" y="{box[1]:.1f}" width="{box[2]-box[0]:.1f}" height="{h}" rx="3" fill="{backing}"/>')
                    nm = escape(name)
                    rest = text[len(name):]
                    labels.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="15" text-anchor="{anchor}" fill="{fg}"><tspan font-weight="700">{nm}</tspan>{escape(rest)}</text>')
                    if note:
                        labels.append(f'<text x="{tx:.1f}" y="{ty+16:.1f}" font-size="12" font-style="italic" text-anchor="{anchor}" fill="{notecol}">{note}</text>')
                    return
        # nowhere clean: squeeze the name in at the first spot
        tx, ty, anchor, _ = candidates[0]
        labels.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="13" font-weight="700" text-anchor="{anchor}" fill="{fg}">{escape(name)}</text>')

    def in_page(b):
        return b[0] >= 10 and b[2] <= 790 and b[1] >= 10 and b[3] <= 316

    # night birds
    if rep["night"]:
        for t, s, n in rep["night"]:
            a = angle(t); bx, by = pt(a, R)
            svg.append(bird_shape(bx, by, PAPER))
            name = short_name(s) + (f" ×{n}" if n > 1 else "")
            cands = [(bx + 14, by + 20, "start", False), (bx - 14, by + 20, "end", False),
                     (bx + 14, by - 12, "start", False), (bx - 14, by - 12, "end", False),
                     (bx + 14, by + 44, "start", True), (bx - 14, by + 44, "end", True)]
            place_label(bx, by, name, f"{t:%H:%M}", "new this month" if s in new else "",
                        cands, in_night_sky, PAPER, YELLOW, backing=BLUE)
    else:
        qx, qy = pt(140, R * 0.62)
        labels.append(f'<text x="{qx:.1f}" y="{qy:.1f}" font-size="17" font-style="italic" text-anchor="middle" fill="{PAPER}">a quiet night</text>')

    # chorus birds: labels beside the birds if they fit cleanly, otherwise a numbered list
    chorus_pts = []
    for t, sp in rep["chorus"]:
        a = angle(t); bx, by = pt(a, R)
        chorus_pts.append((t, sp, a, bx, by))

    def try_floating():
        trial = list(placed)
        out = []
        for t, sp, a, bx, by in chorus_pts:
            note = "new this month" if sp in new else ""
            if a > 60:
                x, y = pt(a, R + 12); tx, ty, anchor = x - 20, y - 6, "end"
            else:
                (tx, ty), anchor = pt(a, R + 30), "start"
            text = f"{short_name(sp)} {t:%H:%M}"
            w = text_width(text) + 4; h = 34 if note else 19
            x0 = tx if anchor == "start" else tx - w
            box = (x0 - 4, ty - 15, x0 + w + 4, ty - 15 + h)
            if not in_page(box) or overlaps(box, trial):
                return None
            trial.append(box)
            out.append((tx, ty, anchor, sp, t, note, box))
        return out

    floating = try_floating()
    for t, sp, a, bx, by in chorus_pts:
        svg.append(bird_shape(bx, by, INK))

    if floating is not None:
        for tx, ty, anchor, sp, t, note, box in floating:
            placed.append(box)
            labels.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="15" text-anchor="{anchor}" fill="{INK}"><tspan font-weight="700">{escape(short_name(sp))}</tspan> {t:%H:%M}</text>')
            if note:
                labels.append(f'<text x="{tx:.1f}" y="{ty+16:.1f}" font-size="12" font-style="italic" text-anchor="{anchor}" fill="{INK if note == "early bird" else GREEN}">{note}</text>')
    elif chorus_pts:
        # numbered badges just outside the arc, nudged outward when they bump
        badges = []
        for i, (t, sp, a, bx, by) in enumerate(chorus_pts, start=1):
            rr = R + 22
            while True:
                x, y = pt(a, rr)
                if all(math.hypot(x - ox, y - oy) >= 21 for ox, oy in badges):
                    break
                rr += 20
            badges.append((x, y))
            placed.append((x - 11, y - 11, x + 11, y + 11))
            col = INK
            if rr > R + 22:
                lx0, ly0 = pt(a, R + 8)
                labels.append(f'<line x1="{lx0:.1f}" y1="{ly0:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1"/>')
            labels.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="{col}"/>')
            labels.append(f'<text x="{x:.1f}" y="{y+4.5:.1f}" font-size="12" font-weight="700" text-anchor="middle" fill="{PAPER}">{i}</text>')
        # the running order list
        ly = 140
        labels.append(f'<text x="632" y="{ly}" font-size="11" letter-spacing="1.5" fill="{INK}">RUNNING ORDER</text>')
        for i, (t, sp, a, bx, by) in enumerate(chorus_pts, start=1):
            ly += 20
            col = GREEN if sp in new else INK
            labels.append(f'<circle cx="641" cy="{ly-5}" r="9" fill="{INK}"/>')
            labels.append(f'<text x="641" y="{ly-1}" font-size="11" font-weight="700" text-anchor="middle" fill="{PAPER}">{i}</text>')
            labels.append(f'<text x="656" y="{ly}" font-size="14" fill="{col}"><tspan font-family="Space Mono, monospace" font-size="12">{t:%H:%M}</tspan> <tspan font-weight="700">{escape(short_name(sp))}</tspan></text>')
        if rep["chorus_extra"]:
            ly += 20
            labels.append(f'<text x="656" y="{ly}" font-size="13" font-style="italic" fill="{INK}">+{rep["chorus_extra"]} more after</text>')
    # first light and sunrise: small markers on the arc, labelled just inside it
    def marker(a, label, filled):
        x, y = pt(a, R)
        fill = RED if filled else PAPER
        labels.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{fill}" stroke="{INK}" stroke-width="2"/>')
        w = text_width(label, 13)
        for lx, ly, anchor in ((x + 12, y + 20, "start"), (*pt(a, R - 26), "middle"), (x + 12, y - 12, "start")):
            x0 = lx if anchor == "start" else lx - w / 2
            box = (x0 - 4, ly - 12, x0 + w + 4, ly + 6)
            if not overlaps(box, placed) and in_page(box):
                placed.append(box)
                labels.append(f'<text x="{lx:.1f}" y="{ly+2:.1f}" font-size="13" font-style="italic" text-anchor="{anchor}" fill="{INK}">{label}</text>')
                return

    marker(100, f"first light {fl:%-H:%M}", False)
    sa = angle(sr)
    if sa >= 0:
        marker(sa, f"sunrise {sr:%-H:%M}", True)

    if not rep["chorus"]:
        qx, qy = pt(45, R + 34)
        labels.append(f'<text x="{qx:.1f}" y="{qy:.1f}" font-size="16" font-style="italic" fill="{INK}">no song before {MORNING_ENDS:%-H}am</text>')
    if rep["chorus_extra"] and floating is not None:
        labels.append(f'<text x="{CX+R+16}" y="{CY-26}" font-size="15" font-weight="700" fill="{INK}">+{rep["chorus_extra"]} more</text>')

    # waves
    waves = []
    for i, (yy, col) in enumerate([(CY - 6, BLUE), (CY + 26, INK), (CY + 58, BLUE), (CY + 90, INK), (CY + 122, BLUE)]):
        x = -((i % 2) * 30)
        d = f"M{x} {yy+30} "
        while x < 820:
            d += f"Q{x+15} {yy-6} {x+30} {yy+8} Q{x+38} {yy+16} {x+46} {yy+6} Q{x+52} {yy+22} {x+60} {yy+30} "
            x += 60
        waves.append(f'<path d="{d}L820 480 L0 480 Z" fill="{col}" stroke="{PAPER}" stroke-width="2.5" stroke-linejoin="round"/>')

    # stats boxes
    serif = "font-family: 'Shippori Mincho', Georgia, serif; font-weight: 800;"
    box = f"background: {PAPER}; border: 2px solid {INK}; padding: 6px 12px; display: flex; flex-direction: column; min-width: 0;"
    small = "font-size: 11px; letter-spacing: 0.14em;"
    stats = []
    if rep["early"]:
        t, s = rep["early"]
        stats.append(f'<div style="{box}"><span style="{small}">EARLY BIRD</span><span style="{serif} font-size: 18px;">{escape(short_name(s))} {t:%H:%M}</span></div>')
    else:
        stats.append(f'<div style="{box}"><span style="{small}">EARLY BIRD</span><span style="{serif} font-size: 18px;">Still quiet</span></div>')
    if rep["top"]:
        tops = " · ".join(f"{escape(short_name(s))} {n}" for s, n in rep["top"])
        stats.append(f'<div style="{box} flex-shrink: 1;"><span style="{small}">MOST BIRDS</span><span style="{serif} font-size: 18px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{tops}</span></div>')
    if rep["new"]:
        names = ", ".join(escape(short_name(s)) for s in rep["new"][:2]) + (f" +{len(rep['new'])-2}" if len(rep["new"]) > 2 else "")
        stats.append(f'<div style="{box}"><span style="{small}">NEW THIS MONTH</span><span style="{serif} font-size: 18px; color: {GREEN}; white-space: nowrap;">{names}</span></div>')

    day = rep["day"]
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=800, height=480">
<title>Dawn chorus</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;800&family=Work+Sans:wght@400;600;700&family=Space+Mono:wght@400;700&display=swap">
<style>
  html, body {{ margin: 0; padding: 0; width: 800px; height: 480px; overflow: hidden; background: {PAPER}; }}
  body {{ font-family: "Work Sans", "Helvetica Neue", sans-serif; color: {INK}; }}
  svg text {{ font-family: "Work Sans", "Helvetica Neue", sans-serif; }}
</style>
</head>
<body>
<div style="width: 800px; height: 480px; box-sizing: border-box; border: 3px solid {INK}; position: relative; overflow: hidden;">
  <svg width="794" height="474" viewBox="0 0 794 474" role="img" aria-label="Dawn chorus report">
    {"".join(svg)}
    {"".join(waves)}
    {"".join(labels)}
  </svg>
  <div style="position: absolute; top: 20px; right: 22px; width: 176px; background: {PAPER}; border: 3px solid {RED}; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px;">
    <span style="{serif} font-size: 30px; line-height: 1;">Dawn chorus</span>
    <span style="font-size: 12px; letter-spacing: 0.12em;">{escape(PLACE.upper())} · {day.day} {day:%b}</span>
    <span style="font-size: 12px; border-top: 1px solid {INK}; padding-top: 4px; margin-top: 2px;">First light {fl:%-H:%M} · Sunrise {sr:%-H:%M}</span>
  </div>
  <div style="position: absolute; left: 20px; right: 20px; bottom: 16px; display: flex; gap: 12px;">
    {"".join(stats)}
  </div>
</div>
</body>
</html>
'''


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--demo" in args:
        data = demo_data()
    else:
        day = date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args else date.today()
        data = real_data(day)
    rep = summarise(data)
    with open(OUTPUT, "w") as f:
        f.write(render(rep))
    print(f"Wrote {OUTPUT} for {rep['day']:%a %d %b}: {rep['species']} species, {rep['calls']} calls, "
          f"{len(rep['night'])} in the night sky, {len(rep['chorus'])} on the arc"
          + (f" (+{rep['chorus_extra']} more)" if rep["chorus_extra"] else ""))