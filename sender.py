import argparse
import base64
import csv
import os
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CREDS_FILE = ROOT / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = ROOT / os.getenv("GOOGLE_TOKEN_FILE", "token.json")
CV_FILE = ROOT / os.getenv("CV_PATH", "attachments/cv.pdf")
CSV_FILE = ROOT / os.getenv("RECIPIENTS_CSV", "data/recipients.csv")
TEMPLATE_FILE = ROOT / os.getenv("EMAIL_TEMPLATE", "email_template.txt")
MAX_RECIPIENTS = int(os.getenv("MAX_RECIPIENTS", "10"))
DELAY = int(os.getenv("SEND_DELAY_SECONDS", "12"))
if not 1 <= MAX_RECIPIENTS <= 20:
    raise ValueError("MAX_RECIPIENTS must be between 1 and 20.")
if DELAY < 0:
    raise ValueError("SEND_DELAY_SECONDS cannot be negative.")
LOG_FILE = ROOT / "data" / "sent_log.csv"


def gmail_service():
    credentials = None
    if TOKEN_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except (ValueError, KeyError) as error:
            raise RuntimeError(f"OAuth token file is invalid: {TOKEN_FILE}. Remove it and run 'auth' again.") from error

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        except RefreshError as error:
            raise RuntimeError(
                "Google rejected the saved token. Delete token.json and run 'auth' again; "
                "confirm this Google account is listed as an OAuth test user."
            ) from error

    if not credentials or not credentials.valid:
        if not CREDS_FILE.exists():
            raise FileNotFoundError(
                f"OAuth credentials missing: {CREDS_FILE}\n"
                "Download a Google OAuth Desktop credentials JSON and save it as credentials.json."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
        credentials = flow.run_local_server(port=0)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=credentials)


def load_recipients():
    if not CSV_FILE.exists():
        raise FileNotFoundError(
            f"Recipients CSV not found: {CSV_FILE}\n"
            "Copy data/recipients.example.csv to data/recipients.csv and edit it."
        )

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("The recipients CSV is empty.")
    if not {"company", "email"}.issubset(set(rows[0].keys())):
        raise ValueError("The recipients CSV must contain company and email columns (name is optional).")
    if len(rows) > MAX_RECIPIENTS:
        raise ValueError(f"This run is limited to {MAX_RECIPIENTS} recipients. Split the list into batches.")

    clean = []
    seen = set()
    for row_number, row in enumerate(rows, start=2):
        company = (row.get("company") or "").strip()
        email = (row.get("email") or "").strip()
        name = (row.get("name") or "").strip()
        if not company or not email:
            raise ValueError(f"CSV row {row_number}: company and email are required.")
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as error:
            raise ValueError(f"CSV row {row_number}: invalid email address: {error}")
        key = (company.casefold(), email.casefold())
        if key in seen:
            raise ValueError(f"Duplicate recipient in CSV: {company} / {email}")
        seen.add(key)
        clean.append({"company": company, "email": email, "name": name})
    return clean


def load_template():
    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(f"Email template not found: {TEMPLATE_FILE}")
    template = TEMPLATE_FILE.read_text(encoding="utf-8").strip()
    if not template or "{company}" not in template:
        raise ValueError("Email template must be non-empty and contain the {company} placeholder.")
    return template


def load_sent_keys():
    if not LOG_FILE.exists():
        return set()
    with LOG_FILE.open("r", encoding="utf-8", newline="") as file:
        return {
            (r["company"].casefold(), r["email"].casefold())
            for r in csv.DictReader(file)
            if r.get("status") == "sent"
        }


def load_attempted_keys():
    if not LOG_FILE.exists():
        return set()
    with LOG_FILE.open("r", encoding="utf-8", newline="") as file:
        return {
            (r["company"].casefold(), r["email"].casefold())
            for r in csv.DictReader(file)
            if r.get("status") in {"failed", "uncertain"}
        }


def log_result(recipient, status, message_id="", error=""):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    exists = LOG_FILE.exists()
    with LOG_FILE.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=["timestamp_utc", "company", "email", "status", "gmail_message_id", "error"]
        )
        if not exists:
            writer.writeheader()
        writer.writerow({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "company": recipient["company"],
            "email": recipient["email"],
            "status": status,
            "gmail_message_id": message_id,
            "error": error,
        })


def render_body(template, recipient):
    name = recipient["name"] or "Hiring Team"
    return template.replace("{name}", name).replace("{company}", recipient["company"])


def make_message(recipient, template):
    message = EmailMessage()
    message["To"] = recipient["email"]
    message["Subject"] = f"Application for Network Systems Internship - {recipient['company']}"
    message.set_content(render_body(template, recipient))

    if not CV_FILE.exists():
        raise FileNotFoundError(f"CV PDF not found: {CV_FILE}")
    if CV_FILE.suffix.lower() != ".pdf":
        raise ValueError("CV attachment must be a PDF file.")

    message.add_attachment(
        CV_FILE.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=CV_FILE.name,
    )
    return message


def validate_attachment():
    if not CV_FILE.is_file():
        raise FileNotFoundError(f"CV PDF not found: {CV_FILE}")
    if CV_FILE.suffix.lower() != ".pdf":
        raise ValueError("CV attachment must be a PDF file.")
    if CV_FILE.stat().st_size == 0 or CV_FILE.read_bytes()[:5] != b"%PDF-":
        raise ValueError(f"CV attachment does not look like a valid PDF: {CV_FILE}")


def preview():
    recipients = load_recipients()
    template = load_template()
    sent = load_sent_keys()

    print(f"\nRecipients in this batch: {len(recipients)}")
    for item in recipients:
        marker = "ALREADY SENT - WILL SKIP" if (item["company"].casefold(), item["email"].casefold()) in sent else "READY"
        print(f"- {item['company']} | {item['email']} | {marker}")

    validate_attachment()
    for item in recipients:
        if (item["company"].casefold(), item["email"].casefold()) in sent:
            continue
        message = make_message(item, template)
        print(f"\n--- EMAIL PREVIEW: {item['company']} ---")
        print("To:", item["email"])
        print("Subject:", message["Subject"])
        print(message.get_body().get_content())
        print(f"Attachment: {CV_FILE.name} ({CV_FILE.stat().st_size} bytes)")
    print("\nNo email has been sent.")


def send():
    recipients = load_recipients()
    template = load_template()
    validate_attachment()

    sent_keys = load_sent_keys()
    attempted_keys = load_attempted_keys()
    for recipient in recipients:
        key = (recipient["company"].casefold(), recipient["email"].casefold())
        if key in attempted_keys and key not in sent_keys:
            raise ValueError(
                f"Previous send attempt for {recipient['company']} / {recipient['email']} was not confirmed "
                "successful. Check Gmail Sent and data/sent_log.csv, then resolve that record manually before retrying."
            )
    ready = [
        r for r in recipients
        if (r["company"].casefold(), r["email"].casefold()) not in sent_keys
    ]

    print(f"\nThis will send {len(ready)} separate emails.")
    print("Each recipient sees only their own address.")
    print(f"CV attachment: {CV_FILE}")
    for r in ready:
        print(f"  {r['company']} -> {r['email']}")

    if not ready:
        print("No unsent recipients in this batch.")
        return

    confirmation = input("\nType SEND THESE APPLICATIONS to continue: ")
    if confirmation != "SEND THESE APPLICATIONS":
        print("Cancelled. Nothing was sent.")
        return

    service = gmail_service()
    for index, recipient in enumerate(ready):
        try:
            message = make_message(recipient, template)
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            result = service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            log_result(recipient, "sent", result.get("id", ""))
            print(f"SENT: {recipient['company']} ({recipient['email']})")
        except Exception as error:
            # A network failure can happen after Gmail accepted a message. Do not
            # mark it retry-safe: the operator must inspect Gmail Sent first.
            log_result(recipient, "uncertain", error=str(error))
            print(f"STOPPED: {recipient['company']} ({recipient['email']}): {error}")
            print("Stopping now. Check Gmail Sent and the log before trying again.")
            break

        if index < len(ready) - 1:
            time.sleep(DELAY)

    print("\nFinished. Check data/sent_log.csv and your Gmail Sent folder.")


def dry_run():
    recipients = load_recipients()
    template = load_template()
    validate_attachment()
    sent = load_sent_keys()
    pending = [r for r in recipients if (r["company"].casefold(), r["email"].casefold()) not in sent]
    for recipient in pending:
        message = make_message(recipient, template)
        attachment = next(part for part in message.iter_attachments())
        if attachment.get_content_type() != "application/pdf" or attachment.get_filename() != CV_FILE.name:
            raise ValueError("Attachment validation failed.")
        if "{company}" in message.get_body().get_content():
            raise ValueError("Unexpanded {company} placeholder remains in message.")
        print(f"DRY RUN OK: {recipient['company']} -> {recipient['email']} | {attachment.get_filename()}")
    print(f"Dry run complete: {len(pending)} message(s) validated; no email sent.")


def main():
    parser = argparse.ArgumentParser(description="Send a fixed internship application email to a small Gmail batch.")
    parser.add_argument("command", choices=["auth", "preview", "dry-run", "send"])
    args = parser.parse_args()

    try:
        if args.command == "auth":
            gmail_service()
            print("Gmail OAuth authentication completed.")
        elif args.command == "preview":
            preview()
        elif args.command == "dry-run":
            dry_run()
        elif args.command == "send":
            send()
    except (FileNotFoundError, ValueError, RuntimeError, HttpError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
