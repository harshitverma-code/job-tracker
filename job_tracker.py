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
from datetime import datetime
from collections import defaultdict

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
    "greenhouse.io", "lever.co", "workday.com", "icims.com", "taleo.net",
    "jobvite.com", "smartrecruiters.com", "ashbyhq.com", "breezy.hr",
    "bamboohr.com", "successfactors.com", "myworkdayjobs.com",
    "workdayjobs.com", "recruiting.com", "hire.trakstar.com",
    "jazz.co", "resumatormail.com", "applytojob.com",
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
            position = re.sub(r'\s*(?:has been|was|received|submitted|confirmed).*$', '', position, flags=re.IGNORECASE)
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
        return None
    try:
        return json.loads(result.stdout)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# HELPER: Figure out company name from the "From" field of an email
# ─────────────────────────────────────────────────────────────
def extract_company(from_header):
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

    # If there's a display name, clean it up and use it
    if display_name:
        company = display_name
        # Remove things like "via Greenhouse", "Recruiting Team", etc.
        company = re.sub(r'\s+via\s+\S+', '', company, flags=re.IGNORECASE)
        company = re.sub(
            r'\b(recruiting|talent|careers|jobs|hr|noreply|no.reply|team|hiring|notifications?)\b',
            '', company, flags=re.IGNORECASE
        ).strip()
        company = company.strip(' "\'<>-|')
        if len(company) > 1:
            return company

    # If it's an ATS domain and no display name, we can't easily tell — mark it
    if is_ats:
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
        "company": extract_company(from_header),
        "from_email": from_email,
        "subject": subject,
        "date": date_formatted,
        "month": month_year,
        "position": position,
        "snippet": snippet,
    }


# ─────────────────────────────────────────────────────────────
# STEP 3: Create a new Google Sheet with two tabs
# ─────────────────────────────────────────────────────────────
def create_sheet_with_tabs(title):
    """
    Create a new Google Sheet with two tabs: 'Applications' and 'Monthly Summary'
    Returns (spreadsheet_id, applications_sheet_id, summary_sheet_id) or None
    """
    # Create spreadsheet with two sheets
    data = run_gws([
        "sheets", "spreadsheets", "create",
        "--json", json.dumps({
            "properties": {"title": title},
            "sheets": [
                {
                    "properties": {
                        "sheetId": 0,
                        "title": "Applications",
                        "gridProperties": {"frozenRowCount": 1}
                    }
                },
                {
                    "properties": {
                        "sheetId": 1,
                        "title": "Monthly Summary",
                        "gridProperties": {"frozenRowCount": 1}
                    }
                }
            ]
        })
    ])
    if data:
        return data.get("spreadsheetId"), 0, 1
    return None, None, None


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


def format_sheet(spreadsheet_id, applications_sheet_id, summary_sheet_id, app_row_count):
    """
    Apply formatting to both sheets:
    - Bold headers
    - Column widths
    - Header background color
    """
    requests = [
        # ─── Applications tab formatting ───
        # Bold header row
        {
            "repeatCell": {
                "range": {
                    "sheetId": applications_sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)"
            }
        },
        # Column widths for Applications
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 100}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 180}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 200}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
            "properties": {"pixelSize": 350}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": applications_sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5},
            "properties": {"pixelSize": 250}, "fields": "pixelSize"
        }},
        # ─── Monthly Summary tab formatting ───
        # Bold header row
        {
            "repeatCell": {
                "range": {
                    "sheetId": summary_sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)"
            }
        },
        # Column widths for Monthly Summary
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 120}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 120}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 140}, "fields": "pixelSize"
        }},
    ]

    run_gws([
        "sheets", "spreadsheets", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
        "--json", json.dumps({"requests": requests})
    ])


def write_company_counts(spreadsheet_id, applications, start_col="E"):
    """
    Write top companies by application count to the Monthly Summary tab.
    This data will be used as the source for the Top Companies chart.
    Returns the number of companies written (for chart range calculation).
    """
    # Count applications per company
    company_counts = defaultdict(int)
    for app in applications:
        company_counts[app["company"]] += 1

    # Sort by count descending, take top 15
    sorted_companies = sorted(company_counts.items(), key=lambda x: -x[1])[:15]

    if not sorted_companies:
        return 0

    # Write header and data starting at column E
    header = [["Top Companies", "Count"]]
    rows = [[company, count] for company, count in sorted_companies]

    write_to_sheet(spreadsheet_id, f"Monthly Summary!{start_col}1", header + rows)

    return len(sorted_companies)


def create_charts(spreadsheet_id, summary_sheet_id, month_count, company_count):
    """
    Create embedded charts in the Google Sheet:
    1. Monthly Applications column chart on Monthly Summary tab
    2. Top Companies horizontal bar chart on Monthly Summary tab
    """
    requests = []

    # ─── Chart 1: Month-over-Month Applications ───────────────
    # Data is in Monthly Summary!A2:B{month_count+1} (excluding Total row)
    if month_count > 0:
        monthly_chart = {
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Applications by Month",
                        "basicChart": {
                            "chartType": "COLUMN",
                            "legendPosition": "BOTTOM_LEGEND",
                            "axis": [
                                {
                                    "position": "BOTTOM_AXIS",
                                    "title": "Month"
                                },
                                {
                                    "position": "LEFT_AXIS",
                                    "title": "Applications"
                                }
                            ],
                            "domains": [
                                {
                                    "domain": {
                                        "sourceRange": {
                                            "sources": [
                                                {
                                                    "sheetId": summary_sheet_id,
                                                    "startRowIndex": 1,
                                                    "endRowIndex": month_count + 1,
                                                    "startColumnIndex": 0,
                                                    "endColumnIndex": 1
                                                }
                                            ]
                                        }
                                    }
                                }
                            ],
                            "series": [
                                {
                                    "series": {
                                        "sourceRange": {
                                            "sources": [
                                                {
                                                    "sheetId": summary_sheet_id,
                                                    "startRowIndex": 1,
                                                    "endRowIndex": month_count + 1,
                                                    "startColumnIndex": 1,
                                                    "endColumnIndex": 2
                                                }
                                            ]
                                        }
                                    },
                                    "color": {
                                        "red": 0.26,
                                        "green": 0.52,
                                        "blue": 0.96
                                    }
                                }
                            ],
                            "headerCount": 0
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": summary_sheet_id,
                                "rowIndex": month_count + 3,
                                "columnIndex": 0
                            },
                            "widthPixels": 600,
                            "heightPixels": 350
                        }
                    }
                }
            }
        }
        requests.append(monthly_chart)

    # ─── Chart 2: Top Companies by Applications ───────────────
    # Data is in Monthly Summary!E2:F{company_count+1}
    if company_count > 0:
        companies_chart = {
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Top Companies by Applications",
                        "basicChart": {
                            "chartType": "BAR",
                            "legendPosition": "NO_LEGEND",
                            "axis": [
                                {
                                    "position": "BOTTOM_AXIS",
                                    "title": "Applications"
                                },
                                {
                                    "position": "LEFT_AXIS",
                                    "title": "Company"
                                }
                            ],
                            "domains": [
                                {
                                    "domain": {
                                        "sourceRange": {
                                            "sources": [
                                                {
                                                    "sheetId": summary_sheet_id,
                                                    "startRowIndex": 1,
                                                    "endRowIndex": company_count + 1,
                                                    "startColumnIndex": 4,
                                                    "endColumnIndex": 5
                                                }
                                            ]
                                        }
                                    }
                                }
                            ],
                            "series": [
                                {
                                    "series": {
                                        "sourceRange": {
                                            "sources": [
                                                {
                                                    "sheetId": summary_sheet_id,
                                                    "startRowIndex": 1,
                                                    "endRowIndex": company_count + 1,
                                                    "startColumnIndex": 5,
                                                    "endColumnIndex": 6
                                                }
                                            ]
                                        }
                                    },
                                    "color": {
                                        "red": 0.18,
                                        "green": 0.8,
                                        "blue": 0.44
                                    }
                                }
                            ],
                            "headerCount": 0
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": summary_sheet_id,
                                "rowIndex": month_count + 3,
                                "columnIndex": 4
                            },
                            "widthPixels": 500,
                            "heightPixels": 400
                        }
                    }
                }
            }
        }
        requests.append(companies_chart)

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
    query = " OR ".join([f'"{k}"' for k in KEYWORDS])

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

    # ── STEP 4: Create Google Sheet with two tabs ─────────────
    print("[ STEP 4 ] Creating your Google Sheet...")
    today_str = datetime.now().strftime("%b %d, %Y")
    sheet_id, app_sheet_id, summary_sheet_id = create_sheet_with_tabs(
        f"Job Applications Tracker ({today_str})"
    )

    if not sheet_id:
        print("\nCould not create the Google Sheet. Run `gws auth login` and try again.")
        return

    print("   Sheet created! Writing your data...\n")

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

    # ── Write: Top companies data for chart ───────────────────
    company_count = write_company_counts(sheet_id, applications)

    # ── Apply formatting ──────────────────────────────────────
    print("   Applying formatting...")
    format_sheet(sheet_id, app_sheet_id, summary_sheet_id, len(applications))

    # ── Create charts ─────────────────────────────────────────
    print("   Creating charts...")
    create_charts(sheet_id, summary_sheet_id, len(sorted_months), company_count)

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
    print("  Your Google Sheet is ready (2 tabs: Applications + Monthly Summary + Charts):")
    print(f"  https://docs.google.com/spreadsheets/d/{sheet_id}")
    print()


if __name__ == "__main__":
    main()
