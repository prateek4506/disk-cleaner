#!/usr/bin/env python3
"""
photo_blur.py — opt-in "blurry photo" review for disk_cleaner.

This is DELIBERATELY separate from the main cleaner: it needs image libraries
(Pillow, plus pillow-heif for iPhone HEIC) that the rest of the tool does not,
and it touches irreplaceable personal photos. So everything here is built to be
extra careful:

  • REVIEW-ONLY. The blur score only *orders and pre-flags* candidates. The
    human eye makes every decision — you see the actual photo before anything
    happens to it.
  • Nothing is ever deleted. Selected photos are moved to the Trash
    (recoverable) via the caller's trash() function.
  • "Blurry" is a fuzzy, subjective signal — intentional bokeh / portrait-mode
    shots score low too. The UI says so, and never auto-selects anything.

It works by serving a small LOCAL web gallery (photos never leave the machine)
of the lowest-sharpness images as thumbnails, with checkboxes and a
"Move selected to Trash" button.
"""

import http.server
import io
import json
import os
import socketserver
import threading
import urllib.parse
import webbrowser
from pathlib import Path

# Sharpness below this (edge-variance) is "likely soft/blurry". It's only a HINT for
# ordering — never an automatic action. Real photos vary wildly, so we show a range and
# let the user judge by eye.
SHARPNESS_HINT = 100.0
THUMB_MAX = 320              # thumbnail longest edge, px
MAX_CANDIDATES = 200         # cap the gallery so a huge library stays responsive

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".webp"}


def _ensure_libs():
    """Import Pillow (+HEIC). Returns (Image, ImageFilter, ImageStat) or raises ImportError
    with a clear, actionable message the caller can show."""
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError as e:
        raise ImportError(
            "Blurry-photo review needs the Pillow image library.\n"
            "    Install it once with:\n"
            "        python3 -m pip install --user Pillow pillow-heif\n"
            "    (pillow-heif adds iPhone HEIC support.)"
        ) from e
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass  # HEIC just won't be supported; JPEG/PNG/etc still work
    return Image, ImageFilter, ImageStat


def _sharpness(img, ImageFilter, ImageStat) -> float:
    """A no-numpy sharpness proxy: variance of an edge-detected grayscale copy.
    Sharp photo -> strong edges -> high variance. Blurry -> low. Downscaled first so the
    score is resolution-independent and fast."""
    g = img.convert("L")
    g.thumbnail((512, 512))
    edges = g.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]


def scan_blurry(root: Path):
    """Walk `root`, score every image's sharpness, return candidates sorted blurriest-first.
    Each item: {'path', 'score', 'soft': bool}. Skips unreadable files silently."""
    Image, ImageFilter, ImageStat = _ensure_libs()
    results = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False,
                                                onerror=lambda e: None):
        # don't descend into bundles / hidden Library noise
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and not d.endswith((".app", ".photoslibrary"))]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.suffix.lower() not in IMAGE_EXTS or fp.is_symlink():
                continue
            try:
                with Image.open(fp) as img:
                    img.load()
                    score = _sharpness(img, ImageFilter, ImageStat)
            except Exception:
                continue
            results.append({"path": str(fp), "score": round(score, 1), "soft": False})
    results.sort(key=lambda r: r["score"])

    # "soft" is a relative hint, not an absolute cutoff — the sharpness scale depends on each
    # photo's content, so no universal threshold exists. We flag a photo as soft if it's both
    # in the lower part of THIS batch's range and below a loose absolute ceiling. It only
    # tints the card; the user still judges every photo by eye.
    if results:
        scores = [r["score"] for r in results]
        lo, hi = scores[0], scores[-1]
        span = max(hi - lo, 1.0)
        cutoff = lo + span * 0.33          # bottom third of the observed range
        for r in results:
            r["soft"] = r["score"] <= cutoff and r["score"] < SHARPNESS_HINT * 6
    return results[:MAX_CANDIDATES]


def _thumbnail_bytes(path: Path) -> bytes:
    """Return a small JPEG thumbnail of `path` as bytes (for the gallery)."""
    Image, _, _ = _ensure_libs()
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((THUMB_MAX, THUMB_MAX))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Blurry photo review</title><style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#1b1b1d;color:#eee}
 header{position:sticky;top:0;background:#111;padding:14px 20px;border-bottom:1px solid #333;
   display:flex;align-items:center;gap:16px;z-index:10}
 h1{font-size:16px;margin:0;font-weight:600}
 .sub{color:#999;font-size:13px}
 button{background:#c0392b;color:#fff;border:0;padding:9px 16px;border-radius:8px;
   font-size:14px;cursor:pointer}
 button:disabled{background:#444;cursor:default}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;padding:20px}
 .card{background:#262629;border-radius:10px;overflow:hidden;border:2px solid transparent}
 .card.sel{border-color:#c0392b}
 .card img{width:100%;height:200px;object-fit:cover;display:block;background:#000}
 .meta{padding:8px 10px;font-size:12px;color:#bbb;display:flex;justify-content:space-between;
   align-items:center;gap:8px}
 .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .score{padding:2px 6px;border-radius:6px;background:#333;font-variant-numeric:tabular-nums}
 .soft .score{background:#7a3b12;color:#ffd9a8}
 .card label{display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;
   border-top:1px solid #333;font-size:12px}
 .done{padding:40px;text-align:center;color:#9c9}
</style></head><body>
<header>
 <h1>📷 Blurry photo review</h1>
 <span class="sub" id="sub"></span>
 <span style="flex:1"></span>
 <button id="trashBtn" disabled>Move 0 to Trash</button>
</header>
<div class="grid" id="grid"></div>
<script>
let items=[], sel=new Set();
function fmt(p){return p.replace(/^.*\\//,'');}
async function load(){
 items=await (await fetch('/api/items')).json();
 const soft=items.filter(i=>i.soft).length;
 document.getElementById('sub').textContent =
   items.length+' photos, blurriest first — '+soft+' look soft. You decide; nothing is deleted until you click. Trash = recoverable.';
 const g=document.getElementById('grid');
 g.innerHTML=items.map((it,idx)=>`
  <div class="card ${it.soft?'soft':''}" id="c${idx}">
   <img loading="lazy" src="/thumb?i=${idx}">
   <div class="meta"><span class="name" title="${it.path}">${fmt(it.path)}</span>
     <span class="score">${it.score}</span></div>
   <label><input type="checkbox" onchange="tog(${idx},this.checked)">trash this</label>
  </div>`).join('');
}
function tog(i,on){on?sel.add(i):sel.delete(i);
 document.getElementById('c'+i).classList.toggle('sel',on);
 const b=document.getElementById('trashBtn');
 b.textContent='Move '+sel.size+' to Trash'; b.disabled=sel.size===0;}
document.getElementById('trashBtn').onclick=async()=>{
 if(!sel.size||!confirm('Move '+sel.size+' photo(s) to the Trash? (recoverable)'))return;
 const paths=[...sel].map(i=>items[i].path);
 const r=await (await fetch('/api/trash',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({paths})})).json();
 alert('Moved '+r.trashed+' to Trash'+(r.failed?(', '+r.failed+' could not be moved'):''));
 sel.clear(); load(); tog(-1,false);
};
load();
</script></body></html>"""


def serve_gallery(candidates, trash_fn, host="127.0.0.1", port=0):
    """Serve the review gallery locally and open it in the browser. `trash_fn(Path)->bool`
    is the caller's safe Trash function. Blocks until the user stops the server (Ctrl-C)."""
    state = {"items": candidates}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                self._send(200, _PAGE.encode())
            elif u.path == "/api/items":
                self._send(200, json.dumps(state["items"]).encode(),
                           "application/json")
            elif u.path == "/thumb":
                q = urllib.parse.parse_qs(u.query)
                try:
                    i = int(q.get("i", ["-1"])[0])
                    path = Path(state["items"][i]["path"])
                    self._send(200, _thumbnail_bytes(path), "image/jpeg")
                except Exception:
                    self._send(404, b"no thumb")
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if self.path != "/api/trash":
                self._send(404, b"not found")
                return
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            trashed = failed = 0
            remaining = []
            todo = set(req.get("paths", []))
            for it in state["items"]:
                if it["path"] in todo:
                    if trash_fn(Path(it["path"])):
                        trashed += 1
                        continue
                    failed += 1
                remaining.append(it)
            state["items"] = remaining
            self._send(200, json.dumps({"trashed": trashed, "failed": failed}).encode(),
                       "application/json")

    httpd = socketserver.TCPServer((host, port), Handler)
    real_port = httpd.server_address[1]
    url = f"http://{host}:{real_port}/"
    print(f"  Opening the review gallery at {url}")
    print("  Review the photos, tick the ones to trash, click the button.")
    print("  Press Ctrl-C here when you're done.\n")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Closed. Trashed photos are recoverable from the Trash.")
    finally:
        httpd.server_close()
