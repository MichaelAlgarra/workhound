"""
Job Finder
Searches company career sites (Workday + Eightfold platforms) based on
user-configured search profiles stored in a local SQLite database.

Ships with 12 built-in companies, but you can add any company that uses
Workday or Eightfold for their job board.

Usage:
  python job_finder.py --setup                        # create a search profile (required first time)
  python job_finder.py                                # run with default profile
  python job_finder.py --csv                          # also save to CSV
  python job_finder.py --profile jane                 # run with a different profile
  python job_finder.py --list-profiles                # show saved profiles
  python job_finder.py --show-profile                 # show active profile details
  python job_finder.py --companies Merck,Pfizer       # only these companies
  python job_finder.py --list-companies               # show available companies
  python job_finder.py --add-company                  # add a custom Workday or Eightfold company
  python job_finder.py --remove-company Acme          # remove a custom company
  python job_finder.py --max-pages 3                  # limit pages per keyword (default: 5)
  python job_finder.py --ssl                          # enable SSL verification (off by default)
  python job_finder.py --no-salary                    # skip salary lookup (faster)
  python job_finder.py --history                      # show past results from DB
"""

import argparse
import requests
import urllib3
import pandas as pd
import shutil
import time
import re
import sqlite3
import json
import os
from datetime import datetime

VERIFY_SSL = False
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Built-in company definitions
# ---------------------------------------------------------------------------

BUILTIN_WORKDAY_COMPANIES = [
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

BUILTIN_EIGHTFOLD_COMPANIES = [
    {"name": "AstraZeneca", "domain": "astrazeneca.com",
     "api_url": "https://astrazeneca.eightfold.ai/api/apply/v2/jobs"},
]

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

DEFAULT_MAX_PAGES = 5
DEFAULT_PROFILE_NAME = "default"

LEVEL_EXCLUDES = {
    "entry": [
        r"director", r"vice president", r"VP", r"head of", r"chief",
        r"executive", r"principal", r"distinguished",
    ],
    "mid": [
        r"director", r"vice president", r"VP", r"head of", r"chief",
        r"intern", r"co-op", r"manager.*(?:people|team)", r"executive",
    ],
    "senior": [
        r"intern", r"co-op", r"junior",
    ],
    "leadership": [
        r"intern", r"co-op", r"junior",
    ],
    "any": [],
}

# ---------------------------------------------------------------------------
# Location filter
# ---------------------------------------------------------------------------

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
}

US_LOCATION_PATTERN = re.compile(
    r"United States|USA|,\s*(" + "|".join(US_STATES) + r")\b",
    re.IGNORECASE,
)


def has_us_location(location_text: str) -> bool:
    if not location_text:
        return False
    return bool(US_LOCATION_PATTERN.search(location_text))


# ---------------------------------------------------------------------------
# Salary / education / experience regexes
# ---------------------------------------------------------------------------

SALARY_RANGE_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s?[kK])?"
    r"\s*(?:[-–—]|to|and)\s*"
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s?[kK])?"
    r"(?:\s*(?:per\s+(?:year|annum|hour|hr)|/\s*(?:yr|hr|hour|year)|annually|hourly))?",
    re.IGNORECASE,
)

SALARY_SINGLE_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s?[kK])?"
    r"\s*(?:per\s+(?:year|annum|hour|hr)|/\s*(?:yr|hr|hour|year)|annually|hourly)",
    re.IGNORECASE,
)

DEGREE_RE = re.compile(
    r"\b("
    r"Ph\.?D\.?"
    r"|Doctorate|Doctoral"
    r"|Master'?s?(?:\s+degree)?"
    r"|M\.?S\.?c?\.?"
    r"|MBA"
    r"|Bachelor'?s?(?:\s+degree)?"
    r"|B\.?S\.?c?\.?"
    r"|B\.?A\.?"
    r")\b",
    re.IGNORECASE,
)

YEARS_EXP_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|\s*[-–—]\s*\d{1,2})?\s*\+?\s*years?\s+(?:of\s+)?(?:\w+\s+){0,3}experience",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "job_finder.db")


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        name TEXT PRIMARY KEY,
        level TEXT NOT NULL DEFAULT 'mid',
        keywords TEXT NOT NULL,
        title_patterns TEXT NOT NULL,
        exclude_patterns TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_name TEXT NOT NULL,
        run_id TEXT NOT NULL,
        company TEXT NOT NULL,
        title TEXT NOT NULL,
        location TEXT DEFAULT '',
        url TEXT DEFAULT '',
        salary TEXT DEFAULT '',
        education TEXT DEFAULT '',
        years_exp TEXT DEFAULT '',
        scraped_at TEXT NOT NULL,
        UNIQUE(profile_name, company, title, url)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS custom_companies (
        name TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        api_url TEXT NOT NULL,
        base_url TEXT DEFAULT '',
        eightfold_domain TEXT DEFAULT '',
        added_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Company registry (built-in + custom from DB)
# ---------------------------------------------------------------------------

def load_custom_companies(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    rows = conn.execute("SELECT * FROM custom_companies ORDER BY name").fetchall()
    workday = []
    eightfold = []
    for r in rows:
        if r["platform"] == "workday":
            workday.append({
                "name": r["name"],
                "url": r["api_url"],
                "base_url": r["base_url"] or "",
            })
        elif r["platform"] == "eightfold":
            eightfold.append({
                "name": r["name"],
                "domain": r["eightfold_domain"] or "",
                "api_url": r["api_url"],
            })
    return workday, eightfold


def get_all_companies(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    custom_wd, custom_ef = load_custom_companies(conn)
    custom_wd_names = {c["name"] for c in custom_wd}
    custom_ef_names = {c["name"] for c in custom_ef}
    all_wd = [c for c in BUILTIN_WORKDAY_COMPANIES if c["name"] not in custom_wd_names] + custom_wd
    all_ef = [c for c in BUILTIN_EIGHTFOLD_COMPANIES if c["name"] not in custom_ef_names] + custom_ef
    return all_wd, all_ef


def get_all_company_names(conn: sqlite3.Connection) -> list[str]:
    all_wd, all_ef = get_all_companies(conn)
    return sorted([c["name"] for c in all_wd] + [c["name"] for c in all_ef])


def get_builtin_names() -> set[str]:
    return {c["name"] for c in BUILTIN_WORKDAY_COMPANIES} | {c["name"] for c in BUILTIN_EIGHTFOLD_COMPANIES}


# ---------------------------------------------------------------------------
# Custom company management
# ---------------------------------------------------------------------------

def add_company_interactive(conn: sqlite3.Connection):
    print("\n=== Add Custom Company ===\n")
    print("Supported platforms:")
    print("  1. Workday   (most large companies: Google, Amazon, Meta, pharma, finance, etc.)")
    print("  2. Eightfold (some tech/pharma companies)")
    platform_choice = input("\nPlatform [1]: ").strip()
    platform = "eightfold" if platform_choice == "2" else "workday"

    name = input("\nCompany name (e.g. Google, Tesla): ").strip()
    if not name:
        print("  No name entered. Aborted.")
        return

    existing = conn.execute("SELECT name FROM custom_companies WHERE name = ?", (name,)).fetchone()
    if existing or name in get_builtin_names():
        print(f"  '{name}' already exists. Use --remove-company first to replace it.")
        return

    if platform == "workday":
        print("\nWorkday API URL:")
        print("  Go to the company's careers page, open browser DevTools (F12) > Network tab,")
        print("  search for a job, and look for a POST request to a URL like:")
        print("  https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs")
        api_url = input("\nAPI URL: ").strip()
        if not api_url or "myworkdayjobs.com" not in api_url:
            print("  Invalid Workday URL. Aborted.")
            return

        print("\nBase URL (for human-readable job links):")
        print("  Usually: https://<tenant>.wd<N>.myworkdayjobs.com/en-US/<site>")
        print("  (Leave blank if unsure — job paths will still be shown)")
        base_url = input("\nBase URL []: ").strip()

        conn.execute(
            "INSERT INTO custom_companies (name, platform, api_url, base_url, added_at) VALUES (?, ?, ?, ?, ?)",
            (name, "workday", api_url, base_url, datetime.now().isoformat()),
        )
    else:
        print("\nEightfold domain:")
        print("  The company's domain used in their Eightfold portal (e.g. astrazeneca.com)")
        domain = input("\nDomain: ").strip()
        if not domain:
            print("  No domain entered. Aborted.")
            return

        api_url = f"https://{domain.split('.')[0]}.eightfold.ai/api/apply/v2/jobs"
        print(f"\n  Auto-detected API URL: {api_url}")
        custom_url = input("  Override? (leave blank to keep): ").strip()
        if custom_url:
            api_url = custom_url

        conn.execute(
            "INSERT INTO custom_companies (name, platform, api_url, eightfold_domain, added_at) VALUES (?, ?, ?, ?, ?)",
            (name, "eightfold", api_url, domain, datetime.now().isoformat()),
        )

    conn.commit()
    print(f"\n  '{name}' added ({platform})!")
    print(f"  It will now appear in --list-companies and be included in searches.\n")


def remove_company(conn: sqlite3.Connection, name: str):
    if name in get_builtin_names():
        print(f"  '{name}' is a built-in company and cannot be removed.")
        return
    result = conn.execute("DELETE FROM custom_companies WHERE name = ?", (name,))
    conn.commit()
    if result.rowcount:
        print(f"  '{name}' removed.")
    else:
        print(f"  '{name}' not found in custom companies.")


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

def load_profile(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return {
        "name": row["name"],
        "level": row["level"],
        "keywords": json.loads(row["keywords"]),
        "title_patterns": json.loads(row["title_patterns"]),
        "exclude_patterns": json.loads(row["exclude_patterns"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_profile(conn: sqlite3.Connection, profile: dict):
    now = datetime.now().isoformat()
    existing = conn.execute("SELECT created_at FROM profiles WHERE name = ?", (profile["name"],)).fetchone()
    created = existing["created_at"] if existing else now
    conn.execute(
        """INSERT OR REPLACE INTO profiles
           (name, level, keywords, title_patterns, exclude_patterns, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (profile["name"], profile["level"],
         json.dumps(profile["keywords"]),
         json.dumps(profile["title_patterns"]),
         json.dumps(profile["exclude_patterns"]),
         created, now),
    )
    conn.commit()


def save_jobs_to_db(conn: sqlite3.Connection, jobs: list[dict], profile_name: str, run_id: str):
    now = datetime.now().isoformat()
    for job in jobs:
        conn.execute(
            """INSERT OR REPLACE INTO jobs
               (profile_name, run_id, company, title, location, url, salary, education, years_exp, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_name, run_id, job["company"], job["title"],
             job.get("location", ""), job.get("url", ""),
             job.get("salary", ""), job.get("education", ""),
             job.get("years_exp", ""), now),
        )
    conn.commit()


def get_job_history(conn: sqlite3.Connection, profile_name: str, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT company, title, location, url, salary, education, years_exp, scraped_at
           FROM jobs WHERE profile_name = ?
           ORDER BY scraped_at DESC LIMIT ?""",
        (profile_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Interactive profile setup
# ---------------------------------------------------------------------------

def show_profile(profile: dict):
    kw = profile["keywords"]
    tp = profile["title_patterns"]
    ep = profile["exclude_patterns"]
    print(f"  Name:       {profile['name']}")
    print(f"  Level:      {profile['level']}")
    print(f"  Keywords:   {', '.join(kw[:6])}{'  ... +' + str(len(kw) - 6) + ' more' if len(kw) > 6 else ''}")
    print(f"  Filters:    {', '.join(tp[:6])}{'  ... +' + str(len(tp) - 6) + ' more' if len(tp) > 6 else ''}")
    print(f"  Excludes:   {', '.join(ep[:5])}{'  ... +' + str(len(ep) - 5) + ' more' if len(ep) > 5 else ''}")
    if "created_at" in profile:
        print(f"  Created:    {profile['created_at'][:10]}")
        print(f"  Updated:    {profile['updated_at'][:10]}")
    print()


def interactive_setup(conn: sqlite3.Connection):
    print("\n=== Job Finder - Profile Setup ===\n")

    name = input(f"Profile name [{DEFAULT_PROFILE_NAME}]: ").strip()
    if not name:
        name = DEFAULT_PROFILE_NAME

    existing = load_profile(conn, name)
    if existing:
        print(f"\n  Profile '{name}' already exists:")
        show_profile(existing)
        choice = input("  Overwrite? [y/N]: ").strip().lower()
        if choice != "y":
            print("  Aborted.")
            return

    print("\nWhat job level are you targeting?")
    print("  1. Entry-level   (excludes: director, VP, chief, executive, principal)")
    print("  2. Mid-level     (excludes: director, VP, chief, executive, intern, co-op)")
    print("  3. Senior IC     (excludes: intern, co-op, junior)")
    print("  4. Leadership    (excludes: intern, co-op, junior)")
    print("  5. Any level     (no title exclusions)")
    level_choice = input("\nChoice [2]: ").strip()
    level_map = {"1": "entry", "2": "mid", "3": "senior", "4": "leadership", "5": "any"}
    level = level_map.get(level_choice, "mid")

    print("\nSearch keywords (comma-separated):")
    print("  These are sent to job board APIs to find postings.")
    print("  Example: software engineer, frontend, backend, full stack")
    kw_input = input("\n> ").strip()
    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
    if not keywords:
        print("\n  No keywords entered. Aborted.")
        return

    print("\nTitle must match at least one pattern (comma-separated):")
    print("  Use partial words for broader matching. Leave blank to auto-fill from keywords.")
    print("  Example: software eng, frontend, full stack")
    tf_input = input("\n> ").strip()
    title_patterns = [p.strip() for p in tf_input.split(",") if p.strip()]
    if not title_patterns:
        title_patterns = [k.strip() for k in keywords]
        print(f"  (auto-filled from keywords: {', '.join(title_patterns)})")

    profile = {
        "name": name,
        "level": level,
        "keywords": keywords,
        "title_patterns": title_patterns,
        "exclude_patterns": LEVEL_EXCLUDES[level],
    }
    save_profile(conn, profile)
    print(f"\nProfile '{name}' saved!\n")
    show_profile(profile)


# ---------------------------------------------------------------------------
# Build regex filters from profile
# ---------------------------------------------------------------------------

def build_title_filter(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


def build_exclude_filter(patterns: list[str]) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile("|".join(patterns), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def _extract_all_text(obj) -> str:
    if isinstance(obj, str):
        return obj + " "
    if isinstance(obj, dict):
        return "".join(_extract_all_text(v) for v in obj.values())
    if isinstance(obj, list):
        return "".join(_extract_all_text(item) for item in obj)
    return ""


def _normalize_degree(raw: str) -> str:
    r = raw.strip().rstrip(".")
    low = r.lower().replace(".", "").replace("'", "")
    if low in ("phd", "doctorate", "doctoral"):
        return "PhD"
    if low in ("masters", "master", "masters degree", "master degree", "msc", "ms", "mba"):
        if low == "mba":
            return "MBA"
        return "MS"
    if low in ("bachelors", "bachelor", "bachelors degree", "bachelor degree", "bsc", "bs", "ba"):
        return "BS"
    return r


def extract_education(text: str) -> str:
    matches = DEGREE_RE.findall(text)
    if not matches:
        return ""
    order = {"PhD": 0, "MBA": 1, "MS": 2, "BS": 3}
    normalized = sorted(set(_normalize_degree(m) for m in matches), key=lambda d: order.get(d, 99))
    return ", ".join(normalized)


def extract_years_experience(text: str) -> str:
    matches = YEARS_EXP_RE.findall(text)
    if not matches:
        return ""
    years = sorted(set(int(m) for m in matches))
    return ", ".join(f"{y}+" for y in years)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def search_workday(company: dict, keywords: list[str], verify_ssl: bool,
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
                results.append({
                    "company": company["name"],
                    "title": job.get("title", ""),
                    "url": job.get("externalPath", ""),
                    "location": job.get("locationsText", ""),
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


def search_eightfold(company: dict, keywords: list[str], verify_ssl: bool,
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
            results.append({
                "company": company["name"],
                "title": pos.get("name", ""),
                "url": pos.get("canonicalPositionUrl", ""),
                "location": pos.get("location", ""),
                "_api_base": None,
                "_external_path": None,
                "_platform": "eightfold",
            })
        time.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# Job detail fetching
# ---------------------------------------------------------------------------

def fetch_workday_detail(api_base: str, external_path: str, verify_ssl: bool) -> dict:
    detail_url = api_base + external_path
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15, verify=verify_ssl)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"salary": "", "education": "", "years_exp": ""}

    info = data.get("jobPostingInfo", {})
    salary = ""
    for key in ("payRange", "payTransparencyRange", "salary", "compensationRange", "startingPay", "basePay"):
        val = info.get(key)
        if val and isinstance(val, str) and val.strip():
            salary = val.strip()
            break

    all_text = _extract_all_text(data)

    if not salary:
        match = SALARY_RANGE_RE.search(all_text)
        if match:
            salary = match.group(0).strip()
    if not salary:
        match = SALARY_SINGLE_RE.search(all_text)
        if match:
            salary = match.group(0).strip()

    return {
        "salary": salary,
        "education": extract_education(all_text),
        "years_exp": extract_years_experience(all_text),
    }


def build_full_url(company_name: str, path: str, all_workday: list[dict]) -> str:
    for c in all_workday:
        if c["name"] == company_name:
            base = c.get("base_url", "")
            return base + path if base and path else path
    return path


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_jobs(jobs: list[dict], title_re: re.Pattern, exclude_re: re.Pattern | None) -> list[dict]:
    seen = set()
    filtered = []
    for job in jobs:
        title = job["title"]
        key = (job["company"], title)
        if key in seen:
            continue
        seen.add(key)
        if not title_re.search(title):
            continue
        if exclude_re and exclude_re.search(title):
            continue
        if not has_us_location(job.get("location", "")):
            continue
        filtered.append(job)
    return filtered


def fetch_job_details(jobs: list[dict], verify_ssl: bool, skip_salary: bool = False):
    workday_jobs = [j for j in jobs if j.get("_api_base")]
    if not workday_jobs:
        return

    print(f"Fetching job details for {len(workday_jobs)} Workday jobs...", flush=True)
    for i, job in enumerate(workday_jobs, 1):
        if i % 10 == 0 or i == len(workday_jobs):
            print(f"  [{i}/{len(workday_jobs)}]", flush=True)
        detail = fetch_workday_detail(job["_api_base"], job["_external_path"], verify_ssl)
        if not skip_salary:
            job["salary"] = detail["salary"]
        job["education"] = detail["education"]
        job["years_exp"] = detail["years_exp"]
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_table(jobs: list[dict]):
    if not jobs:
        print("No matching jobs found.")
        return

    term_width = shutil.get_terminal_size((200, 24)).columns
    company_w = max(len(j["company"]) for j in jobs)
    company_w = max(company_w, 7)
    salary_w = max((len(j.get("salary", "")) for j in jobs), default=0)
    salary_w = max(salary_w, 6)
    salary_w = min(salary_w, 40)
    edu_w = max((len(j.get("education", "")) for j in jobs), default=0)
    edu_w = max(edu_w, 9)
    edu_w = min(edu_w, 20)
    exp_w = max((len(j.get("years_exp", "")) for j in jobs), default=0)
    exp_w = max(exp_w, 7)
    exp_w = min(exp_w, 15)
    location_w = 25
    url_w = 50
    title_w = term_width - company_w - location_w - salary_w - edu_w - exp_w - url_w - 19
    title_w = max(title_w, 25)

    header = (f"{'COMPANY':<{company_w}} | {'TITLE':<{title_w}} | {'LOCATION':<{location_w}} | "
              f"{'EDUCATION':<{edu_w}} | {'YRS EXP':<{exp_w}} | {'SALARY':<{salary_w}} | {'URL'}")
    sep = "-" * len(header)

    print(f"\n{sep}")
    print(header)
    print(sep)

    current_company = None
    for job in jobs:
        if job["company"] != current_company:
            if current_company is not None:
                print(f"{'':>{company_w}} |{'':>{title_w + 2}}|{'':>{location_w + 2}}|"
                      f"{'':>{edu_w + 2}}|{'':>{exp_w + 2}}|{'':>{salary_w + 2}}|")
            current_company = job["company"]

        title = job["title"]
        if len(title) > title_w:
            title = title[: title_w - 1] + "…"

        location = job.get("location", "")
        if len(location) > location_w:
            location = location[: location_w - 1] + "…"

        education = job.get("education", "")
        if len(education) > edu_w:
            education = education[: edu_w - 1] + "…"

        years_exp = job.get("years_exp", "")
        if len(years_exp) > exp_w:
            years_exp = years_exp[: exp_w - 1] + "…"

        salary = job.get("salary", "")
        if len(salary) > salary_w:
            salary = salary[: salary_w - 1] + "…"

        url = job["url"]
        print(f"{job['company']:<{company_w}} | {title:<{title_w}} | {location:<{location_w}} | "
              f"{education:<{edu_w}} | {years_exp:<{exp_w}} | {salary:<{salary_w}} | {url}")

    print(sep)
    print(f"\n  {len(jobs)} US-based jobs found across {len(set(j['company'] for j in jobs))} companies\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Job board scraper for Workday and Eightfold career sites")
    parser.add_argument("--csv", action="store_true", help="Save results to CSV")
    parser.add_argument("--ssl", action="store_true", help="Enable SSL verification (disabled by default)")
    parser.add_argument("--companies", type=str, default=None,
                        help="Comma-separated list of companies to scrape")
    parser.add_argument("--list-companies", action="store_true",
                        help="Print available company names and exit")
    parser.add_argument("--add-company", action="store_true",
                        help="Add a custom Workday or Eightfold company")
    parser.add_argument("--remove-company", type=str, default=None, metavar="NAME",
                        help="Remove a custom company by name")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max pages to fetch per keyword per company (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--no-salary", action="store_true",
                        help="Skip salary lookup (faster)")
    parser.add_argument("--profile", type=str, default=DEFAULT_PROFILE_NAME,
                        help=f"Profile to use (default: {DEFAULT_PROFILE_NAME})")
    parser.add_argument("--setup", action="store_true",
                        help="Create or update a search profile")
    parser.add_argument("--list-profiles", action="store_true",
                        help="Show all saved profiles")
    parser.add_argument("--show-profile", action="store_true",
                        help="Show the active profile's full config")
    parser.add_argument("--history", action="store_true",
                        help="Show past job results from the database")
    args = parser.parse_args()

    conn = init_db()

    if args.add_company:
        add_company_interactive(conn)
        conn.close()
        return

    if args.remove_company:
        remove_company(conn, args.remove_company)
        conn.close()
        return

    if args.list_companies:
        all_names = get_all_company_names(conn)
        builtin = get_builtin_names()
        print("\nAvailable companies:")
        for name in all_names:
            tag = "" if name in builtin else "  (custom)"
            print(f"  {name}{tag}")
        print(f"\n  {len(all_names)} total ({len(builtin)} built-in, {len(all_names) - len(builtin)} custom)")
        print(f"  Use --add-company to add more.\n")
        conn.close()
        return

    if args.setup:
        interactive_setup(conn)
        conn.close()
        return

    if args.list_profiles:
        rows = conn.execute(
            "SELECT name, level, created_at, updated_at FROM profiles ORDER BY name"
        ).fetchall()
        if not rows:
            print("No profiles found. Run with --setup to create one.")
        else:
            print("\nSaved profiles:")
            for r in rows:
                job_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE profile_name = ?", (r["name"],)
                ).fetchone()[0]
                print(f"  {r['name']:<20} level={r['level']:<12} jobs_found={job_count:<6} updated={r['updated_at'][:10]}")
            print()
        conn.close()
        return

    profile = load_profile(conn, args.profile)
    if not profile:
        print(f"Profile '{args.profile}' not found.")
        print(f"Run with --setup to create one, or use --list-profiles to see available profiles.")
        conn.close()
        return

    if args.show_profile:
        print(f"\nActive profile:")
        show_profile(profile)
        conn.close()
        return

    if args.history:
        history = get_job_history(conn, args.profile)
        if not history:
            print(f"No saved jobs for profile '{args.profile}'.")
        else:
            print(f"\n  {len(history)} saved jobs for profile '{args.profile}':\n")
            print_table(history)
        conn.close()
        return

    print(f"\nUsing profile: {profile['name']} (level: {profile['level']}, "
          f"{len(profile['keywords'])} keywords, {len(profile['title_patterns'])} filters)\n")

    verify_ssl = args.ssl
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    title_re = build_title_filter(profile["title_patterns"])
    exclude_re = build_exclude_filter(profile["exclude_patterns"])
    keywords = profile["keywords"]

    all_workday, all_eightfold = get_all_companies(conn)
    all_names = sorted([c["name"] for c in all_workday] + [c["name"] for c in all_eightfold])

    selected = None
    if args.companies:
        selected = {name.strip() for name in args.companies.split(",")}
        unknown = selected - set(all_names)
        if unknown:
            print(f"Unknown companies: {', '.join(sorted(unknown))}")
            print(f"Available: {', '.join(all_names)}")
            conn.close()
            return

    all_jobs = []

    for company in all_workday:
        if selected and company["name"] not in selected:
            continue
        print(f"Searching {company['name']}...", flush=True)
        jobs = search_workday(company, keywords, verify_ssl, max_pages=args.max_pages)
        print(f"  {len(jobs)} raw results")
        all_jobs.extend(jobs)

    for company in all_eightfold:
        if selected and company["name"] not in selected:
            continue
        print(f"Searching {company['name']}...", flush=True)
        jobs = search_eightfold(company, keywords, verify_ssl, max_pages=args.max_pages)
        print(f"  {len(jobs)} raw results")
        all_jobs.extend(jobs)

    filtered = filter_jobs(all_jobs, title_re, exclude_re)
    print(f"\n  {len(filtered)} jobs after filtering (US-only, title match, dedup)")

    for job in filtered:
        if job.get("_platform") == "workday":
            job["url"] = build_full_url(job["company"], job["url"], all_workday)

    fetch_job_details(filtered, verify_ssl, skip_salary=args.no_salary)

    filtered.sort(key=lambda j: (j["company"], j["title"]))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_jobs_to_db(conn, filtered, profile["name"], run_id)
    print(f"  {len(filtered)} jobs saved to database (run: {run_id})")

    print_table(filtered)

    if args.csv:
        export = [{k: v for k, v in job.items() if not k.startswith("_")} for job in filtered]
        df = pd.DataFrame(export)
        filename = f"jobs_{run_id}.csv"
        df.to_csv(filename, index=False)
        print(f"  Saved to {filename}")

    conn.close()


if __name__ == "__main__":
    main()
