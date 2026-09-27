"""claimcheck unit tests — stdlib unittest only, zero network.

The resolver is injected everywhere a test needs one; the only network
touches are the live verification steps in the receipt script.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claimcheck as cc

HERE = os.path.dirname(os.path.abspath(__file__))

UUID_A = "67e77ede-b5c4-8008-81af-bc032ed5311c"   # real chatgpt id
UUID_B = "aa16767a-2c9d-4868-be7a-6f2ef4000e8e"   # real perplexity id


class TestIdShapes(unittest.TestCase):
    def test_uuid_sources(self):
        self.assertTrue(cc.id_matches("chatgpt", UUID_A))
        self.assertTrue(cc.id_matches("perplexity", UUID_B))
        self.assertFalse(cc.id_matches("chatgpt", "not-a-uuid"))
        self.assertFalse(cc.id_matches("chatgpt", UUID_A.upper()))

    def test_reddit_ids(self):
        for good in ("1v35x85", "1oyxtgq", "1mx3k2a"):
            self.assertTrue(cc.id_matches("reddit", good))
        for bad in ("1V35X85", "1v3", "1234567890a"):
            self.assertFalse(cc.id_matches("reddit", bad))

    def test_instagram_ids(self):
        self.assertTrue(cc.id_matches("instagram", "DPpgI3CCBvj"))
        self.assertTrue(cc.id_matches("instagram", "DQ7hsrjEj3d"))
        self.assertFalse(cc.id_matches("instagram", "short"))

    def test_hex16_sources(self):
        for source in ("github-stars", "bookmarks", "obsidian-vault"):
            self.assertTrue(cc.id_matches(source, "005b02fde07f21fe"))
            self.assertFalse(cc.id_matches(source, "005b02fde07f21zz"))
            self.assertFalse(cc.id_matches(source, "0b8b6e51"))  # too short

    def test_notes_allows_16_and_8(self):
        self.assertTrue(cc.id_matches("notes", "0b8b6e51"))
        self.assertTrue(cc.id_matches("notes", "1c78c124699bc5ec"))

    def test_threads_snowflake(self):
        self.assertTrue(cc.id_matches("threads", "3592335335485057449"))
        self.assertFalse(cc.id_matches("threads", "123"))

    def test_voice_memo_stamp(self):
        self.assertTrue(cc.id_matches("voice-memos", "20260705 104334-CCEAA1AB"))
        self.assertFalse(cc.id_matches("voice-memos", "20260705"))


class TestExtraction(unittest.TestCase):
    def test_prefixed_uuid(self):
        text = "Grounded in chatgpt/67e77ede-b5c4-8008-81af-bc032ed5311c per the PRD."
        cites = cc.extract_citations(text)
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0].source, "chatgpt")
        self.assertEqual(cites[0].item_id, UUID_A)

    def test_many_sources_one_doc(self):
        text = (
            "A: chatgpt/{u1}; B: perplexity/{u2}; C: obs-23546; "
            "D: reddit/1oyxtgq; E: instagram/DPpgI3CCBvj; "
            "F: github-stars/5933ed96ca275a10; G: notes/0b8b6e51; "
            "H: threads/3592335335485057449."
        ).format(u1=UUID_A, u2=UUID_B)
        cites = cc.extract_citations(text)
        self.assertEqual(len(cites), 8)
        self.assertEqual([c.source for c in cites],
                         ["chatgpt", "perplexity", "", "reddit", "instagram",
                          "github-stars", "notes", "threads"])

    def test_bare_obs_not_double_counted(self):
        text = "claude-mem-work/obs-31612 and a bare obs-23546 and obs-31612 again"
        cites = cc.extract_citations(text)
        obs = [c for c in cites if c.item_id.startswith("obs-")]
        # prefixed token + each separate bare occurrence, none double-counted
        self.assertEqual(len(obs), 3)
        self.assertEqual([c.item_id for c in obs],
                         ["obs-31612", "obs-23546", "obs-31612"])
        self.assertEqual(len(cites), 3)

    def test_short_id_classified_advisory(self):
        text = "from chatgpt/67b66ffe (the 8-hex form the seams use)"
        cites = cc.extract_citations(text)
        cc.resolve_citations(cites, cc._NullResolver({}))
        self.assertEqual(cites[0].status, "SHORT-ID")
        self.assertEqual(cites[0].detail, "abbreviated id; the library only resolves full ids")
        table = {("chatgpt", "67b66ffe"): ("", "ok")}
        audit = cc.audit_document("x.md", cc._NullResolver(table),
                                  text="from chatgpt/67b66ffe here\n")
        self.assertEqual(audit.verdict, "PASS")  # advisory: not a hard defect

    def test_session_token(self):
        text = "see claude-mem session-1225 for the fix"
        cites = cc.extract_citations(text)
        self.assertIn("session-1225", [c.item_id for c in cites])

    def test_url_mapping(self):
        text = ("https://www.reddit.com/r/livesound/comments/1oyxtgq/name/ "
                "https://www.instagram.com/p/DPpgI3CCBvj/ "
                "https://www.youtube.com/watch?v=bNlGhOuQ8ls")
        cites = cc.extract_citations(text)
        by_id = {c.item_id: c.source for c in cites}
        self.assertEqual(by_id.get("1oyxtgq"), "reddit")
        self.assertEqual(by_id.get("DPpgI3CCBvj"), "instagram")
        self.assertEqual(by_id.get("bNlGhOuQ8ls"), "youtube")

    def test_trailing_punctuation_stripped(self):
        text = "cite (chatgpt/{u}). next".format(u=UUID_A)
        cites = cc.extract_citations(text)
        self.assertEqual(cites[0].item_id, UUID_A)

    def test_voice_memo_space_id(self):
        text = "from voice-memos/20260705 104334-CCEAA1AB came the fact"
        cites = cc.extract_citations(text)
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0].item_id, "20260705 104334-CCEAA1AB")

    def test_unknown_source_captured(self):
        text = "see twitter/12345 for the thread"
        cites = cc.extract_citations(text)
        self.assertEqual(cites[0].source, "twitter")
        self.assertEqual(cites[0].status, "unresolved")


class TestResolution(unittest.TestCase):
    def _resolver(self, table=None):
        return cc._NullResolver(table or {})

    def test_found_and_missing(self):
        r = self._resolver({("chatgpt", UUID_A): ("Pro AV Role", "ok")})
        cites = cc.extract_citations(f"chatgpt/{UUID_A} and reddit/1oyxtgq")
        cc.resolve_citations(cites, r)
        statuses = {c.item_id: c.status for c in cites}
        self.assertEqual(statuses[UUID_A], "FOUND")
        self.assertEqual(statuses["1oyxtgq"], "MISSING")

    def test_bare_obs_falls_back_to_mac_corpus(self):
        r = self._resolver({("claude-mem-work", "obs-31612"): ("", "not found"),
                            ("claude-mem", "obs-31612"): ("worked", "ok")})
        cites = cc.extract_citations("obs-31612")
        cc.resolve_citations(cites, r)
        self.assertEqual(cites[0].status, "FOUND")
        self.assertEqual(cites[0].resolved_source, "claude-mem")

    def test_bare_obs_not_anywhere(self):
        r = self._resolver({})
        cites = cc.extract_citations("obs-999999")
        cc.resolve_citations(cites, r)
        self.assertEqual(cites[0].status, "MISSING")

    def test_unknown_source_classified(self):
        r = self._resolver({})
        cites = cc.extract_citations("twitter/12345")
        cc.resolve_citations(cites, r)
        self.assertEqual(cites[0].status, "UNKNOWN-SOURCE")

    def test_malformed_id_classified(self):
        r = self._resolver({})
        cites = cc.extract_citations("reddit/1V35X85UPPER")
        cc.resolve_citations(cites, r)
        self.assertEqual(cites[0].status, "MALFORMED")

    def test_unchecked_on_network_failure(self):
        class Boom:
            def lookup(self, source, item_id):
                return "", "unreachable: boom"

        cites = cc.extract_citations(f"chatgpt/{UUID_A}")
        cc.resolve_citations(cites, Boom())
        self.assertEqual(cites[0].status, "UNCHECKED")


class TestAuditIntegration(unittest.TestCase):
    def _doc(self, text):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_pass_verdict(self):
        path = self._doc(f"# Good\n\nSee chatgpt/{UUID_A} for the PRD.\n\nAll cited.\n")
        table = {("chatgpt", UUID_A): ("PRD", "ok")}
        result = cc.audit_document(path, cc._NullResolver(table))
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.found, 1)
        self.assertEqual(result.missing + result.malformed + result.unknown_source, 0)
        os.unlink(path)

    def test_fail_verdict(self):
        path = self._doc(f"# Bad\n\nSee chatgpt/{UUID_A} for the PRD and reddit/1oyxtgq.\n")
        table = {("chatgpt", UUID_A): ("PRD", "ok")}
        result = cc.audit_document(path, cc._NullResolver(table))
        self.assertEqual(result.verdict, "FAIL")
        self.assertEqual(result.found, 1)
        self.assertEqual(result.missing, 1)
        os.unlink(path)

    def test_paragraph_coverage_advisory(self):
        path = self._doc(f"para one has chatgpt/{UUID_A}\n\npara two has no citation\n\npara three also bare\n")
        table = {("chatgpt", UUID_A): ("PRD", "ok")}
        result = cc.audit_document(path, cc._NullResolver(table))
        self.assertEqual(result.total_paragraphs, 3)
        self.assertEqual(result.cited_paragraphs, 1)
        self.assertEqual(result.uncited_paragraphs, 2)
        self.assertEqual(len(result.uncited_preview), 2)
        # advisory only: verdict unaffected
        self.assertEqual(result.verdict, "PASS")
        os.unlink(path)

    def test_determinism(self):
        path = self._doc(f"chatgpt/{UUID_A} and perplexity/{UUID_B} and obs-23546\n")
        table = {("chatgpt", UUID_A): ("A", "ok"),
                 ("perplexity", UUID_B): ("B", "ok"),
                 ("claude-mem-work", "obs-23546"): ("C", "ok")}
        r1 = cc.audit_document(path, cc._NullResolver(table))
        r2 = cc.audit_document(path, cc._NullResolver(table))
        self.assertEqual(json.dumps(cc.build_receipt(r1, "t"), sort_keys=True),
                         json.dumps(cc.build_receipt(r2, "t"), sort_keys=True))
        os.unlink(path)


class TestReceipt(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        payload = {"tool": "claimcheck", "verdict": "PASS", "rows": [{"a": 1}]}
        signed = cc.sign_receipt(payload)
        self.assertIn("signature", signed)
        self.assertTrue(cc.verify_receipt(signed))

    def test_tamper_detected(self):
        payload = {"tool": "claimcheck", "verdict": "PASS", "rows": []}
        signed = cc.sign_receipt(payload)
        signed["verdict"] = "FAIL"
        self.assertFalse(cc.verify_receipt(signed))

    def test_signature_excluded_from_hash(self):
        p1 = {"tool": "t", "rows": [1, 2]}
        p2 = {"tool": "t", "rows": [1, 2]}
        s1 = cc.sign_receipt(dict(p1))
        s2 = cc.sign_receipt(dict(p2))
        self.assertEqual(s1["signature"], s2["signature"])


class TestCli(unittest.TestCase):
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "cli.py"), *argv],
            capture_output=True, text=True, timeout=60)

    def test_demo_default_exit_zero(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])

    def test_bad_fixture_exit_two(self):
        proc = self._run("audit", "--file",
                         os.path.join(HERE, "examples", "bad_sample.md"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("FAIL", proc.stdout)

    def test_missing_file_usage(self):
        proc = self._run("audit", "--file", "/nonexistent/nope.md")
        self.assertEqual(proc.returncode, 1)

    def test_selfcheck(self):
        proc = self._run("selfcheck")
        self.assertEqual(proc.returncode, 0)

    def test_resolve_usage_error(self):
        proc = self._run("resolve", "no-slash-token")
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)