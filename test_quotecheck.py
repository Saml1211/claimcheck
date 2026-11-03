"""Offline checks for quote fidelity, transport failures and safe HTML output."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import claimcheck as cc
import quotecheck as qc

CITATION = "youtube/2q2cmwjxink"


def row(quote="quarter notes", citation=CITATION):
    return {"citation": citation, "quote": quote}


class QuoteTests(unittest.TestCase):
    def test_exact_and_whitespace_and_all_occurrences(self):
        body = "🙂 quarter notes\nthen quarter\n  notes again"
        result = qc.locate(body, "quarter notes")
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["count"], 2)
        self.assertEqual([m["mode"] for m in result["matches"]], ["EXACT", "WHITESPACE_ONLY"])
        for match in result["matches"]:
            self.assertEqual(body[match["start"]:match["end"]], match["text"])
        self.assertEqual(result["matches"][0]["start"], 2)

    def test_no_paraphrase_fuzzy_case_or_ellipsis(self):
        body = "Use quarter notes. Capture 25 samples, then listen."
        for quote in ["Use eighth notes.", "use quarter notes.", "Capture 26 samples",
                      "Use quarter...then listen.", "Use quarter notes!"]:
            with self.subTest(quote=quote):
                self.assertEqual(qc.locate(body, quote)["status"], "NOT_FOUND")

    def test_overlapping_occurrences_are_counted_and_capped(self):
        result = qc.locate("a" * 40, "a" * 8)
        self.assertEqual(result["count"], 33)
        self.assertEqual(len(result["matches"]), qc.MAX_MATCHES)
        self.assertEqual(result["omitted_matches"], 13)

    def test_literal_regex_characters(self):
        self.assertEqual(qc.locate("literal [a-z]+ and (.*)", "[a-z]+ and (.*)")["count"], 1)

    def test_validation_before_loading(self):
        bad = [None, [], [{}], [row("")], [row("a" * 8001)],
               [row(citation="obs-12345")], [row(citation="reddit/../../secret")],
               [row(citation="chatgpt/687305b8")], [row(citation="unknown/abcdef")],
               [dict(row(), extra="x")], [row(123)], [row(citation=123)], [row()] * 33]
        for rows in bad:
            with self.subTest(rows=str(rows)[:50]), patch.object(qc, "fetch_body") as loader:
                with self.assertRaises(ValueError):
                    qc.audit(rows, loader)
                loader.assert_not_called()

    def test_instagram_shortcode_validation_and_matching(self):
        for item_id in ("DLgdnbvx-Np", "DUPJFVvDA_2", "DLHZFI-h5g-"):
            citation = "instagram/" + item_id
            with self.subTest(citation=citation):
                with patch.object(qc, "fetch_body", return_value={
                        "status": "AVAILABLE", "body": "quarter notes", "title": "Capture"}) as loader:
                    receipt = qc.audit([row(citation=citation)], loader)
                loader.assert_called_once_with(citation)
                self.assertEqual(receipt["verdict"], "MATCHED")
                self.assertEqual(receipt["rows"][0]["matches"][0]["mode"], "EXACT")
                self.assertTrue(cc.verify_receipt(receipt))
        for item_id in ("short-id", "DLgdnbvx-NpABC", "DUPJFVvDA/2"):
            with self.subTest(item_id=item_id), patch.object(qc, "fetch_body") as loader:
                with self.assertRaises(ValueError):
                    qc.audit([row(citation="instagram/" + item_id)], loader)
                loader.assert_not_called()

    def test_deduplication_snapshot_hash_and_receipt(self):
        body = "quarter notes then eighth notes"
        with patch.object(qc, "fetch_body", return_value={"status": "AVAILABLE", "body": body, "title": "Test"}) as loader:
            result = qc.audit([row(), row("eighth notes")], loader)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(result["verdict"], "MATCHED")
        self.assertEqual(result["sources"][CITATION]["sha256"], hashlib.sha256(body.encode()).hexdigest())
        self.assertTrue(cc.verify_receipt(result))
        result["sources"][CITATION]["body"] += " changed"
        self.assertFalse(cc.verify_receipt(result))

    def test_unchecked_and_missing_not_conflated(self):
        for status, verdict in [("UNCHECKED", "UNCHECKED"), ("MISSING", "MISMATCH")]:
            with self.subTest(status=status):
                result = qc.audit([row()], lambda _: {"status": status, "detail": "test"})
                self.assertEqual(result["verdict"], verdict)
                self.assertEqual(result["rows"][0]["status"], status)
        result = qc.audit([row()], lambda _: {"status": "AVAILABLE", "body": "eighth notes", "title": "Test"})
        self.assertEqual(result["verdict"], "MISMATCH")

    def test_snapshot_budget(self):
        with patch.object(qc, "MAX_TOTAL", 5):
            result = qc.audit([row()], lambda _: {"status": "AVAILABLE", "body": "quarter notes"})
        self.assertEqual(result["verdict"], "UNCHECKED")
        self.assertNotIn("body", result["sources"][CITATION])

    def test_untrusted_source_cannot_execute_html(self):
        payload = '<script>alert("x")</script>'
        receipt = qc.audit([row(payload)], lambda _: {"status": "AVAILABLE", "body": payload, "title": payload})
        page = qc.render(receipt)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("default-src 'none'", page)
        self.assertIn("full captured body", page)
        self.assertIn("WHITESPACE", page.upper())

    def fetch(self, payload):
        response = io.BytesIO(payload)
        with patch.object(qc.urllib.request, "urlopen", return_value=response):
            return qc.fetch_body(CITATION, cc.DEFAULT_BASE_URL)

    def test_http_body_required_not_preview(self):
        for payload in [b'{"preview":"quarter notes"}', b'{"body":null}', b'{"body":"  "}', b'[]', b'not json', b'\xff']:
            with self.subTest(payload=payload):
                self.assertEqual(self.fetch(payload)["status"], "UNCHECKED")
        self.assertEqual(self.fetch(b'{"body":"quarter notes"}')["status"], "AVAILABLE")
        with patch.object(qc, "MAX_RESPONSE", 4):
            self.assertEqual(self.fetch(b'{"body":"quarter notes"}')["status"], "UNCHECKED")
        with patch.object(qc, "MAX_BODY", 4):
            self.assertEqual(self.fetch(b'{"body":"quarter notes"}')["status"], "UNCHECKED")

    def test_http_status_and_offline(self):
        for code in [404, 429, 500]:
            error = urllib.error.HTTPError("http://example", code, "test", {}, None)
            with self.subTest(code=code), patch.object(qc.urllib.request, "urlopen", side_effect=error):
                self.assertEqual(qc.fetch_body(CITATION, cc.DEFAULT_BASE_URL)["status"], "MISSING" if code == 404 else "UNCHECKED")
        with patch.object(qc.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(qc.fetch_body(CITATION, cc.DEFAULT_BASE_URL)["status"], "UNCHECKED")

    def test_output_no_overwrite_and_cli_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root / "quotes.json"
            input_file.write_text(json.dumps([row()]))
            for status, body, code in [("AVAILABLE", "quarter notes", 0), ("AVAILABLE", "eighth notes", 2),
                                       ("UNCHECKED", None, 3), ("MISSING", None, 2)]:
                out = root / (status + str(code))
                args = argparse.Namespace(file=str(input_file), out=str(out), base=cc.DEFAULT_BASE_URL)
                source = {"status": status, "body": body, "title": "Test"}
                with patch.object(qc, "fetch_body", return_value=source), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(qc.run(args), code)
                receipt_bytes = (out / "receipt.json").read_bytes()
                self.assertTrue(cc.verify_receipt(json.loads(receipt_bytes)))
                self.assertTrue((out / "index.html").exists())
                with patch.object(qc, "fetch_body") as loader, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(qc.run(args), 1)
                    loader.assert_not_called()
                self.assertEqual((out / "receipt.json").read_bytes(), receipt_bytes)


if __name__ == "__main__":
    unittest.main()
