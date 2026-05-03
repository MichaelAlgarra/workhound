import time
import requests

from .. import HEADERS, DEFAULT_MAX_PAGES

BUILTIN_COMPANIES = [
    {"name": "AstraZeneca", "domain": "astrazeneca.com",
     "api_url": "https://astrazeneca.eightfold.ai/api/apply/v2/jobs"},
]


def search(company: dict, keywords: list[str], verify_ssl: bool,
           max_pages: int = DEFAULT_MAX_PAGES) -> list[dict]:
    results = []
    for keyword in keywords:
        params = {"domain": company["domain"], "query": keyword,
                  "num_jobs": min(50, max_pages * 20), "location": "United States"}
        try:
            resp = requests.get(company["api_url"], params=params, headers=HEADERS, timeout=15, verify=verify_ssl)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [{company['name']}] Error: {e}")
            continue

        for pos in data.get("positions", []):
            posted = pos.get("t_update", "") or pos.get("t_create", "")
            if posted and len(posted) >= 10:
                posted = posted[:10]
            results.append({
                "company": company["name"],
                "title": pos.get("name", ""),
                "url": pos.get("canonicalPositionUrl", ""),
                "location": pos.get("location", ""),
                "posted_date": posted,
                "_api_base": None,
                "_external_path": None,
                "_platform": "eightfold",
            })
        time.sleep(0.5)
    return results
