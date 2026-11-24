"""Render the Windows 7 README from the desktop scene and public GitHub data."""
from __future__ import annotations
import base64
import hashlib
import html as markup
import io
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "windows7"
OWNER = "marxusss"
# All repositories existing at setup are excluded except Reality Restyler.
# Future public, owned, non-fork repositories are picked up automatically.
CUTOFF = "2026-11-24T07:39:01Z"
FEATURED = "reality-restyler"
WIDTH, HEIGHT, FPS, SECONDS = 1024, 768, 30, 14
WALLPAPER = "https://cdn.wallpaperhub.app/cloudcache/6/9/0/e/e/f/690eefe3ba1f553e0ea527f51ee407b604b681b4.jpg"
ICONS = {
    "folder-full.png": "https://raw.githubusercontent.com/TheJustRun/Dildows-7/main/assets/icons/folderwithfile.png",
    "explorer-task.png": "https://raw.githubusercontent.com/TheJustRun/Dildows-7/main/assets/icons/explorer.png",
}

def get(url, api=False):
    headers = {"User-Agent": "Windows7-profile-render"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as res:
        return res.read(), res.headers

def api(path):
    data, headers = get("https://api.github.com" + path, api=True)
    return json.loads(data), headers

def eligible(r):
    return (r["owner"]["login"].lower() == OWNER and not r["private"] and not r["fork"]
            and r["name"].lower() != OWNER
            and (r["name"].lower() == FEATURED or r["created_at"] > CUTOFF))

def fetch_stats():
    repos = []
    for n in range(1, 101):
        page, _ = api(f"/users/{OWNER}/repos?type=owner&per_page=100&page={n}")
        repos.extend(r for r in page if eligible(r))
        if len(page) < 100:
            break
    repos.sort(key=lambda r: (r["name"].lower() != FEATURED, r["name"].lower()))
    commits = 0
    for r in repos:
        full = urllib.parse.quote(r["name"], safe="")
        rows, headers = api(f"/repos/{OWNER}/{full}/commits?author={OWNER}&per_page=1")
        last = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>; rel="last"', headers.get("Link", ""))
        commits += int(last.group(1)) if last else len(rows)
    return {
        "repos": [{"name": r["name"], "url": r["html_url"],
                   "updated": r["pushed_at"][:10]} for r in repos],
        "commits": commits,
        "stars": sum(r["stargazers_count"] for r in repos),
        "count": len(repos),
    }

def uri(data, mime):
    return "data:" + mime + ";base64," + base64.b64encode(data).decode()

def build_html(source, stats):
    wall, _ = get(WALLPAPER)
    with Image.open(io.BytesIO(wall)) as im:
        assert im.size == (5333, 4000), f"Unexpected wallpaper resolution: {im.size}"
        buffer = io.BytesIO()
        im.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(buffer, "PNG")
    source = source.replace("url('bliss.jpg')", "url(" + uri(buffer.getvalue(), "image/png") + ")")
    extra = {}
    for name, url in ICONS.items():
        raw, _ = get(url)
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            assert im.width >= 32 and im.height >= 32, name
        extra[name] = uri(raw, "image/png")
    source = re.sub(r'/\*PROFILE_DATA\*/.*?/\*END_PROFILE_DATA\*/',
                    lambda _: json.dumps(stats, ensure_ascii=True).replace("<", "\\u003c"), source, count=1, flags=re.S)
    source = re.sub(r'/\*EXTRA_ASSETS\*/.*?/\*END_EXTRA_ASSETS\*/',
                    lambda _: json.dumps(extra), source, count=1, flags=re.S)
    return source.replace("<script>", "<script>window.__renderMode=true;", 1)

def frame_key(i):
    t = i/FPS
    if t < 1: return 0
    if t < 2.50: return i
    if t < 2.86: return 75
    if t < 4.30: return i
    if t < 4.65: return 129
    if t < 6.15: return i
    if t < 6.50: return 185
    if t < 7.40: return i
    if t < 7.60: return 222
    if t < 8.00: return 228
    if t < 8.40: return 240
    if t < 8.80: return 252
    return 264

def render(source, stats):
    import subprocess
    import tempfile
    import imageio_ffmpeg
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(source, wait_until="load")
            page.evaluate("Promise.all([...document.images].map(i=>i.decode()))")
            assert not errors, errors
            assert page.evaluate("[...document.images].every(i=>i.complete&&i.naturalWidth>0)")
            page.evaluate("frame(10)")
            targets = page.evaluate("""[...document.querySelectorAll('#repo-desktop [data-profile-link], #profile [data-profile-link]')].map(a=>{let r=a.getBoundingClientRect();return {coords:[Math.floor(r.x),Math.floor(r.y),Math.ceil(r.right),Math.ceil(r.bottom)],href:a.href,label:a.textContent.trim()}})""")
            assert len(targets) == 2 + 2*min(4, len(stats["repos"])), targets
            last = None
            for i in range(FPS*SECONDS):
                key = frame_key(i)
                path = tmp/f"frame-{i:04d}.png"
                if key!=last:
                    page.evaluate("t=>frame(t)",key/FPS)
                    page.screenshot(path=str(path))
                    prev=path
                    last=key
                else:
                    os.link(prev,path)
            assert not errors, errors
            browser.close()
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        inp = ["-framerate",str(FPS),"-i",str(tmp/"frame-%04d.png")]
        def run(args):
            subprocess.run([ff,"-hide_banner","-loglevel","error","-y",*args],check=True)
        palette = tmp/"palette.png"
        run([*inp,"-vf","palettegen=stats_mode=full","-frames:v","1",str(palette)])
        gif = OUT/"desktop.gif"
        run([*inp,"-i",str(palette),"-lavfi","paletteuse=dither=sierra2_4a:diff_mode=rectangle","-loop","-1","-gifflags","+transdiff",str(gif)])
        with Image.open(gif) as im:
            assert im.size == (WIDTH,HEIGHT)
            assert im.n_frames > 100
            assert "loop" not in im.info, im.info
            duration=sum((im.seek(i),im.info["duration"])[1] for i in range(im.n_frames))
            assert abs(duration-SECONDS*1000)<100, duration
        assert gif.stat().st_size<10*1024*1024, gif.stat().st_size
        for old in OUT.glob("tile-*.png"):
            old.unlink()
    links = {}
    for target in targets:
        links[target["href"]] = target["label"]
    anchors = " &nbsp;·&nbsp; ".join(f'<a href="{markup.escape(url,quote=True)}">{markup.escape(label)}</a>' for url,label in reversed(list(links.items())))
    readme = '<!-- Generated by profile/render.py -->\n<p align="center"><img src="./assets/windows7/desktop.gif" width="100%" alt="Max Vorwaller – Software Engineer, Seattle; animated Windows 7 desktop"></p>\n<p align="center">'+anchors+'</p>\n'
    (ROOT/"README.md").write_text(readme,encoding="utf-8")
    print(f"Verified single non-looping GIF: {gif.stat().st_size:,} bytes, {duration} ms")

def main():
    stats = fetch_stats()
    source = (ROOT/"profile"/"desktop.html").read_text(encoding="utf-8")
    state = ROOT/"profile"/"stats.json"
    source_hash=hashlib.sha256(Path(__file__).read_bytes()+source.encode()).hexdigest()
    snapshot={"source_hash":source_hash,**stats}
    if state.exists() and json.loads(state.read_text())==snapshot and (OUT/"desktop.gif").is_file():
        print("No public project or statistics changes.")
        return
    print(json.dumps(stats))
    render(build_html(source,stats),stats)
    state.write_text(json.dumps(snapshot,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
