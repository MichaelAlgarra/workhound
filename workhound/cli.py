"""
WorkHound — sniff out job listings across company career sites.

Usage:
  workhound --setup                        # create a search profile (required first time)
  workhound                                # run with default profile
  workhound --csv                          # also save to CSV
  workhound --json                         # also save to JSON
  workhound --profile jane                 # run with a different profile
  workhound --list-profiles                # show saved profiles
  workhound --show-profile                 # show active profile details
  workhound --edit-profile                 # edit individual profile fields
  workhound --companies Merck,Pfizer       # only these companies
  workhound --list-companies               # show available companies
  workhound --add-company                  # add a custom Workday or Eightfold company
  workhound --remove-company Acme          # remove a custom company
  workhound --max-pages 3                  # limit pages per keyword (default: 5)
  workhound --ssl                          # enable SSL verification (off by default)
  workhound --no-salary                    # skip salary lookup (faster)
  workhound --days 7                       # only jobs posted in the last N days
  workhound --history                      # show past results from DB
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

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

BUILTIN_GREENHOUSE_COMPANIES = [
    {"name": "Moderna", "board_token": "modernatx"},
    {"name": "Recursion", "board_token": "recursionpharmaceuticals"},
    {"name": "Insitro", "board_token": "insitro"},
    {"name": "Tempus", "board_token": "tempus"},
    {"name": "Benchling", "board_token": "benchling"},
    {"name": "Schrödinger", "board_token": "schaboratories"},
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

DEFAULT_LOCATION_FILTER = {"scope": "us", "include_remote": True}


def has_us_location(location_text: str) -> bool:
    if not location_text:
        return False
    return bool(US_LOCATION_PATTERN.search(location_text))


def matches_location(location_text: str, location_filter: dict) -> bool:
    scope = location_filter.get("scope", "us")
    include_remote = location_filter.get("include_remote", True)

    if scope == "any":
        return True

    if not location_text:
        return False

    if include_remote and re.search(r"\bremote\b", location_text, re.IGNORECASE):
        return True

    if scope == "us":
        return has_us_location(location_text)

    if scope == "states":
        states = location_filter.get("states", [])
        if not states:
            return has_us_location(location_text)
        state_pattern = re.compile(
            r",\s*(" + "|".join(re.escape(s) for s in states) + r")\b",
            re.IGNORECASE,
        )
        return bool(state_pattern.search(location_text))

    return True


def format_location_filter(lf: dict) -> str:
    scope = lf.get("scope", "us")
    include_remote = lf.get("include_remote", True)
    if scope == "any":
        return "Any location"
    if scope == "us":
        base = "All US"
    elif scope == "states":
        states = lf.get("states", [])
        base = ", ".join(states) if states else "All US"
    else:
        base = scope
    if include_remote:
        base += " + remote"
    return base


def prompt_location_filter(current: dict | None = None) -> dict:
    if current is None:
        current = DEFAULT_LOCATION_FILTER.copy()

    print(f"\n  Current location: {format_location_filter(current)}")
    print("\n  Location scope:")
    print("    1. All US locations")
    print("    2. Specific US states")
    print("    3. Any location (no filter)")
    scope_choice = input(f"\n  Choice [keep current]: ").strip()
    scope_map = {"1": "us", "2": "states", "3": "any"}

    if scope_choice in scope_map:
        scope = scope_map[scope_choice]
    else:
        scope = current.get("scope", "us")

    states = current.get("states", [])
    if scope == "states":
        current_states = ", ".join(states) if states else ""
        print(f"\n  Current states: {current_states or '(none)'}")
        new_states = input("  State codes, comma-separated (e.g. PA, NJ, NY): ").strip()
        if new_states:
            states = [s.strip().upper() for s in new_states.split(",") if s.strip()]
            invalid = [s for s in states if s not in US_STATES]
            if invalid:
                print(f"  Warning: unrecognized state codes ignored: {', '.join(invalid)}")
                states = [s for s in states if s in US_STATES]

    include_remote = current.get("include_remote", True)
    if scope != "any":
        default = "Y" if include_remote else "N"
        remote_input = input(f"  Include remote positions? [{'Y/n' if include_remote else 'y/N'}]: ").strip().lower()
        if remote_input == "y":
            include_remote = True
        elif remote_input == "n":
            include_remote = False

    result = {"scope": scope, "include_remote": include_remote}
    if scope == "states":
        result["states"] = states
    return result


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

DATA_DIR = os.path.join(os.path.expanduser("~"), ".workhound")
DB_PATH = os.path.join(DATA_DIR, "workhound.db")


def init_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        name TEXT PRIMARY KEY,
        level TEXT NOT NULL DEFAULT 'mid',
        keywords TEXT NOT NULL,
        title_patterns TEXT NOT NULL,
        exclude_patterns TEXT NOT NULL,
        location_filter TEXT NOT NULL DEFAULT '{}',
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
        posted_date TEXT DEFAULT '',
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
    try:
        conn.execute("SELECT location_filter FROM profiles LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN location_filter TEXT NOT NULL DEFAULT '{}'"
        )
    try:
        conn.execute("SELECT posted_date FROM jobs LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE jobs ADD COLUMN posted_date TEXT DEFAULT ''")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Company registry (built-in + custom from DB)
# ---------------------------------------------------------------------------

def load_custom_companies(conn: sqlite3.Connection) -> tuple[list[dict], list[dict], list[dict]]:
    rows = conn.execute("SELECT * FROM custom_companies ORDER BY name").fetchall()
    workday = []
    eightfold = []
    greenhouse = []
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
        elif r["platform"] == "greenhouse":
            greenhouse.append({
                "name": r["name"],
                "board_token": r["api_url"],
            })
    return workday, eightfold, greenhouse


def get_all_companies(conn: sqlite3.Connection) -> tuple[list[dict], list[dict], list[dict]]:
    custom_wd, custom_ef, custom_gh = load_custom_companies(conn)
    custom_wd_names = {c["name"] for c in custom_wd}
    custom_ef_names = {c["name"] for c in custom_ef}
    custom_gh_names = {c["name"] for c in custom_gh}
    all_wd = [c for c in BUILTIN_WORKDAY_COMPANIES if c["name"] not in custom_wd_names] + custom_wd
    all_ef = [c for c in BUILTIN_EIGHTFOLD_COMPANIES if c["name"] not in custom_ef_names] + custom_ef
    all_gh = [c for c in BUILTIN_GREENHOUSE_COMPANIES if c["name"] not in custom_gh_names] + custom_gh
    return all_wd, all_ef, all_gh


def get_all_company_names(conn: sqlite3.Connection) -> list[str]:
    all_wd, all_ef, all_gh = get_all_companies(conn)
    return sorted([c["name"] for c in all_wd] + [c["name"] for c in all_ef] + [c["name"] for c in all_gh])


def get_builtin_names() -> set[str]:
    return (
        {c["name"] for c in BUILTIN_WORKDAY_COMPANIES}
        | {c["name"] for c in BUILTIN_EIGHTFOLD_COMPANIES}
        | {c["name"] for c in BUILTIN_GREENHOUSE_COMPANIES}
    )


# ---------------------------------------------------------------------------
# Custom company management
# ---------------------------------------------------------------------------

def add_company_interactive(conn: sqlite3.Connection):
    print("\n=== Add Custom Company ===\n")
    print("Supported platforms:")
    print("  1. Workday    (most large companies: Google, Amazon, Meta, pharma, finance, etc.)")
    print("  2. Eightfold  (some tech/pharma companies)")
    print("  3. Greenhouse (biotech startups, mid-size companies)")
    platform_choice = input("\nPlatform [1]: ").strip()
    platform_map = {"1": "workday", "2": "eightfold", "3": "greenhouse"}
    platform = platform_map.get(platform_choice, "workday")

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
    elif platform == "eightfold":
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
    elif platform == "greenhouse":
        print("\nGreenhouse board token:")
        print("  This is the slug in the company's job board URL:")
        print("  https://boards.greenhouse.io/<board_token>")
        print("  Example: modernatx, recursionpharmaceuticals, insitro")
        board_token = input("\nBoard token: ").strip()
        if not board_token:
            print("  No board token entered. Aborted.")
            return

        conn.execute(
            "INSERT INTO custom_companies (name, platform, api_url, added_at) VALUES (?, ?, ?, ?)",
            (name, "greenhouse", board_token, datetime.now().isoformat()),
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
    lf_raw = row["location_filter"] if "location_filter" in row.keys() else "{}"
    return {
        "name": row["name"],
        "level": row["level"],
        "keywords": json.loads(row["keywords"]),
        "title_patterns": json.loads(row["title_patterns"]),
        "exclude_patterns": json.loads(row["exclude_patterns"]),
        "location_filter": json.loads(lf_raw) if lf_raw else DEFAULT_LOCATION_FILTER.copy(),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_profile(conn: sqlite3.Connection, profile: dict):
    now = datetime.now().isoformat()
    existing = conn.execute("SELECT created_at FROM profiles WHERE name = ?", (profile["name"],)).fetchone()
    created = existing["created_at"] if existing else now
    lf = profile.get("location_filter", DEFAULT_LOCATION_FILTER)
    conn.execute(
        """INSERT OR REPLACE INTO profiles
           (name, level, keywords, title_patterns, exclude_patterns, location_filter, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (profile["name"], profile["level"],
         json.dumps(profile["keywords"]),
         json.dumps(profile["title_patterns"]),
         json.dumps(profile["exclude_patterns"]),
         json.dumps(lf),
         created, now),
    )
    conn.commit()


def save_jobs_to_db(conn: sqlite3.Connection, jobs: list[dict], profile_name: str, run_id: str):
    now = datetime.now().isoformat()
    for job in jobs:
        conn.execute(
            """INSERT OR REPLACE INTO jobs
               (profile_name, run_id, company, title, location, url, salary, education, years_exp, posted_date, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_name, run_id, job["company"], job["title"],
             job.get("location", ""), job.get("url", ""),
             job.get("salary", ""), job.get("education", ""),
             job.get("years_exp", ""), job.get("posted_date", ""), now),
        )
    conn.commit()


def get_job_history(conn: sqlite3.Connection, profile_name: str, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT company, title, location, url, salary, education, years_exp, posted_date, scraped_at
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
    lf = profile.get("location_filter", DEFAULT_LOCATION_FILTER)
    print(f"  Name:       {profile['name']}")
    print(f"  Level:      {profile['level']}")
    print(f"  Keywords:   {', '.join(kw[:6])}{'  ... +' + str(len(kw) - 6) + ' more' if len(kw) > 6 else ''}")
    print(f"  Filters:    {', '.join(tp[:6])}{'  ... +' + str(len(tp) - 6) + ' more' if len(tp) > 6 else ''}")
    print(f"  Excludes:   {', '.join(ep[:5])}{'  ... +' + str(len(ep) - 5) + ' more' if len(ep) > 5 else ''}")
    print(f"  Location:   {format_location_filter(lf)}")
    if "created_at" in profile:
        print(f"  Created:    {profile['created_at'][:10]}")
        print(f"  Updated:    {profile['updated_at'][:10]}")
    print()


def interactive_setup(conn: sqlite3.Connection):
    print("\n=== WorkHound - Profile Setup ===\n")

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

    location_filter = prompt_location_filter()

    profile = {
        "name": name,
        "level": level,
        "keywords": keywords,
        "title_patterns": title_patterns,
        "exclude_patterns": LEVEL_EXCLUDES[level],
        "location_filter": location_filter,
    }
    save_profile(conn, profile)
    print(f"\nProfile '{name}' saved!\n")
    show_profile(profile)


def edit_profile_interactive(conn: sqlite3.Connection, profile_name: str):
    profile = load_profile(conn, profile_name)
    if not profile:
        print(f"Profile '{profile_name}' not found.")
        print(f"Run with --setup to create one, or use --list-profiles to see available profiles.")
        return

    print(f"\n=== Edit Profile: {profile_name} ===\n")
    show_profile(profile)
    print("Leave blank to keep current value.\n")

    print(f"  Current level: {profile['level']}")
    print("  Options: entry, mid, senior, leadership, any")
    new_level = input("  New level: ").strip().lower()
    if new_level and new_level in LEVEL_EXCLUDES:
        profile["level"] = new_level
        profile["exclude_patterns"] = LEVEL_EXCLUDES[new_level]
        print(f"  -> level set to '{new_level}'")
    elif new_level:
        print(f"  Unknown level '{new_level}', keeping '{profile['level']}'")

    print(f"\n  Current keywords: {', '.join(profile['keywords'])}")
    new_kw = input("  New keywords (comma-separated): ").strip()
    if new_kw:
        profile["keywords"] = [k.strip() for k in new_kw.split(",") if k.strip()]
        print(f"  -> {len(profile['keywords'])} keywords set")

    print(f"\n  Current title patterns: {', '.join(profile['title_patterns'])}")
    new_tp = input("  New title patterns (comma-separated): ").strip()
    if new_tp:
        profile["title_patterns"] = [p.strip() for p in new_tp.split(",") if p.strip()]
        print(f"  -> {len(profile['title_patterns'])} title patterns set")

    current_lf = profile.get("location_filter", DEFAULT_LOCATION_FILTER)
    print(f"\n  Edit location? Current: {format_location_filter(current_lf)}")
    edit_loc = input("  Change location settings? [y/N]: ").strip().lower()
    if edit_loc == "y":
        profile["location_filter"] = prompt_location_filter(current_lf)

    save_profile(conn, profile)
    print(f"\nProfile '{profile_name}' updated!\n")
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


def search_greenhouse(company: dict, keywords: list[str], verify_ssl: bool,
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
        return {"salary": "", "education": "", "years_exp": "", "posted_date": ""}

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


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_jobs(jobs: list[dict], title_re: re.Pattern, exclude_re: re.Pattern | None,
                location_filter: dict | None = None) -> list[dict]:
    if location_filter is None:
        location_filter = DEFAULT_LOCATION_FILTER
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
        if not matches_location(job.get("location", ""), location_filter):
            continue
        filtered.append(job)
    return filtered


def extract_greenhouse_details(jobs: list[dict], skip_salary: bool = False):
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


def fetch_job_details(jobs: list[dict], verify_ssl: bool, skip_salary: bool = False):
    extract_greenhouse_details(jobs, skip_salary)

    workday_jobs = [j for j in jobs if j.get("_api_base")]
    if not workday_jobs:
        return

    total = len(workday_jobs)
    print(f"Fetching details for {total} Workday jobs...", flush=True)
    done = 0

    def _fetch_one(job: dict) -> tuple[dict, dict]:
        return job, fetch_workday_detail(job["_api_base"], job["_external_path"], verify_ssl)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_one, job): job for job in workday_jobs}
        for future in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  [{done}/{total}]", flush=True)
            try:
                job, detail = future.result()
                if not skip_salary:
                    job["salary"] = detail["salary"]
                job["education"] = detail["education"]
                job["years_exp"] = detail["years_exp"]
                if not job.get("posted_date") and detail.get("posted_date"):
                    job["posted_date"] = detail["posted_date"]
            except Exception:
                pass


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
    posted_w = 10
    location_w = 25
    url_w = 50
    title_w = term_width - company_w - location_w - salary_w - edu_w - exp_w - posted_w - url_w - 22
    title_w = max(title_w, 25)

    header = (f"{'COMPANY':<{company_w}} | {'TITLE':<{title_w}} | {'LOCATION':<{location_w}} | "
              f"{'POSTED':<{posted_w}} | "
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
                      f"{'':>{posted_w + 2}}|{'':>{edu_w + 2}}|{'':>{exp_w + 2}}|{'':>{salary_w + 2}}|")
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

        posted = job.get("posted_date", "")
        if len(posted) > posted_w:
            posted = posted[: posted_w - 1] + "…"

        salary = job.get("salary", "")
        if len(salary) > salary_w:
            salary = salary[: salary_w - 1] + "…"

        url = job["url"]
        print(f"{job['company']:<{company_w}} | {title:<{title_w}} | {location:<{location_w}} | "
              f"{posted:<{posted_w}} | "
              f"{education:<{edu_w}} | {years_exp:<{exp_w}} | {salary:<{salary_w}} | {url}")

    print(sep)
    print(f"\n  {len(jobs)} jobs found across {len(set(j['company'] for j in jobs))} companies\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WorkHound — sniff out job listings across career sites")
    parser.add_argument("--csv", action="store_true", help="Save results to CSV")
    parser.add_argument("--json", action="store_true", help="Save results to JSON")
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
    parser.add_argument("--days", type=int, default=None, metavar="N",
                        help="Only show jobs posted within the last N days")
    parser.add_argument("--profile", type=str, default=DEFAULT_PROFILE_NAME,
                        help=f"Profile to use (default: {DEFAULT_PROFILE_NAME})")
    parser.add_argument("--setup", action="store_true",
                        help="Create or update a search profile")
    parser.add_argument("--edit-profile", action="store_true",
                        help="Edit individual fields on an existing profile")
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

    if args.edit_profile:
        edit_profile_interactive(conn, args.profile)
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

    location_filter = profile.get("location_filter", DEFAULT_LOCATION_FILTER)
    print(f"\nUsing profile: {profile['name']} (level: {profile['level']}, "
          f"{len(profile['keywords'])} keywords, {len(profile['title_patterns'])} filters, "
          f"location: {format_location_filter(location_filter)})\n")

    verify_ssl = args.ssl
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    title_re = build_title_filter(profile["title_patterns"])
    exclude_re = build_exclude_filter(profile["exclude_patterns"])
    keywords = profile["keywords"]

    all_workday, all_eightfold, all_greenhouse = get_all_companies(conn)
    all_names = sorted(
        [c["name"] for c in all_workday]
        + [c["name"] for c in all_eightfold]
        + [c["name"] for c in all_greenhouse]
    )

    selected = None
    if args.companies:
        selected = {name.strip() for name in args.companies.split(",")}
        unknown = selected - set(all_names)
        if unknown:
            print(f"Unknown companies: {', '.join(sorted(unknown))}")
            print(f"Available: {', '.join(all_names)}")
            conn.close()
            return

    companies_to_scrape = []
    for company in all_workday:
        if selected and company["name"] not in selected:
            continue
        companies_to_scrape.append(("workday", company))
    for company in all_eightfold:
        if selected and company["name"] not in selected:
            continue
        companies_to_scrape.append(("eightfold", company))
    for company in all_greenhouse:
        if selected and company["name"] not in selected:
            continue
        companies_to_scrape.append(("greenhouse", company))

    all_jobs = []
    print(f"Searching {len(companies_to_scrape)} companies concurrently...\n", flush=True)

    def _scrape_company(platform: str, company: dict) -> tuple[str, list[dict]]:
        if platform == "workday":
            return company["name"], search_workday(company, keywords, verify_ssl, max_pages=args.max_pages)
        if platform == "greenhouse":
            return company["name"], search_greenhouse(company, keywords, verify_ssl, max_pages=args.max_pages)
        return company["name"], search_eightfold(company, keywords, verify_ssl, max_pages=args.max_pages)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_scrape_company, platform, company): company["name"]
            for platform, company in companies_to_scrape
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, jobs = future.result()
                print(f"  {name}: {len(jobs)} raw results", flush=True)
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"  {name}: error - {e}", flush=True)

    filtered = filter_jobs(all_jobs, title_re, exclude_re, location_filter)
    print(f"\n  {len(filtered)} jobs after filtering (location, title match, dedup)")

    for job in filtered:
        if job.get("_platform") == "workday":
            job["url"] = build_full_url(job["company"], job["url"], all_workday)

    fetch_job_details(filtered, verify_ssl, skip_salary=args.no_salary)

    if args.days is not None:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        before_count = len(filtered)
        filtered = [j for j in filtered if (j.get("posted_date") or "") >= cutoff]
        print(f"  {len(filtered)} jobs within last {args.days} days (filtered out {before_count - len(filtered)})")

    filtered.sort(key=lambda j: (j["company"], j["title"]))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_jobs_to_db(conn, filtered, profile["name"], run_id)
    print(f"  {len(filtered)} jobs saved to database (run: {run_id})")

    print_table(filtered)

    export = [{k: v for k, v in job.items() if not k.startswith("_")} for job in filtered]

    if args.csv:
        df = pd.DataFrame(export)
        filename = f"jobs_{run_id}.csv"
        df.to_csv(filename, index=False)
        print(f"  Saved to {filename}")

    if args.json:
        filename = f"jobs_{run_id}.json"
        with open(filename, "w") as f:
            json.dump(export, f, indent=2)
        print(f"  Saved to {filename}")

    conn.close()


if __name__ == "__main__":
    main()
