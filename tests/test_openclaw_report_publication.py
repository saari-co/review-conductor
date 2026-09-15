#!/usr/bin/env python3
"""Offline native collector → bridge → real check client publication regressions."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_conductor as core
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import review_result_projection as projection
import test_review_conductor_profiles as profiles
import test_review_conductor_userland as legacy


class RecordingTransportClient(runtime.GitHubAppClient):
    """Exercise production create/update/URL validation with only HTTP replaced."""
    def __init__(self, config):
        super().__init__(config, "unused-offline-key")
        self.checks = {}
        self.calls = []
        self.read_mutant = None

    def _call(self, method, path, payload, **kwargs):
        operation = self._allow(method, path)
        self.calls.append((method, path, copy.deepcopy(payload)))
        if operation == "check-create":
            check_id = 1000 + len(self.checks)
            result = {**payload, "id": check_id, "app": {"id": self._app["app_id"]},
                      "html_url": f"https://github.com/{self.repository}/runs/{check_id}",
                      "details_url": "https://github.com/saari-co/review-conductor"}
            self.checks[check_id] = result
        elif operation in {"check-read", "check-update"}:
            check_id = int(path.rsplit("/", 1)[1])
            if method == "PATCH":
                self.checks[check_id].update(copy.deepcopy(payload))
            result = copy.deepcopy(self.checks[check_id])
            if method == "GET" and self.read_mutant:
                result.update(self.read_mutant)
        else:
            raise AssertionError(operation)
        return copy.deepcopy(result)

    def remove_ready_label(self, pr, **kwargs):
        pass

    def remove_owned_label(self, pr, name, **kwargs):
        pass

    def list_issue_labels(self, pr, **kwargs):
        return []


class OriginalReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = profiles.ProfilesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.config()
        self.action = self.fixture.enqueue(self.config)
        self.request_id = json.loads(self.action["payload_json"])["queue_request_id"]
        self.run = legacy.openclaw_run_fixture(self.fixture.root, self.request_id)
        self.fixture.write_openclaw_status(self.run, self.action, self.config)
        self.artifact_path = Path(self.config["spark"]["terminal_inbox"]) / f"{self.request_id}.terminal.json"
        self.client = RecordingTransportClient(self.config)

    def collect(self, raw):
        if raw is not None:
            (self.run / "review_output.txt").write_bytes(raw)
        result = userland.collect_openclaw_terminals(
            self.config, dry_run=False, runner=legacy.SparkStatusRunner(self.run)
        )
        self.assertEqual(result[0]["result"], "terminal_materialized")
        return json.loads(self.artifact_path.read_text())

    def project(self):
        runtime.reconcile_projection(self.config, self.client, pr_number=7)
        return next(value for value in self.client.checks.values() if value["name"] == "OpenClaw Review Rail")

    def test_real_collector_bridge_check_roundtrip_and_same_check_idempotency(self):
        raw = b"overall: patch is correct\nScope limitation: dependency was unavailable.\n```\n[untrusted](https://example.test)\n"
        self.project()  # Genuine queued check identity precedes native completion.
        artifact = self.collect(raw)
        report = artifact["original_report"]
        self.assertEqual(Path(report["ref"]).read_bytes(), raw)
        self.assertEqual(report["sha256"], hashlib.sha256(raw).hexdigest())
        runtime.bridge_openclaw(self.config, self.artifact_path)
        check = self.project()
        self.assertEqual(check["conclusion"], "success")
        self.assertEqual(check["details_url"], check["html_url"])
        self.assertIn(raw.decode(), check["output"]["text"])
        self.assertIn(report["sha256"], check["output"]["text"])
        self.assertIn("Merge authorized: no", check["output"]["text"])
        self.assertIn("~~~text\n" + raw.decode(), check["output"]["text"])
        previous = copy.deepcopy(check)
        self.project()
        self.assertEqual(previous, self.client.checks[check["id"]])
        self.assertEqual(sum(method == "POST" for method, _, _ in self.client.calls), 2)

    def test_late_created_check_persists_id_before_link_update(self):
        self.collect(b"Report completed before first projection.\n")
        runtime.bridge_openclaw(self.config, self.artifact_path)
        first = self.project()
        check_id = first["id"]
        self.assertIn("Report completed before first projection.", first["output"]["text"])
        # No second mutation is hidden inside non-idempotent creation.
        self.assertFalse(any(method == "PATCH" for method, _, _ in self.client.calls))
        linked = self.project()
        self.assertEqual(linked["id"], check_id)
        self.assertEqual(linked["details_url"], linked["html_url"])
        self.assertEqual(sum(method == "POST" for method, _, _ in self.client.calls), 2)

    def test_missing_report_bridge_and_check_are_honest(self):
        self.project()
        self.collect(None)
        runtime.bridge_openclaw(self.config, self.artifact_path)
        check = self.project()
        self.assertEqual(check["conclusion"], "success")  # Native verdict is independent.
        self.assertEqual(check["details_url"], check["html_url"])
        self.assertIn("Original report unavailable: missing", check["output"]["text"])
        self.assertNotIn("complete native output", check["output"]["text"])

    def test_wrong_epoch_request_digest_and_path_rejected_before_acceptance(self):
        artifact = self.collect(b"Original report.\n")
        for patch in ({"review_epoch": 1}, {"request_id": "wrong-request"},
                      {"head_sha": "3" * 40}, {"base_sha": "4" * 40},
                      {"original_report": {**artifact["original_report"], "sha256": "0" * 64}},
                      {"original_report": {**artifact["original_report"], "ref": str(self.run / "review_output.txt")}}):
            with self.subTest(patch=patch):
                self.artifact_path.write_text(json.dumps({**artifact, **patch}))
                with self.assertRaises((core.ContractError, runtime.RuntimeError)):
                    runtime.bridge_openclaw(self.config, self.artifact_path)
                self.assertEqual(self.fixture.state(self.config)["state"], "openclaw_queued")
        self.artifact_path.write_text(json.dumps(artifact))
        runtime.bridge_openclaw(self.config, self.artifact_path)
        Path(artifact["original_report"]["ref"]).write_text("Changed after acceptance")
        with self.assertRaises(runtime.RuntimeError):
            self.project()
        self.assertEqual(self.client.calls, [])

    def test_missing_oversized_and_invalid_reports_never_claim_complete_publication(self):
        for raw, status in ((None, "missing"), (b"x" * (core.OPENCLAW_REPORT_MAX_BYTES + 1), "oversized"),
                            (b"\xff", "invalid_text"), (b"\x00", "invalid_text"), (b"", "invalid_text")):
            with self.subTest(status=status, raw_size=len(raw) if raw else 0):
                artifact = self.collect(raw)
                self.assertEqual(artifact["original_report"], {"status": status})
                output = runtime.original_report_output(self.config, artifact)
                rendered = projection.check_output("OpenClaw Review Rail", "success", repository=profiles.REPO,
                                                    pr_number=7, head_sha=profiles.HEAD, original_report=output)
                self.assertIn(f"Original report unavailable: {status}", rendered["text"])
                self.assertNotIn("complete native output", rendered["text"])
                self.assertNotIn("details_url", rendered)

    def test_check_link_and_identity_mutants_block_patch(self):
        self.project()
        self.collect(b"Original reviewer assessment\n")
        runtime.bridge_openclaw(self.config, self.artifact_path)
        check = next(value for value in self.client.checks.values() if value["name"] == "OpenClaw Review Rail")
        mutations = [
            {"app": {"id": 123}}, {"id": check["id"] + 1}, {"id": True},
            {"name": "ClawSweeper Review Rail"}, {"head_sha": "f" * 40}, {"external_id": "other-epoch"},
            {"html_url": f"https://github.com/other/repo/runs/{check['id']}"},
            {"html_url": f"https://github.com/{profiles.REPO}/pull/7"},
            {"html_url": check["html_url"] + "?redirect=bad"},
            {"html_url": check["html_url"] + "#other"},
            {"html_url": f"https://github.com/{profiles.REPO}/runs/{check['id'] + 1}"},
            {"html_url": f"https://github.com.evil.test/{profiles.REPO}/runs/{check['id']}"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.client.read_mutant = mutation
                self.client.calls.clear()
                with self.assertRaises(runtime.GitHubApiError):
                    self.project()
                self.assertEqual([method for method, _, _ in self.client.calls], ["GET"])
        self.client.read_mutant = None
        self.assertEqual(self.project()["details_url"], check["html_url"])

    def test_closed_receipt_fields_and_legacy_omission(self):
        for report in ({"status": "available"}, {"status": "missing", "ref": "/tmp/x"},
                       {"status": "missing", "url": "https://github.com/other/repo"},
                       {"status": ["missing"]}, {"status": "invented"}):
            with self.subTest(report=report), self.assertRaises(core.ContractError):
                core.validate_original_report(report)
        artifact = self.collect(b"Do not backfill this report\n")
        del artifact["original_report"]  # An old terminal does not gain new evidence.
        self.artifact_path.write_text(json.dumps(artifact))
        runtime.bridge_openclaw(self.config, self.artifact_path)
        check = self.project()
        self.project()
        self.assertNotIn("Do not backfill", check["output"]["text"])
        self.assertFalse(any(method == "GET" for method, _, _ in self.client.calls))

    def test_literal_output_bounds_and_external_run_links_remain_closed(self):
        text = "`" * (core.OPENCLAW_REPORT_MAX_BYTES // 2) + "~" * (core.OPENCLAW_REPORT_MAX_BYTES // 2)
        report = {"status": "available", "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        output = projection.check_output("OpenClaw Review Rail", "success", repository=profiles.REPO,
                                         pr_number=7, head_sha=profiles.HEAD, original_report=report)
        self.assertLess(len(output["text"].encode()), 65535)
        self.assertIn(text, output["text"])
        with self.assertRaises(projection.ProjectionError):
            projection.require_report_url(f"https://github.com/{profiles.REPO}/runs/1000", repository=profiles.REPO, pr_number=7)
        output = projection.check_output("ClawSweeper Review Rail", "success", repository=profiles.REPO,
                                         pr_number=7, head_sha=profiles.HEAD, workflow_run_id=99)
        self.assertEqual(output["details_url"], f"https://github.com/{profiles.REPO}/actions/runs/99")

    def test_report_descriptor_survives_leaf_replacement_and_disappearance(self):
        source = self.run / "review_output.txt"
        source.write_bytes(b"this request")
        other = self.fixture.root / "unrelated.txt"
        other.write_bytes(b"unrelated local bytes")
        destination = self.fixture.root / "copied.txt"
        real_open = os.open

        def replace_after_open(path, flags, **kwargs):
            fd = real_open(path, flags, **kwargs)
            if Path(path) == source:
                source.unlink()
                source.symlink_to(other)
            return fd

        with patch.object(userland.os, "open", side_effect=replace_after_open):
            receipt = userland.preserve_openclaw_report(source, destination)
        self.assertEqual(receipt["status"], "available")
        self.assertEqual(destination.read_bytes(), b"this request")
        source.unlink()
        source.write_bytes(b"gone before open")

        def remove_before_open(path, flags, **kwargs):
            if Path(path) == source:
                source.unlink()
            return real_open(path, flags, **kwargs)

        with patch.object(userland.os, "open", side_effect=remove_before_open):
            self.assertEqual(userland.preserve_openclaw_report(source, destination), {"status": "missing"})

    def test_collector_rejects_outside_traversal_and_wrong_request_sources(self):
        outside = self.fixture.root / "unrelated"
        shutil.copytree(self.run, outside)
        (outside / "review_output.txt").write_text("unrelated local bytes")
        source_root = Path(self.config["source_root"])
        relative = self.run.relative_to(source_root)
        for proof_path in (str(outside / "PROOF.md"),
                           "../unrelated/PROOF.md",
                           str(relative / ".." / self.run.name / "PROOF.md"),
                           "runs/other-lane/spark-openclaw-autoreview-20260915T133456Z-1/PROOF.md"):
            with self.subTest(proof_path=proof_path):
                runner = legacy.SparkStatusRunner(Path(proof_path).parent)
                with self.assertRaises(userland.UserlandError):
                    userland.collect_openclaw_terminals(self.config, dry_run=False, runner=runner)
                self.assertFalse(self.artifact_path.exists())
        status = json.loads((self.run / "REQUEST_STATUS.json").read_text())
        status["id"] = "another-request"
        (self.run / "REQUEST_STATUS.json").write_text(json.dumps(status))
        with self.assertRaisesRegex(userland.UserlandError, "exact action"):
            self.collect(b"wrong request report")
        self.assertFalse(self.artifact_path.exists())

    def test_collector_accepts_transport_relative_path(self):
        (self.run / "review_output.txt").write_text("relative transport receipt")
        relative = self.run.relative_to(Path(self.config["source_root"]))
        userland.collect_openclaw_terminals(self.config, dry_run=False, runner=legacy.SparkStatusRunner(relative))
        artifact = json.loads(self.artifact_path.read_text())
        self.assertEqual(Path(artifact["original_report"]["ref"]).read_text(), "relative transport receipt")

    def test_fetch_symlinked_ancestors_and_status_proof_leaves_are_rejected(self):
        for directory in (self.run, self.run.parent, self.run.parent.parent):
            with self.subTest(directory=directory.name):
                moved = directory.with_name(directory.name + "-real")
                directory.rename(moved)
                directory.symlink_to(moved, target_is_directory=True)
                try:
                    with self.assertRaises(userland.UserlandError):
                        self.collect(None)
                    self.assertFalse(self.artifact_path.exists())
                finally:
                    directory.unlink()
                    moved.rename(directory)
        for name in ("REQUEST_STATUS.json", "PROOF.md"):
            with self.subTest(leaf=name):
                source = self.run / name
                moved = source.with_name(name + ".real")
                source.rename(moved)
                source.symlink_to(moved)
                try:
                    with self.assertRaises(userland.UserlandError):
                        self.collect(None)
                    self.assertFalse(self.artifact_path.exists())
                finally:
                    source.unlink()
                    moved.rename(source)

    def test_fetch_directory_replacement_does_not_redirect_opened_files(self):
        (self.run / "review_output.txt").write_text("original directory report")
        other = self.fixture.root / "replacement"
        shutil.copytree(self.run, other)
        (other / "review_output.txt").write_text("unrelated directory report")
        original = self.run.with_name(self.run.name + "-original")
        real_open = os.open

        def replace_opened_directory(path, flags, **kwargs):
            fd = real_open(path, flags, **kwargs)
            if str(path) == self.run.name:
                self.run.rename(original)
                self.run.symlink_to(other, target_is_directory=True)
            return fd

        with patch.object(userland.os, "open", side_effect=replace_opened_directory):
            artifact = self.collect(None)
        self.assertEqual(Path(artifact["original_report"]["ref"]).read_text(), "original directory report")

    def test_projection_distinguishes_empty_text_from_oversized_report(self):
        for text, error in ((None, "non-empty text"), ("", "non-empty text"),
                            (" \n\t", "non-empty text"),
                            ("x" * (core.OPENCLAW_REPORT_MAX_BYTES + 1), "exceeds its text bound")):
            with self.subTest(text_length=len(text) if text is not None else None):
                report = {"status": "available", "text": text, "sha256": "0" * 64}
                with self.assertRaisesRegex(projection.ProjectionError, error):
                    projection.check_output("OpenClaw Review Rail", "success", repository=profiles.REPO,
                                            pr_number=7, head_sha=profiles.HEAD, original_report=report)

    def test_nonregular_report_fails_before_read_without_fifo_blocking(self):
        source = self.run / "review_output.txt"
        for make_source in (source.mkdir, lambda: os.mkfifo(source)):
            make_source()
            try:
                with self.assertRaisesRegex(userland.UserlandError, "regular non-symlink"):
                    self.collect(None)
                self.assertFalse(self.artifact_path.exists())
            finally:
                if source.is_dir():
                    source.rmdir()
                else:
                    source.unlink()

    def test_collector_rejects_symlinks(self):
        other = self.fixture.root / "unrelated.txt"
        other.write_text("not this request")
        (self.run / "review_output.txt").symlink_to(other)
        with self.assertRaises(userland.UserlandError):
            self.collect(None)
        self.assertFalse(self.artifact_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
