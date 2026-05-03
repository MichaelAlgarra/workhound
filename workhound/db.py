import sqlite3
import json
import os
from datetime import datetime

from . import DEFAULT_PROFILE_NAME
from .filters import DEFAULT_LOCATION_FILTER

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
