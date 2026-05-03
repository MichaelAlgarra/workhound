# WorkHound

A CLI tool that sniffs out job listings across company career sites, filters them by title, level, and location (US-only), and tracks results in a local database. Works with any company that uses **Workday** or **Eightfold** for their job board.

Ships with 12 built-in pharma companies, but you can add any company you want.

## Why This Exists

If you're actively job searching, you know the pain of scrolling through multiple career sites every day looking for relevant postings. This tool does that for you — it pulls listings from company job boards, filters them to what actually matches your search, and keeps a history so you don't re-check the same stuff. The built-in companies are pharma because that's my background, but you can add any company that runs on Workday or Eightfold.

## Built-in Companies

Amgen, AstraZeneca, Biogen, BMS, Gilead, GSK, J&J, Merck, Novartis, Pfizer, Regeneron, Sanofi

## Install

Requires Python 3.10+.

```bash
# Clone the repo
git clone https://github.com/MichaelAlgarra/workhound.git
cd workhound

# (Recommended) Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install
make install               # or: pip install .
```

Or install directly from GitHub without cloning:

```bash
pip install git+https://github.com/MichaelAlgarra/workhound.git
```

For development (changes to the code take effect immediately):

```bash
make dev                   # or: pip install -e .
```

## Quick Start

```bash
# 1. Create your search profile (interactive wizard)
workhound --setup

# 2. Run a search
workhound

# 3. Export results to CSV
workhound --csv
```

The `--setup` wizard asks for:
- **Profile name** — save multiple search configs (e.g. "data-science", "engineering")
- **Job level** — entry, mid, senior, leadership, or any (auto-excludes irrelevant titles)
- **Keywords** — search terms sent to job board APIs (e.g. "software engineer, frontend, ML")
- **Title filters** — patterns the job title must match (auto-filled from keywords if left blank)

## What It Does

1. Searches each company's Workday or Eightfold career API using your keywords
2. Filters results: title must match your patterns, must be US-based, excludes unwanted levels
3. Fetches job details (salary, education, years of experience) from individual postings
4. Deduplicates and displays a formatted table in the terminal
5. Saves everything to a local SQLite database (`~/.workhound/workhound.db`)

## Make Commands

| Command        | Description                                 |
|----------------|---------------------------------------------|
| `make help`    | Show all available make commands             |
| `make install` | Install workhound into the active environment |
| `make dev`     | Install in editable mode for development     |
| `make run`     | Run a search with the default profile        |
| `make csv`     | Run a search and save results to CSV         |
| `make setup`   | Create or edit a search profile              |
| `make clean`   | Remove build artifacts                       |

## CLI Reference

```
workhound [options]
```

### Searching

| Flag                         | Description                                        |
|------------------------------|----------------------------------------------------|
| `--setup`                    | Create or edit a search profile (interactive)       |
| `--profile NAME`             | Use a specific profile (default: "default")         |
| `--list-profiles`            | Show all saved profiles                             |
| `--show-profile`             | Show the active profile's full config               |
| `--companies Merck,Pfizer`   | Only search specific companies                      |
| `--list-companies`           | Show all available companies (built-in + custom)    |
| `--max-pages N`              | Max pages per keyword per company (default: 5)      |
| `--csv`                      | Save results to a CSV file                          |
| `--no-salary`                | Skip salary lookup (faster)                         |
| `--history`                  | Show past results from the database                 |
| `--ssl`                      | Enable SSL verification (off by default)            |

### Managing Companies

| Flag                         | Description                                        |
|------------------------------|----------------------------------------------------|
| `--add-company`              | Add a custom Workday or Eightfold company           |
| `--remove-company NAME`      | Remove a custom company                             |
| `--list-companies`           | Show all available companies                        |

## Adding Custom Companies

You can add any company that uses Workday or Eightfold for their careers page:

```bash
# Interactive wizard
workhound --add-company

# Remove one
workhound --remove-company Acme

# See what's available
workhound --list-companies
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

## Data Storage

All data is stored locally in `~/.workhound/`:

| File              | Purpose                                    |
|-------------------|--------------------------------------------|
| `workhound.db`    | SQLite database (profiles, jobs, companies) |

CSV exports are saved to the current working directory as `jobs_YYYYMMDD_HHMMSS.csv`.

## SSL Issues

If you hit SSL certificate errors (common on WSL), either:
1. `sudo apt update && sudo apt install ca-certificates && pip install certifi --upgrade`
2. Or just leave SSL verification off (the default) — the tool only reads public career pages

## License

MIT
