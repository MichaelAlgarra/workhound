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
  workhound --add-company                  # add a custom Workday, Eightfold, or Greenhouse company
  workhound --remove-company Acme          # remove a custom company
  workhound --max-pages 3                  # limit pages per keyword (default: 5)
  workhound --ssl                          # enable SSL verification (off by default)
  workhound --no-salary                    # skip salary lookup (faster)
  workhound --days 7                       # only jobs posted in the last N days
  workhound --history                      # show past results from DB
"""

import argparse
import urllib3
import pandas as pd
import shutil
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from . import DEFAULT_MAX_PAGES, DEFAULT_PROFILE_NAME
from .db import (
    init_db, load_profile, save_jobs_to_db, get_job_history, load_custom_companies,
)
from .profiles import show_profile, interactive_setup, edit_profile_interactive
from .filters import (
    DEFAULT_LOCATION_FILTER, format_location_filter,
    build_title_filter, build_exclude_filter, filter_jobs,
)
from .sites import (
    BUILTIN_WORKDAY_COMPANIES, BUILTIN_EIGHTFOLD_COMPANIES, BUILTIN_GREENHOUSE_COMPANIES,
)
from .sites.workday import search as search_workday, fetch_detail, build_full_url
from .sites.eightfold import search as search_eightfold
from .sites.greenhouse import search as search_greenhouse, extract_details as extract_greenhouse_details

VERIFY_SSL = False
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

def get_all_companies(conn) -> tuple[list[dict], list[dict], list[dict]]:
    custom_wd, custom_ef, custom_gh = load_custom_companies(conn)
    custom_wd_names = {c["name"] for c in custom_wd}
    custom_ef_names = {c["name"] for c in custom_ef}
    custom_gh_names = {c["name"] for c in custom_gh}
    all_wd = [c for c in BUILTIN_WORKDAY_COMPANIES if c["name"] not in custom_wd_names] + custom_wd
    all_ef = [c for c in BUILTIN_EIGHTFOLD_COMPANIES if c["name"] not in custom_ef_names] + custom_ef
    all_gh = [c for c in BUILTIN_GREENHOUSE_COMPANIES if c["name"] not in custom_gh_names] + custom_gh
    return all_wd, all_ef, all_gh


def get_all_company_names(conn) -> list[str]:
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

def add_company_interactive(conn):
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


def remove_company(conn, name: str):
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
# Job detail fetching
# ---------------------------------------------------------------------------

def fetch_job_details(jobs: list[dict], verify_ssl: bool, skip_salary: bool = False):
    extract_greenhouse_details(jobs, skip_salary)

    workday_jobs = [j for j in jobs if j.get("_api_base")]
    if not workday_jobs:
        return

    total = len(workday_jobs)
    print(f"Fetching details for {total} Workday jobs...", flush=True)
    done = 0

    def _fetch_one(job: dict) -> tuple[dict, dict]:
        return job, fetch_detail(job["_api_base"], job["_external_path"], verify_ssl)

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
                        help="Add a custom Workday, Eightfold, or Greenhouse company")
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
