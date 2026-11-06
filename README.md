# Gmail Internship Application Sender

This version does **not** use AI. You write one fixed email, add up to 20 recipients to a CSV, attach one CV PDF, then run the sender.

## 1. Requirements
- Windows + Python 3.12
- Gmail account
- Google Cloud OAuth Desktop credentials for Gmail API
- Your CV as a PDF

## 2. Install
Open PowerShell in this folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

## 3. Enable Gmail API and create OAuth credentials
1. Open https://console.cloud.google.com/
2. Create/select a Google Cloud project.
3. Enable **Gmail API**.
4. Configure the OAuth consent screen / Google Auth Platform. Choose **External** for a personal Gmail account (if prompted), keep **Publishing status** as Testing while developing, and add the exact Google account you will authorize as a **Test user**.
5. Create an OAuth Client ID and choose **Desktop app**.
6. Download the JSON file and save it in this folder as `credentials.json`.
7. If you get `Error 403: access_denied` saying only developer-approved testers can use the app, your signed-in account is not in the OAuth app's test-user list (or you signed into a different Google account). In Google Cloud Console, open Google Auth Platform → Audience → Test users, add the account, save, wait briefly for the change to propagate, then retry. Do not try to bypass Google's restriction.
8. Run `python sender.py auth` and sign in with that same test account.

Official Google guide: https://developers.google.com/workspace/gmail/api/quickstart/python

The app requests only the Gmail `gmail.send` permission. Keep `credentials.json`, `token.json`, and `.env` private; they are excluded by `.gitignore`. The token may include a refresh token and should be treated like a password. If credentials or tokens are exposed, delete/revoke them in Google Account security and Google Cloud, then create fresh credentials and authenticate again. Gmail's `gmail.send` is a restricted scope; production deployment or broad use may require Google verification. This personal local app does not claim verified production status.

## 4. Add your CV
Put your PDF here:

`attachments/cv.pdf`

## 5. Write the fixed email
Edit `email_template.txt`. You can use these placeholders:
- `{name}` recipient name, if supplied
- `{company}` company name

Example:
`Dear {name},`
If no name is supplied, the program uses `Dear Hiring Team,`.

## 6. Add up to 10 recipients
Edit `data/recipients.csv`. Example:

```csv
company,email,name
Example Company,careers@example.com,Recruitment Team
Another Company,hr@example.com,
```

Use only verified, publicly listed recruitment contacts or addresses that have invited applications. Do not guess addresses. Check whether the organisation requires applications through an online portal instead.

## 7. Preview before sending
```powershell
python sender.py preview
```
Preview validates and shows every unsent recipient message and the CV filename. It does not connect to Gmail.

## 8. Run local dry-run checks
```powershell
python -m unittest discover -s tests -v
python sender.py dry-run
```
These commands do not send email. Dry-run checks the CSV, duplicate history, `{company}` replacement, and that the attached file is a PDF. The tests use a temporary mock PDF.

## 9. Send
```powershell
python sender.py send
```

The program shows the recipient list and asks you to type exactly:

`SEND THESE APPLICATIONS`

It then sends separate one-to-one emails, each with the same CV attachment. It does not expose the other recipients to each other.

## 10. Prevent duplicate sending
Successful sends are recorded in `data/sent_log.csv`. On later runs, the same company + email combination is skipped. Do not delete this file unless you intentionally want to reset the history.

## Important
- The sender defaults to a maximum of 20 recipients per run. Set `MAX_RECIPIENTS` in `.env` to a value from 1 to 20; values above 20 are rejected.
- It waits 12 seconds between messages.
- Gmail sending limits and anti-abuse checks still apply. Do not use this to evade limits or send unsolicited repeated messages.
- If Gmail reports a quota, spam, or abuse warning, stop.
- A successful Gmail API response means Gmail accepted the message for sending; it does not guarantee the recipient received or read it.
# gmail_internship_sender
