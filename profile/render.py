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

def partitions(targets):
    # Merge vertically overlapping link bands; do not put a seam through a link.
    # Each row uses adjacent img elements, so no image maps or CSS are required.
    bands = []
    for target in sorted(targets, key=lambda r:r["y0"]):
        if bands and target["y0"] < bands[-1][1]:
            bands[-1][1] = max(bands[-1][1], target["y1"])
            bands[-1][2].append(target)
        else:
            bands.append([target["y0"], target["y1"], [target]])
    rows = []
    y = 0
    for top, bottom, items in bands:
        if y < top:
            rows.append([(0,y,WIDTH,top,None)])
        row, x = [], 0
        for r in sorted(items,key=lambda r:r["x0"]):
            if r["x0"] < x:
                # Adjacent desktop entries can land in the same merged band for
                # long labels. Reject overlapping clickable regions, never mislink.
                raise ValueError("Overlapping link rectangles: " + str(items))
            if r["x0"] > x:
                row.append((x,top,r["x0"],bottom,None))
            row.append((r["x0"],top,r["x1"],bottom,r))
            x = r["x1"]
        if x < WIDTH:
            row.append((x,top,WIDTH,bottom,None))
        rows.append(row)
        y = bottom
    if y < HEIGHT:
        rows.append([(0,y,WIDTH,HEIGHT,None)])
    return rows

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
        targets = page.evaluate("""[...document.querySelectorAll('#repo-desktop [data-profile-link], #profile [data-profile-link]')].map(a=>{
          let r=a.getBoundingClientRect();
          return {x0:Math.floor(r.x/4)*4, x1:Math.ceil((r.right)/4)*4,
                  y0:Math.floor(r.y/4)*4,y1:Math.ceil(r.bottom/4)*4,
                  href:a.href,label:a.textContent.trim()}
        })""")
        assert len(targets) == 2 + 2*min(4, len(stats["repos"])), targets
        assert any(t["href"] == "mailto:max@vorwaller.net" for t in targets)
        assert any("linkedin.com/in/max-vorwaller-724418259" in t["href"] for t in targets)
        assert page.evaluate("document.querySelector('.tm-stats').innerText").startswith("Commits:")
        assert page.evaluate("document.querySelector('.weather-gadget').getBoundingClientRect().width") == 230
        # If several desktop rows and favorite rows overlap in y, use an exact
        # planar partition instead of growing clickable rectangles into each other.
        # All rectangles are rounded outward to 4px. Equal-y neighboring links
        # share a single row; non-overlapping intervals are split at their edges.
        yedges=sorted({0,HEIGHT,*[r[k] for r in targets for k in ("y0","y1")]})
        rows=[]
        for y0,y1 in zip(yedges,yedges[1:]):
            active=[r for r in targets if r["y0"]<=y0 and r["y1"]>=y1]
            row=[]; x=0
            for r in sorted(active,key=lambda r:r["x0"]):
                assert r["x0"]>=x, active
                if r["x0"]>x: row.append((x,y0,r["x0"],y1,None))
                row.append((r["x0"],y0,r["x1"],y1,r)); x=r["x1"]
            if x<WIDTH: row.append((x,y0,WIDTH,y1,None))
            rows.append(row)
        # Simplify the present one-repo layout: merge overlapping vertical bands
        # when the horizontal links do not collide.
        try:
            rows=partitions(targets)
        except ValueError:
            pass
        # The original scene and animation remain intact. Capture a full frame
        # for each distinct animation instant, then partition the same pixels.
        slices=[{"rect":r[:4],"link":r[4],"frames":[],"durations":[],"last":None}
                for row in rows for r in row]
        last_key=None
        for i in range(FPS*SECONDS):
            key=frame_key(i)
            if key!=last_key:
                page.evaluate("t=>frame(t)",key/FPS)
                scene=Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                last_key=key
            ms=round((i+1)*1000/FPS)-round(i*1000/FPS)
            for item in slices:
                if not item["frames"] or item.get("key")!=key:
                    crop=scene.crop(item["rect"])
                    digest=hashlib.sha256(crop.tobytes()).digest()
                    item["key"]=key
                    if digest!=item["last"]:
                        item["frames"].append(crop)
                        item["durations"].append(0)
                        item["last"]=digest
                item["durations"][-1]+=ms
        assert not errors, errors
        browser.close()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    lines=[]
    idx=0
    for row in rows:
        parts=[]
        for r in row:
            item=slices[idx]
            path=OUT/f"tile-{idx:02d}.png"
            imgs=item["frames"]
            ds=item["durations"]
            assert sum(ds)==SECONDS*1000
            if len(imgs)==1:
                imgs[0].save(path,compress_level=7)
            else:
                imgs[0].save(path,save_all=True,append_images=imgs[1:],duration=ds,
                             loop=1,disposal=0,blend=0,compress_level=6)
                with Image.open(path) as im:
                    assert im.info["loop"]==1
                    assert sum((im.seek(i),im.info["duration"])[1] for i in range(im.n_frames))==SECONDS*1000
            assert path.stat().st_size<10*1024*1024, f"Large tile: {path}"
            label=item["link"]["label"] if item["link"] else ("Max Vorwaller – Software Engineer, Seattle; animated Windows 7 desktop" if idx==0 else "")
            w=(r[2]-r[0])*100/WIDTH
            img=f'<img src="./assets/windows7/{path.name}" width="{w}%" alt="{markup.escape(label,quote=True)}">'
            if item["link"]:
                img=f'<a href="{markup.escape(item["link"]["href"],quote=True)}">{img}</a>'
            parts.append(img);idx+=1
        lines.append("".join(parts))
    readme='<!-- Generated by profile/render.py; Task Manager counts eligible public repositories, their stars and the owner\'s default-branch commits. -->\n<p align="center">'+ "<br>\n".join(lines)+"</p>\n"
    (ROOT/"README.md").write_text(readme,encoding="utf-8")
    print(f"Rendered {idx} tiles; {sum(x.stat().st_size for x in OUT.glob('*.png')):,} bytes; links: {[(t['label'],t['href']) for t in targets]}")

def main():
    stats = fetch_stats()
    source = (ROOT/"profile"/"desktop.html").read_text(encoding="utf-8")
    state = ROOT/"profile"/"stats.json"
    source_hash=hashlib.sha256(Path(__file__).read_bytes()+source.encode()).hexdigest()
    snapshot={"source_hash":source_hash,**stats}
    if state.exists() and json.loads(state.read_text())==snapshot and list(OUT.glob("*.png")):
        print("No public project or statistics changes.")
        return
    print(json.dumps(stats))
    render(build_html(source,stats),stats)
    state.write_text(json.dumps(snapshot,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
