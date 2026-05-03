import sqlite3

from . import DEFAULT_PROFILE_NAME, LEVEL_EXCLUDES
from .db import load_profile, save_profile
from .filters import DEFAULT_LOCATION_FILTER, format_location_filter, prompt_location_filter


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
