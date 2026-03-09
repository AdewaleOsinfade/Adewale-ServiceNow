"""
Application Tracker Viewer — Adewale Osinfade

HOW TO RUN:
    cd Job-Application-Automation
    python tracker_viewer.py

OPTIONS:
    [1] View all logged applications
    [2] View summary (count by status)
    [3] Update an application's status
    [4] Filter applications by status
    [5] Export a full text report
    [q] Quit

STATUSES: Applied → Phone Screen → Interview Scheduled → Offer / Rejected / Withdrawn
"""

import csv
import os
from datetime import datetime

TRACKER_FILE = "applications.csv"

FIELDS = [
    "date_applied", "title", "company", "location",
    "url", "source", "salary", "status", "resume_attached", "notes",
]

STATUSES = [
    "Applied",
    "Phone Screen",
    "Interview Scheduled",
    "Offer",
    "Rejected",
    "Withdrawn",
]


# ─── DATA ───────────────────────────────────────────────────────────────────

def load_applications():
    if not os.path.exists(TRACKER_FILE):
        return []
    with open(TRACKER_FILE, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows   = []
        for row in reader:
            # Backward compat: fill in missing columns for older CSV files
            for field in FIELDS:
                if field not in row:
                    row[field] = ""
            rows.append(row)
    return rows


def save_applications(rows):
    with open(TRACKER_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# ─── DISPLAY ────────────────────────────────────────────────────────────────

def display_all(rows):
    if not rows:
        print("\n  No applications tracked yet.")
        return
    print(f"\n{'='*70}")
    print(f"  Job Application Tracker — {len(rows)} Application(s)")
    print(f"{'='*70}")
    for i, row in enumerate(rows, 1):
        resume_flag = "  [resume attached]" if row.get("resume_attached") == "Yes" else ""
        print(f"\n[{i}] {row['title']} @ {row['company']}{resume_flag}")
        print(f"     Date     : {row['date_applied']}")
        print(f"     Status   : {row['status']}")
        print(f"     Location : {row['location']}")
        print(f"     Salary   : {row['salary']}")
        print(f"     Source   : {row['source']}")
        print(f"     URL      : {row['url']}")
        if row.get("notes"):
            print(f"     Notes    : {row['notes']}")


def display_summary(rows):
    from collections import Counter
    if not rows:
        print("\n  No applications to summarize.")
        return
    status_counts  = Counter(row["status"] for row in rows)
    resume_yes     = sum(1 for r in rows if r.get("resume_attached") == "Yes")
    resume_no      = len(rows) - resume_yes

    print(f"\n{'='*45}")
    print("  Application Summary")
    print(f"{'='*45}")
    print(f"  Total Applications : {len(rows)}")
    print(f"  Resume Attached    : {resume_yes} Yes / {resume_no} No")
    print()
    print("  By Status:")
    for status in STATUSES:
        count = status_counts.get(status, 0)
        bar   = "█" * count
        print(f"    {status:<24} {count:>3}  {bar}")

    # Sources breakdown
    from collections import Counter as C
    source_counts = C(row["source"] for row in rows)
    print()
    print("  By Source:")
    for src, cnt in source_counts.most_common():
        print(f"    {src:<24} {cnt:>3}")
    print(f"{'='*45}")


# ─── UPDATE ─────────────────────────────────────────────────────────────────

def update_status(rows):
    display_all(rows)
    if not rows:
        return rows
    try:
        idx = int(input("\n  Enter application number to update: ").strip()) - 1
        if not (0 <= idx < len(rows)):
            print("  Invalid selection.")
            return rows
    except ValueError:
        print("  Invalid input.")
        return rows

    row = rows[idx]
    print(f"\n  Updating: {row['title']} @ {row['company']}")
    print(f"  Current status: {row['status']}")
    print("\n  Available statuses:")
    for i, s in enumerate(STATUSES, 1):
        print(f"    [{i}] {s}")

    try:
        s_idx = int(input("  Choose status number: ").strip()) - 1
        if 0 <= s_idx < len(STATUSES):
            row["status"] = STATUSES[s_idx]
            notes = input("  Add/update notes (Enter to keep existing): ").strip()
            if notes:
                row["notes"] = notes
            # Update resume_attached flag manually if needed
            resume = input("  Mark resume as attached? [y/n/keep]: ").strip().lower()
            if resume == "y":
                row["resume_attached"] = "Yes"
            elif resume == "n":
                row["resume_attached"] = "No"
            save_applications(rows)
            print(f"  Updated to: {row['status']}")
        else:
            print("  Invalid status number.")
    except ValueError:
        print("  Invalid input.")
    return rows


# ─── FILTER ─────────────────────────────────────────────────────────────────

def filter_by_status(rows):
    print("\n  Filter by status:")
    for i, s in enumerate(STATUSES, 1):
        print(f"    [{i}] {s}")
    try:
        choice = int(input("  Choose: ").strip()) - 1
        if 0 <= choice < len(STATUSES):
            filtered = [r for r in rows if r["status"] == STATUSES[choice]]
            display_all(filtered)
            print(f"\n  {len(filtered)} application(s) with status '{STATUSES[choice]}'")
        else:
            print("  Invalid choice.")
    except ValueError:
        print("  Invalid input.")


# ─── EXPORT ─────────────────────────────────────────────────────────────────

def export_summary_report(rows):
    if not rows:
        print("  No applications to export.")
        return
    from collections import Counter
    report_file = f"application_report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

    status_counts = Counter(row["status"] for row in rows)
    source_counts = Counter(row["source"] for row in rows)
    resume_yes    = sum(1 for r in rows if r.get("resume_attached") == "Yes")

    with open(report_file, "w") as f:
        f.write("JOB APPLICATION SUMMARY REPORT\n")
        f.write(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("=" * 65 + "\n\n")

        f.write(f"Total Applications : {len(rows)}\n")
        f.write(f"Resume Attached    : {resume_yes} / {len(rows)}\n\n")

        f.write("Status Breakdown:\n")
        for status in STATUSES:
            f.write(f"  {status:<24}: {status_counts.get(status, 0)}\n")

        f.write("\nSource Breakdown:\n")
        for src, cnt in source_counts.most_common():
            f.write(f"  {src:<24}: {cnt}\n")

        f.write("\n" + "=" * 65 + "\n\n")
        f.write("All Applications:\n\n")
        for i, row in enumerate(rows, 1):
            f.write(f"[{i}] {row['title']} @ {row['company']}\n")
            f.write(f"     Date   : {row['date_applied']}  Status : {row['status']}\n")
            f.write(f"     Resume : {row.get('resume_attached', 'Unknown')}\n")
            f.write(f"     URL    : {row['url']}\n")
            if row.get("notes"):
                f.write(f"     Notes  : {row['notes']}\n")
            f.write("\n")

    print(f"  Report exported to: {report_file}")


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  Application Tracker Viewer — Adewale Osinfade")
    print("=" * 65)

    rows = load_applications()

    while True:
        print(f"\n  Applications on file: {len(rows)}")
        print("  [1] View all applications")
        print("  [2] View summary (by status & source)")
        print("  [3] Update application status")
        print("  [4] Filter by status")
        print("  [5] Export full report")
        print("  [q] Quit")
        choice = input("\n  Choice: ").strip().lower()

        if choice == "1":
            display_all(rows)
        elif choice == "2":
            display_summary(rows)
        elif choice == "3":
            rows = update_status(rows)
        elif choice == "4":
            filter_by_status(rows)
        elif choice == "5":
            export_summary_report(rows)
        elif choice == "q":
            break
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
