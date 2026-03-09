#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# run_digest.sh — Daily job digest runner for Adewale Osinfade
#
# WHAT IT DOES:
#   1. Runs job_searcher.py option 3 (daily digest — new jobs only)
#   2. Copies the dated digest file to ~/Documents/JobDigests/
#   3. Emails the digest to adewaleosinfade@gmail.com via Gmail SMTP
#   4. Appends a run log to digest.log in this directory
#
# SETUP (one-time):
#   1. Fill in GMAIL_APP_PASSWORD below  (see comments for how to get one)
#   2. Uncomment and fill in any API keys you have
#   3. Verify SCRIPT_DIR points to this folder on your machine
#   4. chmod +x run_digest.sh
#   5. Add to crontab (see CRON SETUP section at the bottom of this file)
# ─────────────────────────────────────────────────────────────────────────────

# ── Paths ─────────────────────────────────────────────────────────────────────
# Absolute path to this script's directory (works when called from cron)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Where to save digest copies on your Mac
DOCS_DIR="$HOME/Documents/JobDigests"

# Log file for cron run history
LOG_FILE="$SCRIPT_DIR/digest.log"


# ── Email settings ─────────────────────────────────────────────────────────────
EMAIL_TO="adewaleosinfade@gmail.com"
EMAIL_FROM="adewaleosinfade@gmail.com"

# Gmail App Password (16 characters, no spaces)
# How to get one:
#   1. Go to https://myaccount.google.com/security
#   2. Enable 2-Step Verification (required)
#   3. Go to https://myaccount.google.com/apppasswords
#   4. Create a new app password → App: "Mail", Device: "Mac"
#   5. Copy the 16-character password and paste it here (no spaces)
GMAIL_APP_PASSWORD="your-16-char-app-password-here"


# ── API keys (optional — set the ones you have) ───────────────────────────────
# export RAPIDAPI_KEY="your_key"          # JSearch — rapidapi.com/jsearch
# export REED_API_KEY="your_key"          # Reed     — reed.co.uk/developers
# export USAJOBS_API_KEY="your_key"       # USAJobs  — developer.usajobs.gov
# export USAJOBS_USER_AGENT="adewaleosinfade@gmail.com"
# export ADZUNA_APP_ID="your_id"          # Adzuna   — developer.adzuna.com
# export ADZUNA_APP_KEY="your_key"


# ─────────────────────────────────────────────────────────────────────────────
# DO NOT EDIT BELOW THIS LINE
# ─────────────────────────────────────────────────────────────────────────────

TODAY=$(date +%Y-%m-%d)
DIGEST_FILE="$SCRIPT_DIR/jobs_${TODAY}.txt"

# Ensure output directory exists
mkdir -p "$DOCS_DIR"

# Add PATH entries cron strips out (Homebrew Python, system Python3)
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

# ── Run digest ────────────────────────────────────────────────────────────────
echo "" >> "$LOG_FILE"
echo "═══════════════════════════════════════════════" >> "$LOG_FILE"
echo "  Job Digest Run: $(date)" >> "$LOG_FILE"
echo "═══════════════════════════════════════════════" >> "$LOG_FILE"

cd "$SCRIPT_DIR" || { echo "  ERROR: cannot cd to $SCRIPT_DIR" >> "$LOG_FILE"; exit 1; }

# Feed "3" (daily digest option) to the script via stdin
echo "3" | python3 job_searcher.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "  WARNING: job_searcher.py exited with code $EXIT_CODE" >> "$LOG_FILE"
fi


# ── Copy digest file to Documents ─────────────────────────────────────────────
if [ -f "$DIGEST_FILE" ]; then
    cp "$DIGEST_FILE" "$DOCS_DIR/jobs_${TODAY}.txt"
    echo "  Digest saved : $DOCS_DIR/jobs_${TODAY}.txt" >> "$LOG_FILE"
else
    echo "  WARNING: Digest file not found ($DIGEST_FILE). No new jobs today?" >> "$LOG_FILE"
    # Still send a "no new jobs" email so you know the script ran
    DIGEST_FILE=""
fi


# ── Send email via Gmail SMTP (curl) ──────────────────────────────────────────
if [ "$GMAIL_APP_PASSWORD" = "your-16-char-app-password-here" ]; then
    echo "  SKIP EMAIL: GMAIL_APP_PASSWORD not configured in run_digest.sh" >> "$LOG_FILE"
else
    # Count new job listings
    if [ -n "$DIGEST_FILE" ] && [ -f "$DIGEST_FILE" ]; then
        JOB_COUNT=$(grep -c '^\[' "$DIGEST_FILE" 2>/dev/null || echo "0")
        SUBJECT="Daily Job Digest — ${TODAY} (${JOB_COUNT} new job(s))"
        BODY=$(cat "$DIGEST_FILE")
    else
        JOB_COUNT=0
        SUBJECT="Daily Job Digest — ${TODAY} (no new jobs)"
        BODY="No new jobs found today. The script ran successfully — check back tomorrow."
    fi

    # Build RFC 2822 email and send via Gmail SMTPS (port 465)
    curl --ssl-reqd \
        --url "smtps://smtp.gmail.com:465" \
        --user "${EMAIL_FROM}:${GMAIL_APP_PASSWORD}" \
        --mail-from "${EMAIL_FROM}" \
        --mail-rcpt "${EMAIL_TO}" \
        --upload-file - \
        --silent \
        --show-error \
        2>> "$LOG_FILE" \
        <<EMAIL_EOF
From: Job Digest <${EMAIL_FROM}>
To: ${EMAIL_TO}
Subject: ${SUBJECT}
Content-Type: text/plain; charset=utf-8

${BODY}
EMAIL_EOF

    CURL_EXIT=$?
    if [ $CURL_EXIT -eq 0 ]; then
        echo "  Email sent   : ${EMAIL_TO} (${JOB_COUNT} job(s))" >> "$LOG_FILE"
    else
        echo "  ERROR: curl email failed (exit code $CURL_EXIT)" >> "$LOG_FILE"
        echo "  Check your GMAIL_APP_PASSWORD and that 2-Step Verification is on." >> "$LOG_FILE"
    fi
fi

echo "  Done: $(date)" >> "$LOG_FILE"
