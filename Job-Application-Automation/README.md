# Job Application Automation Tool

Automated job search and application tracking tool tailored for **Adewale Osinfade** — a ServiceNow administrator and ITSM professional.

## Features

- **Job Search**: Searches multiple job boards (Remotive, Adzuna) for ServiceNow and ITSM roles
- **Smart Filtering**: Excludes irrelevant listings (intern, entry-level, etc.)
- **Cover Letter Generator**: Auto-fills personalized cover letters for each job
- **Application Tracker**: Logs every application to a CSV file with date, company, and status
- **Status Tracker**: Update application statuses (Applied → Interview → Offer, etc.)
- **Export Reports**: Generate text-based summary reports of your job search

---

## Setup

### 1. Requirements
- Python 3.7+ (no third-party packages needed — uses only standard library)

### 2. Configure Your Profile
Edit `profile.json` to update:
- Personal details (phone number, location)
- Job title preferences
- Target keywords and locations
- Cover letter template

### 3. (Optional) Set Up Adzuna API
Get a free API key from [https://developer.adzuna.com/](https://developer.adzuna.com/), then set environment variables:
```bash
export ADZUNA_APP_ID=your_app_id
export ADZUNA_APP_KEY=your_app_key
```

---

## Usage

### Search for Jobs
```bash
cd Job-Application-Automation
python job_searcher.py
```
Choose option `[1]` to search, or `[2]` to search and apply interactively.

### Apply Interactively
When applying interactively, for each job you can:
- **[a]** Apply — generates a cover letter and logs the application
- **[s]** Skip the listing
- **[v]** Preview the cover letter before deciding
- **[q]** Quit

Cover letters are saved to the `cover_letters/` folder.

### Track Your Applications
```bash
python tracker_viewer.py
```
Options:
- View all logged applications
- View summary by status
- Update an application's status (Applied, Interview, Offer, etc.)
- Filter by status
- Export a report

---

## Files

| File | Description |
|---|---|
| `profile.json` | Your personal info, job preferences, and cover letter template |
| `job_searcher.py` | Main script: search jobs and apply interactively |
| `tracker_viewer.py` | View and manage your application history |
| `applications.csv` | Auto-created log of all applications |
| `found_jobs.json` | Cache of jobs from the last search |
| `cover_letters/` | Auto-generated cover letters per application |

---

## Job Sources

| Source | API Key Required | Best For |
|---|---|---|
| [Remotive](https://remotive.com) | No | Remote jobs |
| [Adzuna](https://adzuna.com) | Yes (free) | US-based jobs |

---

## Customization

To add more job sources, add a new `search_jobs_<source>()` function in `job_searcher.py` and call it in `main()`. Each function should return a list of job dicts with keys: `title`, `company`, `location`, `url`, `description`, `posted`, `source`, `salary`, `tags`.
