import re

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
        remote_input = input(f"  Include remote positions? [{'Y/n' if include_remote else 'y/N'}]: ").strip().lower()
        if remote_input == "y":
            include_remote = True
        elif remote_input == "n":
            include_remote = False

    result = {"scope": scope, "include_remote": include_remote}
    if scope == "states":
        result["states"] = states
    return result


def build_title_filter(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


def build_exclude_filter(patterns: list[str]) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile("|".join(patterns), re.IGNORECASE)


def extract_all_text(obj) -> str:
    if isinstance(obj, str):
        return obj + " "
    if isinstance(obj, dict):
        return "".join(extract_all_text(v) for v in obj.values())
    if isinstance(obj, list):
        return "".join(extract_all_text(item) for item in obj)
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
