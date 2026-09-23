import os
import re
import time
import threading
from urllib.parse import urljoin
from flask import Flask, jsonify, render_template, request
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

DEFAULT_CREX_URL = os.environ.get(
    "CREX_URL",
    "https://crex.com/cricket-live-score/bw-vs-ecr-final-european-t20-premier-league-2026-match-updates-13FP"
)

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "15"))

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
})

cache = {
    "data": None,
    "last_fetch": 0,
    "error": None,
}
lock = threading.Lock()


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def absolute_url(base, value):
    if not value:
        return ""
    return urljoin(base, value.strip())


def image_url_from_tag(img, base_url):
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        value = img.get(attr)
        if value and not value.startswith("data:"):
            return absolute_url(base_url, value)

    srcset = img.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        return absolute_url(base_url, first)

    return ""


def page_text(soup):
    # Remove noise but keep text that is useful for score parsing.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True))


def first_float(text, pattern):
    m = re.search(pattern, text, flags=re.I)
    return m.group(1) if m else ""


def parse_score_and_overs(text):
    # Prefer score lines such as 150/5 (20.0), 93-2 15.5, etc.
    patterns = [
        r"\b(\d{1,3})\s*[/\-]\s*(\d{1,2})\s*\(\s*(\d{1,2}(?:\.\d)?)\s*\)",
        r"\b(\d{1,3})\s*[/\-]\s*(\d{1,2})\s+(\d{1,2}\.\d)\b",
    ]
    for pattern in patterns:
        matches = list(re.finditer(pattern, text))
        if matches:
            m = matches[-1]
            return f"{m.group(1)}/{m.group(2)}", m.group(3)

    score = ""
    overs = ""
    m = re.search(r"\b(\d{1,3})\s*[/\-]\s*(\d{1,2})\b", text)
    if m:
        score = f"{m.group(1)}/{m.group(2)}"

    # Common CREX wording: OVER 20, then 150/5.
    m = re.search(r"\bOVER\s+(\d{1,2}(?:\.\d)?)\b", text, re.I)
    if m:
        overs = m.group(1)

    return score, overs


def parse_team_names(soup, text):
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""

    # Try the H1 first.
    h1 = clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else ""
    source = f"{h1} {title} {text[:1800]}"

    m = re.search(
        r"\b([A-Z][A-Za-z& .'-]{1,35})\s+vs\s+([A-Z][A-Za-z& .'-]{1,35})\b",
        source,
        re.I,
    )
    if m:
        return clean_text(m.group(1)), clean_text(m.group(2))

    return "TEAM A", "TEAM B"


def parse_current_batsmen(soup, base_url):
    """
    CREX has changed CSS class names over time. This intentionally uses
    class-name fragments instead of relying on one generated Angular class.
    """
    players = []
    seen = set()

    # Most specific first: elements whose class contains batsmen/player.
    candidates = []
    for tag in soup.find_all(["div", "section", "article", "li"]):
        classes = " ".join(tag.get("class", []))
        if re.search(r"batsmen|batter|player-card|playing-batsmen", classes, re.I):
            candidates.append(tag)

    # Search candidate blocks for names, scores and player images.
    for block in candidates[:80]:
        txt = clean_text(block.get_text(" ", strip=True))
        if not txt or len(txt) > 500:
            continue

        # Avoid picking a complete scorecard/team block.
        score_matches = re.findall(r"\b\d{1,3}\s*\(\s*\d{1,3}\s*\)", txt)
        if not score_matches:
            continue

        imgs = block.find_all("img")
        image = image_url_from_tag(imgs[0], base_url) if imgs else ""

        # Try common name-bearing attributes/classes.
        name = ""
        for el in block.find_all(["span", "div", "p", "a"], limit=40):
            cls = " ".join(el.get("class", []))
            candidate = clean_text(el.get_text(" ", strip=True))
            if candidate and len(candidate) <= 60 and re.search(
                r"name|player|batsman|batter", cls, re.I
            ):
                if not re.search(r"\d", candidate):
                    name = candidate
                    break

        if not name:
            # Fallback: choose the first short text fragment without digits.
            fragments = [
                clean_text(x) for x in block.stripped_strings
                if clean_text(x) and not re.search(r"\d", clean_text(x))
            ]
            for fragment in fragments:
                if 2 <= len(fragment) <= 45 and fragment.lower() not in {
                    "batsman", "batter", "player", "not out"
                }:
                    name = fragment
                    break

        stat = score_matches[0].replace(" ", "")
        key = (name, stat)
        if name and key not in seen:
            seen.add(key)
            players.append({
                "name": name,
                "score": stat,
                "image": image,
            })

        if len(players) >= 2:
            break

    return players[:2]


def parse_partnership(text):
    patterns = [
        r"P'?ship\s*[:\-]?\s*([0-9]+(?:\([0-9]+\))?)",
        r"Partnership\s*[:\-]?\s*([0-9]+(?:\([0-9]+\))?)",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1)
    return "--"


def parse_crr(text, score, overs):
    m = re.search(r"\bCRR\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if m:
        return m.group(1)

    if score and overs:
        try:
            runs = int(score.split("/")[0])
            ov = float(overs)
            whole = int(ov)
            balls = int(round((ov - whole) * 10))
            legal_balls = whole * 6 + balls
            if legal_balls:
                return f"{runs * 6 / legal_balls:.2f}"
        except Exception:
            pass
    return "--"


def parse_player_images_by_names(soup, base_url, players):
    if not players:
        return players

    all_imgs = soup.find_all("img")
    for player in players:
        if player.get("image"):
            continue
        name = player.get("name", "").lower()
        if not name:
            continue

        parts = [p for p in re.split(r"\W+", name) if len(p) >= 3]
        for img in all_imgs:
            alt = clean_text(img.get("alt", "")).lower()
            title = clean_text(img.get("title", "")).lower()
            hay = f"{alt} {title}"
            if any(part in hay for part in parts):
                player["image"] = image_url_from_tag(img, base_url)
                break
    return players


def parse_team_logo(soup, base_url, team_code):
    # CREX commonly serves team graphics from cricketvectors.akamaized.net.
    for img in soup.find_all("img"):
        src = image_url_from_tag(img, base_url)
        alt = clean_text(img.get("alt", "")).lower()
        if team_code.lower() in alt or team_code.lower() in src.lower():
            return src

    # Known team logo URL pattern discovered from the public CREX page.
    known = {
        "ecr": "https://cricketvectors.akamaized.net/Teams/1K9.png",
    }
    return known.get(team_code.lower(), "")


def scrape_crex(url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = page_text(soup)

    team_a, team_b = parse_team_names(soup, text)
    score, overs = parse_score_and_overs(text)
    crr = parse_crr(text, score, overs)
    partnership = parse_partnership(text)
    players = parse_current_batsmen(soup, url)
    players = parse_player_images_by_names(soup, url, players)

    # Try to extract team codes from the URL as a final fallback.
    code_match = re.search(r"/([a-z0-9]+)-vs-([a-z0-9]+)-", url, re.I)
    code_a = code_match.group(1) if code_match else ""
    code_b = code_match.group(2) if code_match else ""

    return {
        "ok": True,
        "source": url,
        "updated": int(time.time()),
        "team_a": team_a,
        "team_b": team_b,
        "score": score or "--",
        "overs": overs or "--",
        "crr": crr or "--",
        "partnership": partnership or "--",
        "players": players,
        "team_a_logo": parse_team_logo(soup, url, code_a),
        "team_b_logo": parse_team_logo(soup, url, code_b),
    }


def background_worker():
    while True:
        try:
            data = scrape_crex(DEFAULT_CREX_URL)
            with lock:
                cache["data"] = data
                cache["last_fetch"] = time.time()
                cache["error"] = None
        except Exception as exc:
            with lock:
                cache["error"] = str(exc)
        time.sleep(POLL_SECONDS)


@app.route("/")
def index():
    return render_template("overlay.html")


@app.route("/api/score")
def api_score():
    url = request.args.get("url", DEFAULT_CREX_URL)

    # Keep this simple and safe: only allow CREX URLs.
    if not re.match(r"^https?://(www\.)?crex\.com/", url, re.I):
        return jsonify({"ok": False, "error": "Only crex.com URLs are allowed."}), 400

    # For a custom URL, fetch on demand. For the configured URL, use cache.
    if url != DEFAULT_CREX_URL:
        try:
            return jsonify(scrape_crex(url))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502

    with lock:
        data = dict(cache["data"]) if cache["data"] else None
        error = cache["error"]
        last_fetch = cache["last_fetch"]

    if data:
        data["cache_age_seconds"] = round(time.time() - last_fetch, 1)
        return jsonify(data)

    return jsonify({
        "ok": False,
        "error": error or "Waiting for first CREX fetch..."
    }), 503


@app.route("/health")
def health():
    with lock:
        return jsonify({
            "ok": True,
            "last_fetch": cache["last_fetch"],
            "error": cache["error"],
        })


if __name__ == "__main__":
    # Render supplies PORT. Binding to 0.0.0.0 is required for a Render web service.
    port = int(os.environ.get("PORT", "10000"))

    # Start background polling.
    thread = threading.Thread(target=background_worker, daemon=True)
    thread.start()

    app.run(host="0.0.0.0", port=port, debug=False)
