# WorkHound

A CLI tool that sniffs out job listings across company career sites, filters them by title, level, and location (US-only), and tracks results in a local database. Works with any company that uses **Workday** or **Eightfold** for their job board.

Ships with 12 built-in companies, but you can add any company you want.

## Built-in Companies

Amgen, AstraZeneca, Biogen, BMS, Gilead, GSK, J&J, Merck, Novartis, Pfizer, Regeneron, Sanofi

## Install

Requires Python 3.10+.

```bash
# (Recommended) Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Quick Start

```bash
# Create your search profile (interactive)
python job_finder.py --setup

# Run a search
python job_finder.py

# Export to CSV
python job_finder.py --csv
```

The `--setup` wizard asks for:
- **Profile name** - so you can save multiple search configs
- **Job level** - entry, mid, senior, leadership, or any (auto-excludes irrelevant titles)
- **Keywords** - search terms sent to job board APIs (e.g. "software engineer, frontend, ML")
- **Title filters** - patterns the job title must match (auto-filled from keywords if left blank)

## Adding Custom Companies

You can add any company that uses Workday or Eightfold for their careers page:

```bash
# Interactive wizard to add a company
python job_finder.py --add-company

# Remove a custom company
python job_finder.py --remove-company Acme

# See all available companies (built-in + custom)
python job_finder.py --list-companies
```

### Finding a Workday URL

Most large companies use Workday. To find the API URL:

1. Go to the company's careers page
2. Open browser DevTools (F12) > **Network** tab
3. Search for a job
4. Look for a POST request to a URL like:
   `https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`

### Finding an Eightfold URL

Some companies use Eightfold. You just need the company's domain (e.g. `astrazeneca.com`) and the tool will auto-detect the API URL.

## Usage

```
python job_finder.py [options]

Search:
  --setup                    Create or edit a search profile
  --profile NAME             Use a specific profile (default: "default")
  --list-profiles            Show all saved profiles
  --show-profile             Show the active profile's config
  --companies Merck,Google   Only search specific companies
  --list-companies           Show all available companies
  --max-pages N              Max pages per keyword per company (default: 5)
  --csv                      Save results to a CSV file
  --ssl                      Enable SSL verification (off by default)
  --no-salary                Skip salary lookup (faster)
  --history                  Show past results from the database

Companies:
  --add-company              Add a custom Workday or Eightfold company
  --remove-company NAME      Remove a custom company
```

## How It Works

1. Searches each company's Workday or Eightfold career API using your keywords
2. Filters results: title must match your patterns, must be US-based, excludes unwanted levels
3. Fetches job details (salary, education, years of experience) from individual postings
4. Deduplicates and displays a formatted table in the terminal
5. Saves everything to a local SQLite database (`job_finder.db`)

## SSL Issues

If you hit SSL certificate errors (common on WSL), either:
1. `sudo apt update && sudo apt install ca-certificates && pip install certifi --upgrade`
2. Or just leave SSL verification off (the default) - the tool only reads public career pages

## License

MIT
