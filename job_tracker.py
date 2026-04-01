#!/usr/bin/env python3
"""
Job Application Tracker
-----------------------
Scans your entire Gmail inbox for job application confirmation emails,
figures out which company each one is from, and builds a Google Sheet
with a master list + monthly summary stats.

HOW TO RUN:
    python3 job_tracker.py
"""

import subprocess
import json
import re
import time
import os
from datetime import datetime
from collections import defaultdict

CONFIG_PATH = os.path.expanduser("~/.config/gws/job_tracker_config.json")
FOLDER_NAME = "Job Application Tracker"
SHEET_NAME  = "Job Applications Tracker"

# ─────────────────────────────────────────────────────────────
# KEYWORDS — These are phrases we look for in email bodies
# Add more here if you want to cast a wider net
# ─────────────────────────────────────────────────────────────
KEYWORDS = [
    "thank you for applying",
    "thanks for applying",
    "thank you for your application",
    "thanks for your application",
    "we received your application",
    "we have received your application",
    "your application has been received",
    "you have successfully applied",
    "your application has been submitted",
    "successfully received your application",
    "application was successfully submitted",
    "we'll be in touch",
    "we will be in touch",
    "our team will review your application",
]

# ─────────────────────────────────────────────────────────────
# ATS DOMAINS — These are hiring software platforms, NOT actual companies.
# e.g. no-reply@greenhouse.io is sent on behalf of a company, not Greenhouse itself.
# We handle these specially to extract the real company name.
# ─────────────────────────────────────────────────────────────
ATS_DOMAINS = {
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "workday.com", "icims.com", "taleo.net",
    "jobvite.com", "smartrecruiters.com", "ashbyhq.com", "breezy.hr",
    "bamboohr.com", "successfactors.com", "myworkdayjobs.com",
    "workdayjobs.com", "recruiting.com", "hire.trakstar.com",
    "jazz.co", "resumatormail.com", "applytojob.com",
    "rippling.com", "workable.com", "personio.com", "recruitee.com",
    "pinpointhq.com", "dover.com", "gem.com",
}

# ─────────────────────────────────────────────────────────────
# KNOWN JOB PLATFORMS — High confidence job-related domains
# Emails from these are almost certainly job applications
# ─────────────────────────────────────────────────────────────
JOB_PLATFORM_DOMAINS = ATS_DOMAINS | {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "careerbuilder.com", "dice.com", "hired.com",
    "angel.co", "wellfound.com", "triplebyte.com", "otta.com",
    "underdog.io", "cord.co", "gem.com", "eightfold.ai",
}

# ─────────────────────────────────────────────────────────────
# EXCLUSION KEYWORDS — Skip emails containing these phrases
# These indicate non-job applications (DMV, apartments, etc.)
# ─────────────────────────────────────────────────────────────
EXCLUSION_KEYWORDS = [
    # Government / DMV
    "dmv", "driver license", "driver's license", "vehicle registration",
    "department of motor", "motor vehicle",
    # Housing
    "apartment", "lease agreement", "rental application", "tenant",
    "landlord", "rent payment", "move-in", "security deposit",
    # Financial
    "loan application", "credit card", "mortgage", "bank account",
    "insurance policy", "insurance claim", "claim number",
    # Education (non-job)
    "university admission", "college admission", "school application",
    "financial aid", "fafsa", "student loan", "course registration",
    # Travel / Immigration
    "passport application", "visa application", "travel visa",
    "immigration", "green card",
    # Other non-job
    "membership application", "gym membership", "subscription",
    "warranty registration", "product registration",
]

# ─────────────────────────────────────────────────────────────
# JOB CONTEXT WORDS — Words that indicate job-related content
# Used to validate that "application" emails are actually for jobs
# ─────────────────────────────────────────────────────────────
JOB_CONTEXT_WORDS = [
    "position", "role", "job", "career", "opportunity", "team",
    "hiring", "recruiter", "resume", "interview", "candidate",
    "employment", "employer", "salary", "compensation", "benefits",
    "full-time", "part-time", "remote", "hybrid", "onsite",
    "engineer", "developer", "manager", "analyst", "designer",
    "coordinator", "specialist", "associate", "director", "lead",
]

# ─────────────────────────────────────────────────────────────
# SUBJECT LINE SIGNALS — High-confidence subject patterns
# ─────────────────────────────────────────────────────────────
SUBJECT_SIGNALS = [
    "application received", "application confirmation", "applied to",
    "your application", "application for", "applying to", "applied for",
    "thank you for applying", "thanks for applying",
    "careers", "jobs at", "job at", "role at", "position at",
]


# ─────────────────────────────────────────────────────────────
# FILTER: Determine if an email is a real job application
# Returns (is_job, confidence) where confidence is 'high', 'medium', or None
# ─────────────────────────────────────────────────────────────
def is_job_application(subject, body, from_email):
    """
    Multi-layer filtering to determine if an email is a job application.
    Returns (True/False, confidence_level)
    """
    subject_lower = subject.lower() if subject else ""
    body_lower = body.lower() if body else ""
    from_lower = from_email.lower() if from_email else ""
    combined_text = f"{subject_lower} {body_lower}"

    # Layer 1: Check exclusion keywords first (reject early)
    for excl in EXCLUSION_KEYWORDS:
        if excl in combined_text:
            return (False, None)

    # Layer 2: Check if from known job platform (high confidence)
    for domain in JOB_PLATFORM_DOMAINS:
        if domain in from_lower:
            return (True, "high")

    # Layer 3: Check subject line signals (high confidence)
    for signal in SUBJECT_SIGNALS:
        if signal in subject_lower:
            # Verify with at least one job context word
            for ctx in JOB_CONTEXT_WORDS:
                if ctx in combined_text:
                    return (True, "high")
            # Subject signal alone is medium confidence
            return (True, "medium")

    # Layer 4: Check for application keywords + job context
    has_application_keyword = any(kw.lower() in combined_text for kw in KEYWORDS)
    if has_application_keyword:
        # Count how many job context words appear
        context_count = sum(1 for ctx in JOB_CONTEXT_WORDS if ctx in combined_text)
        if context_count >= 2:
            return (True, "high")
        elif context_count >= 1:
            return (True, "medium")
        # No job context - likely a false positive
        return (False, None)

    return (False, None)


# ─────────────────────────────────────────────────────────────
# HELPER: Extract job position/title from subject line
# ─────────────────────────────────────────────────────────────
def extract_position(subject):
    """
    Try to extract the job position from the email subject.
    Returns position string or empty string if not found.
    """
    if not subject:
        return ""

    # Patterns to match job positions in subjects
    patterns = [
        # "Application for Software Engineer at Company"
        r'application\s+for\s+(?:the\s+)?(.+?)\s+(?:at|with|@)\s+',
        # "Your Software Engineer application"
        r'your\s+(.+?)\s+application',
        # "An update on your application for Senior Product Manager (Amex Digital)"
        r'(?:update|status)\s+on\s+your\s+application\s+for\s+(?:the\s+)?(.+?)$',
        # "applying to Software Engineer"
        r'applying\s+to\s+(?:the\s+)?(.+?)\s+(?:at|with|@|position|role)',
        # "applied for Software Engineer"
        r'applied\s+(?:for|to)\s+(?:the\s+)?(.+?)\s+(?:at|with|@|position|role)',
        # "Software Engineer role at Company"
        r'^(.+?)\s+(?:role|position)\s+(?:at|with|@)\s+',
        # "Thank you for applying to Software Engineer"
        r'(?:thank you|thanks)\s+for\s+applying\s+(?:to|for)\s+(?:the\s+)?(.+?)\s+(?:at|with|@|position|role|$)',
        # "Your application for Software Engineer"
        r'application\s+for\s+(?:the\s+)?(.+?)(?:\s+has|\s+was|\s+received|$)',
    ]

    for pattern in patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            position = match.group(1).strip()
            # Clean up common trailing words
            position = re.sub(r'\s*(?:has been|was|received|submitted|confirmed|is being|under review).*$', '', position, flags=re.IGNORECASE)
            # Strip trailing job/requisition IDs like "- (26000062)" or "(REQ-12345)"
            position = re.sub(r'\s*-?\s*\([\w\-]+\)\s*$', '', position)
            # Strip trailing dash separators and anything after
            position = re.sub(r'\s*\-\s*$', '', position)
            position = position.strip(' -')
            # Don't return if it looks like a company name or is too short
            if len(position) > 2 and not re.match(r'^(?:the|a|an|our|your)$', position, re.IGNORECASE):
                return position.strip()

    return ""


# ─────────────────────────────────────────────────────────────
# HELPER: Run a gws command and return the result as data
# (Think of this as: "speak to Google on our behalf")
# ─────────────────────────────────────────────────────────────
def run_gws(args_list):
    cmd = ["gws"] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err_msg = result.stderr.strip() if result.stderr else "unknown error"
        print(f"\n   [gws error] {' '.join(args_list[:4])}... → {err_msg}", flush=True)
        return None
    try:
        return json.loads(result.stdout)
    except Exception as e:
        print(f"\n   [gws parse error] Could not parse response: {e}", flush=True)
        return None


# ─────────────────────────────────────────────────────────────
# HELPER: Extract company name from subject line (used for ATS emails)
# e.g. "Thank you for applying to Kikoff" → "Kikoff"
# ─────────────────────────────────────────────────────────────
def _clean_company_from_match(raw):
    """Post-process a captured company name: strip trailing noise phrases and punctuation."""
    if not raw:
        return ""
    company = raw.strip()
    # Strip trailing status phrases that lazy regex can accidentally capture
    company = re.sub(
        r'\s+(?:has been|was|is|received|submitted|confirmed|next steps|under review|being reviewed|successfully).*$',
        '', company, flags=re.IGNORECASE
    )
    # Strip trailing dash/pipe separators and anything after
    company = re.sub(r'\s*[\-\|—–]\s+.*$', '', company)
    company = company.strip(' "\'!.,<>-|—–')
    return company


def extract_company_from_subject(subject):
    if not subject:
        return ""
    patterns = [
        # ── Company AFTER a keyword (existing patterns, improved) ──

        # "Thank you for applying to Brex!"
        r'(?:thank(?:s| you) for (?:applying|your application)(?: to| with| at| for)?)\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',
        # "Applied to/for/with Stripe"
        r'(?:applied (?:to|for|with))\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',
        # "Your application to/at Stripe"
        r'(?:your application (?:to|at|with|for))\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',
        # "Application to/at/for Stripe"
        r'(?:application (?:to|at|with|for))\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',
        # "Applying to Stripe"
        r'(?:applying to)\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',
        # "Application Update from Tesla"
        r'(?:application\s+(?:update|status|confirmation)\s+from)\s+([A-Za-z0-9][^!,\.\n]+?)(?:[!,\.]|$)',

        # ── Company BEFORE a keyword (new patterns for ATS emails) ──

        # "Decagon: Application Confirmation" — company before colon + application keyword
        r'^([A-Za-z0-9][^:\n]+?):\s*(?:application|your application|application status)',
        # "Comfy Application Update" — company before "Application Update/Status/Confirmation"
        r'^([A-Za-z0-9][^!\n]+?)\s+(?:application\s+(?:update|status|confirmation|received))',
        # "Acme Corp — Application Received" / "Acme Corp - Application Status"
        r'^([A-Za-z0-9][^—–\-\n]+?)\s*[\-—–]\s*(?:application|your application)',
        # "Acme Corp | Your Application"
        r'^([A-Za-z0-9][^|\n]+?)\s*\|\s*(?:application|your application)',
    ]
    for pattern in patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            company = _clean_company_from_match(match.group(1))
            # Skip if it looks like a generic phrase
            if len(company) > 1 and not re.match(r'^(?:the|a|an|our|your|us|this|re)$', company, re.IGNORECASE):
                return company
    return ""


# ─────────────────────────────────────────────────────────────
# HELPER: Figure out company name from the "From" field of an email
# Falls back to subject-line extraction for ATS senders
# ─────────────────────────────────────────────────────────────
def extract_company(from_header, subject=""):
    if not from_header:
        return "Unknown"

    # Emails look like:  Stripe Careers <jobs@stripe.com>
    # Or sometimes just: no-reply@greenhouse.io
    display_name = ""
    email_addr = ""

    match = re.match(r'^"?([^"<\n]+?)"?\s*<([^>]+)>', from_header.strip())
    if match:
        display_name = match.group(1).strip().strip('"').strip("'")
        email_addr = match.group(2).strip().lower()
    else:
        email_addr = from_header.strip().lower()

    domain_match = re.search(r'@([\w.\-]+)', email_addr)
    domain = domain_match.group(1) if domain_match else ""
    is_ats = any(ats in domain for ats in ATS_DOMAINS)

    # If there's a display name and it's not an ATS, clean it up and use it
    if display_name and not is_ats:
        company = display_name
        company = re.sub(r'\s+via\s+\S+', '', company, flags=re.IGNORECASE)
        # Only strip qualifier words when they appear as TRAILING suffixes
        # e.g. "Stripe Careers" → "Stripe", but "Team Rubicon" stays "Team Rubicon"
        company = re.sub(
            r'\s+(recruiting|talent|careers|jobs|hr|noreply|no.reply|team|hiring|notifications?)$',
            '', company, flags=re.IGNORECASE
        ).strip()
        # Strip leading job words (e.g. "Careers Netflix" → "Netflix", "at EliseAI" → "EliseAI")
        company = re.sub(r'^(careers|recruiting|talent|jobs|hr|at)\s+', '', company, flags=re.IGNORECASE)
        # Strip standalone noreply-style names entirely
        if re.match(r'^(noreply|no[\.\-]reply)$', company, re.IGNORECASE):
            company = ""
        company = company.strip(' "\'<>-|')
        if len(company) > 1:
            return company

    # For ATS senders, extract company from subject line
    if is_ats:
        from_subject = extract_company_from_subject(subject)
        if from_subject:
            return from_subject
        return f"Unknown (via {domain})"

    # Otherwise use the domain name (e.g. stripe.com → Stripe)
    if domain:
        parts = domain.split(".")
        skip = {"mail", "jobs", "careers", "recruiting", "www", "email",
                "notifications", "info", "hello", "apply", "no-reply", "noreply"}
        meaningful = [p for p in parts[:-1] if p.lower() not in skip]
        if meaningful:
            return meaningful[-1].title()

    return from_header.split("<")[0].strip() or from_header


# ─────────────────────────────────────────────────────────────
# STEP 1: Search Gmail and return all matching message IDs
# ─────────────────────────────────────────────────────────────
def get_all_message_ids(query):
    all_ids = []
    page_token = None
    page = 1

    while True:
        params = {"userId": "me", "q": query, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token

        print(f"   Scanning inbox... (batch {page})", end="\r")
        data = run_gws([
            "gmail", "users", "messages", "list",
            "--params", json.dumps(params)
        ])

        if not data:
            break

        messages = data.get("messages", [])
        all_ids.extend([m["id"] for m in messages])

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        page += 1
        time.sleep(0.1)  # Small pause so we don't overwhelm Google's servers

    return all_ids


# ─────────────────────────────────────────────────────────────
# STEP 2: For each email ID, fetch the details (who sent it, subject, date)
# ─────────────────────────────────────────────────────────────
def get_email_details(msg_id, include_snippet=True):
    """
    Fetch email metadata and optionally snippet for filtering.
    Returns dict with email details or None if failed.
    """
    params = {
        "userId": "me",
        "id": msg_id,
        "format": "metadata",
        "metadataHeaders": ["From", "Subject", "Date"]
    }

    data = run_gws([
        "gmail", "users", "messages", "get",
        "--params", json.dumps(params)
    ])

    if not data:
        return None

    headers = {}
    for h in data.get("payload", {}).get("headers", []):
        headers[h["name"]] = h["value"]

    from_header = headers.get("From", "Unknown")
    subject = headers.get("Subject", "No Subject")
    date_str = headers.get("Date", "")

    # Get snippet (first ~100 chars of body) for filtering
    snippet = data.get("snippet", "")

    date_formatted = "Unknown"
    month_year = "Unknown"

    if date_str:
        date_clean = re.sub(r'\s*\([^)]*\)\s*$', '', date_str).strip()
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M:%S",
        ]:
            try:
                date_obj = datetime.strptime(date_clean[:31], fmt)
                date_formatted = date_obj.strftime("%Y-%m-%d")
                month_year = date_obj.strftime("%B %Y")
                break
            except Exception:
                continue

    from_email_match = re.search(r'<([^>]+)>', from_header)
    from_email = from_email_match.group(1) if from_email_match else from_header

    # Extract position from subject
    position = extract_position(subject)

    return {
        "company": extract_company(from_header, subject),
        "from_email": from_email,
        "subject": subject,
        "date": date_formatted,
        "month": month_year,
        "position": position,
        "snippet": snippet,
    }


# ─────────────────────────────────────────────────────────────
# CONFIG — persist folder + sheet IDs between runs
# ─────────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────
# DRIVE — find or create the tracker folder
# ─────────────────────────────────────────────────────────────
def find_or_create_folder(name):
    """Return the Drive folder ID for `name`, creating it if needed."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    result = run_gws(["drive", "files", "list", "--params", json.dumps({"q": query, "fields": "files(id,name)"})])
    files = (result or {}).get("files", [])
    if files:
        return files[0]["id"]

    # Create the folder
    created = run_gws([
        "drive", "files", "create",
        "--json", json.dumps({
            "name": name,
            "mimeType": "application/vnd.google-apps.folder"
        }),
        "--params", json.dumps({"fields": "id"})
    ])
    return (created or {}).get("id")


def move_to_folder(file_id, folder_id):
    """Move a Drive file into the given folder."""
    # Get current parents first
    info = run_gws(["drive", "files", "get", "--params", json.dumps({"fileId": file_id, "fields": "parents"})])
    current_parents = ",".join((info or {}).get("parents", []))
    run_gws([
        "drive", "files", "update",
        "--params", json.dumps({
            "fileId": file_id,
            "addParents": folder_id,
            "removeParents": current_parents,
            "fields": "id"
        })
    ])


def verify_sheet_exists(spreadsheet_id):
    """Return True if the spreadsheet still exists and is accessible."""
    result = run_gws([
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id, "fields": "spreadsheetId"})
    ])
    return result is not None


def clear_sheet_for_reuse(spreadsheet_id):
    """
    Delete all embedded charts and clear all data ranges so the sheet
    can be fully rewritten on this run.
    """
    # Get current spreadsheet state (charts live inside each sheet)
    info = run_gws([
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id,
                                "fields": "sheets(properties(sheetId),charts(chartId))"})
    ])
    if not info:
        return

    delete_requests = []
    clear_ranges = []

    for sheet in info.get("sheets", []):
        sid = sheet["properties"]["sheetId"]
        # Queue chart deletions
        for chart in sheet.get("charts", []):
            delete_requests.append({
                "deleteEmbeddedObject": {"objectId": chart["chartId"]}
            })
        # Queue data clears
        sheet_title_map = {10: "Dashboard", 20: "Applications", 30: "Monthly Summary", 40: "Chart Data"}
        title = sheet_title_map.get(sid)
        if title:
            clear_ranges.append(f"{title}!A1:ZZ10000")

    # Delete charts
    if delete_requests:
        run_gws([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
            "--json", json.dumps({"requests": delete_requests})
        ])

    # Clear data
    for r in clear_ranges:
        run_gws([
            "sheets", "spreadsheets", "values", "clear",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id, "range": r})
        ])

    # Also clear any existing banding so it doesn't stack up
    info2 = run_gws([
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id,
                                "fields": "sheets(bandedRanges(bandedRangeId))"})
    ])
    banding_requests = []
    for sheet in (info2 or {}).get("sheets", []):
        for band in sheet.get("bandedRanges", []):
            banding_requests.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})
    if banding_requests:
        run_gws([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
            "--json", json.dumps({"requests": banding_requests})
        ])


# ─────────────────────────────────────────────────────────────
# STEP 3: Create a new Google Sheet with two tabs
# ─────────────────────────────────────────────────────────────
def create_sheet_with_tabs(title):
    """
    Create a new Google Sheet with four tabs:
      0 — Dashboard       (charts only, shown first)
      1 — Applications    (full email list)
      2 — Monthly Summary (clean stats table)
      3 — Chart Data      (helper data for charts, hidden from view)
    Returns (spreadsheet_id, dashboard_id, app_id, summary_id, chart_data_id)
    """
    data = run_gws([
        "sheets", "spreadsheets", "create",
        "--json", json.dumps({
            "properties": {"title": title},
            "sheets": [
                {"properties": {"sheetId": 10, "title": "Dashboard", "index": 0}},
                {"properties": {"sheetId": 20, "title": "Applications",
                                "gridProperties": {"frozenRowCount": 1}, "index": 1}},
                {"properties": {"sheetId": 30, "title": "Monthly Summary",
                                "gridProperties": {"frozenRowCount": 1}, "index": 2}},
                {"properties": {"sheetId": 40, "title": "Chart Data",
                                "gridProperties": {"frozenRowCount": 1}, "index": 3}},
            ]
        })
    ])
    if data:
        return data.get("spreadsheetId"), 10, 20, 30, 40
    return None, None, None, None, None


def write_to_sheet(spreadsheet_id, range_name, values):
    """Write values to a specific range in the sheet."""
    run_gws([
        "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps({
            "spreadsheetId": spreadsheet_id,
            "range": range_name,
            "valueInputOption": "USER_ENTERED"
        }),
        "--json", json.dumps({"values": values})
    ])


def format_sheet(spreadsheet_id, dashboard_sheet_id, applications_sheet_id, summary_sheet_id,
                 chart_data_sheet_id, app_row_count, month_count):
    """
    Apply formatting to both sheets:
    - Dark navy headers with white bold text
    - Alternating row banding
    - No gridlines
    - Clean column widths
    - Total row bold styling on summary
    """
    NAVY   = {"red": 0.102, "green": 0.137, "blue": 0.278}
    WHITE  = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
    BAND1  = {"red": 1.0,   "green": 1.0,   "blue": 1.0}        # white
    BAND2  = {"red": 0.937, "green": 0.949, "blue": 0.980}      # very light blue-grey
    TOTAL  = {"red": 0.878, "green": 0.878, "blue": 0.878}      # light grey for total row

    requests = [
        # ══════════════════════════════════════════════════
        # APPLICATIONS TAB
        # ══════════════════════════════════════════════════

        # Hide gridlines
        {"updateSheetProperties": {
            "properties": {
                "sheetId": applications_sheet_id,
                "gridProperties": {"hideGridlines": True}
            },
            "fields": "gridProperties.hideGridlines"
        }},

        # Header row — navy background, white bold text, centered
        {"repeatCell": {
            "range": {"sheetId": applications_sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY,
                    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "padding": {"top": 8, "bottom": 8, "left": 8, "right": 8}
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,padding)"
        }},

        # Header row height
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 36}, "fields": "pixelSize"
        }},

        # Data rows height
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": app_row_count + 1},
            "properties": {"pixelSize": 28}, "fields": "pixelSize"
        }},

        # Alternating row banding
        {"addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": applications_sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": app_row_count + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 5
                },
                "rowProperties": {
                    "firstBandColor": BAND1,
                    "secondBandColor": BAND2
                }
            }
        }},

        # Data rows — font size + vertical alignment
        {"repeatCell": {
            "range": {"sheetId": applications_sheet_id, "startRowIndex": 1, "endRowIndex": app_row_count + 1},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"fontSize": 10},
                    "verticalAlignment": "MIDDLE",
                    "padding": {"top": 4, "bottom": 4, "left": 8, "right": 8}
                }
            },
            "fields": "userEnteredFormat(textFormat,verticalAlignment,padding)"
        }},

        # Date column — center aligned
        {"repeatCell": {
            "range": {"sheetId": applications_sheet_id, "startRowIndex": 1, "endRowIndex": app_row_count + 1,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"
        }},

        # Column widths — Date, Company, Position, Subject, Sender
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 110}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 180}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 220}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
            "properties": {"pixelSize": 340}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5},
            "properties": {"pixelSize": 230}, "fields": "pixelSize"
        }},

        # ══════════════════════════════════════════════════
        # MONTHLY SUMMARY TAB
        # ══════════════════════════════════════════════════

        # Hide gridlines
        {"updateSheetProperties": {
            "properties": {
                "sheetId": summary_sheet_id,
                "gridProperties": {"hideGridlines": True}
            },
            "fields": "gridProperties.hideGridlines"
        }},

        # Header row — navy + white bold
        {"repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY,
                    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "padding": {"top": 8, "bottom": 8, "left": 8, "right": 8}
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,padding)"
        }},

        # Header row height
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 36}, "fields": "pixelSize"
        }},

        # Data rows height
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": month_count + 2},
            "properties": {"pixelSize": 28}, "fields": "pixelSize"
        }},

        # Alternating row banding (month rows only, not total)
        {"addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": summary_sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": month_count + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3
                },
                "rowProperties": {
                    "firstBandColor": BAND1,
                    "secondBandColor": BAND2
                }
            }
        }},

        # Data rows font + alignment
        {"repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 1, "endRowIndex": month_count + 1},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"fontSize": 10},
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "CENTER",
                    "padding": {"top": 4, "bottom": 4, "left": 8, "right": 8}
                }
            },
            "fields": "userEnteredFormat(textFormat,verticalAlignment,horizontalAlignment,padding)"
        }},

        # Total row — bold, grey background (scoped to A-C only)
        {"repeatCell": {
            "range": {
                "sheetId": summary_sheet_id,
                "startRowIndex": month_count + 1,
                "endRowIndex": month_count + 2,
                "startColumnIndex": 0,
                "endColumnIndex": 3
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": TOTAL,
                    "textFormat": {"bold": True, "fontSize": 10},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "padding": {"top": 6, "bottom": 6, "left": 8, "right": 8}
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,padding)"
        }},

        # Column widths — Month, Applications, Unique Companies
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 150}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 130}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 160}, "fields": "pixelSize"
        }},

        # ══════════════════════════════════════════════════
        # DASHBOARD TAB — no gridlines, clean background
        # ══════════════════════════════════════════════════
        {"updateSheetProperties": {
            "properties": {
                "sheetId": dashboard_sheet_id,
                "gridProperties": {"hideGridlines": True}
            },
            "fields": "gridProperties.hideGridlines"
        }},

        # ══════════════════════════════════════════════════
        # CHART DATA TAB — minimal styling, just usable
        # ══════════════════════════════════════════════════
        {"repeatCell": {
            "range": {"sheetId": chart_data_sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY,
                    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": chart_data_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 150}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": chart_data_sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 100}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": chart_data_sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
            "properties": {"pixelSize": 160}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": chart_data_sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5},
            "properties": {"pixelSize": 80}, "fields": "pixelSize"
        }},
    ]

    run_gws([
        "sheets", "spreadsheets", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
        "--json", json.dumps({"requests": requests})
    ])


def write_chart_data(spreadsheet_id, applications, sorted_months, month_counts):
    """
    Write all chart source data to the 'Chart Data' sheet.
      A-B: Monthly applications (Month, Count)
      D-E: Top companies (Company, Count)
    Returns company_count for chart range calculation.
    """
    # Monthly data — columns A-B
    monthly_header = [["Month", "Applications"]]
    monthly_rows = [[m, month_counts[m]] for m in sorted_months]
    write_to_sheet(spreadsheet_id, "Chart Data!A1", monthly_header + monthly_rows)

    # Top companies — columns D-E
    company_counts = defaultdict(int)
    for app in applications:
        company_counts[app["company"]] += 1
    sorted_companies = sorted(company_counts.items(), key=lambda x: -x[1])[:15]
    if not sorted_companies:
        return 0
    company_header = [["Company", "Count"]]
    company_rows = [[c, n] for c, n in sorted_companies]
    write_to_sheet(spreadsheet_id, "Chart Data!D1", company_header + company_rows)

    return len(sorted_companies)


def create_charts(spreadsheet_id, dashboard_sheet_id, chart_data_sheet_id, month_count, company_count):
    """
    Create embedded charts on the Dashboard tab, sourcing data from Chart Data tab.
    1. Column chart — Applications by Month
    2. Horizontal bar chart — Top Companies
    Both placed side-by-side on the clean Dashboard sheet.
    """
    ANCHOR_ROW = 1   # start near the top of the Dashboard
    NAVY_COLOR = {"red": 0.102, "green": 0.137, "blue": 0.278}
    GREEN_COLOR = {"red": 0.133, "green": 0.694, "blue": 0.298}

    requests = []

    # ─── Chart 1: Applications by Month (Column chart) ────────
    if month_count > 0:
        requests.append({
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Applications by Month",
                        "titleTextFormat": {"bold": True, "fontSize": 13},
                        "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "basicChart": {
                            "chartType": "COLUMN",
                            "legendPosition": "NO_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": ""},
                                {"position": "LEFT_AXIS", "title": "Applications"}
                            ],
                            "domains": [{
                                "domain": {
                                    "sourceRange": {"sources": [{
                                        "sheetId": chart_data_sheet_id,
                                        "startRowIndex": 1, "endRowIndex": month_count + 1,
                                        "startColumnIndex": 0, "endColumnIndex": 1
                                    }]}
                                }
                            }],
                            "series": [{
                                "series": {
                                    "sourceRange": {"sources": [{
                                        "sheetId": chart_data_sheet_id,
                                        "startRowIndex": 1, "endRowIndex": month_count + 1,
                                        "startColumnIndex": 1, "endColumnIndex": 2
                                    }]}
                                },
                                "color": NAVY_COLOR,
                                "dataLabel": {
                                    "type": "DATA",
                                    "textFormat": {"bold": True, "fontSize": 10}
                                }
                            }],
                            "headerCount": 0
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": dashboard_sheet_id,
                                "rowIndex": ANCHOR_ROW,
                                "columnIndex": 0
                            },
                            "widthPixels": 560,
                            "heightPixels": 380
                        }
                    }
                }
            }
        })

    # ─── Chart 2: Top Companies (Horizontal Bar chart) ────────
    if company_count > 0:
        requests.append({
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Top Companies Applied To",
                        "titleTextFormat": {"bold": True, "fontSize": 13},
                        "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "basicChart": {
                            "chartType": "BAR",
                            "legendPosition": "NO_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": "Applications"},
                                {"position": "LEFT_AXIS",   "title": ""}
                            ],
                            "domains": [{
                                "domain": {
                                    "sourceRange": {"sources": [{
                                        "sheetId": chart_data_sheet_id,
                                        "startRowIndex": 1, "endRowIndex": company_count + 1,
                                        "startColumnIndex": 3, "endColumnIndex": 4
                                    }]}
                                }
                            }],
                            "series": [{
                                "series": {
                                    "sourceRange": {"sources": [{
                                        "sheetId": chart_data_sheet_id,
                                        "startRowIndex": 1, "endRowIndex": company_count + 1,
                                        "startColumnIndex": 4, "endColumnIndex": 5
                                    }]}
                                },
                                "color": GREEN_COLOR,
                                "dataLabel": {
                                    "type": "DATA",
                                    "textFormat": {"bold": True, "fontSize": 10}
                                }
                            }],
                            "headerCount": 0
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": dashboard_sheet_id,
                                "rowIndex": ANCHOR_ROW,
                                "columnIndex": 9
                            },
                            "widthPixels": 560,
                            "heightPixels": 460
                        }
                    }
                }
            }
        })

    if requests:
        run_gws([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
            "--json", json.dumps({"requests": requests})
        ])


# ─────────────────────────────────────────────────────────────
# MAIN — Orchestrates everything
# ─────────────────────────────────────────────────────────────
def main():
    print()
    print("=" * 55)
    print("   JOB APPLICATION TRACKER  — by Harshit")
    print("=" * 55)
    print()

    # Build the Gmail search query
    query = "(" + " OR ".join([f'"{k}"' for k in KEYWORDS]) + ") after:2026/03/15"

    # ── STEP 1: Find matching emails ──────────────────────────
    print("[ STEP 1 ] Searching Gmail for application confirmation emails...")
    msg_ids = get_all_message_ids(query)
    print(f"\n   Found {len(msg_ids)} candidate emails.\n")

    if not msg_ids:
        print("No emails found. Double-check your gws auth or try broader keywords.")
        return

    # ── STEP 2: Read and filter each email ────────────────────
    print("[ STEP 2 ] Reading and filtering emails (this takes a moment)...")
    applications = []
    filtered_count = 0

    for i, msg_id in enumerate(msg_ids):
        print(f"   Processing email {i + 1} of {len(msg_ids)}...   ", end="\r")
        details = get_email_details(msg_id)
        if details:
            # Apply multi-layer filtering
            is_job, confidence = is_job_application(
                details["subject"],
                details["snippet"],
                details["from_email"]
            )
            if is_job:
                details["confidence"] = confidence
                applications.append(details)
            else:
                filtered_count += 1
        time.sleep(0.05)  # Gentle pacing

    print(f"\n   Found {len(applications)} job applications (filtered out {filtered_count} non-job emails).\n")

    if not applications:
        print("No job applications found after filtering.")
        return

    # Sort by date (newest first for better readability)
    applications.sort(key=lambda x: x["date"] if x["date"] != "Unknown" else "0000", reverse=True)

    # ── STEP 3: Build summary stats ───────────────────────────
    print("[ STEP 3 ] Crunching your stats...")

    month_counts = defaultdict(int)
    month_companies = defaultdict(set)

    for app in applications:
        m = app["month"]
        month_counts[m] += 1
        month_companies[m].add(app["company"])

    def month_sort_key(m):
        try:
            return datetime.strptime(m, "%B %Y")
        except Exception:
            return datetime.min

    sorted_months = sorted(
        [m for m in month_counts if m != "Unknown"],
        key=month_sort_key
    )
    # Include "Unknown" at the end if any emails had unparseable dates
    if "Unknown" in month_counts:
        sorted_months.append("Unknown")

    # ── STEP 4: Find or create the Drive folder + sheet ───────
    print("[ STEP 4 ] Setting up Google Drive folder and Sheet...")
    config = load_config()

    # Ensure the folder exists
    folder_id = config.get("folder_id")
    if not folder_id:
        print("   Creating Drive folder...")
        folder_id = find_or_create_folder(FOLDER_NAME)
        if not folder_id:
            print("\nCould not create Drive folder. Run `gws auth login` and try again.")
            return
        config["folder_id"] = folder_id
        save_config(config)
        print(f"   Folder created: {FOLDER_NAME}")
    else:
        print(f"   Using existing folder: {FOLDER_NAME}")

    # Check if tracker sheet already exists
    sheet_id = config.get("spreadsheet_id")
    reusing = False

    if sheet_id and verify_sheet_exists(sheet_id):
        print("   Found existing sheet — clearing for fresh data...")
        clear_sheet_for_reuse(sheet_id)
        dash_sheet_id, app_sheet_id, summary_sheet_id, chart_data_sheet_id = 10, 20, 30, 40
        reusing = True
    else:
        print("   Creating new sheet...")
        sheet_id, dash_sheet_id, app_sheet_id, summary_sheet_id, chart_data_sheet_id = \
            create_sheet_with_tabs(SHEET_NAME)
        if not sheet_id:
            print("\nCould not create the Google Sheet. Run `gws auth login` and try again.")
            return
        move_to_folder(sheet_id, folder_id)
        config["spreadsheet_id"] = sheet_id
        save_config(config)

    print(f"   {'Updated' if reusing else 'Created'} sheet. Writing your data...\n")

    # ── Write: Applications tab (master list) ─────────────────
    app_headers = [["Date", "Company", "Position", "Subject", "Sender"]]
    app_rows = [
        [app["date"], app["company"], app["position"], app["subject"], app["from_email"]]
        for app in applications
    ]
    write_to_sheet(sheet_id, "Applications!A1", app_headers + app_rows)

    # ── Write: Monthly Summary tab ────────────────────────────
    summary_headers = [["Month", "Applications", "Unique Companies"]]
    summary_rows = [
        [month, month_counts[month], len(month_companies[month])]
        for month in sorted_months
    ]
    total_unique = len(set(a["company"] for a in applications))
    summary_rows.append(["Total", len(applications), total_unique])
    write_to_sheet(sheet_id, "Monthly Summary!A1", summary_headers + summary_rows)

    # ── Write: Chart Data tab (source for Dashboard charts) ───
    company_count = write_chart_data(sheet_id, applications, sorted_months, month_counts)

    # ── Apply formatting ──────────────────────────────────────
    print("   Applying formatting...")
    format_sheet(sheet_id, dash_sheet_id, app_sheet_id, summary_sheet_id,
                 chart_data_sheet_id, len(applications), len(sorted_months))

    # ── Create charts on Dashboard ────────────────────────────
    print("   Creating charts...")
    create_charts(sheet_id, dash_sheet_id, chart_data_sheet_id, len(sorted_months), company_count)

    # ── Print final results ───────────────────────────────────
    print()
    print("=" * 55)
    print("   ALL DONE!")
    print("=" * 55)
    print()
    print(f"  Total applications found : {len(applications)}")
    print(f"  Unique companies          : {total_unique}")
    print(f"  Filtered out (non-job)    : {filtered_count}")
    print()
    print("  Monthly breakdown:")
    for month in sorted_months:
        print(f"    {month:<20} {month_counts[month]:>3} applications   "
              f"{len(month_companies[month]):>3} unique companies")
    print()
    print(f"  Saved in Drive folder    : {FOLDER_NAME}")
    print("  Your Google Sheet:")
    print(f"  https://docs.google.com/spreadsheets/d/{sheet_id}")
    print()


if __name__ == "__main__":
    main()
