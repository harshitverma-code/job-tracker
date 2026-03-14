# Job Application Tracker

Automatically scans your Gmail inbox for job application confirmation emails and creates a Google Sheet with:
- **Applications tab**: Master list of all applications with date, company, position, and sender
- **Monthly Summary tab**: Month-by-month breakdown with charts

## Features

- Intelligent email filtering to identify job applications
- Extracts company names from email headers (handles ATS platforms like Greenhouse, Lever, etc.)
- Extracts job positions from subject lines
- Creates visualizations:
  - Monthly applications column chart
  - Top companies by applications bar chart

## Prerequisites

- Python 3
- [gws CLI](https://github.com/anthropics/gws) authenticated with Gmail and Google Sheets access

## Usage

```bash
python3 job_tracker.py
```

The script will:
1. Search your Gmail for application confirmation emails
2. Filter out non-job emails (DMV, apartments, etc.)
3. Create a new Google Sheet with all your applications
4. Generate charts for visualization

## Output

A Google Sheet with two tabs:
- **Applications**: Date, Company, Position, Subject, Sender
- **Monthly Summary**: Month, Applications count, Unique Companies count, plus embedded charts
