import re
import requests

from .. import HEADERS, DEFAULT_MAX_PAGES
from ..filters import (
    extract_education, extract_years_experience,
    SALARY_RANGE_RE, SALARY_SINGLE_RE,
)

BUILTIN_COMPANIES = [
    {"name": "Moderna", "board_token": "modernatx"},
    {"name": "Recursion", "board_token": "recursionpharmaceuticals"},
    {"name": "Insitro", "board_token": "insitro"},
    {"name": "Tempus", "board_token": "tempus"},
    {"name": "Benchling", "board_token": "benchling"},
    {"name": "Schrödinger", "board_token": "schaboratories"},
]


def search(company: dict, keywords: list[str], verify_ssl: bool,
           max_pages: int = DEFAULT_MAX_PAGES) -> list[dict]:
    token = company["board_token"]
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    results = []
    try:
        resp = requests.get(api_url, params={"content": "true"}, headers=HEADERS,
                            timeout=15, verify=verify_ssl)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [{company['name']}] Error: {e}")
        return results

    keyword_patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]

    for job in data.get("jobs", []):
        title = job.get("title", "")
        if not any(p.search(title) for p in keyword_patterns):
            continue

        location = job.get("location", {}).get("name", "")
        posted = job.get("updated_at", "") or job.get("first_published_at", "")
        if posted and len(posted) >= 10:
            posted = posted[:10]

        job_url = job.get("absolute_url", "")

        results.append({
            "company": company["name"],
            "title": title,
            "url": job_url,
            "location": location,
            "posted_date": posted,
            "_api_base": None,
            "_external_path": None,
            "_platform": "greenhouse",
            "_gh_content": job.get("content", ""),
        })

    return results


def extract_details(jobs: list[dict], skip_salary: bool = False):
    for job in jobs:
        if job.get("_platform") != "greenhouse" or not job.get("_gh_content"):
            continue
        text = job["_gh_content"]
        if not skip_salary:
            match = SALARY_RANGE_RE.search(text)
            if match:
                job["salary"] = match.group(0).strip()
            elif not job.get("salary"):
                match = SALARY_SINGLE_RE.search(text)
                if match:
                    job["salary"] = match.group(0).strip()
        job["education"] = extract_education(text)
        job["years_exp"] = extract_years_experience(text)
