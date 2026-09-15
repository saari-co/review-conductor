#!/usr/bin/env python3
"""Offline native collector → bridge → real check client publication regressions."""
from __future__ import annotations

import copy
from contextlib import closing
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
            result = {"conclusion": None, **payload, "id": check_id, "app": {"id": self._app["app_id"]},
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

    def test_nonterminal_update_rejects_retained_terminal_conclusion(self):
        name = "ClawSweeper Review Rail"
        external = "fixture-exact-check"
        check_id = self.client.create_check(name, profiles.HEAD, external, "skipped")
        # Model the observed partial PATCH: omitted fields retain prior values.
        # HTTP 200 must not count as queued while the terminal conclusion remains.
        for state in ("queued", "in_progress"):
            with self.subTest(state=state), self.assertRaisesRegex(runtime.GitHubApiError, "nonterminal check state"):
                self.client.update_check(check_id, name, profiles.HEAD, external, state)
        self.client.update_check(check_id, name, profiles.HEAD, external, "action_required")
        self.assertEqual(self.client.checks[check_id]["conclusion"], "action_required")
        fresh_id = self.client.create_check(name, profiles.HEAD, "fixture-other-epoch", "queued")
        self.client.update_check(fresh_id, name, profiles.HEAD, "fixture-other-epoch", "in_progress")
        self.assertIsNone(self.client.checks[fresh_id]["conclusion"])

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
        self.assertFalse(any(method == "GET" and path.endswith(str(check["id"]))
                             for method, path, _ in self.client.calls))

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

    def test_publication_rejects_leaf_replacement_before_open_and_closes_descriptors(self):
        artifact = self.collect(b"request-bound report\n")
        source = Path(artifact["original_report"]["ref"])
        other = self.fixture.root / "unrelated-report.txt"
        other.write_text("unrelated bytes")
        real_open = os.open
        for replacement in ("fifo", "symlink", "directory", "missing", "oversized"):
            with self.subTest(replacement=replacement):
                opened = []
                replaced = []

                def replace_before_open(path, flags, **kwargs):
                    if str(path) == "review_output.txt":
                        # Fail the regression instead of hanging if nonblocking is lost.
                        self.assertTrue(flags & os.O_NONBLOCK)
                        self.assertTrue(flags & os.O_NOFOLLOW)
                        source.unlink()
                        if replacement == "fifo":
                            os.mkfifo(source)
                        elif replacement == "symlink":
                            source.symlink_to(other)
                        elif replacement == "directory":
                            source.mkdir()
                        elif replacement == "oversized":
                            source.write_bytes(b"x" * (core.OPENCLAW_REPORT_MAX_BYTES + 1))
                        replaced.append(True)
                    fd = real_open(path, flags, **kwargs)
                    opened.append(fd)
                    return fd

                try:
                    with patch.object(runtime.os, "open", side_effect=replace_before_open):
                        with self.assertRaises(runtime.RuntimeError):
                            runtime.original_report_output(self.config, artifact)
                    self.assertEqual(replaced, [True])
                    for fd in opened:
                        with self.assertRaises(OSError):
                            os.fstat(fd)
                finally:
                    if source.is_dir():
                        source.rmdir()
                    else:
                        source.unlink(missing_ok=True)
                    source.write_bytes(b"request-bound report\n")

    def test_publication_reads_validated_descriptor_after_leaf_replacement(self):
        raw = b"original request assessment\n"
        artifact = self.collect(raw)
        source = Path(artifact["original_report"]["ref"])
        real_open, real_fstat = os.open, os.fstat
        opened = []
        leaf = []
        replaced = []

        def track_open(path, flags, **kwargs):
            fd = real_open(path, flags, **kwargs)
            opened.append(fd)
            if str(path) == "review_output.txt":
                leaf.append(fd)
            return fd

        def replace_after_stat(fd):
            metadata = real_fstat(fd)
            if fd in leaf:
                source.unlink()
                os.mkfifo(source)
                replaced.append(True)
            return metadata

        with patch.object(runtime.os, "open", side_effect=track_open), \
                patch.object(runtime.os, "fstat", side_effect=replace_after_stat):
            output = runtime.original_report_output(self.config, artifact)
        self.assertEqual(replaced, [True])
        self.assertEqual(output["text"], raw.decode())
        self.assertEqual(output["sha256"], hashlib.sha256(raw).hexdigest())
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_publication_directory_binding_rejects_links_and_survives_replacement(self):
        artifact = self.collect(b"original directory assessment\n")
        source = Path(artifact["original_report"]["ref"])
        for directory in (source.parent, source.parent.parent):
            with self.subTest(symlinked_ancestor=directory.name):
                moved = directory.with_name(directory.name + "-original")
                directory.rename(moved)
                directory.symlink_to(moved, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(runtime.RuntimeError, "unavailable"):
                        runtime.original_report_output(self.config, artifact)
                finally:
                    directory.unlink()
                    moved.rename(directory)
        other = self.fixture.root / "unrelated-directory"
        other.mkdir()
        (other / "review_output.txt").write_text("unrelated assessment")
        original = source.parent.with_name(source.parent.name + "-original")
        real_open = os.open
        replaced = []

        def replace_directory_after_open(path, flags, **kwargs):
            fd = real_open(path, flags, **kwargs)
            if str(path) == self.request_id:
                source.parent.rename(original)
                source.parent.symlink_to(other, target_is_directory=True)
                replaced.append(True)
            return fd

        with patch.object(runtime.os, "open", side_effect=replace_directory_after_open):
            output = runtime.original_report_output(self.config, artifact)
        self.assertEqual(replaced, [True])
        self.assertEqual(output["text"], "original directory assessment\n")


class GitHubArrayTransport:
    """Only HTTP is injected: production auth, shape, ownership and writes run."""
    def __init__(self, config):
        self.app_id = config["github_app"]["app_id"]
        self.repository = config["github_app"]["repository"]
        self.checks, self.comments = {}, []
        self.labels = {"docs", "P3"}
        self.calls, self.overrides = [], {}
        self.omit_conclusion = False
        self.fail_created_check = False

    def __call__(self, method, url, headers, body, timeout):
        from urllib.parse import urlsplit, unquote
        path = urlsplit(url).path
        payload = json.loads(body) if body is not None else None
        # Never retain authentication headers, even in synthetic proof.
        self.calls.append((method, path, copy.deepcopy(payload)))
        if (method, path) in self.overrides:
            return self.overrides[(method, path)]
        status = 200
        if path.endswith("/access_tokens"):
            result = {"token": "offline-fixture", "expires_at": "2099-01-01T00:00:00Z"}
            status = 201
        elif path.endswith("/check-runs") and method == "POST":
            check_id = 1000 + len(self.checks)
            result = {"conclusion": None, **payload, "id": check_id,
                      "app": {"id": self.app_id},
                      "html_url": f"https://github.com/{self.repository}/runs/{check_id}"}
            self.checks[check_id] = copy.deepcopy(result)
            status = 201
            if self.fail_created_check:
                raise runtime.GitHubTransientError("injected uncertain check creation")
        elif "/check-runs/" in path:
            check = self.checks[int(path.rsplit("/", 1)[1])]
            if method == "PATCH":
                check.update(payload)
            result = copy.deepcopy(check)
            if method == "PATCH" and self.omit_conclusion:
                result.pop("conclusion", None)
        elif path.endswith("/labels"):
            if method == "POST":
                self.labels.update(payload["labels"])
            result = [{"id": index + 1, "name": name} for index, name in enumerate(sorted(self.labels))]
        elif "/labels/" in path and method == "DELETE":
            name = unquote(path.rsplit("/", 1)[1])
            if name not in self.labels:
                status, result = 404, {"message": "Not Found"}
            else:
                self.labels.remove(name)
                result = [{"name": label} for label in sorted(self.labels)]
        elif path.endswith("/comments"):
            if method == "POST":
                self.comments.append({"id": 900 + len(self.comments), **payload,
                                      "performed_via_github_app": {"id": self.app_id}})
                status, result = 201, self.comments[-1]
            else:
                result = self.comments
        elif "/issues/comments/" in path and method == "PATCH":
            result = next(item for item in self.comments if item["id"] == int(path.rsplit("/", 1)[1]))
            result.update(payload)
        else:
            raise AssertionError((method, path))
        return status, json.dumps(result).encode()


class ProductionProjectionRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = profiles.ProfilesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.config()
        self.action = self.fixture.enqueue(self.config)
        self.http = GitHubArrayTransport(self.config)
        self.client = runtime.GitHubAppClient(
            self.config, "unused-offline-key", transport=self.http,
            signer=lambda *args: "offline-fixture-jwt",
        )
        self.authorities = []
        self.client.set_authority_guard(lambda method, path, authority: self.authorities.append((method, path, authority)))
        self.root_path = f"/repos/{profiles.REPO}"

    def project(self):
        return runtime.reconcile_projection(self.config, self.client, pr_number=7)

    def db(self):
        return core.open_database(Path(self.config["paths"]["state_root"]), profiles.REPO)

    def prepare_ready(self):
        runtime.bridge_openclaw(self.config, self.fixture.terminal(self.config, self.action))
        claw = legacy.action(self.config, 7, "clawsweeper.dispatch")
        legacy.mark_dispatched(self.config, claw["action_id"])
        self.fixture.ingest(self.config, "workflow_run", "claw-workflow",
                            self.fixture.payload(legacy.claw_workflow_payload(801)))
        class Artifacts(legacy.FakeGitHub):
            def list_run_artifacts(self, run_id):
                return [{"id": 9001, "name": f"smcbd-suite-review-{run_id}-1", "expired": False}]
        userland.collect_clawsweeper_terminals(
            self.config, Artifacts(801, self.fixture.bundle(self.config, claw)), dry_run=False,
        )
        runtime.bridge_clawsweeper(self.config, Path(self.config["clawsweeper_bridge"]["terminal_inbox"]) / "801.terminal.json")

    def test_real_label_arrays_allow_ready_projection_one_owned_summary_and_replay(self):
        self.project()
        self.prepare_ready()
        result = self.project()
        self.assertEqual(result["projected"][0]["publication"]["result"], "published")
        self.assertEqual(len(self.http.comments), 1)
        self.assertEqual(self.http.comments[0]["performed_via_github_app"]["id"], self.http.app_id)
        self.assertIn(runtime.READY_LABEL, self.http.labels)
        self.assertTrue({"docs", "P3"} <= self.http.labels)
        self.assertFalse(result["merge_authorized"])
        with closing(self.db()) as connection:
            row = connection.execute("SELECT * FROM projections").fetchone()
            self.assertEqual(row["last_projected_state"], "ready_for_human_merge")
            self.assertEqual(row["ready_label_applied"], 1)
        repeat = self.project()
        self.assertEqual(repeat["projected"][0]["publication"]["result"], "unchanged")
        self.assertEqual(len(self.http.checks), 2)
        self.assertEqual(len(self.http.comments), 1)
        for method, path, authority in self.authorities:
            self.assertEqual(authority["repository"], profiles.REPO)
            self.assertEqual(authority["pr_number"], 7)
            self.assertEqual(authority["head_sha"], profiles.HEAD)
            self.assertEqual(authority["base_sha"], profiles.BASE)
            self.assertEqual(authority["review_epoch"], 0)

    def test_owned_and_ready_add_remove_arrays_and_idempotent_absence(self):
        label = next(name for name in projection.OWNED_LABELS if name != runtime.READY_LABEL)
        self.client.add_owned_label(7, label)
        self.client.add_ready_label(7)
        self.client.remove_owned_label(7, label)
        self.client.remove_ready_label(7)
        self.client.remove_owned_label(7, label)  # Real 404 object, not an array.
        self.client.remove_ready_label(7)
        self.assertEqual(self.http.labels, {"docs", "P3"})
        for operation in (self.client.add_owned_label, self.client.remove_owned_label):
            before = len(self.http.calls)
            with self.assertRaises(runtime.GitHubApiError):
                operation(7, "docs")
            self.assertEqual(len(self.http.calls), before)

    def test_label_shapes_statuses_and_other_object_endpoints_fail_closed(self):
        path = self.root_path + "/issues/7/labels"
        for raw in (b"{}", b"null", b"", b'["label"]', b'[{}]', b"not json"):
            self.http.overrides[("POST", path)] = (200, raw)
            with self.subTest(raw=raw), self.assertRaises(runtime.GitHubApiError):
                self.client.add_ready_label(7)
        for status in (404, 422, 500):
            self.http.overrides[("POST", path)] = (status, b"[]")
            with self.subTest(status=status), self.assertRaises(runtime.GitHubApiError):
                self.client.add_ready_label(7)
        self.http.overrides[("POST", self.root_path + "/check-runs")] = (201, b"[]")
        with self.assertRaises(runtime.GitHubApiError):
            self.client.create_check("OpenClaw Review Rail", profiles.HEAD, "fixture", "queued")
        from urllib.parse import quote
        delete_path = path + "/" + quote(runtime.READY_LABEL, safe="")
        for status, raw in ((200, b"{}"), (200, b""), (422, b"[]")):
            self.http.overrides[("DELETE", delete_path)] = (status, raw)
            with self.assertRaises(runtime.GitHubApiError):
                self.client.remove_ready_label(7)
        self.http.overrides[("DELETE", delete_path)] = (204, b"")
        self.client.remove_ready_label(7)
        self.client.set_authority_guard(lambda *args: (_ for _ in ()).throw(core.AuthorityDenied("revoked")))
        before = len(self.http.calls)
        with self.assertRaises(core.AuthorityDenied):
            self.client.add_ready_label(7)
        self.assertEqual(len(self.http.calls), before)

    def test_delayed_started_event_cannot_revive_retired_failed_workflow(self):
        runtime.bridge_openclaw(self.config, self.fixture.terminal(self.config, self.action))
        claw = legacy.action(self.config, 7, "clawsweeper.dispatch")
        legacy.mark_dispatched(self.config, claw["action_id"])
        self.fixture.ingest(self.config, "workflow_run", "late-start-failure",
                            self.fixture.payload(legacy.claw_workflow_payload(801, conclusion="failure")))
        outcomes = userland.collect_clawsweeper_terminals(self.config, legacy.EmptyGitHub(), dry_run=False)
        self.assertEqual(outcomes[0]["result"], "rail_failure_alerted")
        event = {"schema": core.INTERNAL_EVENT_SCHEMA, "event_id": "late-start",
                 "type": "clawsweeper.started", "repository": profiles.REPO, "pr_number": 7,
                 "base_sha": profiles.BASE, "head_sha": profiles.HEAD,
                 "review_epoch": claw["review_epoch"], "workflow_run_id": "801"}
        config = core.load_config(Path(self.config["core_config"]))
        with closing(self.db()) as connection:
            for _ in range(2):
                with self.assertRaisesRegex(core.ContractError, "retired terminal workflow"):
                    core.process_internal_event(connection, config, event)
                self.assertEqual(core.current_head(connection, profiles.REPO, 7)["state"], "clawsweeper_queued")
            with self.assertRaisesRegex(core.ContractError, "epoch is stale"):
                core.process_internal_event(connection, config, {**event, "review_epoch": 1})
            stale = core.process_internal_event(connection, config, {**event, "head_sha": "f" * 40})
            self.assertEqual(stale["result"], "stale")
            connection.rollback()
            # An unrelated receipt must not fence a different exact start.
            core.process_internal_event(connection, config, {**event, "workflow_run_id": "802"})
            self.assertEqual(core.current_head(connection, profiles.REPO, 7)["state"], "clawsweeper_running")
            connection.rollback()
            receipt = connection.execute("SELECT * FROM rail_workflow_runs WHERE workflow_run_id='801'").fetchone()
            self.assertEqual(receipt["status"], "terminal_attention_required")
            self.assertIsNone(receipt["bound_pr_number"])
            self.assertIsNone(receipt["verdict"])
            self.assertIsNone(receipt["proof_ref"])
        self.assertEqual(userland.collect_clawsweeper_terminals(self.config, legacy.EmptyGitHub(), dry_run=False), [])
        self.assertEqual(list(Path(self.config["clawsweeper_bridge"]["terminal_inbox"]).glob("*.json")), [])

    def test_nonterminal_readback_requires_explicit_null(self):
        check_id = self.client.create_check("ClawSweeper Review Rail", profiles.HEAD, "fixture", "queued")
        self.http.omit_conclusion = True
        for state in ("queued", "in_progress"):
            with self.assertRaisesRegex(runtime.GitHubApiError, "nonterminal check state"):
                self.client.update_check(check_id, "ClawSweeper Review Rail", profiles.HEAD, "fixture", state)
        self.http.omit_conclusion = False
        self.client.update_check(check_id, "ClawSweeper Review Rail", profiles.HEAD, "fixture", "in_progress")

    def legacy_skipped_check(self):
        self.project()
        check = next(item for item in self.http.checks.values() if item["name"] == "ClawSweeper Review Rail")
        check.update(status="completed", conclusion="skipped")
        return check

    def test_existing_owned_terminal_skip_migrates_once_then_runs_and_completes(self):
        old = self.legacy_skipped_check()
        result = self.project()
        replacement = result["projected"][0]["check_ids"][old["name"]]
        self.assertNotEqual(replacement, old["id"])
        self.assertEqual(old["conclusion"], "skipped")
        self.assertIsNone(self.http.checks[replacement]["conclusion"])
        self.assertEqual(self.http.checks[replacement]["external_id"], old["external_id"])
        self.project()
        self.assertEqual(len(self.http.checks), 3)
        with closing(self.db()) as connection:
            events = connection.execute("SELECT payload_json FROM events WHERE kind='projection.skipped_check_replacement'").fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0][0])["old_check_run_id"], old["id"])
        runtime.bridge_openclaw(self.config, self.fixture.terminal(self.config, self.action))
        claw = legacy.action(self.config, 7, "clawsweeper.dispatch")
        legacy.mark_dispatched(self.config, claw["action_id"])
        with closing(self.db()) as connection:
            core.process_internal_event(connection, core.load_config(Path(self.config["core_config"])), {
                "schema": core.INTERNAL_EVENT_SCHEMA, "event_id": "migration-start",
                "type": "clawsweeper.started", "repository": profiles.REPO, "pr_number": 7,
                "base_sha": profiles.BASE, "head_sha": profiles.HEAD,
                "review_epoch": claw["review_epoch"], "workflow_run_id": "801",
            })
            connection.commit()
        self.project()
        self.assertEqual(self.http.checks[replacement]["status"], "in_progress")
        self.assertIsNone(self.http.checks[replacement]["conclusion"])
        self.prepare_ready()
        self.project()
        self.assertEqual(self.http.checks[replacement]["conclusion"], "success")
        self.assertEqual(old["conclusion"], "skipped")
        self.assertEqual(len(self.http.comments), 1)

    def test_migration_recovers_retained_skip_after_a_prior_partial_patch(self):
        old = self.legacy_skipped_check()
        old["status"] = "queued"
        self.project()
        self.assertEqual(len(self.http.checks), 3)
        self.assertEqual(self.http.checks[1002]["status"], "queued")
        self.assertIsNone(self.http.checks[1002]["conclusion"])
        self.assertEqual(old["conclusion"], "skipped")
        self.project()
        self.assertEqual(len(self.http.checks), 3)

    def test_migration_rejects_foreign_identity_and_non_skipped_verdict(self):
        old = self.legacy_skipped_check()
        original = copy.deepcopy(old)
        for mutation in ({"id": True}, {"name": "Other"}, {"head_sha": "f" * 40},
                         {"external_id": "other-epoch"}, {"app": {"id": 77}},
                         {"conclusion": "failure"}, {"conclusion": "success"}):
            old.update(mutation)
            self.http.calls.clear()
            with self.subTest(mutation=mutation), self.assertRaises(runtime.GitHubApiError):
                self.project()
            self.assertFalse(any(method == "POST" for method, _, _ in self.http.calls))
            old.clear(); old.update(original)
        self.assertEqual(len(self.http.checks), 2)

    def test_uncertain_replacement_is_fenced(self):
        old = self.legacy_skipped_check()
        self.http.fail_created_check = True
        with self.assertRaises(runtime.GitHubTransientError):
            self.project()
        self.http.fail_created_check = False
        with self.assertRaisesRegex(runtime.RuntimeError, "creation outcome is uncertain"):
            self.project()
        self.assertEqual(len(self.http.checks), 3)
        with closing(self.db()) as connection:
            row = connection.execute("SELECT * FROM projections").fetchone()
            self.assertIsNone(row["clawsweeper_check_run_id"])
            self.assertEqual(row["clawsweeper_check_create_state"], "creating")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='projection.skipped_check_replacement'").fetchone()[0], 1)
        self.assertEqual(old["conclusion"], "skipped")

    def test_replacement_authority_revocation_prevents_post_and_is_recoverable(self):
        self.legacy_skipped_check()
        def revoke_creation(method, path, authority):
            if method == "POST" and path.endswith("/check-runs"):
                raise core.AuthorityDenied("revoked exact tuple")
        self.client.set_authority_guard(revoke_creation)
        with self.assertRaises(core.AuthorityDenied):
            self.project()
        self.assertEqual(len(self.http.checks), 2)
        with closing(self.db()) as connection:
            row = connection.execute("SELECT * FROM projections").fetchone()
            self.assertIsNone(row["clawsweeper_check_run_id"])
            self.assertEqual(row["clawsweeper_check_create_state"], "pending")
        self.client.set_authority_guard(None)
        self.project()
        self.assertEqual(len(self.http.checks), 3)

    def test_confirmed_replacement_id_survives_later_label_failure(self):
        self.legacy_skipped_check()
        from urllib.parse import quote
        path = self.root_path + "/issues/7/labels/" + quote(runtime.READY_LABEL, safe="")
        self.http.overrides[("DELETE", path)] = (500, b"{}")
        with self.assertRaises(runtime.GitHubTransientError):
            self.project()
        with closing(self.db()) as connection:
            row = connection.execute("SELECT * FROM projections").fetchone()
            self.assertEqual(row["clawsweeper_check_run_id"], 1002)
            self.assertEqual(row["clawsweeper_check_create_state"], "active")
        self.http.overrides.clear()
        self.project()
        self.assertEqual(len(self.http.checks), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
