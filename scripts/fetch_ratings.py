import json
import re
from pathlib import Path
from datetime import datetime

import requests

SCRIPT_DIR = Path(__file__).resolve().parent.parent
RATINGS_PATH = SCRIPT_DIR / "ratings.json"

WC_TEAMS = [
    "Algeria", "Argentina", "Australia", "Austria", "Belgium",
    "Bosnia and Herzegovina", "Brazil", "Burkina Faso", "Cameroon", "Canada",
    "Chile", "Colombia", "Croatia", "Czechia", "Denmark", "DR Congo",
    "Ecuador", "Egypt", "England", "France", "Germany", "Ghana", "Greece",
    "Hungary", "Iran", "Iraq", "Italy", "Ivory Coast", "Japan", "Mali",
    "Mexico", "Morocco", "Netherlands", "Nigeria", "Norway", "Paraguay",
    "Peru", "Poland", "Portugal", "Qatar", "Romania", "Saudi Arabia",
    "Senegal", "Serbia", "Slovakia", "Slovenia", "South Africa",
    "South Korea", "Spain", "Sweden", "Switzerland", "Tunisia", "Turkey",
    "Ukraine", "United States", "Uruguay", "Uzbekistan", "Venezuela",
]

SITE_TO_CANONICAL = {
    "Czech Republic": "Czechia",
    "Korea Republic": "South Korea",
    "C\u00f4te d'Ivoire": "Ivory Coast",
    "USA": "United States",
}


def fetch_ratings():
    today = datetime.now()
    url = (
        "https://www.international-football.net/elo-ratings-table"
        f"?day={today.day}&month={today.month:02d}&year={today.year}"
    )

    resp = requests.get(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        timeout=15,
    )
    resp.raise_for_status()

    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)

    pattern = re.compile(
        r"(\d+)\.\s*([A-Za-z\u00c0-\u024f .'\-]+?)\s*(\d{3,4})"
        r"(?=\s|\d|\.|$|,|<)"
    )
    matches = pattern.findall(text)

    site_ratings = {}
    for rank, name, rating in matches:
        name = name.strip().rstrip(".")
        site_ratings[name] = int(rating)

    result = {}
    for team in WC_TEAMS:
        if team in site_ratings:
            result[team] = site_ratings[team]
        elif team in SITE_TO_CANONICAL:
            site_name = SITE_TO_CANONICAL[team]
            if site_name in site_ratings:
                result[team] = site_ratings[site_name]
        else:
            for site_name, canonical in SITE_TO_CANONICAL.items():
                if canonical == team and site_name in site_ratings:
                    result[team] = site_ratings[site_name]
                    break

    return result


if __name__ == "__main__":
    ratings = fetch_ratings()
    if ratings:
        RATINGS_PATH.write_text(json.dumps(ratings, indent=2, ensure_ascii=False))
        print(f"Saved {len(ratings)} team ratings to {RATINGS_PATH}")
    else:
        print("Warning: no ratings scraped, keeping existing ratings.json")
