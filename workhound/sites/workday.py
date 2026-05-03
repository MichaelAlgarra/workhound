import time
import requests

from .. import HEADERS, DEFAULT_MAX_PAGES
from ..filters import (
    extract_all_text, extract_education, extract_years_experience,
    SALARY_RANGE_RE, SALARY_SINGLE_RE,
)

BUILTIN_COMPANIES = [
    {"name": "Merck", "url": "https://msd.wd5.myworkdayjobs.com/wday/cxs/msd/SearchJobs/jobs",
     "base_url": "https://msd.wd5.myworkdayjobs.com/en-US/SearchJobs"},
    {"name": "GSK", "url": "https://gsk.wd5.myworkdayjobs.com/wday/cxs/gsk/GSKCareers/jobs",
     "base_url": "https://gsk.wd5.myworkdayjobs.com/en-US/GSKCareers"},
    {"name": "BMS", "url": "https://bristolmyerssquibb.wd5.myworkdayjobs.com/wday/cxs/bristolmyerssquibb/BMS/jobs",
     "base_url": "https://bristolmyerssquibb.wd5.myworkdayjobs.com/en-US/BMS"},
    {"name": "J&J", "url": "https://jj.wd5.myworkdayjobs.com/wday/cxs/jj/JJ/jobs",
     "base_url": "https://jj.wd5.myworkdayjobs.com/en-US/JJ"},
    {"name": "Pfizer", "url": "https://pfizer.wd1.myworkdayjobs.com/wday/cxs/pfizer/PfizerCareers/jobs",
     "base_url": "https://pfizer.wd1.myworkdayjobs.com/en-US/PfizerCareers"},
    {"name": "Novartis", "url": "https://novartis.wd3.myworkdayjobs.com/wday/cxs/novartis/Novartis_Careers/jobs",
     "base_url": "https://novartis.wd3.myworkdayjobs.com/en-US/Novartis_Careers"},
    {"name": "Sanofi", "url": "https://sanofi.wd3.myworkdayjobs.com/wday/cxs/sanofi/SanofiCareers/jobs",
     "base_url": "https://sanofi.wd3.myworkdayjobs.com/en-US/SanofiCareers"},
    {"name": "Amgen", "url": "https://amgen.wd1.myworkdayjobs.com/wday/cxs/amgen/Careers/jobs",
     "base_url": "https://amgen.wd1.myworkdayjobs.com/en-US/Careers"},
    {"name": "Regeneron", "url": "https://regeneron.wd1.myworkdayjobs.com/wday/cxs/regeneron/Careers/jobs",
     "base_url": "https://regeneron.wd1.myworkdayjobs.com/en-US/Careers"},
    {"name": "Gilead", "url": "https://gilead.wd1.myworkdayjobs.com/wday/cxs/gilead/gileadCareers/jobs",
     "base_url": "https://gilead.wd1.myworkdayjobs.com/en-US/gileadCareers"},
    {"name": "Biogen", "url": "https://biibhr.wd3.myworkdayjobs.com/wday/cxs/biibhr/external/jobs",
     "base_url": "https://biibhr.wd3.myworkdayjobs.com/en-US/external"},
]


def search(company: dict, keywords: list[str], verify_ssl: bool,
           max_pages: int = DEFAULT_MAX_PAGES) -> list[dict]:
    results = []
    for keyword in keywords:
        offset = 0
        page = 0
        while page < max_pages:
            payload = {"limit": 20, "offset": offset, "searchText": keyword, "appliedFacets": {}}
            try:
                resp = requests.post(company["url"], json=payload, headers=HEADERS, timeout=15, verify=verify_ssl)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [{company['name']}] Error: {e}")
                break

            postings = data.get("jobPostings", [])
            if not postings:
                break

            for job in postings:
                posted = job.get("postedOn", "")
                if posted:
                    posted = posted[:10]
                results.append({
                    "company": company["name"],
                    "title": job.get("title", ""),
                    "url": job.get("externalPath", ""),
                    "location": job.get("locationsText", ""),
                    "posted_date": posted,
                    "_api_base": company["url"].replace("/jobs", ""),
                    "_external_path": job.get("externalPath", ""),
                    "_platform": "workday",
                })

            if offset + 20 >= data.get("total", 0):
                break
            offset += 20
            page += 1
            time.sleep(0.3)
        time.sleep(0.5)
    return results


def fetch_detail(api_base: str, external_path: str, verify_ssl: bool) -> dict:
    detail_url = api_base + external_path
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15, verify=verify_ssl)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"salary": "", "education": "", "years_exp": "", "posted_date": ""}

    info = data.get("jobPostingInfo", {})
    salary = ""
    for key in ("payRange", "payTransparencyRange", "salary", "compensationRange", "startingPay", "basePay"):
        val = info.get(key)
        if val and isinstance(val, str) and val.strip():
            salary = val.strip()
            break

    all_text = extract_all_text(data)

    if not salary:
        match = SALARY_RANGE_RE.search(all_text)
        if match:
            salary = match.group(0).strip()
    if not salary:
        match = SALARY_SINGLE_RE.search(all_text)
        if match:
            salary = match.group(0).strip()

    posted_date = ""
    for key in ("postedOn", "postingDate", "startDate"):
        val = info.get(key)
        if val and isinstance(val, str) and val.strip():
            posted_date = val.strip()[:10]
            break

    return {
        "salary": salary,
        "education": extract_education(all_text),
        "years_exp": extract_years_experience(all_text),
        "posted_date": posted_date,
    }


def build_full_url(company_name: str, path: str, all_workday: list[dict]) -> str:
    for c in all_workday:
        if c["name"] == company_name:
            base = c.get("base_url", "")
            return base + path if base and path else path
    return path
