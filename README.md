# Job Application Tracker

Automatically scans your Gmail inbox for job application confirmation emails and creates a beautiful Google Sheet with all your applications organized and visualized.

## Demo

https://github.com/harshitverma-code/job-tracker/releases/download/v1.0/demo.mov

## What You'll Get

A Google Sheet with:
- **Applications tab**: Every job application with date, company, position, subject line, and sender
- **Monthly Summary tab**: Month-by-month stats with two charts:
  - Applications by month (bar chart)
  - Top companies you've applied to (bar chart)

---

## Setup Guide (Start Here)

This guide assumes you're on a Mac. Follow each step in order.

### Step 1: Open Terminal

Press `Cmd + Space`, type **Terminal**, and press Enter.

You'll see a window with a command prompt. This is where you'll type all the commands below.

### Step 2: Install Homebrew (Package Manager)

Homebrew helps you install software easily. Paste this command and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts. When it's done, it may tell you to run additional commands — copy and run those too.

To verify it worked:
```bash
brew --version
```
You should see a version number like `Homebrew 4.x.x`.

### Step 3: Install Python

```bash
brew install python
```

Verify it worked:
```bash
python3 --version
```
You should see `Python 3.x.x`.

### Step 4: Install the Google Workspace CLI (gws)

This tool lets the script access your Gmail and Google Sheets.

```bash
brew install anthropics/tap/gws
```

Verify it worked:
```bash
gws --version
```

### Step 5: Set Up Google Cloud Project

You need to create a Google Cloud project and enable the Gmail and Sheets APIs. This is free.

#### 5a. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Sign in with your Google account (the one with your job application emails)
3. Click the project dropdown at the top (might say "Select a project")
4. Click **New Project**
5. Name it `job-tracker` and click **Create**
6. Wait for it to create, then select it from the dropdown

#### 5b. Enable the APIs

1. Go to [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com) and click **Enable**
2. Go to [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com) and click **Enable**

#### 5c. Configure OAuth Consent Screen

1. Go to [OAuth Consent Screen](https://console.cloud.google.com/apis/credentials/consent)
2. Select **External** and click **Create**
3. Fill in:
   - App name: `Job Tracker`
   - User support email: your email
   - Developer contact email: your email
4. Click **Save and Continue**
5. On Scopes page, click **Add or Remove Scopes**
6. Search and select:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/spreadsheets`
7. Click **Update**, then **Save and Continue**
8. On Test Users page, click **Add Users**
9. Add your own email address
10. Click **Save and Continue**, then **Back to Dashboard**

#### 5d. Create OAuth Credentials

1. Go to [Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `Job Tracker Desktop`
5. Click **Create**
6. Click **Download JSON**
7. Rename the downloaded file to `credentials.json`
8. Move it to your home folder:
   ```bash
   mv ~/Downloads/credentials.json ~/.gws/credentials.json
   ```

   (If the `.gws` folder doesn't exist, create it first: `mkdir -p ~/.gws`)

### Step 6: Authenticate with Google

Run this command to log in:

```bash
gws auth login
```

Your browser will open. Sign in with your Google account and click **Allow** to grant access.

You should see "Authentication successful" in the terminal.

### Step 7: Download the Script

**Option A: Download ZIP (easiest)**

1. Go to [this repository](https://github.com/harshitverma-code/job-tracker)
2. Click the green **Code** button
3. Click **Download ZIP**
4. Unzip the downloaded file
5. Move the `job-tracker-main` folder to your Desktop
6. Rename it to `job-tracker`

**Option B: Clone with Git**

If you have git installed, run:

```bash
cd ~/Desktop
git clone https://github.com/harshitverma-code/job-tracker.git
cd job-tracker
```

---

## Running the Tracker

Once setup is complete, run the script:

```bash
cd ~/Desktop/job-tracker
python3 job_tracker.py
```

The script will:
1. Search your Gmail for application confirmation emails
2. Filter out non-job emails (DMV, apartments, etc.)
3. Create a Google Sheet with all your applications
4. Add charts for visualization

When finished, it will print a link to your Google Sheet.

---

## Running It Again Later

Whenever you want to update your tracker with new applications:

```bash
cd ~/Desktop/job-tracker
python3 job_tracker.py
```

The script updates the same Google Sheet each time — no duplicate files.

---

## Troubleshooting

### "command not found: brew"
Run the Homebrew install command from Step 2 again, and make sure to run the extra commands it shows at the end.

### "command not found: gws"
Try:
```bash
brew install anthropics/tap/gws
```

### "Authentication required" or credentials error
Re-run:
```bash
gws auth login
```

### "Access Denied" or "App not verified"
Make sure you added yourself as a test user in Step 5c (OAuth Consent Screen).

### The script found 0 applications
- Make sure you're using the Gmail account that receives your job application emails
- Check that you have job application confirmation emails in your inbox

---

## Privacy & Security

- The script only **reads** your emails — it cannot send, delete, or modify them
- Your email content is processed locally on your computer
- The only data sent to Google is the sheet creation request
- Your credentials stay on your computer in `~/.gws/`

---

## Questions?

Open an issue on this repository if you run into problems.
