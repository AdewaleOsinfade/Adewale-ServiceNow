"""
Application Tracker Viewer
View and update the status of your logged job applications.
"""

import csv
import os
import sys
from datetime import datetime

TRACKER_FILE = "applications.csv"
FIELDS = ["date_applied", "title", "company", "location", "url", "source", "salary", "status", "notes"]
STATUSES = ["Applied", "Phone Screen", "Interview Scheduled", "Offer", "Rejected", "Withdrawn"]


def load_applications():
    if not os.path.exists(TRACKER_FILE):
        return []
    with open(TRACKER_FILE, "r") as f:
        return list(csv.DictReader(f))


def save_applications(rows):
    with open(TRACKER_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def display_all(rows):
    if not rows:
        print("No applications tracked yet.")
        return
    print(f"\n{'='*70}")
    print(f"  Job Application Tracker — {len(rows)} Application(s)")
    print(f"{'='*70}")
    for i, row in enumerate(rows, 1):
        print(f"\n[{i}] {row['title']} @ {row['company']}")
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
        print("No applications to summarize.")
        return
    status_counts = Counter(row["status"] for row in rows)
    print(f"\n{'='*40}")
    print("  Application Summary")
    print(f"{'='*40}")
    print(f"  Total Applications : {len(rows)}")
    for status, count in sorted(status_counts.items()):
        print(f"  {status:<22}: {count}")


def update_status(rows):
    display_all(rows)
    if not rows:
        return rows
    try:
        idx = int(input("\nEnter application number to update: ").strip()) - 1
        if idx < 0 or idx >= len(rows):
            print("Invalid selection.")
            return rows
    except ValueError:
        print("Invalid input.")
        return rows

    print(f"\nUpdating: {rows[idx]['title']} @ {rows[idx]['company']}")
    print("Available statuses:")
    for i, s in enumerate(STATUSES, 1):
        print(f"  [{i}] {s}")
    try:
        status_idx = int(input("Choose status number: ").strip()) - 1
        if 0 <= status_idx < len(STATUSES):
            rows[idx]["status"] = STATUSES[status_idx]
            notes = input("Add/update notes (press Enter to keep existing): ").strip()
            if notes:
                rows[idx]["notes"] = notes
            save_applications(rows)
            print(f"Updated status to: {rows[idx]['status']}")
        else:
            print("Invalid status.")
    except ValueError:
        print("Invalid input.")
    return rows


def filter_by_status(rows):
    print("\nFilter by status:")
    for i, s in enumerate(STATUSES, 1):
        print(f"  [{i}] {s}")
    try:
        choice = int(input("Choose: ").strip()) - 1
        if 0 <= choice < len(STATUSES):
            filtered = [r for r in rows if r["status"] == STATUSES[choice]]
            display_all(filtered)
            print(f"\n{len(filtered)} application(s) with status '{STATUSES[choice]}'")
        else:
            print("Invalid choice.")
    except ValueError:
        print("Invalid input.")


def export_summary_report(rows):
    if not rows:
        print("No applications to export.")
        return
    report_file = f"application_report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(report_file, "w") as f:
        f.write("JOB APPLICATION SUMMARY REPORT\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total Applications: {len(rows)}\n\n")
        from collections import Counter
        status_counts = Counter(row["status"] for row in rows)
        f.write("Status Breakdown:\n")
        for status, count in sorted(status_counts.items()):
            f.write(f"  {status}: {count}\n")
        f.write("\n" + "=" * 60 + "\n\n")
        f.write("All Applications:\n\n")
        for i, row in enumerate(rows, 1):
            f.write(f"[{i}] {row['title']} @ {row['company']}\n")
            f.write(f"     Date: {row['date_applied']} | Status: {row['status']}\n")
            f.write(f"     URL: {row['url']}\n")
            if row.get("notes"):
                f.write(f"     Notes: {row['notes']}\n")
            f.write("\n")
    print(f"Report exported to: {report_file}")


def main():
    print("=" * 60)
    print("  Application Tracker Viewer")
    print("=" * 60)

    rows = load_applications()

    while True:
        print("\nOptions:")
        print("  [1] View all applications")
        print("  [2] View summary")
        print("  [3] Update application status")
        print("  [4] Filter by status")
        print("  [5] Export report")
        print("  [q] Quit")
        choice = input("\nChoice: ").strip().lower()

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
            print("Invalid choice.")


if __name__ == "__main__":
    main()
