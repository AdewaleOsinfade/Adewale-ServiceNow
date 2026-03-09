# Job Application Automation Tool

Automated job search, application tracking, and browser-assisted applying — tailored for **Adewale Osinfade**, a ServiceNow administrator and Business Analyst professional.

---

## Quick Start

```bash
cd Job-Application-Automation
python job_searcher.py       # search & apply
python tracker_viewer.py    # manage your application pipeline
```

**Requirements:** Python 3.7+ only. No third-party packages needed except `selenium` (optional, for LinkedIn Easy Apply auto-fill).

---

## Menu Options (`job_searcher.py`)

| Option | Description |
|--------|-------------|
| `[1]` | Search all sources and display results |
| `[2]` | Search all sources + apply interactively (opens browser, saves cover letters) |
| `[3]` | **Daily digest** — finds only NEW jobs since last run, saves `jobs_YYYY-MM-DD.txt` |
| `[4]` | View / apply to saved jobs from the last search or digest |

---

## Job Sources

| Source | API Key? | Specialty |
|--------|----------|-----------|
| Remotive | No | Remote tech jobs |
| Indeed | No (RSS) | Broad job market |
| Dice | No (RSS) | IT / tech BA roles |
| The Muse | No | Mid/senior roles |
| SimplyHired | No (RSS) | General market |
| Adzuna | Yes (free) | US broad market |
| USAJobs | Yes (free) | Government IT roles |

### Optional API Keys (all free)

**Adzuna** — get a key at https://developer.adzuna.com/
```bash
export ADZUNA_APP_ID=your_app_id
export ADZUNA_APP_KEY=your_app_key
```

**USAJobs** — register at https://developer.usajobs.gov/
```bash
export USAJOBS_API_KEY=your_key
export USAJOBS_EMAIL=your@email.com
```

---

## Features

### Resume Support
- Set `resume_path` in `profile.json` to your resume file path
- Every time you log an application, it records whether your resume was attached (`Yes`/`No`)
- The application checklist reminds you to attach it when applying

### Smart Filtering
- **Deduplicates** by title + company across all sources (same job from Indeed & Dice = one result)
- **Hard excludes** `intern` / `internship` regardless of salary
- **Soft excludes** `junior` / `entry-level` — but overridden if salary is above your `salary_min`
- **Sorts** newest-first, with a ★ relevance score based on alert keywords
- **Salary** shown when available

### Interactive Applying
For each job you can:
- **[a]** Open in browser + save cover letter + log application
- **[v]** Preview the auto-generated cover letter first
- **[s]** Skip
- **[q]** Quit the session

**LinkedIn jobs** — optionally use Selenium to pre-fill name, email, and phone into the Easy Apply form, then pause for your review before submission.

**All other sites** — opens the URL in your default browser and shows a checklist:
```
[ ] Attach resume     → /path/to/your/resume.pdf
[ ] Paste cover letter → saved to cover_letters/ folder
[ ] Verify name, email, phone
[ ] Double-check requirements before submitting
```

### Universal Selenium Auto-Fill
When you choose option `[2]` and press `[a]` on any job, you'll be asked:
```
Use Selenium auto-fill? [y/n]:
```
If you choose **y**, the tool will:
1. **Auto-install** `selenium` and `webdriver-manager` if not already present
2. **Launch Chrome** automatically (no manual ChromeDriver download needed)
3. **Auto-fill** as many form fields as possible:
   - First name, last name, email, phone
   - LinkedIn URL, GitHub URL
   - Resume upload (attaches file at your `resume_path`)
   - Cover letter (pastes personalized letter into text area)
   - Work authorization dropdowns → selects "Yes, I am authorized"
   - Sponsorship dropdowns → selects "No, I do not require sponsorship"
   - Experience dropdowns → selects closest match to 5–7 years
4. **Scroll to the bottom** and **highlight the Submit button in green**
5. Print `READY — please review the form and click Submit when ready`
6. **Wait for you to press Enter** in Terminal
7. Ask `Did you submit? [y/n]` — only logs to `applications.csv` if you confirm **y**

Works for: **LinkedIn Easy Apply**, **Indeed**, **Greenhouse**, **Lever**, and most standard job application forms.

**Requirements:** Google Chrome must be installed. `selenium` and `webdriver-manager` are installed automatically.

### Daily Digest Mode
- Remembers every job it has shown you (stored in `seen_jobs.json`)
- On subsequent runs it only surfaces **new** listings
- Saves a readable `jobs_2026-03-09.txt` file with titles, companies, and links
- Great for running on a schedule (cron, Task Scheduler)

**Run on a schedule (Linux/Mac cron example):**
```bash
# Run daily at 8am, pipe output to a log
0 8 * * * cd /path/to/Job-Application-Automation && python job_searcher.py <<< "3" >> digest.log 2>&1
```

---

## Files

| File | Description |
|------|-------------|
| `profile.json` | Your info, job preferences, resume path, cover letter template |
| `job_searcher.py` | Main script — search, filter, apply, daily digest |
| `tracker_viewer.py` | View and manage your application pipeline |
| `applications.csv` | Auto-created log of every application |
| `found_jobs.json` | Jobs from last search (used by option `[4]`) |
| `seen_jobs.json` | Fingerprints of all previously surfaced jobs (daily digest) |
| `cover_letters/` | Auto-generated cover letters per company/role |
| `jobs_YYYY-MM-DD.txt` | Daily digest output files |

---

## Configuring `profile.json`

Key fields to fill in:

```json
{
  "personal": {
    "phone": "555-123-4567",
    "resume_path": "/home/adewale/Documents/Adewale_Resume.pdf"
  },
  "job_preferences": {
    "salary_min": 70000,
    "keywords": ["ServiceNow", "ITSM", "Business Analyst", ...],
    "target_roles": ["Business Analyst", "ServiceNow Business Analyst", ...],
    "alert_keywords": ["ServiceNow", "ITSM", "Agile", "SQL", "Jira"],
    "exclude_keywords": ["intern", "internship", "junior", "entry-level"]
  }
}
```

---

## Tracker Viewer (`tracker_viewer.py`)

| Option | Description |
|--------|-------------|
| `[1]` | View all logged applications |
| `[2]` | Summary: count by status, source, and resume attachment rate |
| `[3]` | Update status (Applied → Phone Screen → Interview → Offer/Rejected) |
| `[4]` | Filter by status |
| `[5]` | Export a full `application_report_YYYYMMDD_HHMM.txt` |
