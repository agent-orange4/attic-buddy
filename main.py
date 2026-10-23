import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests

app = FastAPI(title="Attic Buddy")

ATTIC_URL = os.getenv("ATTIC_URL", "http://attic:9750")
ATTIC_EMAIL = os.getenv("ATTIC_EMAIL", "admin@example.com")
ATTIC_PASSWORD = os.getenv("ATTIC_PASSWORD", "admin")
USER_AGENT = "AtticBuddy/1.0 (https://github.com/)"


class ScanRequest(BaseModel):
    barcode: str


def get_attic_session():
    """Logs into Attic and returns an authenticated session."""
    session = requests.Session()
    resp = session.post(
        f"{ATTIC_URL}/api/auth/login",
        json={"email": ATTIC_EMAIL, "password": ATTIC_PASSWORD},
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=500, detail=f"Attic auth failed: {resp.text}"
        )
    return session


def resolve_metadata(barcode: str):
    """Cascades lookups: MusicBrainz for CDs -> extendable to UPC/TMDB."""
    # 1. MusicBrainz (CDs/Vinyl)
    mb_url = "https://musicbrainz.org/ws/2/release/"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    mb_res = requests.get(
        mb_url, headers=headers, params={"query": f"barcode:{barcode}"}
    )

    if mb_res.status_code == 200:
        releases = mb_res.json().get("releases", [])
        if releases:
            r = releases[0]
            artist = r.get("artist-credit", [{}])[0].get("name", "Unknown")
            title = r.get("title", "Unknown")
            year = r.get("date", "")[:4]
            mbid = r.get("id")
            return {
                "name": f"{artist} - {title}",
                "description": f"Artist: {artist}\nAlbum: {title}\nYear: {year}\nUPC: {barcode}\nMBID: {mbid}",
                "cover_url": f"https://coverartarchive.org/release/{mbid}/front-500",
            }

    # Fallback if no database match
    return {
        "name": f"Unrecognized Item ({barcode})",
        "description": f"Scanned barcode: {barcode}. Metadata lookup returned no matches.",
        "cover_url": None,
    }


@app.post("/scan")
def process_scan(payload: ScanRequest):
    code = payload.barcode.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Empty barcode")

    # 1. Look up item
    meta = resolve_metadata(code)

    # 2. Push to Attic
    session = get_attic_session()
    asset_res = session.post(
        f"{ATTIC_URL}/api/assets",
        json={"name": meta["name"], "description": meta["description"]},
    )
    if asset_res.status_code not in (200, 201):
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Attic asset: {asset_res.text}",
        )

    asset_id = asset_res.json().get("id")

    # 3. Attach Cover Image (if available)
    if meta.get("cover_url"):
        img_res = requests.get(
            meta["cover_url"],
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        if img_res.status_code == 200:
            files = {"file": ("cover.jpg", img_res.content, "image/jpeg")}
            session.post(
                f"{ATTIC_URL}/api/assets/{asset_id}/attachments", files=files
            )

    return {
        "status": "success",
        "asset_id": asset_id,
        "name": meta["name"],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <html>
      <head>
        <title>Attic Buddy</title>
        <style>body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; }</style>
      </head>
      <body>
        <h2>Attic Buddy Scanner</h2>
        <form onsubmit="event.preventDefault(); scan();">
          <input id="bc" type="text" placeholder="Scan Barcode here..." autofocus style="font-size: 1.2rem; width: 100%; padding: 8px;" />
        </form>
        <div id="log" style="margin-top: 20px; white-space: pre-wrap; font-family: monospace;"></div>
        <script>
          async function scan() {
            const input = document.getElementById('bc');
            const log = document.getElementById('log');
            const code = input.value;
            input.value = '';
            log.innerText = 'Looking up ' + code + '...\\n' + log.innerText;
            const res = await fetch('/scan', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({barcode: code})
            });
            const data = await res.json();
            log.innerText = JSON.stringify(data, null, 2) + '\\n\\n' + log.innerText;
          }
        </script>
      </body>
    </html>
    """