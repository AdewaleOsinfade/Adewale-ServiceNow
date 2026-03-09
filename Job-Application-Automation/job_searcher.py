"""
Job Application Automation Tool — Adewale Osinfade
Roles: Business Analyst | ServiceNow | ITSM

HOW TO RUN:
    cd Job-Application-Automation
    python job_searcher.py

MENU OPTIONS:
    [1] Search all sources and display results
    [2] Search + apply interactively (opens browser, generates cover letters)
    [3] Daily digest — finds only NEW jobs since last run, saves jobs_YYYY-MM-DD.txt
    [4] View saved jobs from last search/digest

API KEYS REQUIRED (all free — set as environment variables):

    JSearch via RapidAPI  →  https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
        export RAPIDAPI_KEY=your_key              (free tier: 200 req/month)

    Reed API              →  https://www.reed.co.uk/developers/jobseeker
        export REED_API_KEY=your_key              (free registration)

    USAJobs               →  https://developer.usajobs.gov/
        export USAJOBS_API_KEY=your_key
        export USAJOBS_USER_AGENT=your@email.com  (required by USAJobs)

OPTIONAL API KEYS:
    Adzuna  →  https://developer.adzuna.com/
        export ADZUNA_APP_ID=your_id
        export ADZUNA_APP_KEY=your_key

OPTIONAL (LinkedIn Easy Apply automation only):
    pip install selenium
    Download ChromeDriver matching your Chrome version:
    https://chromedriver.chromium.org/downloads
"""

import base64
import csv
import hashlib
import json
import os
import re
import time
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date
from html.parser import HTMLParser


PROFILE_FILE  = "profile.json"
TRACKER_FILE  = "applications.csv"
FOUND_JOBS_FILE = "found_jobs.json"
SEEN_JOBS_FILE  = "seen_jobs.json"

_UA = "Mozilla/5.0 (compatible; JobSearchBot/1.0; +https://github.com/AdewaleOsinfade)"


# ─── HELPERS ────────────────────────────────────────────────────────────────

def load_profile():
    with open(PROFILE_FILE) as f:
        return json.load(f)


def _fetch(url, headers=None, timeout=14):
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _make_job(title, company, location, url, description, posted,
              source, salary="", tags=""):
    return {
        "title":       title.strip(),
        "company":     company.strip(),
        "location":    location.strip(),
        "url":         url.strip(),
        "description": re.sub(r"<[^>]+>", " ", description)[:500],
        "posted":      posted,
        "source":      source,
        "salary":      salary.strip() if salary else "Not listed",
        "tags":        tags,
    }


def _parse_date(posted):
    """Normalize a posted date string to YYYY-MM-DD HH:MM:SS for sorting."""
    if not posted:
        return ""
    # ISO 8601
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(posted[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    # RFC 2822 (RSS feeds)
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(posted).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return posted


def job_fingerprint(job):
    """Stable MD5 ID based on title + company + url for deduplication."""
    raw = f"{job['title'].lower()}|{job['company'].lower()}|{job['url']}"
    return hashlib.md5(raw.encode()).hexdigest()


def _job_dedup_key(job):
    """Normalized title+company key for cross-source deduplication."""
    title   = re.sub(r"\W+", " ", job["title"].lower()).strip()
    company = re.sub(r"\W+", " ", job["company"].lower()).strip()
    return f"{title}||{company}"


# US state abbreviations used by the location filter
_US_STATE_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

_US_MARKERS = {
    "united states", "usa", "remote", "anywhere",
    "work from home", "wfh", "us only", "north america",
}


def _is_us_or_remote(location, source=""):
    """
    Return True if the job location is in the US or remote.
    Sources that are inherently US/remote (USAJobs, Remotive) always pass.
    Unknown/unlisted locations pass through so they are not silently dropped.
    """
    if source in ("USAJobs", "Remotive"):
        return True
    loc = location.lower().strip()
    if not loc or loc in ("see listing", "worldwide", ""):
        return True  # unknown — let the user see it and judge
    if any(m in loc for m in _US_MARKERS):
        return True
    # "City, ST" patterns — check every token against state abbreviations
    tokens = re.split(r"[\s,/]+", loc)
    if any(t in _US_STATE_ABBREVS for t in tokens):
        return True
    return False


def _matches_target_role(title, profile):
    """
    Return True if the job title contains at least one substring from
    target_roles or titles in profile.json (case-insensitive).
    """
    prefs      = profile["job_preferences"]
    all_roles  = prefs.get("target_roles", []) + prefs.get("titles", [])
    title_low  = title.lower()
    return any(role.lower() in title_low for role in all_roles)


# ─── JOB SOURCES ────────────────────────────────────────────────────────────

def search_jobs_remotive(keywords):
    """Remotive public API — free, no key needed. Best for remote roles."""
    found = []
    for kw in keywords[:5]:
        try:
            url  = f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote(kw)}&limit=25"
            data = json.loads(_fetch(url))
            for job in data.get("jobs", []):
                found.append(_make_job(
                    title       = job.get("title", ""),
                    company     = job.get("company_name", ""),
                    location    = job.get("candidate_required_location", "Remote"),
                    url         = job.get("url", ""),
                    description = job.get("description", ""),
                    posted      = job.get("publication_date", ""),
                    source      = "Remotive",
                    salary      = job.get("salary", ""),
                    tags        = ", ".join(job.get("tags", [])),
                ))
            time.sleep(0.8)
        except Exception as e:
            print(f"  Remotive error ({kw}): {e}")
    return found


def search_jobs_adzuna(keywords, location="United States"):
    """
    Adzuna API — free key from https://developer.adzuna.com/
    Set env: ADZUNA_APP_ID, ADZUNA_APP_KEY
    """
    app_id  = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("  Adzuna: set ADZUNA_APP_ID + ADZUNA_APP_KEY env vars to enable.")
        return []
    query = urllib.parse.quote(" ".join(keywords[:4]))
    loc   = urllib.parse.quote(location)
    url   = (
        f"https://api.adzuna.com/v1/api/jobs/us/search/1"
        f"?app_id={app_id}&app_key={app_key}&results_per_page=25"
        f"&what={query}&where={loc}&content-type=application/json"
    )
    found = []
    try:
        data = json.loads(_fetch(url))
        for job in data.get("results", []):
            s_min = job.get("salary_min", "")
            s_max = job.get("salary_max", "")
            sal   = f"${float(s_min):,.0f} – ${float(s_max):,.0f}" if s_min and s_max else ""
            found.append(_make_job(
                title       = job.get("title", ""),
                company     = job.get("company", {}).get("display_name", ""),
                location    = job.get("location", {}).get("display_name", ""),
                url         = job.get("redirect_url", ""),
                description = job.get("description", ""),
                posted      = job.get("created", ""),
                source      = "Adzuna",
                salary      = sal,
            ))
    except Exception as e:
        print(f"  Adzuna error: {e}")
    return found


def search_jobs_jsearch(keywords):
    """
    JSearch via RapidAPI — reliable aggregator covering LinkedIn, Indeed, Glassdoor, ZipRecruiter.
    Free tier: 200 requests/month.  Sign up: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
    Set env: RAPIDAPI_KEY
    """
    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("  JSearch: set RAPIDAPI_KEY env var (free at rapidapi.com/jsearch).")
        return []

    found = []
    headers = {
        "X-RapidAPI-Key":  api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    for kw in keywords[:4]:
        try:
            params = urllib.parse.urlencode({
                "query":       f"{kw} United States",
                "page":        "1",
                "num_pages":   "1",
                "date_posted": "month",
            })
            url  = f"https://jsearch.p.rapidapi.com/search?{params}"
            data = json.loads(_fetch(url, headers=headers))
            for job in data.get("data", []):
                city    = job.get("job_city", "")
                state   = job.get("job_state", "")
                country = job.get("job_country", "")
                loc     = ", ".join(filter(None, [city, state, country])) or "See listing"
                if job.get("job_is_remote"):
                    loc = f"Remote — {loc}" if loc != "See listing" else "Remote"
                s_min = job.get("job_min_salary")
                s_max = job.get("job_max_salary")
                period = job.get("job_salary_period", "")
                sal = ""
                if s_min and s_max:
                    sal = f"${float(s_min):,.0f} – ${float(s_max):,.0f} {period}".strip()
                elif s_min:
                    sal = f"${float(s_min):,.0f}+ {period}".strip()
                found.append(_make_job(
                    title       = job.get("job_title", ""),
                    company     = job.get("employer_name", ""),
                    location    = loc,
                    url         = job.get("job_apply_link", job.get("job_google_link", "")),
                    description = job.get("job_description", ""),
                    posted      = job.get("job_posted_at_datetime_utc", ""),
                    source      = f"JSearch/{job.get('job_publisher', 'Aggregator')}",
                    salary      = sal,
                    tags        = job.get("job_employment_type", ""),
                ))
            time.sleep(1)
        except Exception as e:
            print(f"  JSearch error ({kw}): {e}")
    return found


def search_jobs_linkedin(keywords):
    """
    LinkedIn guest jobs API — public endpoint, no key required.
    Parses the HTML fragment returned by LinkedIn's unauthenticated job search.
    Note: LinkedIn may throttle heavy usage; results are best-effort.
    """
    class _CardParser(HTMLParser):
        """Pull job-card fields out of a LinkedIn jobs HTML fragment."""
        def __init__(self):
            super().__init__()
            self.jobs, self._cur, self._cap = [], {}, None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            cls = d.get("class", "")
            if tag == "a" and "base-card__full-link" in cls:
                self._cur["url"] = d.get("href", "").split("?")[0]
            elif tag == "h3" and "base-search-card__title" in cls:
                self._cap = "title"
            elif tag == "h4" and "base-search-card__subtitle" in cls:
                self._cap = "company"
            elif tag == "span" and "job-search-card__location" in cls:
                self._cap = "location"
            elif tag == "time":
                self._cur["posted"] = d.get("datetime", "")

        def handle_data(self, data):
            if self._cap:
                self._cur[self._cap] = data.strip()
                self._cap = None

        def handle_endtag(self, tag):
            if tag == "li" and self._cur.get("title") and self._cur.get("url"):
                self.jobs.append(dict(self._cur))
                self._cur = {}

    found = []
    for kw in keywords[:4]:
        try:
            params = urllib.parse.urlencode({
                "keywords": kw,
                "location": "United States",
                "start":    "0",
                "count":    "25",
            })
            url  = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
            html = _fetch(url).decode("utf-8", errors="replace")
            parser = _CardParser()
            parser.feed(html)
            for card in parser.jobs:
                found.append(_make_job(
                    title       = card.get("title", ""),
                    company     = card.get("company", ""),
                    location    = card.get("location", "See listing"),
                    url         = card.get("url", ""),
                    description = "",
                    posted      = card.get("posted", ""),
                    source      = "LinkedIn",
                ))
            time.sleep(1.5)
        except Exception as e:
            print(f"  LinkedIn error ({kw}): {e}")
    return found


def search_jobs_reed(keywords):
    """
    Reed API — free, requires registration at https://www.reed.co.uk/developers/jobseeker
    Uses HTTP Basic auth: username = API key, password = empty.
    Set env: REED_API_KEY
    """
    api_key = os.environ.get("REED_API_KEY")
    if not api_key:
        print("  Reed: set REED_API_KEY env var (free at reed.co.uk/developers).")
        return []

    token   = base64.b64encode(f"{api_key}:".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    found   = []

    for kw in keywords[:4]:
        try:
            params = urllib.parse.urlencode({
                "keywords":     kw,
                "locationName": "United States",
                "resultsToTake": 25,
                "distanceFromLocation": 0,
            })
            url  = f"https://www.reed.co.uk/api/1.0/search?{params}"
            data = json.loads(_fetch(url, headers=headers))
            for job in data.get("results", []):
                s_min = job.get("minimumSalary")
                s_max = job.get("maximumSalary")
                sal   = ""
                if s_min and s_max:
                    sal = f"${float(s_min):,.0f} – ${float(s_max):,.0f}"
                elif s_min:
                    sal = f"${float(s_min):,.0f}+"
                found.append(_make_job(
                    title       = job.get("jobTitle", ""),
                    company     = job.get("employerName", ""),
                    location    = job.get("locationName", "See listing"),
                    url         = f"https://www.reed.co.uk/jobs/{job.get('jobId', '')}",
                    description = job.get("jobDescription", ""),
                    posted      = job.get("date", ""),
                    source      = "Reed",
                    salary      = sal,
                ))
            time.sleep(1)
        except Exception as e:
            print(f"  Reed error ({kw}): {e}")
    return found


def search_jobs_themuse(keywords):
    """
    The Muse public API — free, no key needed.
    API docs: https://www.themuse.com/developers/api/v2
    Scans multiple pages and filters to relevant titles.
    """
    found    = []
    relevant = {"analyst", "servicenow", "itsm", "it ", "systems", "process", "business"}
    for page in range(4):
        try:
            url  = f"https://www.themuse.com/api/public/jobs?page={page}&level=Mid+Level&level=Senior+Level"
            data = json.loads(_fetch(url))
            jobs = data.get("results", [])
            if not jobs:
                break
            for job in jobs:
                title = job.get("name", "")
                if not any(r in title.lower() for r in relevant):
                    continue
                locations = job.get("locations", [])
                loc       = locations[0].get("name", "See listing") if locations else "See listing"
                levels    = job.get("levels", [])
                level     = levels[0].get("name", "") if levels else ""
                found.append(_make_job(
                    title       = title,
                    company     = job.get("company", {}).get("name", ""),
                    location    = loc,
                    url         = job.get("refs", {}).get("landing_page", ""),
                    description = (job.get("contents") or "")[:500],
                    posted      = job.get("publication_date", ""),
                    source      = "The Muse",
                    tags        = level,
                ))
            time.sleep(0.6)
        except Exception as e:
            print(f"  The Muse error (page {page}): {e}")
            break
    return found


def search_jobs_usajobs(keywords):
    """
    USAJobs API — free key from https://developer.usajobs.gov/
    Set env: USAJOBS_API_KEY, USAJOBS_USER_AGENT (must be your email address)
    Great for government IT/BA positions with ServiceNow.
    """
    api_key    = os.environ.get("USAJOBS_API_KEY")
    user_agent = os.environ.get("USAJOBS_USER_AGENT", "")
    if not api_key:
        print("  USAJobs: set USAJOBS_API_KEY + USAJOBS_USER_AGENT env vars (developer.usajobs.gov).")
        return []
    found = []
    for kw in keywords[:3]:
        try:
            q   = urllib.parse.quote(kw)
            url = (
                f"https://data.usajobs.gov/api/search"
                f"?Keyword={q}&ResultsPerPage=25&RemoteIndicator=True"
            )
            headers = {
                "Authorization-Key": api_key,
                "Host":              "data.usajobs.gov",
                "User-Agent":        user_agent,
            }
            data  = json.loads(_fetch(url, headers=headers))
            items = data.get("SearchResult", {}).get("SearchResultItems", [])
            for item in items:
                pos  = item.get("MatchedObjectDescriptor", {})
                pay  = (pos.get("PositionRemuneration") or [{}])[0]
                locs = pos.get("PositionLocation") or []
                loc  = locs[0].get("LocationName", "See listing") if locs else "See listing"
                sal  = ""
                if pay.get("MinimumRange"):
                    sal = (f"${float(pay['MinimumRange']):,.0f}"
                           f" – ${float(pay.get('MaximumRange', pay['MinimumRange'])):,.0f}"
                           f" {pay.get('RateIntervalCode', '')}")
                summary = (
                    pos.get("UserArea", {})
                       .get("Details", {})
                       .get("JobSummary", "")
                )
                found.append(_make_job(
                    title       = pos.get("PositionTitle", ""),
                    company     = pos.get("OrganizationName", ""),
                    location    = loc,
                    url         = pos.get("PositionURI", ""),
                    description = summary,
                    posted      = pos.get("PublicationStartDate", ""),
                    source      = "USAJobs",
                    salary      = sal,
                ))
            time.sleep(1)
        except Exception as e:
            print(f"  USAJobs error ({kw}): {e}")
    return found


# ─── FILTERING & SORTING ────────────────────────────────────────────────────

def filter_jobs(jobs, profile):
    """
    Four-gate filter applied in order:
      1. Dedup by normalized title + company (cross-source)
      2. Title must match at least one entry in target_roles or titles
      3. Location must be United States or Remote
      4. Exclude/include keyword rules with salary-aware soft-exclude override
    Results are sorted newest-first, then by relevance score.
    """
    prefs      = profile["job_preferences"]
    salary_min = prefs.get("salary_min", 0)
    hard_excl  = {"intern", "internship"}
    soft_excl  = [
        kw.lower() for kw in prefs.get("exclude_keywords", [])
        if kw.lower() not in hard_excl
    ]
    include_kws = [kw.lower() for kw in prefs.get("include_keywords", [])]

    seen_keys = set()
    filtered  = []

    for job in jobs:
        # Gate 1: cross-source deduplication
        key = _job_dedup_key(job)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Gate 2: title must match a target role or job title from profile
        if not _matches_target_role(job["title"], profile):
            continue

        # Gate 3: location must be United States or Remote
        if not _is_us_or_remote(job["location"], source=job.get("source", "")):
            continue

        combined = f"{job['title']} {job['description']}".lower()

        # Gate 4a: hard excludes (intern/internship) — no salary override
        if any(ex in combined for ex in hard_excl):
            continue

        # Gate 4b: check if salary clears the minimum (unlocks soft-exclude override)
        has_good_salary = False
        sal_nums = re.findall(r"\d[\d,]+", job.get("salary", "").replace(",", ""))
        if sal_nums:
            try:
                if max(int(n) for n in sal_nums if len(n) >= 4) >= salary_min:
                    has_good_salary = True
            except ValueError:
                pass

        # Gate 4c: soft excludes (junior/entry-level) — skip unless salary qualifies
        if not has_good_salary and any(ex in combined for ex in soft_excl):
            continue

        # Score by how many alert/include keywords appear in title + description
        job["_score"]       = sum(1 for kw in include_kws if kw in combined)
        job["_posted_sort"] = _parse_date(job.get("posted", ""))
        filtered.append(job)

    # Sort: newest first, then by relevance score
    filtered.sort(key=lambda j: (j["_posted_sort"], j["_score"]), reverse=True)
    return filtered


# ─── COVER LETTER ───────────────────────────────────────────────────────────

def generate_cover_letter(profile, job):
    template = profile.get("cover_letter_template", "")
    return (
        template
        .replace("{job_title}", job.get("title", "this role"))
        .replace("{company}",   job.get("company", "your company"))
    )


def save_cover_letter(profile, job):
    safe_co = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
    safe_t  = re.sub(r"[^a-zA-Z0-9_-]", "_", job["title"])
    os.makedirs("cover_letters", exist_ok=True)
    filepath = f"cover_letters/cover_{safe_co}_{safe_t}.txt"
    with open(filepath, "w") as f:
        f.write(generate_cover_letter(profile, job))
    return filepath


# ─── BROWSER & APPLY ────────────────────────────────────────────────────────

def open_in_browser(url):
    """Open a URL in the system default browser."""
    try:
        webbrowser.open(url)
        print(f"  Opened: {url}")
    except Exception as e:
        print(f"  Could not open browser ({e}). Visit manually: {url}")


def show_application_checklist(job, profile):
    resume_path = profile.get("personal", {}).get("resume_path", "")
    print("\n" + "─" * 55)
    print("  APPLICATION CHECKLIST")
    print("─" * 55)
    print(f"  Job    : {job['title']} @ {job['company']}")
    print(f"  Source : {job['source']}  |  Salary: {job['salary']}")
    print(f"  URL    : {job['url']}")
    print()
    if resume_path:
        print(f"  [ ] Attach resume     → {resume_path}")
    else:
        print("  [ ] Attach resume     → (add 'resume_path' to profile.json)")
    print("  [ ] Paste cover letter → saved to cover_letters/ folder")
    print("  [ ] Verify name, email, phone are correct")
    print("  [ ] Double-check job requirements before submitting")
    print("─" * 55)


def linkedin_easy_apply(job, profile):
    """
    Uses Selenium to pre-fill a LinkedIn Easy Apply form.
    Requires: pip install selenium  and  ChromeDriver on PATH.
    Falls back to browser-open if Selenium is unavailable.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import NoSuchElementException, TimeoutException
    except ImportError:
        print("  Selenium not installed. Run: pip install selenium")
        print("  Falling back to regular browser open...")
        open_in_browser(job["url"])
        show_application_checklist(job, profile)
        return

    personal = profile.get("personal", {})
    print("\n  Launching Chrome for LinkedIn Easy Apply...")
    print("  You may need to log in to LinkedIn in the browser window.")

    options = webdriver.ChromeOptions()
    driver  = webdriver.Chrome(options=options)

    try:
        driver.get(job["url"])
        wait = WebDriverWait(driver, 15)

        # Click Easy Apply button
        try:
            btn = wait.until(EC.element_to_be_clickable((
                By.CSS_SELECTOR,
                ".jobs-apply-button, [data-control-name='jobdetails_topcard_inapply'], "
                ".apply-button, button[aria-label*='Easy Apply']"
            )))
            btn.click()
            time.sleep(2)
        except TimeoutException:
            print("  Easy Apply button not found — job may use an external application.")
            show_application_checklist(job, profile)
            input("  Press Enter when done to close the browser...")
            return

        # Pre-fill common form fields
        def fill(selectors, value):
            if not value:
                return
            for sel in selectors:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    el.clear()
                    el.send_keys(value)
                    return
                except NoSuchElementException:
                    pass

        fill(
            ["input[name='name']", "input[aria-label*='name' i]", "input[id*='name']"],
            personal.get("name", "")
        )
        fill(
            ["input[type='email']", "input[name='email']", "input[aria-label*='email' i]"],
            personal.get("email", "")
        )
        fill(
            ["input[type='tel']", "input[name='phone']", "input[aria-label*='phone' i]"],
            personal.get("phone", "")
        )

        print("\n  Form pre-filled where possible. Review everything carefully.")
        show_application_checklist(job, profile)
        input("\n  Make any final changes in the browser, then press Enter here to continue...")

    finally:
        input("  Press Enter to close the browser window...")
        driver.quit()


def handle_apply(job, profile):
    """Route to Selenium (LinkedIn) or plain browser open, then show checklist."""
    url          = job.get("url", "")
    is_linkedin  = "linkedin.com" in url

    if is_linkedin:
        print("\n  LinkedIn job detected.")
        use_sel = input("  Use Selenium for Easy Apply pre-fill? [y/n]: ").strip().lower()
        if use_sel == "y":
            linkedin_easy_apply(job, profile)
            return

    open_in_browser(url)
    show_application_checklist(job, profile)


# ─── DISPLAY ────────────────────────────────────────────────────────────────

def display_jobs(jobs, max_show=None):
    if not jobs:
        print("\nNo matching jobs found.")
        return
    limit = max_show or len(jobs)
    print(f"\n{'='*65}")
    print(f"  {len(jobs)} job(s) found  (sorted: newest first, ★ = keyword match)")
    print(f"{'='*65}")
    for i, job in enumerate(jobs[:limit], 1):
        stars = "★" * min(job.get("_score", 0), 5)
        print(f"\n[{i}] {job['title']}  {stars}")
        print(f"    Company  : {job['company']}")
        print(f"    Location : {job['location']}")
        print(f"    Salary   : {job['salary']}")
        print(f"    Source   : {job['source']}")
        print(f"    Posted   : {job.get('posted', '')}")
        print(f"    URL      : {job['url']}")
        if job.get("tags"):
            print(f"    Tags     : {job['tags']}")
    if max_show and len(jobs) > max_show:
        print(f"\n  ... {len(jobs) - max_show} more job(s) not shown.")


# ─── INTERACTIVE APPLY ──────────────────────────────────────────────────────

def interactive_apply(jobs, profile):
    tracker       = ApplicationTracker(TRACKER_FILE)
    applied_count = 0
    resume_path   = profile.get("personal", {}).get("resume_path", "")

    for i, job in enumerate(jobs, 1):
        print(f"\n{'='*65}")
        print(f"Job {i}/{len(jobs)}: {job['title']} @ {job['company']}")
        print(f"  Location : {job['location']}  |  Salary: {job['salary']}")
        print(f"  Source   : {job['source']}    |  Posted: {job.get('posted', '')}")
        print(f"  URL      : {job['url']}")
        print(f"{'='*65}")
        print("\n  [a] Apply (open browser + log)  [v] View cover letter")
        print("  [s] Skip                        [q] Quit")
        action = input("  Choice: ").strip().lower()

        if action == "q":
            break
        if action == "s":
            print("  Skipped.")
            continue
        if action == "v":
            print("\n--- Cover Letter Preview ---")
            print(generate_cover_letter(profile, job))
            print("---")
            action = input("\n  [a] Apply + log  [s] Skip: ").strip().lower()
            if action != "a":
                print("  Skipped.")
                continue

        if action == "a":
            cover_file = save_cover_letter(profile, job)
            print(f"\n  Cover letter saved: {cover_file}")
            handle_apply(job, profile)
            notes           = input("\n  Notes (press Enter to skip): ").strip()
            resume_included = bool(resume_path)
            tracker.log_application(job, notes=notes, resume_included=resume_included)
            applied_count += 1
            print("  Application logged.")

    print(f"\nDone. Logged {applied_count} application(s) to {TRACKER_FILE}")


# ─── DAILY DIGEST ───────────────────────────────────────────────────────────

def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_jobs(seen):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f)


def run_daily_digest(profile):
    """
    Searches all sources, keeps only jobs not seen in previous runs,
    saves a dated file (jobs_YYYY-MM-DD.txt) and updates found_jobs.json.
    """
    today = date.today().isoformat()
    print(f"\n{'='*65}")
    print(f"  Daily Job Digest — {today}")
    print(f"{'='*65}")

    prefs    = profile["job_preferences"]
    keywords = list(dict.fromkeys(
        prefs.get("keywords", []) + prefs.get("target_roles", [])
    ))
    print(f"  Keywords: {', '.join(keywords[:6])}{'...' if len(keywords) > 6 else ''}")

    seen     = load_seen_jobs()
    all_jobs = _run_all_searches(profile, keywords)
    filtered = filter_jobs(all_jobs, profile)

    new_jobs = [j for j in filtered if job_fingerprint(j) not in seen]
    for j in new_jobs:
        seen.add(job_fingerprint(j))
    save_seen_jobs(seen)

    print(f"\n  Total found: {len(filtered)}  |  New since last run: {len(new_jobs)}")

    if not new_jobs:
        print("  No new jobs found. Try again later or add more keywords to profile.json.")
        return []

    # Save found_jobs.json
    with open(FOUND_JOBS_FILE, "w") as f:
        json.dump(new_jobs, f, indent=2)

    # Write dated digest file
    digest_file = f"jobs_{today}.txt"
    with open(digest_file, "w") as f:
        f.write(f"DAILY JOB DIGEST — {today}\n")
        f.write(f"Found {len(new_jobs)} new job(s)\n")
        f.write("=" * 65 + "\n\n")
        for i, job in enumerate(new_jobs, 1):
            f.write(f"[{i}] {job['title']}\n")
            f.write(f"     Company  : {job['company']}\n")
            f.write(f"     Location : {job['location']}\n")
            f.write(f"     Salary   : {job['salary']}\n")
            f.write(f"     Source   : {job['source']}\n")
            f.write(f"     Posted   : {job.get('posted', '')}\n")
            f.write(f"     URL      : {job['url']}\n\n")

    print(f"\n  Digest saved to : {digest_file}")
    print(f"  Full JSON saved : {FOUND_JOBS_FILE}")
    display_jobs(new_jobs, max_show=10)
    return new_jobs


# ─── APPLICATION TRACKER ────────────────────────────────────────────────────

class ApplicationTracker:
    """Logs job applications to a CSV file with resume tracking."""

    FIELDS = [
        "date_applied", "title", "company", "location",
        "url", "source", "salary", "status", "resume_attached", "notes",
    ]

    def __init__(self, filepath):
        self.filepath = filepath
        if not os.path.exists(filepath):
            with open(filepath, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def log_application(self, job, status="Applied", notes="", resume_included=False):
        with open(self.filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow({
                "date_applied":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                "title":           job.get("title", ""),
                "company":         job.get("company", ""),
                "location":        job.get("location", ""),
                "url":             job.get("url", ""),
                "source":          job.get("source", ""),
                "salary":          job.get("salary", ""),
                "status":          status,
                "resume_attached": "Yes" if resume_included else "No",
                "notes":           notes,
            })


# ─── SEARCH RUNNER ──────────────────────────────────────────────────────────

def _run_all_searches(profile, keywords):
    """Run all job source searches and return the combined raw results."""
    prefs    = profile["job_preferences"]
    location = (prefs.get("locations") or ["United States"])[0]
    all_jobs = []

    sources = [
        ("Remotive",  lambda: search_jobs_remotive(keywords[:5])),
        ("JSearch",   lambda: search_jobs_jsearch(keywords[:4])),
        ("LinkedIn",  lambda: search_jobs_linkedin(keywords[:4])),
        ("Reed",      lambda: search_jobs_reed(keywords[:4])),
        ("The Muse",  lambda: search_jobs_themuse(keywords[:3])),
        ("Adzuna",    lambda: search_jobs_adzuna(keywords[:4], location)),
        ("USAJobs",   lambda: search_jobs_usajobs(keywords[:3])),
    ]

    for name, fn in sources:
        print(f"  [{name:<12}] ", end="", flush=True)
        try:
            results = fn()
            print(f"{len(results)} results")
            all_jobs.extend(results)
        except Exception as e:
            print(f"error — {e}")

    return all_jobs


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  Job Application Automation Tool")
    print("  Adewale Osinfade  |  ServiceNow & Business Analyst Roles")
    print("=" * 65)

    profile  = load_profile()
    prefs    = profile["job_preferences"]
    keywords = list(dict.fromkeys(
        prefs.get("keywords", []) + prefs.get("target_roles", [])
    ))

    print("\n  What would you like to do?")
    print("  [1] Search all sources (view results only)")
    print("  [2] Search all sources + apply interactively")
    print("  [3] Run daily digest (new jobs only, saves jobs_YYYY-MM-DD.txt)")
    print("  [4] View / apply to saved jobs from last search or digest")
    choice = input("\n  Choice: ").strip()

    if choice == "4":
        if os.path.exists(FOUND_JOBS_FILE):
            with open(FOUND_JOBS_FILE) as f:
                jobs = json.load(f)
            display_jobs(jobs)
            go = input("\n  [a] Apply interactively  [q] Quit: ").strip().lower()
            if go == "a":
                interactive_apply(jobs, profile)
        else:
            print("  No saved jobs found. Run a search first (options 1, 2, or 3).")

    elif choice == "3":
        new_jobs = run_daily_digest(profile)
        if new_jobs:
            go = input("\n  Apply to new jobs interactively? [y/n]: ").strip().lower()
            if go == "y":
                interactive_apply(new_jobs, profile)

    elif choice in ("1", "2"):
        print(f"\n  Searching {len(keywords)} keyword(s) across all sources...\n")
        all_jobs = _run_all_searches(profile, keywords)
        filtered = filter_jobs(all_jobs, profile)
        print(f"\n  Filtered to {len(filtered)} unique, relevant jobs.")

        with open(FOUND_JOBS_FILE, "w") as f:
            json.dump(filtered, f, indent=2)
        print(f"  Saved to {FOUND_JOBS_FILE}")

        display_jobs(filtered)

        if choice == "2" and filtered:
            go = input("\n  Start applying interactively? [y/n]: ").strip().lower()
            if go == "y":
                interactive_apply(filtered, profile)

    else:
        print("  Invalid choice. Please run the script again.")


if __name__ == "__main__":
    main()
