"""
Job Application Automation Tool
Searches for ServiceNow/ITSM jobs across multiple platforms and tracks applications.
"""

import json
import csv
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime


PROFILE_FILE = "profile.json"
TRACKER_FILE = "applications.csv"
FOUND_JOBS_FILE = "found_jobs.json"


def load_profile():
    with open(PROFILE_FILE, "r") as f:
        return json.load(f)


def save_found_jobs(jobs):
    with open(FOUND_JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"Saved {len(jobs)} jobs to {FOUND_JOBS_FILE}")


def search_jobs_remotive(keywords, location="Remote"):
    """Search for jobs using Remotive public API (no key required)."""
    found = []
    for keyword in keywords:
        try:
            encoded = urllib.parse.quote(keyword)
            url = f"https://remotive.com/api/remote-jobs?search={encoded}&limit=20"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                jobs = data.get("jobs", [])
                for job in jobs:
                    found.append({
                        "title": job.get("title", ""),
                        "company": job.get("company_name", ""),
                        "location": job.get("candidate_required_location", "Remote"),
                        "url": job.get("url", ""),
                        "description": job.get("description", "")[:500],
                        "posted": job.get("publication_date", ""),
                        "source": "Remotive",
                        "salary": job.get("salary", "Not listed"),
                        "tags": ", ".join(job.get("tags", []))
                    })
            time.sleep(1)
        except Exception as e:
            print(f"  Remotive search error for '{keyword}': {e}")
    return found


def search_jobs_adzuna(keywords, location, app_id=None, app_key=None):
    """
    Search Adzuna API. Requires free API credentials from:
    https://developer.adzuna.com/
    Set ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables.
    """
    app_id = app_id or os.environ.get("ADZUNA_APP_ID")
    app_key = app_key or os.environ.get("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        print("  Adzuna: Set ADZUNA_APP_ID and ADZUNA_APP_KEY env vars for Adzuna search.")
        return []

    found = []
    query = " ".join(keywords[:3])
    encoded_query = urllib.parse.quote(query)
    encoded_location = urllib.parse.quote(location)
    url = (
        f"https://api.adzuna.com/v1/api/jobs/us/search/1"
        f"?app_id={app_id}&app_key={app_key}"
        f"&results_per_page=20&what={encoded_query}&where={encoded_location}"
        f"&content-type=application/json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for job in data.get("results", []):
                found.append({
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("display_name", ""),
                    "location": job.get("location", {}).get("display_name", ""),
                    "url": job.get("redirect_url", ""),
                    "description": job.get("description", "")[:500],
                    "posted": job.get("created", ""),
                    "source": "Adzuna",
                    "salary": f"{job.get('salary_min', '')} - {job.get('salary_max', '')}",
                    "tags": ""
                })
    except Exception as e:
        print(f"  Adzuna search error: {e}")
    return found


def filter_jobs(jobs, profile):
    """Filter out jobs matching excluded keywords and deduplicate."""
    prefs = profile["job_preferences"]
    exclude = [kw.lower() for kw in prefs.get("exclude_keywords", [])]
    seen_urls = set()
    filtered = []
    for job in jobs:
        url = job.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        combined = f"{job['title']} {job['description']}".lower()
        if any(ex in combined for ex in exclude):
            continue
        filtered.append(job)
    return filtered


def generate_cover_letter(profile, job):
    """Fill in the cover letter template with job details."""
    template = profile["personal"].get("cover_letter_template") or profile.get("cover_letter_template", "")
    letter = template.replace("{job_title}", job.get("title", "this role"))
    letter = letter.replace("{company}", job.get("company", "your company"))
    return letter


def display_jobs(jobs):
    """Print job listings in a readable format."""
    if not jobs:
        print("\nNo matching jobs found.")
        return
    print(f"\n{'='*60}")
    print(f"  Found {len(jobs)} matching job(s)")
    print(f"{'='*60}")
    for i, job in enumerate(jobs, 1):
        print(f"\n[{i}] {job['title']}")
        print(f"    Company  : {job['company']}")
        print(f"    Location : {job['location']}")
        print(f"    Salary   : {job['salary']}")
        print(f"    Source   : {job['source']}")
        print(f"    Posted   : {job['posted']}")
        print(f"    URL      : {job['url']}")
        if job.get("tags"):
            print(f"    Tags     : {job['tags']}")


def interactive_apply(jobs, profile):
    """
    Interactively walk through found jobs, generate cover letters,
    and log applications to the tracker CSV.
    """
    tracker = ApplicationTracker(TRACKER_FILE)
    applied_count = 0

    for i, job in enumerate(jobs, 1):
        print(f"\n{'='*60}")
        print(f"Job {i}/{len(jobs)}: {job['title']} at {job['company']}")
        print(f"URL: {job['url']}")
        print(f"Location: {job['location']} | Salary: {job['salary']}")
        print(f"{'='*60}")

        action = input("\nOptions: [a] Apply + log | [s] Skip | [v] View cover letter | [q] Quit\nChoice: ").strip().lower()

        if action == "q":
            break
        elif action == "s":
            print("Skipped.")
            continue
        elif action == "v":
            print("\n--- Cover Letter Preview ---")
            print(generate_cover_letter(profile, job))
            action = input("\n[a] Apply + log | [s] Skip\nChoice: ").strip().lower()
            if action != "a":
                continue

        if action == "a":
            cover = generate_cover_letter(profile, job)
            # Save cover letter to file
            safe_company = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
            safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", job["title"])
            cover_filename = f"cover_letters/cover_{safe_company}_{safe_title}.txt"
            os.makedirs("cover_letters", exist_ok=True)
            with open(cover_filename, "w") as f:
                f.write(cover)
            print(f"\nCover letter saved to: {cover_filename}")
            print(f"Open the URL to apply: {job['url']}")

            notes = input("Notes (press Enter to skip): ").strip()
            tracker.log_application(job, notes=notes)
            applied_count += 1
            print("Application logged!")

    print(f"\nDone! Logged {applied_count} application(s) to {TRACKER_FILE}")


class ApplicationTracker:
    """Tracks job applications in a CSV file."""

    FIELDS = [
        "date_applied", "title", "company", "location",
        "url", "source", "salary", "status", "notes"
    ]

    def __init__(self, filepath):
        self.filepath = filepath
        if not os.path.exists(filepath):
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()

    def log_application(self, job, status="Applied", notes=""):
        with open(self.filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow({
                "date_applied": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "url": job.get("url", ""),
                "source": job.get("source", ""),
                "salary": job.get("salary", ""),
                "status": status,
                "notes": notes
            })

    def view_applications(self):
        if not os.path.exists(self.filepath):
            print("No applications tracked yet.")
            return
        with open(self.filepath, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            print("No applications tracked yet.")
            return
        print(f"\n{'='*60}")
        print(f"  Application Tracker ({len(rows)} total)")
        print(f"{'='*60}")
        for row in rows:
            print(f"\n  {row['date_applied']} | {row['title']} @ {row['company']}")
            print(f"  Status: {row['status']} | {row['url']}")
            if row.get("notes"):
                print(f"  Notes: {row['notes']}")


def main():
    print("=" * 60)
    print("  Job Application Automation Tool")
    print("  Tailored for Adewale Osinfade | ServiceNow & ITSM Jobs")
    print("=" * 60)

    profile = load_profile()
    prefs = profile["job_preferences"]

    print("\nWhat would you like to do?")
    print("  [1] Search for jobs")
    print("  [2] Search + apply interactively")
    print("  [3] View tracked applications")
    print("  [4] View saved jobs from last search")
    choice = input("\nChoice: ").strip()

    if choice == "3":
        tracker = ApplicationTracker(TRACKER_FILE)
        tracker.view_applications()
        return

    if choice == "4":
        if os.path.exists(FOUND_JOBS_FILE):
            with open(FOUND_JOBS_FILE) as f:
                jobs = json.load(f)
            display_jobs(jobs)
            if choice == "2":
                interactive_apply(jobs, profile)
        else:
            print("No saved jobs found. Run a search first.")
        return

    # Search phase
    keywords = prefs.get("keywords", [])
    locations = prefs.get("locations", ["Remote"])
    print(f"\nSearching for jobs with keywords: {', '.join(keywords[:4])}...")

    all_jobs = []

    # Remotive (free, no API key needed - great for remote jobs)
    print("\n[Remotive] Searching remote jobs...")
    remotive_jobs = search_jobs_remotive(keywords[:4])
    print(f"  Found {len(remotive_jobs)} results.")
    all_jobs.extend(remotive_jobs)

    # Adzuna (free API key needed)
    print("\n[Adzuna] Searching jobs...")
    adzuna_jobs = search_jobs_adzuna(keywords[:3], locations[0])
    print(f"  Found {len(adzuna_jobs)} results.")
    all_jobs.extend(adzuna_jobs)

    # Filter
    filtered = filter_jobs(all_jobs, profile)
    print(f"\nFiltered to {len(filtered)} unique, relevant jobs.")

    save_found_jobs(filtered)
    display_jobs(filtered)

    if choice == "2" and filtered:
        proceed = input("\nReady to apply interactively? [y/n]: ").strip().lower()
        if proceed == "y":
            interactive_apply(filtered, profile)


if __name__ == "__main__":
    main()
