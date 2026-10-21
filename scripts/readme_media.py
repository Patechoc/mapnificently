"""Record the README's animations and screenshot from a running server (`make readme-media`).

Needs Playwright with Chromium and Pillow, which are not app dependencies:

    uv run --no-project --python 3.12 --with playwright==1.48.0 --with pillow==11.3.0 \\
        --with numpy python scripts/readme_media.py --base-url http://127.0.0.1:8799

(`playwright install chromium` once, if Chromium is missing.) The data of the Oslofjord,
Bergen and Trondheim regions must be fetched.

GIFs stay small: one shared 255-colour palette, and every frame after the first only stores
the pixels that changed (the rest is transparent).
"""

import argparse
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image
from playwright.sync_api import Page, sync_playwright

SIZE = {"width": 960, "height": 600}
COMPUTED = "document.getElementById('status').textContent.startsWith('Computed')"
TILES_LOADED = """() => {
  const tiles = [...document.querySelectorAll('.leaflet-tile')];
  return tiles.length > 0 && tiles.every((t) => t.complete && t.naturalWidth > 0);
}"""
CAPTION = """([title, sub]) => {
  let el = document.getElementById('rec-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'rec-caption';
    el.style.cssText = 'position:absolute;right:58px;top:12px;z-index:1200;background:#fff;'
      + 'color:#1d2330;padding:8px 12px;border-radius:10px;font:600 15px/1.3 system-ui,sans-serif;'
      + 'box-shadow:0 4px 16px rgba(0,0,0,.25)';
    document.body.append(el);
  }
  el.replaceChildren();
  const t = document.createElement('div'); t.textContent = title;
  const s = document.createElement('div'); s.textContent = sub;
  s.style.cssText = 'font-weight:400;font-size:12px;color:#5d6677';
  el.append(t, s);
}"""

# id, name, station, lat, lon
CITIES = [
    ("oslo", "Oslo", "Oslo S", 59.9109, 10.7536),
    ("bergen", "Bergen", "Bergen stasjon", 60.3903, 5.3330),
    ("trondheim", "Trondheim", "Trondheim S", 63.4365, 10.3990),
]
FRAME_MS, FIRST_MS, HOLD_MS = 130, 700, 1800
CLEAR = 255  # palette index meaning "unchanged since the previous frame"


def next_weekday() -> date:
    day = datetime.now(ZoneInfo("Europe/Oslo")).date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def open_view(page: Page, url: str, lat: float, lon: float, zoom: int) -> None:
    page.goto(url)
    page.reload()  # hash-only navigations do not reload
    page.wait_for_function(COMPUTED, timeout=60_000)
    page.evaluate(
        "([lat, lon, z]) => { map.setView([lat, lon], z, {animate: false}); }", [lat, lon, zoom]
    )


def settle(page: Page) -> None:
    page.wait_for_function(TILES_LOADED, timeout=60_000)
    page.wait_for_timeout(400)


def set_minutes(page: Page, minutes: int) -> None:
    page.evaluate(
        "m => { $('minutes').value = m; $('minutes').dispatchEvent(new Event('input')); }", minutes
    )
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def record_cities(page: Page, base: str, day: date, frames: Path) -> list[list[Path]]:
    """Each city from its main station, 1 to 45 minutes; the panel collapses halfway."""
    clips = []
    for city_id, name, station, lat, lon in CITIES:
        open_view(
            page, f"{base}#city={city_id}&o={lat},{lon}&t=1&at={day}T08:00&ch=2", lat, lon, 11
        )
        page.evaluate("setCollapsed(false)")
        page.evaluate(
            "map.panBy([-170, 0], {animate: false})"
        )  # keep the station clear of the panel
        page.evaluate(CAPTION, [name, f"from {station}, weekday 08:00"])
        settle(page)
        clip = []
        for minute in range(1, 46):
            if minute == 24:
                page.evaluate("setCollapsed(true)")
            set_minutes(page, minute)
            path = frames / f"{city_id}-{minute:02d}.png"
            page.screenshot(path=path)
            clip.append(path)
        clips.append(clip)
    page.evaluate("setCollapsed(false)")
    return clips


def record_meeting(page: Page, base: str, day: date, frames: Path) -> list[list[Path]]:
    """Two people (Majorstuen, Lillestrøm): where can either get, then where can both get."""
    origins = "59.92932,10.71634;59.95520,11.04930"
    open_view(
        page, f"{base}#city=oslo&o={origins}&t=1&at={day}T08:00&mode=union", 59.935, 10.88, 11
    )
    page.evaluate("setCollapsed(true)")
    settle(page)
    union, both = [], []
    page.evaluate(
        CAPTION, ["Where can either of us get?", "Majorstuen and Lillestrøm, weekday 08:00"]
    )
    for minute in range(1, 31):
        set_minutes(page, minute)
        path = frames / f"union-{minute:02d}.png"
        page.screenshot(path=path)
        union.append(path)
    page.evaluate("document.querySelector('input[value=intersection]').click()")
    page.evaluate(CAPTION, ["Where can we both get?", "the meeting area, weekday 08:00"])
    for minute in range(20, 46):
        set_minutes(page, minute)
        path = frames / f"both-{minute:02d}.png"
        page.screenshot(path=path)
        both.append(path)
    page.evaluate("document.querySelector('input[value=union]').click()")
    page.evaluate("setCollapsed(false)")
    return [union, both]


def record_route(page: Page, base: str, day: date, out: Path) -> None:
    """Moss stasjon → Helsfyr, Oslo, leg by leg."""
    page.set_viewport_size({"width": 1100, "height": 820})
    url = f"{base}#city=moss&o=59.43214,10.65705&to=59.91267,10.80105&t=60&at={day}T08:00&ch=2"
    page.goto(url)
    page.reload()
    page.wait_for_function("state.route && state.route.result", timeout=60_000)
    page.evaluate(
        "state.origins[0].label = 'Moss stasjon'; state.destination.label = 'Helsfyr, Oslo';"
        " renderRoute(); renderOrigins();"
    )
    # Start the panel at the change-time row, so no half-cut setting shows above the route.
    page.evaluate(
        "() => { const body = $('panel-body');"
        " body.scrollTop = $('change').closest('.row').offsetTop - body.offsetTop - 4; }"
    )
    settle(page)
    page.screenshot(path=out.with_suffix(".tmp.png"))
    Image.open(out.with_suffix(".tmp.png")).convert("RGB").quantize(
        colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    ).save(out, optimize=True)
    out.with_suffix(".tmp.png").unlink()
    page.set_viewport_size(SIZE)


def write_gif(clips: list[list[Path]], out: Path) -> None:
    """Clips play one after another; each starts slowly and ends on a held frame."""
    sequence = [
        (path, FIRST_MS if i == 0 else HOLD_MS if i == len(clip) - 1 else FRAME_MS)
        for clip in clips
        for i, path in enumerate(clip)
    ]
    samples = [
        Image.open(p).convert("RGB")
        for clip in clips
        for p in (clip[0], clip[len(clip) // 2], clip[-1])
    ]
    montage = Image.new("RGB", (samples[0].width, sum(s.height for s in samples)))
    for i, sample in enumerate(samples):
        montage.paste(sample, (0, i * sample.height))
    palette_img = montage.quantize(
        colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette = [*palette_img.getpalette()[: 255 * 3], 0, 0, 0]
    palette_img.putpalette(palette)

    frames, previous = [], None
    for path, _ in sequence:
        indices = np.array(
            Image.open(path).convert("RGB").quantize(palette=palette_img, dither=Image.Dither.NONE)
        )
        shown = indices.copy()
        if previous is not None:
            indices[indices == previous] = CLEAR
        previous = shown
        frame = Image.fromarray(indices.astype(np.uint8)).convert("P")
        frame.putpalette(palette)
        frames.append(frame)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=[ms for _, ms in sequence],
        loop=0,
        transparency=CLEAR,
        disposal=1,
        optimize=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/")
    parser.add_argument("--out", type=Path, default=Path("docs/media"))
    parser.add_argument("--date", type=date.fromisoformat, default=next_weekday())
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["cities", "meeting", "route"],
        default=["cities", "meeting", "route"],
        help="Record only these (default: all).",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/"
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        frames = Path(tmp)
        browser = p.chromium.launch()
        page = browser.new_page(viewport=SIZE, device_scale_factor=1)
        if "cities" in args.only:
            write_gif(record_cities(page, base, args.date, frames), args.out / "cities.gif")
        if "meeting" in args.only:
            write_gif(record_meeting(page, base, args.date, frames), args.out / "meeting-point.gif")
        if "route" in args.only:
            record_route(page, base, args.date, args.out / "route.png")
        browser.close()
    for path in sorted(args.out.iterdir()):
        print(f"{path}: {path.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
