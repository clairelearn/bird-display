"""
Evening rundown: today's three most-heard birds, each drawn as the
waveform of its clearest recording. Writes index.html (800x480).

  python3 build_evening.py                 today, up to 8pm
  python3 build_evening.py --date 2026-09-30
  python3 build_evening.py --demo          made-up calls, for testing layout

Needs:  python3 -m pip install numpy soundfile
"""
import io
import json
import math
import random
import sys
import urllib.request
from collections import Counter
from datetime import datetime, date, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

import numpy as np
import soundfile as sf

# ---------------- Settings ----------------
STATION_ID = "29318"            # BirdNET-Pi - bedminster (shares sound clips)
PLACE = "Bedminster"
LAT, LON = 51.44, -2.60
DAY_ENDS = time(20, 0)
MIN_CONFIDENCE = 0.7
CLEAR_CONFIDENCE = 0.8          # clips must be at least this sure to be drawn
CLIPS_PER_BIRD = 4              # recordings to compare per bird
HOW_MANY = 3
POINTS = 160                    # waveform resolution
PADDING = 0.25                  # seconds either side of the call
OUTPUT = "index.html"
UK = ZoneInfo("Europe/London")

INK, PAPER, RED, YELLOW, BLUE, GREEN = "#1b1b1b", "#f2efe6", "#b5302a", "#e6b92e", "#24508f", "#3e6e3a"
WAVE_INKS = [BLUE, RED, GREEN]

DROP_PREFIXES = ("Eurasian ", "Common ", "European ", "Northern ")


def short_name(name):
    for p in DROP_PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


# ---------------- Sunset ----------------
def sunset(day):
    rad, deg = math.radians, math.degrees
    n = day.timetuple().tm_yday
    lng_hour = LON / 15
    t = n + ((18 - lng_hour) / 24)
    m = 0.9856 * t - 3.289
    l = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m)) + 282.634) % 360
    ra = deg(math.atan(0.91764 * math.tan(rad(l)))) % 360
    ra += (math.floor(l / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15
    sin_dec = 0.39782 * math.sin(rad(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(rad(90.833)) - sin_dec * math.sin(rad(LAT))) / (cos_dec * math.cos(rad(LAT)))
    h = deg(math.acos(cos_h)) / 15
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


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bird-display"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def todays_detections(day):
    start = datetime.combine(day, time(0), tzinfo=UK)
    end = datetime.combine(day, DAY_ENDS, tzinfo=UK)
    found, cursor = [], None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        page = ask("""{ station(id: "%s") { detections(first: 100, confidenceGte: %s%s) {
            nodes { timestamp confidence species { commonName scientificName }
                    soundscape { url startTime endTime } }
            pageInfo { hasNextPage endCursor } } } }""" % (STATION_ID, MIN_CONFIDENCE, after))["station"]["detections"]
        for d in page["nodes"]:
            when = datetime.fromisoformat(d["timestamp"]).astimezone(UK)
            if when < start:
                return found
            if when < end:
                d["when"] = when
                found.append(d)
        if not page["pageInfo"]["hasNextPage"]:
            return found
        cursor = page["pageInfo"]["endCursor"]


def mono(audio):
    return audio.mean(axis=1) if audio.ndim > 1 else audio


def envelope(audio, rate, start_s, end_s):
    """Shape of the call: loudness in POINTS slices, scaled 0..1."""
    audio = mono(audio)
    a = max(0, int((start_s - PADDING) * rate))
    b = min(len(audio), int((end_s + PADDING) * rate))
    call = audio[a:b] if b - a > rate * 0.3 else audio
    chunks = np.array_split(np.abs(call), POINTS)
    env = np.array([np.percentile(c, 98) if len(c) else 0 for c in chunks])
    env = np.clip(env - np.percentile(env, 10), 0, None)
    return (env / (env.max() or 1)).tolist()


def clarity(audio, rate, start_s, end_s):
    """Signal-to-noise in dB: how much the call stands out from the background."""
    audio = np.abs(mono(audio))
    a, b = int(start_s * rate), int(end_s * rate)
    inside = audio[a:b]
    outside = np.concatenate([audio[:a], audio[b:]])
    if len(inside) < rate * 0.1 or len(outside) < rate * 0.1:
        outside = audio
    signal = np.percentile(inside, 95) if len(inside) else 0
    noise = np.median(outside) or 1e-6
    return 20 * math.log10(max(signal, 1e-6) / noise)


def month_counts():
    data = ask("""{ station(id: "%s") { topSpecies(period: {count: 30, unit: "day"}) {
        count species { commonName } } } }""" % STATION_ID)
    return {e["species"]["commonName"]: e["count"] for e in data["station"]["topSpecies"]}


def best_clip(dets, name):
    """The clearest confident recording of one species, or None."""
    clips = [d for d in dets if d["species"]["commonName"] == name
             and d["confidence"] >= CLEAR_CONFIDENCE
             and d.get("soundscape") and d["soundscape"].get("url")]
    clips = sorted(clips, key=lambda d: -d["confidence"])[:CLIPS_PER_BIRD]
    best = None
    for d in clips:
        sc = d["soundscape"]
        try:
            audio, rate = sf.read(io.BytesIO(download(sc["url"])))
        except Exception as e:
            print(f"  couldn't read a {name} clip: {e}")
            continue
        score = clarity(audio, rate, sc["startTime"], sc["endTime"])
        if best is None or score > best["clarity"]:
            best = {"name": name, "latin": d["species"]["scientificName"], "when": d["when"],
                    "clarity": score, "env": envelope(audio, rate, sc["startTime"], sc["endTime"])}
    return best


def real_data(day):
    dets = todays_detections(day)
    set_at = sunset(day)
    counts = Counter(d["species"]["commonName"] for d in dets)
    month = month_counts()

    clips = {}
    def clip_for(name):
        if name not in clips:
            clips[name] = best_clip(dets, name)
        return clips[name]

    chosen, used = [], set()
    # the regular: most heard today
    for name, _ in counts.most_common():
        c = clip_for(name)
        if c:
            chosen.append(("Leading voice", c)); used.add(name); break
    # the rarity: fewest detections here this month
    for name in sorted(counts, key=lambda n: (month.get(n, 0), -counts[n])):
        if name in used:
            continue
        c = clip_for(name)
        if c:
            chosen.append(("Guest star", c)); used.add(name); break
    # the wildcard: clearest recording of anything else
    others = [clip_for(n) for n in counts if n not in used]
    others = [c for c in others if c]
    if others:
        c = max(others, key=lambda c: c["clarity"])
        chosen.append(("Solo", c)); used.add(c["name"])

    for role, c in chosen:
        c["count"] = counts[c["name"]]
        c["month"] = month.get(c["name"], c["count"])

    before_sunset = Counter(d["species"]["commonName"] for d in dets if d["when"] < set_at)
    return {"day": day, "sunset": set_at, "birds": chosen,
            "species": len(counts), "calls": len(dets),
            "all_species": [n for n, _ in before_sunset.most_common()]}


def demo_data():
    rng = np.random.default_rng(3)
    rate = 16000
    def fake(kind):
        t = np.arange(0, 3.0, 1 / rate)
        sig = 0.03 * rng.standard_normal(len(t))
        if kind == "magpie":      # harsh rattle: fast regular bursts
            for s in np.arange(0.4, 2.4, 0.11):
                m = (t > s) & (t < s + 0.06)
                sig[m] += rng.uniform(0.6, 1.0) * rng.standard_normal(m.sum())
        elif kind == "curlew":    # long rising and falling whistle
            m = (t > 0.3) & (t < 2.7)
            sig[m] += np.sin(np.pi * (t[m] - 0.3) / 2.4) ** 1.5 * np.sin(2 * np.pi * 2500 * t[m])
        else:                     # wren: rapid trill that builds
            for s in np.arange(0.3, 2.6, 0.045):
                m = (t > s) & (t < s + 0.025)
                sig[m] += (0.3 + 0.7 * (s / 2.6)) * np.sin(2 * np.pi * 6000 * t[m])
        return envelope(sig, rate, 0, 3.0)
    day = date(2026, 9, 30)
    def bird(name, latin, count, month, hm, kind):
        return {"name": name, "latin": latin, "count": count, "month": month,
                "when": datetime(2026, 9, 30, *hm, tzinfo=UK), "env": fake(kind)}
    return {"day": day, "sunset": sunset(day), "species": 14, "calls": 212,
            "birds": [("Leading voice", bird("Eurasian Magpie", "Pica pica", 168, 3900, (14, 32), "magpie")),
                      ("Guest star", bird("Eurasian Curlew", "Numenius arquata", 10, 12, (5, 48), "curlew")),
                      ("Solo", bird("Eurasian Wren", "Troglodytes troglodytes", 7, 240, (9, 12), "wren"))],
            "all_species": ["Eurasian Magpie", "Herring Gull", "Rook", "Eurasian Curlew", "Carrion Crow",
                            "Eurasian Wren", "European Robin", "Great Tit", "Eurasian Blue Tit", "Mallard",
                            "Common Moorhen", "Eurasian Jackdaw", "Common Wood-Pigeon", "Gadwall"]}


# ---------------- Draw ----------------
def smooth(values, k=2):
    v = np.array(values, dtype=float)
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    return np.convolve(np.pad(v, k, mode="edge"), kernel, mode="valid")


def carved_wave(env, colour, x0, mid, width, half, rnd, clip_id=None):
    """Waveform as hand-cut bars: slightly uneven widths and edges, like lino."""
    n = 70
    env = np.array([c.max() for c in np.array_split(np.array(env), n)])
    step = width / n
    out = [f'<line x1="{x0 - 6}" y1="{mid}" x2="{x0 + width + 6}" y2="{mid}" stroke="{INK}" stroke-width="2"/>']
    for i, v in enumerate(env):
        h = max(4, v * half * 2)
        w = step * rnd.uniform(0.5, 0.68)
        x = x0 + i * step + (step - w) / 2
        j = lambda: rnd.uniform(-1.2, 1.2)
        out.append(f'<path d="M{x + j():.1f} {mid - h / 2 + j():.1f} L{x + w + j():.1f} {mid - h / 2 + j():.1f} '
                   f'L{x + w + j():.1f} {mid + h / 2 + j():.1f} L{x + j():.1f} {mid + h / 2 + j():.1f} Z" fill="{colour}"/>')
    return "".join(out)


def jitter_rect(x, y, w, h, rnd, amp=1.6, step=18):
    pts = []
    for i in range(0, int(w) + 1, step): pts.append((x + i, y + rnd.uniform(-amp, amp)))
    for i in range(0, int(h) + 1, step): pts.append((x + w + rnd.uniform(-amp, amp), y + i))
    for i in range(int(w), -1, -step): pts.append((x + i, y + h + rnd.uniform(-amp, amp)))
    for i in range(int(h), -1, -step): pts.append((x + rnd.uniform(-amp, amp), y + i))
    return "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in pts) + " Z"


def render(rep):
    rnd = random.Random(rep["day"].toordinal())
    W, H = 800, 480
    svg = []
    serif = "Shippori Mincho, Georgia, serif"

    # header
    day = rep["day"]
    place_line = escape(f"{PLACE} · {day:%A} {day.day} {day:%B}".upper())
    svg.append(f'<text x="30" y="40" font-size="12" letter-spacing="2" fill="{INK}">{place_line}</text>')
    svg.append(f'<text x="28" y="84" font-family="{serif}" font-weight="800" font-size="44" fill="{INK}">Today\'s voices</text>')
    svg.append(f'<text x="772" y="84" text-anchor="end" fill="{INK}">'
               f'<tspan font-family="{serif}" font-weight="800" font-size="26">{rep["species"]}</tspan>'
               f'<tspan font-size="14" dx="5">species</tspan>'
               f'<tspan font-size="14" dx="10">·</tspan>'
               f'<tspan font-family="{serif}" font-weight="800" font-size="26" dx="10">{rep["calls"]}</tspan>'
               f'<tspan font-size="14" dx="5">calls</tspan></text>')

    # three voices
    top, row_h = 100, 80
    wave_x, wave_w = 238, 530
    for i, (role, b) in enumerate(rep["birds"]):
        y0 = top + i * row_h
        mid = y0 + row_h / 2
        colour = WAVE_INKS[i % len(WAVE_INKS)]
        svg.append(f'<line x1="28" y1="{y0}" x2="772" y2="{y0}" stroke="{INK}" stroke-width="1" stroke-dasharray="2 5"/>')
        svg.append(f'<rect x="30" y="{y0 + 12}" width="{len(role) * 9.2 + 16:.0f}" height="18" fill="{colour}"/>')
        svg.append(f'<text x="37" y="{y0 + 25}" font-size="11" font-weight="700" letter-spacing="1.5" fill="{PAPER}">{role.upper()}</text>')
        svg.append(f'<text x="30" y="{y0 + 56}" font-family="{serif}" font-weight="800" font-size="27" fill="{INK}">{escape(short_name(b["name"]))}</text>')
        if role == "Guest star":
            detail = f'{b["month"]} call{"s" if b["month"] != 1 else ""} this month'
        else:
            detail = f'heard {b["count"]} time{"s" if b["count"] != 1 else ""} today'
        svg.append(f'<text x="30" y="{y0 + 76}" font-size="12.5" fill="{INK}"><tspan font-family="Space Mono, monospace" font-weight="700">{b["when"]:%H:%M}</tspan> · {detail}</text>')
        svg.append(carved_wave(b["env"], colour, wave_x, mid, wave_w, row_h / 2 - 8, rnd, f"w{i}"))
    if not rep["birds"]:
        svg.append(f'<text x="400" y="230" font-family="{serif}" font-weight="800" font-size="30" text-anchor="middle" fill="{INK}">A quiet day. No clear voices to draw.</text>')

    # bottom: everyone heard before sunset, then the sun going down into the sea
    list_y = top + 3 * row_h
    sea_y = 418
    sx = 712
    svg.append(f'<line x1="28" y1="{list_y}" x2="772" y2="{list_y}" stroke="{INK}" stroke-width="1" stroke-dasharray="2 5"/>')
    svg.append(f'<circle cx="{sx}" cy="{sea_y + 8}" r="34" fill="{RED}"/>')
    for j, (yy, col) in enumerate([(sea_y, BLUE), (sea_y + 22, INK), (sea_y + 44, BLUE)]):
        x = -((j % 2) * 30)
        d = f"M{x} {yy + 30} "
        while x < 830:
            d += f"Q{x + 15} {yy - 6} {x + 30} {yy + 8} Q{x + 38} {yy + 16} {x + 46} {yy + 6} Q{x + 52} {yy + 22} {x + 60} {yy + 30} "
            x += 60
        svg.append(f'<path d="{d}L830 490 L-40 490 Z" fill="{col}" stroke="{PAPER}" stroke-width="2.5" stroke-linejoin="round"/>')
    sun_cy, arc_r = sea_y + 8, 44
    svg.append(f'<path id="sunarc" d="M{sx - arc_r} {sun_cy} A{arc_r} {arc_r} 0 0 1 {sx + arc_r} {sun_cy}" fill="none"/>')
    svg.append(f'<text font-size="13" font-style="italic" letter-spacing="1" fill="{INK}">'
               f'<textPath href="#sunarc" startOffset="50%" text-anchor="middle">sunset {rep["sunset"]:%-H:%M}</textPath></text>')

    names = [short_name(n) for n in rep["all_species"]]
    band = (f'<div style="position: absolute; left: 30px; right: 170px; top: {list_y + 8}px; font-size: 12.5px; line-height: 1.35;">'
            f'<span style="font-size: 10.5px; letter-spacing: 0.14em; font-weight: 700;">HEARD BEFORE SUNSET&nbsp;&nbsp;</span>'
            f'<span style="font-family: \'Shippori Mincho\', Georgia, serif; font-weight: 800; font-size: 14.5px;">{escape(" · ".join(names)) or "nobody"}</span></div>')

    border = f'<path d="{jitter_rect(6, 6, W - 12, H - 12, rnd, 1.8)}" fill="none" stroke="{INK}" stroke-width="3"/>'
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=800, height=480">
<title>Today's voices</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;800&family=Work+Sans:wght@400;600;700&family=Space+Mono:wght@700&display=swap">
<style>
  html, body {{ margin: 0; padding: 0; width: 800px; height: 480px; overflow: hidden; background: {PAPER}; }}
  body {{ font-family: "Work Sans", "Helvetica Neue", sans-serif; color: {INK}; }}
</style>
</head>
<body>
<div style="width: 800px; height: 480px; position: relative; overflow: hidden;">
  <svg width="800" height="480" viewBox="0 0 800 480" font-family="Work Sans, Helvetica Neue, sans-serif" role="img" aria-label="Today's voices: three bird calls drawn as waveforms">
    {"".join(svg)}
    {border}
  </svg>
  {band}
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
    with open(OUTPUT, "w") as f:
        f.write(render(data))
    names = ", ".join(f"{r}: {short_name(b['name'])}" for r, b in data["birds"]) or "none"
    print(f"Wrote {OUTPUT} for {data['day']:%a %d %b}: {data['species']} species, {data['calls']} calls. Voices: {names}")
