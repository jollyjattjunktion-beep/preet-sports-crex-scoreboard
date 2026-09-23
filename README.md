# Preet Sports - CREX → OBS HTML Scoreboard

This project creates a transparent browser-source scoreboard for OBS.

Flow:

CREX public match page
        ↓
Python Flask scraper
        ↓
/api/score JSON
        ↓
HTML/CSS/JavaScript overlay
        ↓
OBS Browser Source

## Fields

- Team names
- Score
- Overs
- CRR
- Partnership
- Current batter/player cards when CREX exposes them in the page HTML
- Player images when an image URL can be matched
- Team logos when available

## Local test

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

http://127.0.0.1:10000/

Health:

http://127.0.0.1:10000/health

JSON:

http://127.0.0.1:10000/api/score

## Render

Connect this folder/repository to Render as a Python Web Service.

Build:
pip install -r requirements.txt

Start:
gunicorn app:app --bind 0.0.0.0:$PORT

Render supports Flask/Gunicorn deployment and provides an onrender.com URL.
Free web services can spin down after 15 minutes without inbound traffic, so keep this limitation in mind for unattended use.

## OBS

OBS → Sources → Browser

URL:
https://YOUR-APP.onrender.com/

Width: 1920
Height: 1080
FPS: 30

Because the page background is transparent, only the scoreboard is rendered over your match background/video.

## Change match

Set the Render environment variable:

CREX_URL=https://crex.com/your-match-url

Or use a URL parameter:

https://YOUR-APP.onrender.com/?url=https%3A%2F%2Fcrex.com%2Fcricket-live-score%2F...

## Important

This scraper reads the publicly accessible CREX HTML page. CREX can change its HTML structure, rate limits, bot protections, or terms. Use it only in a way permitted by CREX's terms and applicable rights. If CREX blocks server-side requests, the reliable alternative is a licensed/authorized cricket data API rather than trying to bypass the block.
