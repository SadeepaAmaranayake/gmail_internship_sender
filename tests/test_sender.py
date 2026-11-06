import csv
import importlib.util
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "sender.py"
SPEC = importlib.util.spec_from_file_location("sender", MODULE_PATH)
sender = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sender)


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv_path = self.root / "recipients.csv"
        self.log_path = self.root / "sent_log.csv"
        self.template_path = self.root / "template.txt"
        self.pdf_path = self.root / "cv.pdf"
        self.template_path.write_text("Dear {name}, apply to {company}.", encoding="utf-8")
        self.pdf_path.write_bytes(b"%PDF-1.4\nmock pdf\n%%EOF")
        self.recipient = {"company": "Example Ltd", "email": "jobs@example.com", "name": "Hiring Team"}

    def tearDown(self):
        self.temp.cleanup()

    def test_csv_validation_and_normalization(self):
        with self.csv_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["company", "email", "name"])
            writer.writeheader()
            writer.writerow(self.recipient)
        with patch.object(sender, "CSV_FILE", self.csv_path), patch.object(sender, "MAX_RECIPIENTS", 10):
            self.assertEqual(sender.load_recipients()[0]["email"], "jobs@example.com")

        with self.csv_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["company", "email"])
            writer.writeheader()
            writer.writerow({"company": "Bad", "email": "not-an-email"})
        with patch.object(sender, "CSV_FILE", self.csv_path), patch.object(sender, "MAX_RECIPIENTS", 10):
            with self.assertRaisesRegex(ValueError, "invalid email"):
                sender.load_recipients()

    def test_company_placeholder_replaced(self):
        self.assertEqual(sender.render_body("At {company}, {name}", self.recipient), "At Example Ltd, Hiring Team")

    def test_duplicate_detection_from_sent_history(self):
        with self.log_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["company", "email", "status"])
            writer.writeheader()
            writer.writerow({"company": "EXAMPLE LTD", "email": "JOBS@example.com", "status": "sent"})
            writer.writerow({"company": "Other", "email": "x@example.com", "status": "failed"})
        with patch.object(sender, "LOG_FILE", self.log_path):
            self.assertIn(("example ltd", "jobs@example.com"), sender.load_sent_keys())
            self.assertNotIn(("other", "x@example.com"), sender.load_sent_keys())

    def test_attachment_is_pdf_and_attached(self):
        with patch.object(sender, "CV_FILE", self.pdf_path):
            sender.validate_attachment()
            message = sender.make_message(self.recipient, "At {company}")
        attachment = next(message.iter_attachments())
        self.assertIsInstance(message, EmailMessage)
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(attachment.get_filename(), "cv.pdf")
        self.assertEqual(attachment.get_payload(decode=True), self.pdf_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
