"""
Job Application Automation Tool — Adewale Osinfade
Roles: Business Analyst | ServiceNow | ITSM

HOW TO RUN:
    cd Job-Application-Automation
    python3 job_searcher.py

MENU OPTIONS:
    [1] Search all sources and display results
    [2] Search + apply interactively (Playwright auto-fill + Claude cover letters)
    [3] Daily digest — finds only NEW jobs since last run, saves jobs_YYYY-MM-DD.txt
    [4] View saved jobs from last search or digest

API KEYS REQUIRED (all free — set as environment variables):

    Reed API              →  https://www.reed.co.uk/developers/jobseeker
        export REED_API_KEY=your_key

    USAJobs               →  https://developer.usajobs.gov/
        export USAJOBS_API_KEY=your_key
        export USAJOBS_USER_AGENT=your@email.com

OPTIONAL API KEYS:
    Adzuna      →  https://developer.adzuna.com/
        export ADZUNA_APP_ID=your_id
        export ADZUNA_APP_KEY=your_key

    Claude API  →  No longer used for scoring (removed to eliminate API costs)

    SerpAPI     →  https://serpapi.com/  (Google Jobs — 100 free searches/month)
        export SERPAPI_KEY=your_key

BROWSER AUTOMATION (option [2]):
    Uses Playwright with your existing Chrome profile (so you stay logged into LinkedIn).
    Install once:  pip install playwright && playwright install chromium
    Playwright is auto-installed on first use if missing.

APPLICATIONS DATABASE:
    All logged applications are stored in applications.db (SQLite).
    Tracks: title, company, source, location, salary, status,
            match score, cover letter used, follow-up date, notes.
"""

import base64
import hashlib
import json
import os
import platform
import random
import re
import sqlite3
import subprocess
import sys
import time
import warnings
import webbrowser
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date, timedelta
from html.parser import HTMLParser

# Suppress urllib3 SSL / OpenSSL version warnings (NotOpenSSLWarning, etc.)
warnings.filterwarnings("ignore", category=Warning, module="urllib3")
warnings.filterwarnings("ignore", message=".*OpenSSL.*", category=Warning)


PROFILE_FILE    = "profile.json"
TRACKER_DB      = "applications.db"
FOUND_JOBS_FILE = "found_jobs.json"
SEEN_JOBS_FILE  = "seen_jobs.json"
EXCEL_FILE      = "jobs_export.xlsx"
EXPORT_LOG_FILE = "export_log.json"

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


def _is_recent(posted, max_days=14):
    """Return True if job was posted within max_days. No date → pass through."""
    if not posted:
        return True
    normalized = _parse_date(posted)
    if not normalized:
        return True
    try:
        posted_dt = datetime.strptime(normalized[:10], "%Y-%m-%d")
        return (datetime.now() - posted_dt).days <= max_days
    except (ValueError, TypeError):
        return True


def job_fingerprint(job):
    """Stable MD5 ID based on title + company + url for deduplication."""
    raw = f"{job['title'].lower()}|{job['company'].lower()}|{job['url']}"
    return hashlib.md5(raw.encode()).hexdigest()


def _job_dedup_key(job):
    """Normalized title+company key for cross-source deduplication."""
    title   = re.sub(r"\W+", " ", job["title"].lower()).strip()
    company = re.sub(r"\W+", " ", job["company"].lower()).strip()
    return f"{title}||{company}"


def _parse_rss_items(data):
    """Parse RSS 2.0 XML bytes; return list of {title, link, description, pubDate}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        def _t(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""
        items.append({
            "title":       _t("title"),
            "link":        _t("link"),
            "description": re.sub(r"<[^>]+>", " ", _t("description")),
            "pubDate":     _t("pubDate"),
            "author":      _t("author"),
        })
    return items


# Strict allowlist: only these states/abbreviations are accepted
_ALLOWED_STATES = frozenset({
    "maryland", "md",
    "virginia", "va",
    "washington dc", "district of columbia", "dc",
    "northern virginia", "nova",
})

# Strict allowlist: specific DC-metro / Maryland-area cities
_ALLOWED_CITIES = frozenset({
    "baltimore", "rockville", "bethesda", "silver spring", "gaithersburg",
    "bowie", "annapolis", "mclean", "tysons", "tysons corner", "reston",
    "herndon", "arlington", "alexandria", "fairfax", "chantilly", "sterling",
    "manassas", "fort belvoir", "quantico", "laurel", "college park",
    "greenbelt", "hyattsville", "fort meade",
})

# Remote / work-from-home markers — always accepted
_REMOTE_MARKERS = frozenset({
    "remote", "work from home", "wfh", "anywhere", "us only",
    "north america", "telecommute", "distributed",
})

# Bare country-level strings (no city) — treated as nationwide / remote-friendly
_BARE_COUNTRY = frozenset({
    "united states", "united states of america", "usa", "us",
})


def _is_us_or_remote(location, source=""):
    """
    Strict allowlist filter. A job passes only if its location is:
      - From USAJobs or Remotive (always US/remote by nature)
      - Unknown / empty / 'see listing' (passed through for manual review)
      - Remote, Work From Home, WFH, or similar
      - Bare "United States" or "USA" with no city specified
      - Maryland, MD, Virginia, VA, Washington DC, DC, Northern Virginia, NoVA
      - One of the specific DC-metro / Maryland-area cities listed in _ALLOWED_CITIES

    Jobs located in any other US state or foreign country are rejected.
    """
    if source in ("USAJobs", "Remotive"):
        return True
    loc = location.lower().strip()
    if not loc or loc == "see listing":
        return True                          # unknown — let user judge
    if any(m in loc for m in _REMOTE_MARKERS):
        return True
    if loc in _BARE_COUNTRY:
        return True                          # "United States" with no city
    # Split on commas, pipes, slashes; check each component
    parts = [p.strip() for p in re.split(r"[,/|]+", loc)]
    for part in parts:
        if part in _ALLOWED_STATES:
            return True
        # Check whether any allowed city name appears within the part
        # (handles "Tysons Corner, VA" → part "tysons corner")
        if any(city in part for city in _ALLOWED_CITIES):
            return True
        # Single-token state abbreviations: md, va, dc
        for tok in part.split():
            if tok in {"md", "va", "dc"}:
                return True
    return False                             # not in allowlist → reject


# Title keywords that always pass regardless of the profile target_roles list
_TITLE_ACCEPT_KEYWORDS = frozenset([
    "servicenow", "itsm", "business analyst", "business system analyst",
])


def _matches_target_role(title, profile):
    """
    Return True if the job title contains:
      - any of the hard-coded always-accept keywords (ServiceNow, ITSM,
        Business Analyst, Business System Analyst), OR
      - any substring from target_roles or titles in profile.json.
    Case-insensitive substring match throughout.
    """
    title_low = title.lower()
    if any(kw in title_low for kw in _TITLE_ACCEPT_KEYWORDS):
        return True
    prefs     = profile["job_preferences"]
    all_roles = prefs.get("target_roles", []) + prefs.get("titles", [])
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


def search_jobs_adzuna():
    """
    Adzuna API — free key from https://developer.adzuna.com/
    Set env: ADZUNA_APP_ID, ADZUNA_APP_KEY
    Searches 'business analyst' and 'ServiceNow' in Maryland, US.
    """
    app_id  = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("  Adzuna: set ADZUNA_APP_ID + ADZUNA_APP_KEY env vars to enable.")
        return []

    searches = [
        ("business analyst",          "maryland"),
        ("ServiceNow Business Analyst", "maryland"),
    ]
    found = []
    for query, loc in searches:
        try:
            url = (
                f"https://api.adzuna.com/v1/api/jobs/us/search/1"
                f"?app_id={app_id}&app_key={app_key}&results_per_page=25"
                f"&what={urllib.parse.quote(query)}&where={urllib.parse.quote(loc)}"
                f"&content-type=application/json"
            )
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
            time.sleep(1)
        except Exception as e:
            print(f"  Adzuna error ({query}): {e}")
    return found



def search_jobs_linkedin(keywords):
    """
    LinkedIn guest jobs API — public endpoint, no key required.
    Runs two passes:
      1. The caller-supplied keywords (ServiceNow, ITSM, etc.)
      2. A fixed set of BA-specific terms (Business Analyst, IT Business Analyst,
         Systems Analyst) so pure BA roles are found even when no ServiceNow
         keyword is in the list.
    Results from both passes are combined and deduplicated by URL.
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
            # Skip whitespace-only nodes so nested tags (e.g. <a> inside <h4>)
            # don't consume the capture slot before the real text arrives.
            if self._cap and data.strip():
                self._cur[self._cap] = data.strip()
                self._cap = None

        def handle_endtag(self, tag):
            if tag == "li" and self._cur.get("title") and self._cur.get("url"):
                self.jobs.append(dict(self._cur))
                self._cur = {}
                self._cap = None   # clear any dangling capture slot between cards

    def _fetch_linkedin_page(kw, location="Washington DC-Baltimore Area", start=0):
        params = urllib.parse.urlencode({
            "keywords": kw,
            "location": location,
            "start":    str(start),
            "count":    "25",
            "f_TPR":    "r2592000",   # posted within last 30 days
        })
        url    = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
        html   = _fetch(url).decode("utf-8", errors="replace")
        parser = _CardParser()
        parser.feed(html)
        return parser.jobs

    # BA-specific terms always searched, regardless of what keywords are passed
    _BA_KEYWORDS = ["Business Analyst", "IT Business Analyst", "Systems Analyst"]

    # Combine passed keywords with BA terms, deduplicated, preserving order
    all_kws   = list(dict.fromkeys(list(keywords[:4]) + _BA_KEYWORDS))
    found     = []
    seen_urls = set()

    for kw in all_kws:
        # Fetch 2 pages per keyword from the DC-Baltimore metro area so
        # most results already pass the location filter (50 raw per keyword).
        for start in (0, 25):
            try:
                for card in _fetch_linkedin_page(kw, start=start):
                    url = card.get("url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    found.append(_make_job(
                        title       = card.get("title", ""),
                        company     = card.get("company", ""),
                        location    = card.get("location", "See listing"),
                        url         = url,
                        description = "",
                        posted      = card.get("posted", ""),
                        source      = "LinkedIn",
                    ))
                time.sleep(1.5)
            except Exception as e:
                print(f"  LinkedIn error ({kw} start={start}): {e}")
                break   # don't try page 2 if page 1 failed
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
    Set env: USAJOBS_API_KEY  (your API key)
             USAJOBS_USER_AGENT  (your registered email address)
    Required headers: Authorization-Key = API key, User-Agent = email.
    Searches fixed focused terms ('Business Analyst', 'ServiceNow') that
    match government IT job titles, plus LocationName=United States.
    """
    api_key    = os.environ.get("USAJOBS_API_KEY")
    user_agent = os.environ.get("USAJOBS_USER_AGENT", "")
    if not api_key:
        print("  USAJobs: set USAJOBS_API_KEY + USAJOBS_USER_AGENT env vars (developer.usajobs.gov).")
        return []
    # USAJobs titles match government job series names — use focused terms
    usajobs_keywords = ["Business Analyst", "ServiceNow Business Analyst"]
    found = []
    for kw in usajobs_keywords:
        try:
            params = urllib.parse.urlencode({
                "Keyword":         kw,
                "LocationName":    "United States",
                "ResultsPerPage":  25,
                "RemoteIndicator": "True",
                "WhoMayApply":     "All",
            })
            url = f"https://data.usajobs.gov/api/search?{params}"
            headers = {
                "Authorization-Key": api_key,
                "User-Agent":        user_agent,
                "Host":              "data.usajobs.gov",
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


def search_jobs_remoteok():
    """
    RemoteOK public JSON API — free, no key needed.
    API: https://remoteok.com/api
    The first element of the array is metadata and is skipped.
    Only jobs tagged 'business-analyst', 'servicenow', 'itsm', or
    'systems-analyst' are returned.
    """
    _TARGET_TAGS = {
        "business-analyst", "business analyst",
        "servicenow", "itsm", "systems-analyst", "systems analyst",
        "it-analyst", "it analyst",
    }
    found = []
    try:
        data = json.loads(_fetch("https://remoteok.com/api"))
        for job in data[1:]:                        # element 0 is metadata
            tags_raw  = job.get("tags", [])
            tags_norm = {t.lower() for t in tags_raw} | {t.lower().replace(" ", "-") for t in tags_raw}
            if not (tags_norm & _TARGET_TAGS):
                continue
            sal_min = job.get("salary_min", "")
            sal_max = job.get("salary_max", "")
            sal = f"${int(sal_min):,} – ${int(sal_max):,}" if sal_min and sal_max else ""
            epoch  = job.get("date", "")
            posted = ""
            if epoch:
                try:
                    posted = datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass
            job_id = job.get("id", "")
            found.append(_make_job(
                title       = job.get("position", ""),
                company     = job.get("company", ""),
                location    = "Remote",
                url         = job.get("url") or f"https://remoteok.com/remote-jobs/{job_id}",
                description = job.get("description", ""),
                posted      = posted,
                source      = "RemoteOK",
                salary      = sal,
                tags        = ", ".join(tags_raw),
            ))
    except Exception as e:
        print(f"  RemoteOK error: {e}")
    return found


def search_jobs_weworkremotely():
    """
    We Work Remotely RSS feed — free, no key needed.
    Feed: https://weworkremotely.com/remote-jobs.rss
    Filters to titles containing Business Analyst, ServiceNow, ITSM,
    Systems Analyst, or IT Analyst keywords.
    WWR titles are usually formatted "Company: Job Title" — the company
    name is split out from the title automatically.
    """
    _TARGET_KWS = {
        "business analyst", "servicenow", "itsm",
        "systems analyst", "it analyst", "business system",
    }
    found = []
    try:
        data = _fetch("https://weworkremotely.com/remote-jobs.rss")
        for item in _parse_rss_items(data):
            raw_title = item["title"]
            if not any(kw in raw_title.lower() for kw in _TARGET_KWS):
                continue
            # "Company: Job Title" → split on first colon
            if ": " in raw_title:
                company, title = raw_title.split(": ", 1)
            else:
                company, title = "See listing", raw_title
            found.append(_make_job(
                title       = title.strip(),
                company     = company.strip(),
                location    = "Remote",
                url         = item["link"],
                description = item["description"],
                posted      = item["pubDate"],
                source      = "WeWorkRemotely",
            ))
    except Exception as e:
        print(f"  WeWorkRemotely error: {e}")
    return found


def search_jobs_dice():
    """
    Dice.com — Playwright headless scraper (JS-rendered SPA).
    Searches 'business analyst' and 'ServiceNow' in Maryland, Washington DC,
    and Virginia using the 50-mile radius + last-3-days filter.
    Applies playwright-stealth to bypass bot detection.
    No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    searches = [
        ("business+analyst",               "Maryland"),
        ("business+analyst",               "Washington%2C+DC"),
        ("business+analyst",               "Virginia"),
        ("ServiceNow+Business+Analyst",    "Maryland"),
        ("ServiceNow+Business+Analyst",    "Washington%2C+DC"),
        ("ServiceNow+Business+Analyst",    "Virginia"),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        browser, ctx = _pw_stealth_ctx(pw)
        for query, location in searches:
            url = (
                f"https://www.dice.com/jobs?q={query}"
                f"&location={location}&radius=50&radiusUnit=mi"
                f"&page=1&pageSize=20&filters.postedDate=THREE"
            )
            page = ctx.new_page()
            try:
                _pw_apply_stealth(page)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                # Wait specifically for Dice title links — up to 5 seconds
                appeared = False
                try:
                    page.wait_for_selector(
                        "[data-cy='card-title-link']",
                        timeout=5000,
                        state="attached",
                    )
                    appeared = True
                except Exception:
                    pass

                # If cards didn't appear, scroll once to trigger lazy-load and retry
                if not appeared:
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(2000)
                    try:
                        page.wait_for_selector(
                            "[data-cy='card-title-link']",
                            timeout=3000,
                            state="attached",
                        )
                    except Exception:
                        pass

                _pw_dismiss_cookies(page)
                jobs_data = []

                # Strategy 1 — data-cy card containers
                cards = page.query_selector_all(
                    "[data-cy='card'], dhi-search-card"
                )
                for card in cards:
                    title_el = card.query_selector(
                        "[data-cy='card-title-link'], a.card-title-link"
                    )
                    comp_el  = card.query_selector(
                        "[data-cy='card-company'], [data-cy='search-result-company-name']"
                    )
                    loc_el   = card.query_selector(
                        "[data-cy='card-location'], [data-cy='search-result-location']"
                    )
                    date_el  = card.query_selector(
                        "[data-cy='card-posted-date'], [data-cy='card-posted']"
                    )
                    if not title_el:
                        continue
                    title  = (title_el.inner_text() or "").strip()
                    href   = title_el.get_attribute("href") or ""
                    comp   = (comp_el.inner_text()  or "").strip() if comp_el  else ""
                    loc    = (loc_el.inner_text()   or "").strip() if loc_el   else ""
                    posted = (date_el.inner_text()  or "").strip() if date_el  else ""
                    if title and href:
                        full_url = href if href.startswith("http") else f"https://www.dice.com{href}"
                        jobs_data.append((title, comp, loc, full_url, posted))

                # Strategy 2 — grab all data-cy title links directly (flat fallback)
                if not jobs_data:
                    links = page.query_selector_all("[data-cy='card-title-link']")
                    for link in links:
                        title = (link.inner_text() or "").strip()
                        href  = link.get_attribute("href") or ""
                        if title and href:
                            full_url = href if href.startswith("http") else f"https://www.dice.com{href}"
                            jobs_data.append((title, "", "", full_url, ""))

                loc_label = location.replace("%2C+", ", ").replace("+", " ")
                for title, comp, loc, job_url, posted in jobs_data:
                    fp = hashlib.md5(f"{title}|{comp}|{job_url}".encode()).hexdigest()
                    if fp in seen_fps:
                        continue
                    seen_fps.add(fp)
                    found.append(_make_job(
                        title       = title,
                        company     = comp   or "See listing",
                        location    = loc    or loc_label,
                        url         = job_url,
                        description = "",
                        posted      = posted,
                        source      = "Dice",
                    ))

            except Exception as e:
                print(f"  Dice error ({query}/{location}): {e}")
            finally:
                page.close()
            time.sleep(1)

        browser.close()
    return found


# ─── PLAYWRIGHT JOB BOARD SCRAPERS ──────────────────────────────────────────

def search_jobs_glassdoor():
    """
    Glassdoor — Playwright headless scraper with stealth.
    Runs 5 targeted searches so results aren't limited to ~5 listings:
      1. 'business analyst servicenow' — DC metro (locId=9)
      2. 'business analyst servicenow DC' — DC metro
      3. 'business analyst' — Maryland (locId=48, state)
      4. 'ITSM analyst' — Virginia (locId=46, state)
      5. 'servicenow administrator' — DC metro
    Deduplicates across all searches.  No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    # (url, display label for fallback location)
    searches = [
        (
            "https://www.glassdoor.com/Job/jobs.htm"
            "?sc.keyword=business+analyst+servicenow"
            "&locT=M&locId=9&radius=50",
            "DC Metro"
        ),
        (
            "https://www.glassdoor.com/Job/jobs.htm"
            "?sc.keyword=business+analyst+servicenow+DC"
            "&locT=M&locId=9&radius=50",
            "DC Metro"
        ),
        (
            "https://www.glassdoor.com/Job/jobs.htm"
            "?sc.keyword=business+analyst"
            "&locT=S&locId=48&radius=50",
            "Maryland"
        ),
        (
            "https://www.glassdoor.com/Job/jobs.htm"
            "?sc.keyword=ITSM+analyst"
            "&locT=S&locId=46&radius=50",
            "Virginia"
        ),
        (
            "https://www.glassdoor.com/Job/jobs.htm"
            "?sc.keyword=servicenow+business+analyst"
            "&locT=M&locId=9&radius=50",
            "DC Metro"
        ),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        browser, ctx = _pw_stealth_ctx(pw)
        for url, loc_label in searches:
            page = ctx.new_page()
            try:
                _pw_apply_stealth(page)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _pw_dismiss_cookies(page)
                _pw_human_mouse(page)

                # Try to wait for job listings to appear
                try:
                    page.wait_for_selector(
                        "li[data-test='jobListing'], [class*='JobCard']",
                        timeout=5000,
                        state="attached",
                    )
                except Exception:
                    pass

                cards = page.query_selector_all(
                    "li[data-test='jobListing'], "
                    "[class*='JobCard_jobCardContainer'], "
                    "article[class*='JobCard'], "
                    "[class*='job-search-key']"
                )
                for card in cards:
                    title_el = card.query_selector(
                        "[data-test='job-title'], "
                        "a[class*='JobCard_seoLink'], "
                        "a[class*='jobTitle'], h3 a, h2 a"
                    )
                    comp_el  = card.query_selector(
                        "[data-test='employerName'], "
                        "[class*='EmployerProfile'], "
                        "[class*='employerName']"
                    )
                    loc_el   = card.query_selector(
                        "[data-test='location'], "
                        "[class*='jobLocation'], "
                        "[class*='JobCard_location']"
                    )
                    sal_el   = card.query_selector(
                        "[data-test='detailSalary'], "
                        "[class*='salary'], [class*='Salary']"
                    )
                    if not title_el:
                        continue
                    title  = (title_el.inner_text() or "").strip()
                    href   = title_el.get_attribute("href") or ""
                    comp   = (comp_el.inner_text()  or "").strip() if comp_el  else ""
                    loc    = (loc_el.inner_text()   or "").strip() if loc_el   else loc_label
                    salary = (sal_el.inner_text()   or "").strip() if sal_el   else ""
                    if not title:
                        continue
                    full_url = href if href.startswith("http") else f"https://www.glassdoor.com{href}"
                    fp = hashlib.md5(f"{title}|{comp}|{full_url}".encode()).hexdigest()
                    if fp in seen_fps:
                        continue
                    seen_fps.add(fp)
                    found.append(_make_job(
                        title       = title,
                        company     = comp   or "See listing",
                        location    = loc,
                        url         = full_url,
                        description = "",
                        posted      = "",
                        source      = "Glassdoor",
                        salary      = salary,
                    ))

            except Exception as e:
                print(f"  Glassdoor error ({loc_label}): {e}")
            finally:
                page.close()
            time.sleep(2)

        browser.close()
    return found


def search_jobs_ziprecruiter():
    """
    ZipRecruiter — Playwright headless scraper with stealth + human-like headers.
    Searches BA + ServiceNow Business Analyst near Washington DC / Maryland.
    No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    searches = [
        (
            "https://www.ziprecruiter.com/jobs-search"
            "?search=business+analyst+servicenow"
            "&location=Washington+DC&radius=50",
            "Washington DC"
        ),
        (
            "https://www.ziprecruiter.com/jobs-search"
            "?search=servicenow+business+analyst"
            "&location=Maryland&radius=50",
            "Maryland"
        ),
        (
            "https://www.ziprecruiter.com/jobs-search"
            "?search=business+analyst+ITSM"
            "&location=Washington+DC&radius=50",
            "Washington DC"
        ),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        browser, ctx = _pw_stealth_ctx(pw)
        for url, loc_label in searches:
            page = ctx.new_page()
            try:
                _pw_apply_stealth(page)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _pw_dismiss_cookies(page)
                _pw_human_mouse(page)

                cards = page.query_selector_all(
                    "article[data-job-id], "
                    "[class*='job_result'], "
                    "[class*='jobCard'], "
                    "div[class*='JobCard']"
                )
                for card in cards:
                    title_el = card.query_selector(
                        "h2[class*='title'], a[class*='job_title'], "
                        "h2 a, h3 a, [class*='jobTitle'] a"
                    )
                    comp_el  = card.query_selector(
                        "[class*='company'], [class*='employer'], "
                        "a[data-testid='company-name']"
                    )
                    loc_el   = card.query_selector(
                        "[class*='location'], [data-testid='location']"
                    )
                    if not title_el:
                        continue
                    title = (title_el.inner_text() or "").strip()
                    href  = title_el.get_attribute("href") or ""
                    if not href:
                        try:
                            anc  = title_el.evaluate("el => el.closest('a')?.href || ''")
                            href = anc or ""
                        except Exception:
                            pass
                    comp = (comp_el.inner_text() or "").strip() if comp_el else ""
                    loc  = (loc_el.inner_text()  or "").strip() if loc_el  else loc_label
                    if not title:
                        continue
                    full_url = href if href.startswith("http") else f"https://www.ziprecruiter.com{href}"
                    fp = hashlib.md5(f"{title}|{comp}|{full_url}".encode()).hexdigest()
                    if fp in seen_fps:
                        continue
                    seen_fps.add(fp)
                    found.append(_make_job(
                        title       = title,
                        company     = comp   or "See listing",
                        location    = loc,
                        url         = full_url,
                        description = "",
                        posted      = "",
                        source      = "ZipRecruiter",
                    ))

            except Exception as e:
                print(f"  ZipRecruiter error ({loc_label}): {e}")
            finally:
                page.close()
            time.sleep(2)

        browser.close()
    return found


def search_jobs_monster():
    """
    Monster.com — Playwright headless scraper with stealth.
    Searches BA + ServiceNow Business Analyst in DC area, posted within 14 days.
    No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    searches = [
        (
            "https://www.monster.com/jobs/search"
            "?q=business+analyst+servicenow"
            "&where=Washington__2C-DC&radius=50&tm=14",
            "Washington DC"
        ),
        (
            "https://www.monster.com/jobs/search"
            "?q=servicenow+business+analyst"
            "&where=Maryland&radius=50&tm=14",
            "Maryland"
        ),
        (
            "https://www.monster.com/jobs/search"
            "?q=business+analyst+ITSM"
            "&where=Virginia&radius=50&tm=14",
            "Virginia"
        ),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        browser, ctx = _pw_stealth_ctx(pw)
        for url, loc_label in searches:
            page = ctx.new_page()
            try:
                _pw_apply_stealth(page)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _pw_dismiss_cookies(page)
                _pw_human_mouse(page)

                cards = page.query_selector_all(
                    "[data-testid='JobCard'], "
                    ".flex-row[class*='JobResultWrapper'], "
                    "section[class*='card-content'], "
                    "[class*='job-search-card']"
                )
                for card in cards:
                    title_el = card.query_selector(
                        "h3 a, h2 a, [class*='title'] a, "
                        "[data-testid='jobTitle'], a[class*='job-title']"
                    )
                    comp_el  = card.query_selector(
                        "[class*='company'], [data-testid='company'], "
                        "span[class*='name']"
                    )
                    loc_el   = card.query_selector(
                        "[class*='location'], [data-testid='location'], "
                        "span[class*='location']"
                    )
                    date_el  = card.query_selector(
                        "[class*='date'], time, [data-testid='date']"
                    )
                    if not title_el:
                        continue
                    title  = (title_el.inner_text() or "").strip()
                    href   = title_el.get_attribute("href") or ""
                    comp   = (comp_el.inner_text()  or "").strip() if comp_el  else ""
                    loc    = (loc_el.inner_text()   or "").strip() if loc_el   else loc_label
                    posted = (date_el.inner_text()  or "").strip() if date_el  else ""
                    if not title:
                        continue
                    full_url = href if href.startswith("http") else f"https://www.monster.com{href}"
                    fp = hashlib.md5(f"{title}|{comp}|{full_url}".encode()).hexdigest()
                    if fp in seen_fps:
                        continue
                    seen_fps.add(fp)
                    found.append(_make_job(
                        title       = title,
                        company     = comp   or "See listing",
                        location    = loc,
                        url         = full_url,
                        description = "",
                        posted      = posted,
                        source      = "Monster",
                    ))

            except Exception as e:
                print(f"  Monster error ({loc_label}): {e}")
            finally:
                page.close()
            time.sleep(2)

        browser.close()
    return found


def search_jobs_careerbuilder():
    """
    CareerBuilder — Playwright headless scraper with stealth.
    Searches BA + ServiceNow Business Analyst in DC area, posted within 14 days.
    No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    searches = [
        (
            "https://www.careerbuilder.com/jobs"
            "?keywords=business+analyst+servicenow"
            "&location=Washington+DC&radius=50&posted=14",
            "Washington DC"
        ),
        (
            "https://www.careerbuilder.com/jobs"
            "?keywords=servicenow+business+analyst"
            "&location=Maryland&radius=50&posted=14",
            "Maryland"
        ),
        (
            "https://www.careerbuilder.com/jobs"
            "?keywords=business+analyst+ITSM"
            "&location=Virginia&radius=50&posted=14",
            "Virginia"
        ),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        browser, ctx = _pw_stealth_ctx(pw)
        for url, loc_label in searches:
            page = ctx.new_page()
            try:
                _pw_apply_stealth(page)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _pw_dismiss_cookies(page)
                _pw_human_mouse(page)

                cards = page.query_selector_all(
                    "[class*='data-results-content'], "
                    "li[class*='job-listing'], "
                    "[data-testid='job-card'], "
                    ".col-full.head"
                )
                for card in cards:
                    title_el = card.query_selector(
                        "a[class*='show-for-medium'], "
                        "a[data-testid='job-title'], "
                        "h2 a, h3 a, .job-title a"
                    )
                    comp_el  = card.query_selector(
                        "[class*='data-details'] span:first-child, "
                        "[class*='company'], [data-testid='company']"
                    )
                    loc_el   = card.query_selector(
                        "[class*='job-location'], [class*='location'], "
                        "[data-testid='location']"
                    )
                    sal_el   = card.query_selector(
                        "[class*='salary'], [data-testid='salary'], .job-pay"
                    )
                    if not title_el:
                        continue
                    title  = (title_el.inner_text() or "").strip()
                    href   = title_el.get_attribute("href") or ""
                    comp   = (comp_el.inner_text()  or "").strip() if comp_el  else ""
                    loc    = (loc_el.inner_text()   or "").strip() if loc_el   else loc_label
                    salary = (sal_el.inner_text()   or "").strip() if sal_el   else ""
                    if not title:
                        continue
                    full_url = href if href.startswith("http") else f"https://www.careerbuilder.com{href}"
                    fp = hashlib.md5(f"{title}|{comp}|{full_url}".encode()).hexdigest()
                    if fp in seen_fps:
                        continue
                    seen_fps.add(fp)
                    found.append(_make_job(
                        title       = title,
                        company     = comp   or "See listing",
                        location    = loc,
                        url         = full_url,
                        description = "",
                        posted      = "",
                        source      = "CareerBuilder",
                        salary      = salary,
                    ))

            except Exception as e:
                print(f"  CareerBuilder error ({loc_label}): {e}")
            finally:
                page.close()
            time.sleep(2)

        browser.close()
    return found


def search_jobs_workday():
    """
    Workday career portals for major DC-area employers (Booz Allen, Leidos, SAIC, CACI).
    Uses correct Angular data-automation-id selectors:
      - [data-automation-id='jobTitle']   for job title links
      - [data-automation-id='location']   for location
      - [data-automation-id='postedOn']   for posted date
    Waits up to 5 seconds after page load for Angular to fully render.
    Applies playwright-stealth to avoid bot detection.
    No API key required.
    """
    if not _ensure_playwright():
        return []
    _ensure_stealth()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    portals = [
        (
            "Booz Allen",
            "https://boozallen.wd1.myworkdayjobs.com/candidate/jobBoard/External_Career_Site",
            ["business analyst", "ServiceNow Business Analyst"],
        ),
        (
            "Leidos",
            "https://leidos.wd5.myworkdayjobs.com/External",
            ["business analyst", "ServiceNow Business Analyst"],
        ),
        (
            "SAIC",
            "https://jobs.saic.com",
            ["business analyst", "ServiceNow Business Analyst"],
        ),
        (
            "CACI",
            "https://caci.wd1.myworkdayjobs.com/External",
            ["business analyst", "ServiceNow Business Analyst"],
        ),
    ]
    found    = []
    seen_fps = set()

    with sync_playwright() as pw:
        for employer, base_url, keywords in portals:
            for keyword in keywords:
                browser, ctx = _pw_stealth_ctx(pw)
                page = ctx.new_page()
                try:
                    _pw_apply_stealth(page)
                    page.goto(base_url, timeout=35000, wait_until="domcontentloaded")

                    # 5-second wait for Angular to fully render
                    page.wait_for_timeout(5000)
                    _pw_dismiss_cookies(page)

                    # ── Enter search keyword ──────────────────────────────────
                    search_box = page.query_selector(
                        "[data-automation-id='searchBox'], "
                        "input[placeholder*='Search'], "
                        "input[aria-label*='Search'], "
                        "input[type='search']"
                    )
                    if search_box:
                        search_box.click()
                        search_box.fill(keyword)
                        page.keyboard.press("Enter")
                        # Wait for Angular to re-render results
                        page.wait_for_timeout(5000)

                    # ── Wait for the correct Workday job title elements ───────
                    try:
                        page.wait_for_selector(
                            "[data-automation-id='jobTitle']",
                            timeout=5000,
                            state="attached",
                        )
                    except Exception:
                        pass  # may still have results even without explicit wait

                    # ── Extract using authoritative data-automation-id attrs ──
                    title_links = page.query_selector_all(
                        "[data-automation-id='jobTitle']"
                    )
                    for link in title_links:
                        try:
                            title = (link.inner_text() or "").strip()
                            href  = link.get_attribute("href") or ""
                        except Exception:
                            continue
                        if not title:
                            continue

                        # Try to get location from sibling/parent elements
                        loc = ""
                        try:
                            container = link.evaluate_handle(
                                "el => el.closest('li') || el.parentElement"
                            )
                            if container:
                                loc_el = container.query_selector(
                                    "[data-automation-id='location'], "
                                    "[data-automation-id='locations'], "
                                    "[data-automation-id='jobPostingLocation']"
                                )
                                if loc_el:
                                    loc = (loc_el.inner_text() or "").strip()
                        except Exception:
                            pass

                        full_url = href if href.startswith("http") else f"{base_url.rstrip('/')}{href}"
                        fp = hashlib.md5(f"{title}|{employer}|{full_url}".encode()).hexdigest()
                        if fp in seen_fps:
                            continue
                        seen_fps.add(fp)
                        found.append(_make_job(
                            title       = title,
                            company     = employer,
                            location    = loc or "MD / DC / VA",
                            url         = full_url,
                            description = keyword,
                            posted      = "",
                            source      = f"Workday ({employer})",
                        ))

                except Exception as e:
                    print(f"  Workday ({employer}/{keyword}): {e}")
                finally:
                    page.close()
                    ctx.close()
                    browser.close()
                time.sleep(2)

    return found


def search_jobs_serpapi():
    """
    Google Jobs via SerpAPI — 100 free searches/month.
    pip install google-search-results  (auto-installed on first use)
    Set env: SERPAPI_KEY  (serpapi.com)
    Searches Business Analyst / ServiceNow roles in the DC-metro area.
    """
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("  SerpAPI: set SERPAPI_KEY env var (serpapi.com — 100 free/month).")
        return []

    try:
        from serpapi import GoogleSearch
    except ImportError:
        import subprocess, sys
        print("  Auto-installing google-search-results...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "google-search-results"]
            )
            from serpapi import GoogleSearch
        except Exception as e:
            print(f"  google-search-results install failed: {e}")
            return []

    searches = [
        "Business Analyst ServiceNow Maryland DC Virginia remote",
        "ITSM Business Analyst Washington DC",
    ]
    found = []
    for query in searches:
        try:
            params = {
                "engine":   "google_jobs",
                "q":        query,
                "location": "Maryland, United States",
                "api_key":  api_key,
                "chips":    "date_posted:week",   # last 7 days
            }
            results = GoogleSearch(params).get_dict()
            for job in results.get("jobs_results", []):
                ext    = job.get("detected_extensions", {})
                posted = ext.get("posted_at", "")
                salary = ext.get("salary", "")
                # Pick the first apply link available
                apply_link = ""
                for opt in job.get("apply_options", []):
                    if opt.get("link"):
                        apply_link = opt["link"]
                        break
                found.append(_make_job(
                    title       = job.get("title", ""),
                    company     = job.get("company_name", ""),
                    location    = job.get("location", ""),
                    url         = apply_link or job.get("share_link", ""),
                    description = job.get("description", ""),
                    posted      = posted,
                    source      = "Google Jobs",
                    salary      = salary,
                ))
            time.sleep(1)
        except Exception as e:
            print(f"  SerpAPI error ({query}): {e}")
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
        # Gate 0: skip jobs posted more than 14 days ago (no date → keep)
        if not _is_recent(job.get("posted", "")):
            continue

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


def save_cover_letter(profile, job, text=None):
    safe_co = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
    safe_t  = re.sub(r"[^a-zA-Z0-9_-]", "_", job["title"])
    os.makedirs("cover_letters", exist_ok=True)
    filepath = f"cover_letters/cover_{safe_co}_{safe_t}.txt"
    content  = text if text is not None else generate_cover_letter(profile, job)
    with open(filepath, "w") as f:
        f.write(content)
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


def _find_chrome_profile():
    """Return the path to the user's Chrome user-data directory, or None."""
    system = platform.system()
    if system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
        candidates = [
            os.path.join(base, "Google", "Chrome"),
            os.path.join(base, "Google", "Chrome Beta"),
            os.path.join(base, "Chromium"),
        ]
    elif system == "Linux":
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".config", "google-chrome"),
            os.path.join(home, ".config", "chromium"),
        ]
    else:  # Windows
        appdata = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(appdata, "Google", "Chrome", "User Data"),
            os.path.join(appdata, "Chromium", "User Data"),
        ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _ensure_playwright():
    """Auto-install Playwright + Chromium if not already available."""
    import subprocess, sys
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  Auto-installing Playwright...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "playwright"]
            )
        except Exception as e:
            print(f"  Install failed: {e}")
            print("  Run manually:  pip install playwright && playwright install chromium")
            return False
    # Ensure chromium binary is present (silent if already installed)
    try:
        import subprocess as _sp
        _sp.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
    except Exception:
        pass
    return True


def _pw_dismiss_cookies(page):
    """
    Click common cookie-consent / GDPR accept buttons if visible.
    Tries text-based matching first, then attribute-based selectors.
    Safe to call on any page — silently ignores misses.
    """
    accept_phrases = [
        "Accept All", "Accept all", "Accept All Cookies",
        "Accept Cookies", "Accept", "I Accept", "I Agree",
        "Agree", "Agree & Continue", "OK", "Got it", "Close",
    ]
    for phrase in accept_phrases:
        try:
            btn = page.locator(f"button:has-text('{phrase}')").first
            if btn.is_visible(timeout=600):
                btn.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass
    for sel in [
        "#onetrust-accept-btn-handler",
        "[id*='accept-all']", "[id*='acceptAll']",
        "[class*='accept-all']", "[class*='acceptAll']",
        "button[aria-label*='ccept']",
        "[data-testid*='cookie'] button",
        ".cookie-consent button", ".gdpr-consent button",
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def _pw_el_text(el, *selectors):
    """
    Try each CSS selector on a Playwright element handle; return first non-empty
    inner_text match, or '' if nothing found.
    """
    for sel in selectors:
        try:
            child = el.query_selector(sel)
            if child:
                text = (child.inner_text() or "").strip()
                if text:
                    return text
        except Exception:
            pass
    return ""


def _ensure_stealth():
    """
    Auto-install playwright-stealth if not already available.
    Returns True if the package is usable, False otherwise.
    """
    import subprocess, sys
    try:
        import playwright_stealth  # noqa: F401
        return True
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "playwright-stealth"]
            )
            return True
        except Exception:
            return False


def _pw_apply_stealth(page):
    """
    Apply playwright-stealth patches to a page before navigation.
    Removes navigator.webdriver, spoofs plugins/languages, etc.
    Silent no-op if the package is unavailable.
    """
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except Exception:
        pass


def _pw_stealth_ctx(pw, viewport_width=None):
    """
    Launch headless Chromium with a realistic stealth context:
    - Mac Chrome user-agent string
    - Random viewport 1200-1920 px wide (human range)
    - en-US Accept-Language + sensible Accept headers
    - America/New_York timezone
    Returns (browser, context).  Caller must close both.
    """
    import random
    width   = viewport_width or random.randint(1200, 1920)
    height  = random.randint(800, 1080)
    browser = pw.chromium.launch(headless=True)
    ctx     = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport         = {"width": width, "height": height},
        locale           = "en-US",
        timezone_id      = "America/New_York",
        extra_http_headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "DNT": "1",
        },
    )
    return browser, ctx


def _pw_human_mouse(page):
    """
    Simulate brief human-like random mouse movements to defeat simple bot checks.
    Uses the page viewport to keep movements inside the window.
    """
    import random
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        for _ in range(random.randint(3, 5)):
            page.mouse.move(
                random.randint(80, w - 80),
                random.randint(80, h - 80),
            )
            page.wait_for_timeout(random.randint(60, 180))
    except Exception:
        pass


def playwright_autofill_apply(job, profile, cover_letter_text=""):
    """
    Playwright-based auto-fill for LinkedIn Easy Apply, Greenhouse, Lever, etc.

    For LinkedIn jobs: opens Chrome using your existing profile so you are
    already logged in.  Falls back to a fresh Chromium window if the profile
    cannot be attached (e.g. Chrome is already running with that profile).

    For non-LinkedIn jobs: opens a fresh Chromium window and auto-fills.

    If no Easy Apply button is found on a LinkedIn page, falls back to
    opening the URL in the default browser and shows the checklist.

    Every field interaction is wrapped in try/except so a missing field never
    crashes the script.
    """
    if not _ensure_playwright():
        open_in_browser(job["url"])
        show_application_checklist(job, profile)
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"  Could not load Playwright: {e}")
        open_in_browser(job["url"])
        show_application_checklist(job, profile)
        return

    personal    = profile.get("personal", {})
    name_parts  = personal.get("name", "").split(" ", 1)
    first_name  = name_parts[0] if name_parts else ""
    last_name   = name_parts[1] if len(name_parts) > 1 else ""
    email       = personal.get("email", "")
    phone       = personal.get("phone", "")
    linkedin    = personal.get("linkedin", "")
    github      = personal.get("github", "")
    resume_path = personal.get("resume_path", "")
    full_name   = personal.get("name", "")

    url         = job.get("url", "")
    is_linkedin = "linkedin.com" in url
    chrome_profile = _find_chrome_profile() if is_linkedin else None
    filled = []

    with sync_playwright() as pw:
        context = None
        browser = None
        try:
            # ── Try to reuse the user's Chrome profile for LinkedIn ───────────
            if chrome_profile:
                print(f"  Using Chrome profile: {chrome_profile}")
                try:
                    context = pw.chromium.launch_persistent_context(
                        chrome_profile,
                        headless=False,
                        args=["--start-maximized", "--no-sandbox"],
                        ignore_default_args=["--enable-automation"],
                    )
                except Exception as e:
                    print(f"  Could not attach to Chrome profile ({e}) — launching fresh browser.")
                    context = None

            if context is None:
                browser = pw.chromium.launch(
                    headless=False,
                    args=["--start-maximized"],
                )
                context = browser.new_context(viewport=None)

            page = context.new_page()

            # ── Helpers ───────────────────────────────────────────────────────

            def _fill(selectors, value):
                if not value:
                    return False
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            el.fill("")
                            el.type(value, delay=25)
                            return True
                    except Exception:
                        pass
                return False

            def _select(selectors, option_texts):
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            for opt in option_texts:
                                try:
                                    el.select_option(label=opt)
                                    return True
                                except Exception:
                                    pass
                            # Partial-match fallback
                            all_opts = el.locator("option").all_text_contents()
                            for opt_text in all_opts:
                                if any(t.lower() in opt_text.lower() for t in option_texts):
                                    try:
                                        el.select_option(label=opt_text)
                                        return True
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                return False

            def _upload(selectors, filepath):
                if not filepath:
                    return False
                if not os.path.isabs(filepath):
                    filepath = os.path.abspath(filepath)
                if not os.path.exists(filepath):
                    print(f"  Resume not found at: {filepath}")
                    return False
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        el.set_input_files(filepath)
                        return True
                    except Exception:
                        pass
                return False

            def _highlight_submit():
                js = """(el) => {
                    el.style.border = '4px solid green';
                    el.style.backgroundColor = '#c8ffc8';
                    el.scrollIntoView({behavior: 'smooth', block: 'center'});
                }"""
                candidates = [
                    "button:has-text('Submit')",
                    "button:has-text('Review')",
                    "button:has-text('Apply')",
                    "button[type='submit']",
                    "input[type='submit']",
                    "[aria-label*='Submit' i]",
                    "[aria-label*='Review' i]",
                    "[data-easy-apply-next-button]",
                ]
                for sel in candidates:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=800):
                            page.evaluate(js, el.element_handle())
                            return True
                    except Exception:
                        pass
                return False

            # ── Navigate ──────────────────────────────────────────────────────
            print(f"  Opening: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # ── LinkedIn: detect Easy Apply ───────────────────────────────────
            if is_linkedin:
                print("  LinkedIn detected — looking for Easy Apply button...")
                ea_found = False
                for sel in [
                    "button:has-text('Easy Apply')",
                    ".jobs-apply-button",
                    "[aria-label*='Easy Apply' i]",
                    "button[data-control-name='jobdetails_topcard_inapply']",
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=4000):
                            btn.click()
                            page.wait_for_timeout(2000)
                            ea_found = True
                            print("  Easy Apply panel opened.")
                            break
                    except Exception:
                        pass
                if not ea_found:
                    print("  No Easy Apply button found — opening URL in default browser.")
                    open_in_browser(url)
                    show_application_checklist(job, profile)
                    return

            # ── First name ────────────────────────────────────────────────────
            try:
                if _fill([
                    "input[name='first_name']", "input[id*='first_name' i]",
                    "input[id*='firstName' i]", "input[aria-label*='First name' i]",
                    "input[placeholder*='First name' i]", "input[autocomplete='given-name']",
                ], first_name):
                    filled.append("First name")
            except Exception:
                pass

            # ── Last name ─────────────────────────────────────────────────────
            try:
                if _fill([
                    "input[name='last_name']", "input[id*='last_name' i]",
                    "input[id*='lastName' i]", "input[aria-label*='Last name' i]",
                    "input[placeholder*='Last name' i]", "input[autocomplete='family-name']",
                ], last_name):
                    filled.append("Last name")
            except Exception:
                pass

            # ── Full name fallback (Lever / single-name-field sites) ──────────
            if "First name" not in filled:
                try:
                    if _fill([
                        "input[name='name']", "input[aria-label*='Full name' i]",
                        "input[placeholder*='Full name' i]", "input[autocomplete='name']",
                    ], full_name):
                        filled.append("Full name")
                except Exception:
                    pass

            # ── Email ─────────────────────────────────────────────────────────
            try:
                if _fill([
                    "input[type='email']", "input[name='email']",
                    "input[id*='email' i]", "input[aria-label*='email' i]",
                    "input[placeholder*='email' i]", "input[autocomplete='email']",
                ], email):
                    filled.append("Email")
            except Exception:
                pass

            # ── Phone ─────────────────────────────────────────────────────────
            try:
                if _fill([
                    "input[type='tel']", "input[name='phone']",
                    "input[name='phone_number']", "input[id*='phone' i]",
                    "input[aria-label*='phone' i]", "input[placeholder*='phone' i]",
                    "input[autocomplete='tel']",
                ], phone):
                    filled.append("Phone")
            except Exception:
                pass

            # ── LinkedIn URL ──────────────────────────────────────────────────
            try:
                if _fill([
                    "input[name='urls[LinkedIn]']", "input[name='linkedin']",
                    "input[name='linkedin_url']", "input[id*='linkedin' i]",
                    "input[aria-label*='LinkedIn' i]", "input[placeholder*='linkedin.com' i]",
                ], linkedin):
                    filled.append("LinkedIn URL")
            except Exception:
                pass

            # ── GitHub URL ────────────────────────────────────────────────────
            try:
                if _fill([
                    "input[name='urls[GitHub]']", "input[name='github']",
                    "input[name='github_url']", "input[id*='github' i]",
                    "input[aria-label*='GitHub' i]", "input[placeholder*='github.com' i]",
                ], github):
                    filled.append("GitHub URL")
            except Exception:
                pass

            # ── Resume upload ─────────────────────────────────────────────────
            try:
                if _upload([
                    "input[type='file'][name*='resume' i]",
                    "input[type='file'][id*='resume' i]",
                    "input[type='file'][aria-label*='resume' i]",
                    "input[type='file'][accept*='pdf' i]",
                    "input[type='file']",
                ], resume_path):
                    filled.append("Resume upload")
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            # ── Cover letter ──────────────────────────────────────────────────
            if cover_letter_text:
                try:
                    if _fill([
                        "textarea[name='cover_letter']", "textarea[name='comments']",
                        "textarea[id*='cover_letter' i]", "textarea[id*='coverletter' i]",
                        "textarea[aria-label*='cover letter' i]",
                        "textarea[placeholder*='cover letter' i]",
                        "textarea[placeholder*='message' i]",
                    ], cover_letter_text):
                        filled.append("Cover letter")
                except Exception:
                    pass

            # ── Work authorization → Yes ──────────────────────────────────────
            try:
                auth_yes = [
                    "Yes", "Yes, I am authorized", "Yes, I am legally authorized",
                    "Yes, I am authorized to work in the United States",
                    "Yes - I am legally authorized to work in the United States",
                    "Authorized", "I am authorized to work in the US",
                ]
                if _select([
                    "select[name*='authorization' i]", "select[name*='authorized' i]",
                    "select[id*='work_auth' i]", "select[id*='authorization' i]",
                    "select[aria-label*='authorized to work' i]",
                    "select[aria-label*='work authorization' i]",
                ], auth_yes):
                    filled.append("Work authorization")
            except Exception:
                pass

            # ── Sponsorship → No ──────────────────────────────────────────────
            try:
                sponsor_no = [
                    "No", "No, I do not require sponsorship",
                    "No, I don't require sponsorship",
                    "I do not require sponsorship", "No sponsorship needed",
                ]
                if _select([
                    "select[name*='sponsor' i]", "select[id*='sponsor' i]",
                    "select[aria-label*='sponsor' i]", "select[aria-label*='visa' i]",
                ], sponsor_no):
                    filled.append("Sponsorship")
            except Exception:
                pass

            # ── Experience → closest to 5–7 years ────────────────────────────
            try:
                exp_options = [
                    "5", "6", "7", "5-7 years", "5+ years", "5 years",
                    "6 years", "7 years", "More than 5 years", "6-10 years",
                    "5 to 7 years", "5-10 years",
                ]
                if _select([
                    "select[name*='experience' i]", "select[id*='experience' i]",
                    "select[aria-label*='years of experience' i]",
                    "select[aria-label*='experience' i]",
                ], exp_options):
                    filled.append("Experience")
            except Exception:
                pass

            # ── Scroll to bottom ──────────────────────────────────────────────
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            except Exception:
                pass

            # ── Highlight Submit / Review button ──────────────────────────────
            found_submit = False
            try:
                found_submit = _highlight_submit()
            except Exception:
                pass

            print()
            if filled:
                print(f"  Auto-filled  : {', '.join(filled)}")
            else:
                print("  No fields auto-filled — form may require manual entry.")
            if not found_submit:
                print("  Submit button not highlighted — scroll down to find it manually.")

            print("\n" + "=" * 55)
            print("  READY — please review the form and click Submit when ready.")
            print("=" * 55)
            input("\n  Press Enter in Terminal when finished...")

        except Exception as e:
            print(f"\n  Unexpected error: {e}")
            print("  Browser is still open — please fill and submit manually.")
            try:
                input("  Press Enter to continue...")
            except Exception:
                pass
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass


def handle_apply(job, profile, cover_letter_text=""):
    """
    Open job URL with Playwright auto-fill or plain browser.
    For LinkedIn jobs, Playwright uses the existing Chrome profile (stays logged in).
    """
    url         = job.get("url", "")
    is_linkedin = "linkedin.com" in url
    print(f"\n  Job URL: {url}")
    if is_linkedin:
        print("  LinkedIn job — Playwright will use your Chrome profile (stays logged in).")
    use_pw = input("  Use Playwright auto-fill? [y/n]: ").strip().lower()
    if use_pw == "y":
        playwright_autofill_apply(job, profile, cover_letter_text=cover_letter_text)
    else:
        open_in_browser(url)
        show_application_checklist(job, profile)


# ─── DISPLAY ────────────────────────────────────────────────────────────────

def _job_category(job):
    """Classify a job as ServiceNow, Business Analyst, or Other."""
    title = job["title"].lower()
    if "servicenow" in title or "service now" in title:
        return "ServiceNow"
    if any(x in title for x in (
        "business analyst", "business system", "business systems",
        "systems analyst", "process analyst", "it analyst"
    )):
        return "Business Analyst"
    return "Other"


def display_jobs(jobs, max_show=None, profile=None, resume_words=None):
    """
    Print jobs grouped by category (ServiceNow / Business Analyst / Other).
    If resume_words is provided (set of lowercase words from the user's resume PDF),
    an ATS keyword match line is shown under each job.
    """
    if not jobs:
        print("\nNo matching jobs found.")
        return
    limit     = max_show or len(jobs)
    displayed = jobs[:limit]

    # Group jobs by category
    groups = {"ServiceNow": [], "Business Analyst": [], "Other": []}
    for job in displayed:
        groups[_job_category(job)].append(job)

    print(f"\n{'='*65}")
    print(f"  {len(jobs)} job(s) found  (sorted: newest first, ★ = keyword match)")
    print(f"{'='*65}")

    counter = 1
    for group_name in ("ServiceNow", "Business Analyst", "Other"):
        group_jobs = groups[group_name]
        if not group_jobs:
            continue
        sep = "─" * max(1, 52 - len(group_name))
        print(f"\n  ── {group_name} Jobs {sep}")
        for job in group_jobs:
            stars = "★" * min(job.get("_score", 0), 5)
            print(f"\n[{counter}] {job['title']}  {stars}")
            print(f"    Company  : {job['company']}")
            print(f"    Location : {job['location']}")
            print(f"    Salary   : {job['salary']}")
            print(f"    Source   : {job['source']}")
            print(f"    Posted   : {job.get('posted', '')}")
            print(f"    URL      : {job['url']}")
            if job.get("tags"):
                print(f"    Tags     : {job['tags']}")
            if resume_words:
                pct, missing = _ats_check(job, resume_words)
                miss_str = ", ".join(missing[:5]) if missing else "none"
                print(f"    ATS Match   : {pct}% — Missing keywords: {miss_str}")
            counter += 1

    if max_show and len(jobs) > max_show:
        print(f"\n  ... {len(jobs) - max_show} more job(s) not shown.")


# ─── INTERACTIVE APPLY ──────────────────────────────────────────────────────

def interactive_apply(jobs, profile):
    tracker       = ApplicationTracker(TRACKER_DB)
    applied_count = 0

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

        cover_text = generate_cover_letter(profile, job)

        if action == "v":
            print("\n--- Cover Letter Preview ---")
            print(cover_text)
            print("---")
            action = input("\n  [a] Apply + log  [s] Skip: ").strip().lower()
            if action != "a":
                print("  Skipped.")
                continue

        if action == "a":
            cover_file = save_cover_letter(profile, job, text=cover_text)
            print(f"\n  Cover letter saved: {cover_file}")
            handle_apply(job, profile, cover_letter_text=cover_text)

            submitted = input("\n  Did you submit this application? [y/n]: ").strip().lower()
            if submitted == "y":
                notes     = input("  Notes (press Enter to skip): ").strip()
                follow_up = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                tracker.log_application(
                    job,
                    cover_letter   = cover_text,
                    follow_up_date = follow_up,
                    notes          = notes,
                )
                update_excel_applied(job["url"])
                applied_count += 1
                print(f"  Logged → {job['title']} @ {job['company']}")
                print(f"  Follow-up reminder: {follow_up}")
            else:
                print("  Not logged — skipped.")

    print(f"\nDone. Logged {applied_count} application(s) to {TRACKER_DB}")


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
    export_to_excel(new_jobs)
    display_jobs(new_jobs, profile=profile)
    return new_jobs


# ─── APPLICATION TRACKER ────────────────────────────────────────────────────

class ApplicationTracker:
    """Logs job applications to a SQLite database (applications.db)."""

    def __init__(self, db_path=TRACKER_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_applied   TEXT,
                    title          TEXT,
                    company        TEXT,
                    source         TEXT,
                    location       TEXT,
                    url            TEXT UNIQUE,
                    salary         TEXT,
                    status         TEXT DEFAULT 'Applied',
                    match_score    INTEGER DEFAULT 0,
                    cover_letter   TEXT,
                    follow_up_date TEXT,
                    notes          TEXT
                )
            """)

    def log_application(self, job, status="Applied", notes="",
                        match_score=0, cover_letter="", follow_up_date=""):
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO applications
                    (date_applied, title, company, source, location, url, salary,
                     status, match_score, cover_letter, follow_up_date, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("source", ""),
                    job.get("location", ""),
                    job.get("url", ""),
                    job.get("salary", ""),
                    status,
                    match_score,
                    cover_letter,
                    follow_up_date,
                    notes,
                ))
            except sqlite3.Error as e:
                print(f"  DB error: {e}")

    def get_applied_urls(self):
        """Return set of URLs already in the database (for info display)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT url FROM applications").fetchall()
                return {r[0] for r in rows}
        except sqlite3.Error:
            return set()


# ─── EXCEL EXPORT ────────────────────────────────────────────────────────────

_EXCEL_HEADERS = [
    "Job Title", "Company", "Location", "Salary",
    "Source", "Date Posted", "URL", "Applied",
]
_HEADER_FILL   = None  # set lazily after openpyxl import
_GREEN_FILL    = None


def _ensure_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "openpyxl"]
            )
            return True
        except Exception:
            return False


def _open_file(path):
    """Open a file with the OS default application (cross-platform)."""
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        elif platform.system() == "Windows":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"  Could not open file automatically: {e}")


def export_to_excel(jobs, filepath=None):
    """
    Write jobs list to an Excel file with formatted headers.
    Columns: Job Title, Company, Location, Salary, Source, Date Posted, URL, Applied.
    - Header row: bold, blue background, white text, auto-filter.
    - Freezes the header row.
    - Auto-fits column widths.
    - Applied column is blank by default; green background when filled.
    - Opens the file automatically after writing.
    Returns the filepath used.
    """
    if not _ensure_openpyxl():
        print("  openpyxl unavailable — skipping Excel export.")
        return None

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    fp = filepath or EXCEL_FILE

    # Load existing or create new workbook
    if os.path.exists(fp):
        wb = openpyxl.load_workbook(fp)
        ws = wb.active
        # Remap existing URLs so we can preserve Applied dates
        existing_applied = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row) >= 8 and row[6]:
                existing_applied[row[6]] = row[7]  # url → applied date
        wb.remove(ws)
        ws = wb.create_sheet("Jobs", 0)
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Jobs"
        existing_applied = {}

    header_fill = PatternFill("solid", fgColor="1F4E79")
    green_fill  = PatternFill("solid", fgColor="C6EFCE")
    bold_white  = Font(bold=True, color="FFFFFF")
    bold_black  = Font(bold=True)
    center      = Alignment(horizontal="center", vertical="center")

    # Write headers
    ws.append(_EXCEL_HEADERS)
    for col_idx, cell in enumerate(ws[1], 1):
        cell.font      = bold_white
        cell.fill      = header_fill
        cell.alignment = center

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # Write data rows
    for job in jobs:
        url     = job.get("url", "")
        applied = existing_applied.get(url, "")
        ws.append([
            job.get("title",   ""),
            job.get("company", ""),
            job.get("location",""),
            job.get("salary",  ""),
            job.get("source",  ""),
            job.get("posted",  ""),
            url,
            applied,
        ])
        if applied:
            for cell in ws[ws.max_row]:
                cell.fill = green_fill

    # Auto-fit column widths
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)

    wb.save(fp)
    print(f"  Excel exported → {fp}")
    _open_file(fp)
    return fp


def update_excel_applied(url, date_str=None, filepath=None):
    """
    Find the row in the Excel file matching `url` and set its Applied column
    to today's date (or `date_str`).  Colors that row green.
    Silent no-op if the file doesn't exist or URL isn't found.
    """
    if not _ensure_openpyxl():
        return
    import openpyxl
    from openpyxl.styles import PatternFill

    fp      = filepath or EXCEL_FILE
    applied = date_str or date.today().isoformat()
    green   = PatternFill("solid", fgColor="C6EFCE")

    if not os.path.exists(fp):
        return
    try:
        wb = openpyxl.load_workbook(fp)
        ws = wb.active
        url_col     = _EXCEL_HEADERS.index("URL") + 1      # 1-based
        applied_col = _EXCEL_HEADERS.index("Applied") + 1
        for row in ws.iter_rows(min_row=2):
            if row[url_col - 1].value == url:
                row[applied_col - 1].value = applied
                for cell in row:
                    cell.fill = green
                break
        wb.save(fp)
    except Exception as e:
        print(f"  update_excel_applied error: {e}")


# ─── AUTO-REFRESH (every 3 days) ─────────────────────────────────────────────

def _load_export_log():
    if os.path.exists(EXPORT_LOG_FILE):
        try:
            with open(EXPORT_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_export_log(data):
    with open(EXPORT_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def check_auto_refresh():
    """
    Return True if 3+ days have passed since the last full export.
    If so, clear seen_jobs.json (force-fresh) and record today in export_log.json.
    """
    log      = _load_export_log()
    last_str = log.get("last_export", "")
    today    = date.today()

    if last_str:
        try:
            last_date = date.fromisoformat(last_str)
            if (today - last_date).days < 3:
                return False
        except ValueError:
            pass

    # 3+ days elapsed — reset seen jobs and log today
    print("\n  ⟳  Auto-refreshing job list — 3 days have passed since last full export.")
    if os.path.exists(SEEN_JOBS_FILE):
        os.remove(SEEN_JOBS_FILE)
    log["last_export"] = today.isoformat()
    _save_export_log(log)
    return True


# ─── NEW-JOB FILTERING ───────────────────────────────────────────────────────

def filter_new_jobs(jobs):
    """
    Compare jobs against seen_jobs.json (URL-based).
    Returns (new_jobs, total_count).
    Adds new job URLs to seen_jobs.json immediately.
    """
    seen = load_seen_jobs()
    new  = [j for j in jobs if j.get("url") and j["url"] not in seen]
    for j in new:
        seen.add(j["url"])
    save_seen_jobs(seen)
    return new, len(jobs)


# ─── ATS KEYWORD CHECKER ─────────────────────────────────────────────────────

_RESUME_WORDS_CACHE = None


def _load_resume_text(profile):
    """
    Extract text from the user's resume PDF (resume_path in profile.json).
    Returns a frozenset of lowercase words, or empty frozenset on failure.
    Auto-installs PyPDF2 if missing.
    """
    global _RESUME_WORDS_CACHE
    if _RESUME_WORDS_CACHE is not None:
        return _RESUME_WORDS_CACHE

    resume_path = profile.get("personal", {}).get("resume_path", "")
    if not resume_path or not os.path.exists(resume_path):
        _RESUME_WORDS_CACHE = frozenset()
        return _RESUME_WORDS_CACHE

    try:
        import PyPDF2
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "PyPDF2"]
            )
            import PyPDF2
        except Exception:
            _RESUME_WORDS_CACHE = frozenset()
            return _RESUME_WORDS_CACHE

    try:
        text = ""
        with open(resume_path, "rb") as fh:
            reader = PyPDF2.PdfReader(fh)
            for page in reader.pages:
                text += (page.extract_text() or "") + " "
        _RESUME_WORDS_CACHE = frozenset(re.findall(r"[a-z]{3,}", text.lower()))
    except Exception:
        _RESUME_WORDS_CACHE = frozenset()
    return _RESUME_WORDS_CACHE


# Target keywords checked for ATS match (lowercase)
_ATS_KEYWORDS = [
    "servicenow", "itsm", "agile", "sql", "jira", "itil",
    "business analyst", "requirements", "stakeholder", "scrum",
    "flow designer", "javascript", "api", "integration",
    "salesforce", "project management", "change management",
    "incident management", "service catalog", "process improvement",
]


def _ats_check(job, resume_words):
    """
    Compare job title + description against resume_words (frozenset of words).
    Returns (match_pct: int, missing: list[str]).
    Uses Python string matching only — no API calls.
    """
    job_text  = f"{job.get('title','')} {job.get('description','')}".lower()
    present   = [kw for kw in _ATS_KEYWORDS if kw in job_text and kw in resume_words]
    missing   = [kw for kw in _ATS_KEYWORDS if kw in job_text and kw not in resume_words]
    total     = len([kw for kw in _ATS_KEYWORDS if kw in job_text]) or 1
    pct       = round(len(present) / total * 100)
    return pct, missing


# ─── LINKEDIN EASY APPLY BATCH MODE ──────────────────────────────────────────

def batch_linkedin_apply(profile):
    """
    Option [5] — Batch apply to all LinkedIn Easy Apply jobs in found_jobs.json.
    For each LinkedIn job:
      - Opens the job page with Playwright using the user's Chrome profile.
      - Detects Easy Apply button (aria-label contains 'Easy Apply').
      - Clicks Easy Apply, auto-fills name/email/phone from profile.
      - Highlights the Submit/Next button and waits for Enter keypress.
      - Logs to SQLite + updates Applied column in Excel if submitted.
      - Moves to the next job automatically without prompting.
    """
    if not os.path.exists(FOUND_JOBS_FILE):
        print("  No saved jobs found. Run a search first.")
        return

    with open(FOUND_JOBS_FILE) as f:
        all_jobs = json.load(f)

    linkedin_jobs = [j for j in all_jobs if "linkedin.com" in j.get("url", "")]
    if not linkedin_jobs:
        print("  No LinkedIn jobs in found_jobs.json.")
        return

    print(f"\n  Found {len(linkedin_jobs)} LinkedIn job(s). Starting batch Easy Apply...")
    if not _ensure_playwright():
        return
    _ensure_stealth()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return

    personal  = profile.get("personal", {})
    full_name = personal.get("name", "")
    email     = personal.get("email", "")
    phone     = personal.get("phone", "")
    tracker   = ApplicationTracker(TRACKER_DB)
    applied   = 0

    chrome_profile = _find_chrome_profile()

    with sync_playwright() as pw:
        if chrome_profile and os.path.isdir(chrome_profile):
            browser = pw.chromium.launch_persistent_context(
                chrome_profile,
                headless        = False,
                channel         = "chrome",
                args            = ["--start-maximized"],
                ignore_default_args = ["--enable-automation"],
            )
            ctx  = None
            page = browser.new_page()
        else:
            browser, ctx = _pw_stealth_ctx(pw)
            page = ctx.new_page()
            _pw_apply_stealth(page)

        for i, job in enumerate(linkedin_jobs, 1):
            url = job["url"]
            print(f"\n  [{i}/{len(linkedin_jobs)}] {job['title']} @ {job['company']}")
            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                # Look for Easy Apply button
                easy_btn = page.query_selector(
                    "button[aria-label*='Easy Apply'], "
                    "button.jobs-apply-button[aria-label*='Easy Apply']"
                )
                if not easy_btn:
                    print(f"    No Easy Apply button — skipping.")
                    continue

                easy_btn.click()
                page.wait_for_timeout(1500)

                # Auto-fill name / email / phone
                for sel, val in [
                    ("input[id*='name'], input[name*='name']",   full_name),
                    ("input[type='email']",                      email),
                    ("input[type='tel'], input[name*='phone']",  phone),
                ]:
                    try:
                        el = page.query_selector(sel)
                        if el and val:
                            el.fill(val)
                    except Exception:
                        pass

                # Highlight Submit / Next button
                submit_btn = page.query_selector(
                    "button[aria-label*='Submit'], button[aria-label*='submit'], "
                    "button[aria-label*='Next'], footer button[data-easy-apply-next-button]"
                )
                if submit_btn:
                    page.evaluate(
                        "el => el.style.cssText = 'outline: 4px solid red !important; "
                        "background: yellow !important;'",
                        submit_btn,
                    )

                print(f"    Press ENTER to submit, or type 's' + ENTER to skip: ", end="", flush=True)
                user_input = input().strip().lower()
                if user_input == "s":
                    print("    Skipped.")
                    continue

                if submit_btn:
                    submit_btn.click()
                    page.wait_for_timeout(1500)

                tracker.log_application(job)
                update_excel_applied(url)
                applied += 1
                print(f"    Logged ✓")

            except Exception as e:
                print(f"    Error: {e}")

        page.close()
        if ctx:
            ctx.close()
        browser.close()

    print(f"\n  Batch complete. Submitted {applied} application(s).")


# ─── SEARCH RUNNER ──────────────────────────────────────────────────────────

def _run_all_searches(profile, keywords):
    """Run all job source searches and return the combined raw results."""
    all_jobs = []

    # ── Standard API / RSS sources ───────────────────────────────────────────
    api_sources = [
        ("Remotive",        lambda: search_jobs_remotive(keywords[:5])),
        ("RemoteOK",        lambda: search_jobs_remoteok()),
        ("WeWorkRemotely",  lambda: search_jobs_weworkremotely()),
        ("LinkedIn",        lambda: search_jobs_linkedin(keywords[:4])),
        ("Reed",            lambda: search_jobs_reed(keywords[:4])),
        ("The Muse",        lambda: search_jobs_themuse(keywords[:3])),
        ("Adzuna",          lambda: search_jobs_adzuna()),
        ("USAJobs",         lambda: search_jobs_usajobs(keywords[:3])),
        ("Google Jobs",     lambda: search_jobs_serpapi()),
    ]

    # ── Playwright JS-rendered sources ───────────────────────────────────────
    pw_sources = [
        ("Dice (DC/VA/MD)", lambda: search_jobs_dice()),
        ("Glassdoor",       lambda: search_jobs_glassdoor()),
        ("ZipRecruiter",    lambda: search_jobs_ziprecruiter()),
        ("Monster",         lambda: search_jobs_monster()),
        ("CareerBuilder",   lambda: search_jobs_careerbuilder()),
        ("Workday Portals", lambda: search_jobs_workday()),
    ]

    for name, fn in api_sources + pw_sources:
        print(f"  [{name:<16}] ", end="", flush=True)
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

    # Load resume text once for ATS matching (silent if resume not set)
    resume_words = _load_resume_text(profile)

    # Check for auto-refresh (clears seen_jobs.json if 3+ days elapsed)
    auto_refreshed = check_auto_refresh()

    print("\n  What would you like to do?")
    print("  [1] Search all sources (view new jobs only)")
    print("  [2] Search all sources + apply interactively")
    print("  [3] Run daily digest (new jobs only, saves dated .txt)")
    print("  [4] View / apply to saved jobs from last search")
    print("  [5] Batch LinkedIn Easy Apply (all queued LinkedIn jobs)")
    choice = input("\n  Choice: ").strip()

    if choice == "5":
        batch_linkedin_apply(profile)
        return

    if choice == "4":
        if os.path.exists(FOUND_JOBS_FILE):
            with open(FOUND_JOBS_FILE) as f:
                jobs = json.load(f)
            display_jobs(jobs, profile=profile, resume_words=resume_words or None)
            go = input("\n  [a] Apply interactively  [q] Quit: ").strip().lower()
            if go == "a":
                interactive_apply(jobs, profile)
        else:
            print("  No saved jobs found. Run a search first (options 1, 2, or 3).")
        return

    if choice == "3":
        new_jobs = run_daily_digest(profile)
        if new_jobs:
            go = input("\n  Apply to new jobs interactively? [y/n]: ").strip().lower()
            if go == "y":
                interactive_apply(new_jobs, profile)
        return

    if choice in ("1", "2"):
        print(f"\n  Searching {len(keywords)} keyword(s) across all sources...\n")
        all_jobs = _run_all_searches(profile, keywords)
        filtered = filter_jobs(all_jobs, profile)

        # ── New-job filtering ─────────────────────────────────────────────────
        new_jobs, total = filter_new_jobs(filtered)
        print(f"\n  Found {total} total jobs — {len(new_jobs)} are new since last run.")

        if not new_jobs:
            print("  No new jobs since last run.  Use [4] to review saved jobs.")
            return

        # Save new jobs to found_jobs.json for option [4] / batch apply
        with open(FOUND_JOBS_FILE, "w") as f:
            json.dump(new_jobs, f, indent=2)

        # ── Excel export ──────────────────────────────────────────────────────
        if auto_refreshed:
            # Dated file exported to ~/Documents on 3-day refresh
            dated_name = f"jobs_export_{date.today().isoformat()}.xlsx"
            docs_dir   = os.path.expanduser("~/Documents")
            dated_path = os.path.join(docs_dir, dated_name)
            export_to_excel(new_jobs, filepath=dated_path)
        export_to_excel(new_jobs)   # always write/refresh jobs_export.xlsx locally

        # ── Display ───────────────────────────────────────────────────────────
        display_jobs(new_jobs, profile=profile,
                     resume_words=resume_words or None)

        if choice == "2" and new_jobs:
            go = input("\n  Start applying interactively? [y/n]: ").strip().lower()
            if go == "y":
                interactive_apply(new_jobs, profile)
        return

    print("  Invalid choice. Please run the script again.")


if __name__ == "__main__":
    main()
