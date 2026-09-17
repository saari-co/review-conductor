#!/usr/bin/env python3
"""Synthetic native artifact -> accepted receipt -> single rich publication."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import clawsweeper_presentation as native
import review_conductor as core
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import review_result_projection as projection
import test_review_conductor_profiles as profiles
import test_review_conductor_userland as legacy
from test_openclaw_report_publication import GitHubArrayTransport
from test_review_result_projection import RecordingGitHub

FIXTURE = ROOT / "tests/fixtures/clawsweeper-rich-report.md"


def report_identity(text):
    return {"repository": "example/synthetic-review", "pr_number": 7,
            "base_sha": "1" * 40, "head_sha": "2" * 40, "review_epoch": 0,
            "artifact_digest": hashlib.sha256(text.encode()).hexdigest(),
            "issuer_app_id": projection.CONDUCTOR_ISSUER_APP_ID}


def presentation(text=None):
    text = text if text is not None else FIXTURE.read_text()
    identity = report_identity(text)
    return identity, native.parse_report(text, identity, actor="synthetic-clawsweeper")


def plan_for(identity, report, comments=None, labels=None, content="clean"):
    return projection.plan_github_publication(
        identity=identity, native_report=report,
        classification={"content_verdict": content, "process_gates": ["owner_merge_authority"],
                        "merge_authorized": False},
        existing_comments=comments or [], existing_labels=labels or [],
        stage="ready_for_human_merge", reason="human merge authority required", workflow_run_id=801,
    )


class PresentationTests(unittest.TestCase):
    def test_exact_native_proof_status_contract(self):
        expected = {
            "sufficient": "clean", "missing": "proof_deficient",
            "mock_only": "proof_deficient", "insufficient": "proof_deficient",
            "not_applicable": "clean", "override": "clean",
        }
        self.assertEqual(projection.NONBLOCKING_PROOF_STATUSES, {"sufficient", "not_applicable", "override"})
        self.assertEqual(projection.DEFICIENT_PROOF_STATUSES, {"missing", "mock_only", "insufficient"})
        self.assertEqual(native.PROOF_STATUSES, set(expected))
        self.assertEqual(userland.READY_PROOF_STATUSES, {"sufficient"})
        for status, verdict in expected.items():
            with self.subTest(status=status):
                identity, report = presentation(FIXTURE.read_text().replace(
                    "real_behavior_proof_status: sufficient", "real_behavior_proof_status: " + status))
                classified = projection.classify_from_frontmatter(report["fields"], finding_count=0)
                self.assertEqual(classified["content_verdict"], verdict)
                self.assertFalse(classified["merge_authorized"])
                self.assertEqual(classified["merge_policy"], "human_only")
                if verdict == "clean":
                    self.assertIn("owner_merge_authority", classified["process_gates"])
                self.assertEqual(set(report["labels"]) & native.FAMILIES["proof"],
                                 {"proof: sufficient"} if status == "sufficient" else set())
                self.assertNotIn("proof: override", native.managed_labels(report))
                self.assertIn("Native proof status: " + status, plan_for(identity, report)["comment"]["body"])

    def test_noncontract_proof_status_rejected_before_verdict_precedence(self):
        cases = ({}, {"review_terminal_failure": "true"}, {"finding_count": 1},
                 {"maintainer_decision": '{"required":true}'},
                 {"real_behavior_proof_needs_contributor_action": "true"})
        for status in ("not_needed", "failed", "required", "unknown"):
            for case in cases:
                with self.subTest(status=status, case=case):
                    text = FIXTURE.read_text().replace(
                        "real_behavior_proof_status: sufficient", "real_behavior_proof_status: " + status)
                    fields = userland.parse_frontmatter(text)
                    fields.update({key: value for key, value in case.items() if key != "finding_count"})
                    with self.assertRaisesRegex(projection.ProjectionError, "proof_status"):
                        projection.classify_from_frontmatter(fields, finding_count=case.get("finding_count", 0))
                    for key, value in case.items():
                        if key != "finding_count":
                            text = text.replace(key + ": " + userland.parse_frontmatter(text)[key], key + ": " + value)
                    with self.assertRaisesRegex(native.PresentationError, "proof status"):
                        presentation(text)

    def test_native_override_retains_actual_defect_precedence(self):
        _, report = presentation(FIXTURE.read_text().replace(
            "real_behavior_proof_status: sufficient", "real_behavior_proof_status: override"))
        cases = (
            ({"review_terminal_failure": "true", "maintainer_decision": '{"required":true}'}, 1, "failed"),
            ({"maintainer_decision": '{"required":true}'}, 1, "findings"),
            ({"maintainer_decision": '{"required":true}', "real_behavior_proof_needs_contributor_action": "true"}, 0, "human_policy"),
            ({"real_behavior_proof_needs_contributor_action": "true"}, 0, "proof_deficient"),
            ({"pr_rating_overall": "D", "process_gates": "[]"}, 0, "human_policy"),
        )
        for changes, count, verdict in cases:
            with self.subTest(verdict=verdict, changes=changes):
                classified = projection.classify_from_frontmatter({**report["fields"], **changes}, finding_count=count)
                self.assertEqual(classified["content_verdict"], verdict)
                self.assertFalse(classified["merge_authorized"])
                self.assertEqual(classified["merge_policy"], "human_only")

    def test_rich_sections_native_scores_diagram_and_allowlisted_labels(self):
        identity, report = presentation()
        plan = plan_for(identity, report)
        body = plan["comment"]["body"]
        for expected in ("## What This Changes", "## Review scores", "## Verification", "## Evidence",
                         "## How this fits together", "```mermaid\nflowchart LR", "## Findings",
                         "## Next steps", "B · 🐚 platinum hermit | 4/6", "A · 🦞 diamond lobster | 5/6",
                         identity["artifact_digest"], "/actions/runs/801", "human_only"):
            self.assertIn(expected, body)
        self.assertEqual(report["labels"], ["P3", "merge-risk: 🚨 automation", "proof: sufficient",
                                            "proof: 🎥 video", "rating: 🐚 platinum hermit"])
        self.assertNotIn("INTERNAL_", body)
        self.assertNotIn("do-not-copy-this-snapshot", body)
        self.assertNotIn("proof: override", report["labels"])
        self.assertEqual(projection.parse_projection_marker(body), identity)
        self.assertIn(r"\\\![", native.quoted(r"\![untrusted image](https://example.invalid/image)"))

    def test_native_lowercase_and_mixedcase_diagrams_are_preserved(self):
        for declaration in ("flowchart lr", "FLOWCHART TB", "FlOwChArT bT"):
            with self.subTest(declaration=declaration):
                identity, report = presentation(FIXTURE.read_text().replace("flowchart LR", declaration))
                diagram = report["sections"]["Architecture Diagram"]
                self.assertEqual(native.safe_diagram(diagram), diagram)
                body = plan_for(identity, report)["comment"]["body"]
                self.assertIn("```mermaid\n" + diagram + "\n```", body)
                self.assertNotIn("outside the safe presentation subset", body)

    def test_repeated_publication_and_family_transition_preserve_manual_labels(self):
        identity, report = presentation()
        client = RecordingGitHub()
        client.labels += ["proof: override", "rating: manual", "P1", "proof: 📸 screenshot", "merge-risk: 🚨 availability"]
        plan = plan_for(identity, report, client.comments, client.labels)
        projection.apply_github_publication(client, plan)
        self.assertEqual(len(client.comments), 1)
        repeat = plan_for(identity, report, client.comments, client.labels)
        self.assertFalse(repeat["writes"])
        self.assertEqual({"P1", "proof: 📸 screenshot", "merge-risk: 🚨 availability"} & set(client.labels), set())
        self.assertTrue({"docs", "proof: override", "rating: manual"} <= set(client.labels))
        changed = FIXTURE.read_text().replace("triage_priority: P3", "triage_priority: P0").replace(
            "pr_rating_overall: B", "pr_rating_overall: S").replace(
            "real_behavior_proof_status: sufficient", "real_behavior_proof_status: insufficient").replace(
            'merge_risk_labels: ["merge-risk: 🚨 automation"]', "merge_risk_labels: []")
        next_identity, next_report = presentation(changed)
        update = plan_for(next_identity, next_report, client.comments, client.labels, "proof_deficient")
        projection.apply_github_publication(client, update)
        self.assertEqual(len(client.comments), 1)
        self.assertIn("rating: 🦀 challenger crab", client.labels)
        self.assertIn("P0", client.labels)
        self.assertIn("status: 📣 needs proof", client.labels)
        self.assertNotIn("proof: sufficient", client.labels)
        self.assertIn("proof: 🎥 video", client.labels)  # Independent of sufficiency.

    def test_absent_optional_fields_never_invent_labels_or_diagram(self):
        text = FIXTURE.read_text()
        for line in text.splitlines():
            if line.startswith(("triage_priority:", "merge_risk_labels:", "real_behavior_proof_evidence_kind:", "pr_rating_patch:")):
                text = text.replace(line + "\n", "")
        start, end = text.index("## Architecture Diagram"), text.index("## Review Findings")
        text = text[:start] + text[end:]
        identity, report = presentation(text)
        plan = plan_for(identity, report, labels=["P0", "merge-risk: 🚨 automation", "proof: 🎥 video"])
        self.assertNotIn("```mermaid", plan["comment"]["body"])
        self.assertIn("Patch | Not supplied", plan["comment"]["body"])
        self.assertEqual(plan["labels"]["remove"], [])
        self.assertEqual(len(plan["labels"]["preserve"]), 3)

    def test_all_native_tiers_priority_and_failed_rating(self):
        for tier, (name, score) in native.RATINGS.items():
            identity, report = presentation(FIXTURE.read_text().replace("pr_rating_overall: B", "pr_rating_overall: " + tier))
            self.assertIn("rating: " + name, report["labels"])
            self.assertIn(f"{name} | {score}", plan_for(identity, report)["comment"]["body"])
        for priority in ("P0", "P1", "P2", "P3", "none"):
            _, report = presentation(FIXTURE.read_text().replace("triage_priority: P3", "triage_priority: " + priority))
            self.assertEqual(set(report["labels"]) & native.FAMILIES["priority"], {priority} if priority != "none" else set())
        _, report = presentation(FIXTURE.read_text().replace("review_terminal_failure: false", "review_terminal_failure: true"))
        self.assertFalse(set(report["labels"]) & native.FAMILIES["rating"])

    def test_malformed_metadata_and_identity_digest_mutants_rejected(self):
        original = FIXTURE.read_text()
        for old, new in (("triage_priority: P3", "triage_priority: deploy"),
                         ('merge_risk_labels: ["merge-risk: 🚨 automation"]', 'merge_risk_labels: ["proof: override"]'),
                         ('merge_risk_labels: ["merge-risk: 🚨 automation"]', 'merge_risk_labels: {"bad":true}'),
                         ("pr_rating_patch: B", "pr_rating_patch: excellent"),
                         ("reviewer_actor: synthetic-clawsweeper", "reviewer_actor: foreign"),
                         ("review_epoch: 0", "review_epoch: 1"),
                         ("triage_priority: P3", "triage_priority: P3\ntriage_priority: P0"),
                         ("## Evidence", "## What This Changes")):
            with self.subTest(new=new), self.assertRaises(native.PresentationError):
                presentation(original.replace(old, new))
        with self.assertRaises(native.PresentationError):
            native.parse_report(original + "changed", report_identity(original), actor="synthetic-clawsweeper")
        identity, report = presentation()
        for key, value in (("head_sha", "3" * 40), ("review_epoch", 1), ("artifact_digest", "a" * 64)):
            with self.subTest(key=key), self.assertRaises(projection.ProjectionError):
                plan_for({**identity, key: value}, report)

    def test_marker_html_and_fence_injection_and_unsafe_diagrams_are_not_active(self):
        original = FIXTURE.read_text()
        injected = original.replace("Carries accepted", "</details><!-- review-conductor:github-projection fake -->\n@everyone\n```\n# Forged authority\nCarries accepted")
        identity, report = presentation(injected)
        body = plan_for(identity, report)["comment"]["body"]
        self.assertNotIn("</details>", body)
        self.assertNotIn("@everyone", body)
        self.assertEqual(body.count(projection.MARKER_PREFIX), 1)
        self.assertEqual(projection.parse_projection_marker(body), identity)
        for declaration in ("flowchart LR", "flowchart lr", "FlOwChArT lR"):
            for suffix in ('\nclick Bound "https://example.invalid"', '\nBound; style Bound fill:red',
                           '\nA[<img src=x>]', '\n%%{init: {}}', '\nA[data:text/x]', '\nA[//example.invalid]', '\n```'):
                with self.subTest(declaration=declaration, suffix=suffix):
                    self.assertIsNone(native.safe_diagram(declaration + "\nA --> B" + suffix))

    def test_bounds_are_explicit_and_keep_original_report_access(self):
        text = FIXTURE.read_text().replace("Carries accepted", "x" * 10000 + "Carries accepted")
        identity, report = presentation(text)
        body = plan_for(identity, report)["comment"]["body"]
        self.assertIn("Excerpt truncated", body)
        self.assertLess(len(body.encode()), native.COMMENT_MAX_BYTES)
        with patch.object(native, "COMMENT_MAX_BYTES", 3000):
            body = plan_for(identity, report)["comment"]["body"]
            self.assertIn("no partial report is published", body)
            self.assertIn("/actions/runs/801", body)
            self.assertIn(identity["artifact_digest"], body)
            self.assertNotIn("## What This Changes", body)
        with self.assertRaises(native.PresentationError):
            presentation(FIXTURE.read_text() + "x" * native.REPORT_MAX_BYTES)


class ArtifactPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = profiles.ProfilesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.config()
        self.action = self.fixture.enqueue(self.config)
        self.http = GitHubArrayTransport(self.config)
        self.client = runtime.GitHubAppClient(self.config, "unused-offline-key", transport=self.http,
                                             signer=lambda *args: "offline-fixture-jwt")
        self.authorities = []
        self.client.set_authority_guard(lambda method, path, authority: self.authorities.append((method, path, authority)))

    def db(self):
        return core.open_database(Path(self.config["paths"]["state_root"]), profiles.REPO)

    def prepare(self, *, proof_status="sufficient", finding=False, maintainer_required=False,
                needs_contributor_action=False):
        runtime.bridge_openclaw(self.config, self.fixture.terminal(self.config, self.action))
        action = legacy.action(self.config, 7, "clawsweeper.dispatch")
        legacy.mark_dispatched(self.config, action["action_id"])
        self.fixture.ingest(self.config, "workflow_run", "native-rich-run", self.fixture.payload(legacy.claw_workflow_payload(801)))
        files = userland.bounded_zip_files(self.fixture.bundle(self.config, action))
        manifest = json.loads(files["manifest.json"])
        text = FIXTURE.read_text().replace("example/synthetic-review", profiles.REPO).replace(
            "1" * 40, profiles.BASE).replace("2" * 40, profiles.HEAD).replace(
            "synthetic-clawsweeper", "fixture-suite-clawsweeper").replace(
            "real_behavior_proof_status: sufficient", "real_behavior_proof_status: " + proof_status).replace(
            "Status: sufficient", "Status: " + proof_status)
        if maintainer_required:
            text = text.replace('maintainer_decision: {"required":false}', 'maintainer_decision: {"required":true}')
        if needs_contributor_action:
            text = text.replace("real_behavior_proof_needs_contributor_action: false",
                                "real_behavior_proof_needs_contributor_action: true")
        if finding:
            text = text.replace("None. This synthetic clean fixture", "- **[P1] Synthetic finding:** keep the fixture guarded.\n\nThis synthetic findings fixture")
        self.report = text
        raw = text.encode()
        manifest["files"][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("review/7.md", raw)
        class Artifacts(legacy.FakeGitHub):
            def list_run_artifacts(self, run_id):
                return [{"id": 9001, "name": f"smcbd-suite-review-{run_id}-1", "expired": False}]
        self.collected = userland.collect_clawsweeper_terminals(self.config, Artifacts(801, bundle.getvalue()), dry_run=False)
        terminal = Path(self.config["clawsweeper_bridge"]["terminal_inbox"]) / "801.terminal.json"
        runtime.bridge_clawsweeper(self.config, terminal)
        self.report_path = Path(json.loads(terminal.read_text())["proof_ref"])

    def assert_proof_status_publication(self, status, verdict, label, **prepare_options):
        self.http.labels.add("proof: sufficient")  # Retract stale owned sufficiency.
        self.prepare(proof_status=status, **prepare_options)
        self.assertEqual(self.collected[0]["result"], "terminal_materialized")
        self.assertFalse(self.collected[0]["ready_qualified"])
        self.assertEqual(self.report_path.read_text(), self.report)
        with closing(self.db()) as db:
            quality = db.execute("SELECT * FROM clawsweeper_quality").fetchone()
            self.assertEqual(quality["proof_status"], status)
            self.assertEqual(quality["content_verdict"], verdict)
            self.assertEqual(quality["ready_qualified"], 0)
            if verdict == "clean":
                self.assertIn("owner_merge_authority", json.loads(quality["process_gates_json"]))
        first = self.project()
        self.assertFalse(first["merge_authorized"])
        self.assertEqual(first["projected"][0]["publication"]["result"], "published")
        self.assertIn(label, self.http.labels)
        self.assertNotIn("proof: sufficient", self.http.labels)
        self.assertIn("docs", self.http.labels)
        body = self.http.comments[0]["body"]
        for expected in ("## What This Changes", "## Review scores", "Native proof status: " + status,
                         "Review content: " + verdict, "human_only"):
            self.assertIn(expected, body)
        before = len(self.http.calls)
        labels_before = set(self.http.labels)
        repeated = self.project()["projected"][0]["publication"]
        self.assertEqual(repeated["result"], "unchanged")
        self.assertFalse(repeated["writes"])
        self.assertEqual(len(self.http.comments), 1)
        self.assertEqual(self.http.labels, labels_before)
        self.assertFalse(any(method != "GET" and "/comments" in path
                             for method, path, _body in self.http.calls[before:]))
        # Existing ready-status reconciliation can repeat; rich native labels cannot.
        self.assertFalse(any(method == "POST" and "/labels" in path and set(payload["labels"]) & native.NATIVE_LABELS
                             for method, path, payload in self.http.calls[before:]))
        self.assertFalse(any(method == "POST" and "/labels" in path and
                             {"proof: override", "proof: sufficient"} & set(payload["labels"])
                             for method, path, payload in self.http.calls))

    def test_mock_only_bundle_materialization_rich_publication_and_idempotency(self):
        self.assert_proof_status_publication("mock_only", "proof_deficient", "status: 📣 needs proof")
        self.assertNotIn(runtime.READY_LABEL, self.http.labels)
        self.assertNotIn("proof: override", self.http.labels)

    def test_override_bundle_materialization_rich_publication_and_idempotency(self):
        self.assert_proof_status_publication("override", "clean", "status: 👀 ready for maintainer look")
        self.assertNotIn("proof: override", self.http.labels)

    def test_override_preserves_human_owned_label_without_minting_authority(self):
        self.http.labels.add("proof: override")
        self.assert_proof_status_publication("override", "clean", "status: 👀 ready for maintainer look")
        self.assertIn("proof: override", self.http.labels)

    def test_override_does_not_clear_findings(self):
        self.assert_proof_status_publication("override", "findings", "status: ⏳ waiting on author", finding=True)
        self.assertNotIn(runtime.READY_LABEL, self.http.labels)

    def test_override_does_not_clear_maintainer_decision(self):
        self.assert_proof_status_publication("override", "human_policy", "status: needs maintainer proof decision",
                                           maintainer_required=True)
        self.assertNotIn(runtime.READY_LABEL, self.http.labels)

    def test_override_does_not_clear_contributor_action(self):
        self.assert_proof_status_publication("override", "proof_deficient", "status: 📣 needs proof",
                                           needs_contributor_action=True)
        self.assertNotIn(runtime.READY_LABEL, self.http.labels)

    def test_noncontract_status_with_findings_rejected_before_materialization(self):
        with self.assertRaisesRegex(userland.UserlandError, "proof_status"):
            self.prepare(proof_status="required", finding=True)
        self.assertFalse((Path(self.config["clawsweeper_bridge"]["terminal_inbox"]) / "801.terminal.json").exists())
        self.assertFalse((Path(self.config["paths"]["proof_root"]) / "clawsweeper/801/7.md").exists())
        self.assertEqual(self.http.calls, [])

    def project(self):
        return runtime.reconcile_projection(self.config, self.client, pr_number=7)

    def test_collector_bridge_projection_repeated_tick_keeps_one_comment(self):
        self.prepare()
        first = self.project()
        self.assertEqual(first["projected"][0]["publication"]["result"], "published")
        self.assertIn("## What This Changes", self.http.comments[0]["body"])
        self.assertIn("```mermaid", self.http.comments[0]["body"])
        self.assertTrue({"P3", "proof: sufficient", "rating: 🐚 platinum hermit", "docs"} <= self.http.labels)
        self.assertFalse(first["merge_authorized"])
        before = len(self.http.calls)
        self.assertEqual(self.project()["projected"][0]["publication"]["result"], "unchanged")
        self.assertEqual(len(self.http.comments), 1)
        self.assertFalse(any(method != "GET" and "/comments" in path for method, path, _body in self.http.calls[before:]))
        self.assertFalse(any(method == "POST" and "/labels" in path and set(body["labels"]) & native.NATIVE_LABELS
                             for method, path, body in self.http.calls[before:]))
        for _method, _path, authority in self.authorities:
            self.assertEqual(authority["head_sha"], profiles.HEAD)
            self.assertEqual(authority["review_epoch"], 0)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='projection.native_publication'").fetchone()[0], 1)

    def test_findings_status_not_overridden_by_rich_rating(self):
        self.prepare(finding=True)
        self.project()
        self.assertIn("status: ⏳ waiting on author", self.http.labels)
        self.assertNotIn(runtime.READY_LABEL, self.http.labels)
        self.assertIn("Review content: findings", self.http.comments[0]["body"])
        self.assertIn("[P1] Synthetic finding", self.http.comments[0]["body"])
        self.assertIn("P3", self.http.labels)  # Native triage, not max finding priority.

    def test_proof_deficient_status_remains_independent(self):
        self.prepare(proof_status="insufficient")
        self.project()
        self.assertIn("status: 📣 needs proof", self.http.labels)
        self.assertNotIn("proof: sufficient", self.http.labels)
        self.assertIn("proof: 🎥 video", self.http.labels)
        self.assertIn("rating: 🐚 platinum hermit", self.http.labels)

    def test_missing_tampered_symlink_oversized_reports_block_before_writes(self):
        self.prepare()
        original = self.report_path.read_bytes()
        for mutation in ("tamper", "missing", "symlink", "oversized", "directory", "fifo"):
            with self.subTest(mutation=mutation):
                self.report_path.unlink()
                if mutation == "tamper":
                    self.report_path.write_bytes(original + b"changed")
                elif mutation == "symlink":
                    other = self.report_path.parent / "other.md"
                    other.write_bytes(original)
                    self.report_path.symlink_to(other)
                elif mutation == "oversized":
                    self.report_path.write_bytes(b"x" * (native.REPORT_MAX_BYTES + 1))
                elif mutation == "directory":
                    self.report_path.mkdir()
                elif mutation == "fifo":
                    os.mkfifo(self.report_path)
                before = len(self.http.calls)
                with self.assertRaises(runtime.RuntimeError):
                    self.project()
                self.assertEqual(len(self.http.calls), before)
                if self.report_path.is_dir():
                    self.report_path.rmdir()
                elif self.report_path.exists() or self.report_path.is_symlink():
                    self.report_path.unlink()
                self.report_path.write_bytes(original)

    def test_receipt_digest_actor_and_run_binding_conflicts_block(self):
        self.prepare()
        with closing(self.db()) as db:
            row = db.execute("SELECT event_id,payload_json FROM events WHERE kind='clawsweeper.terminal'").fetchone()
            original = json.loads(row["payload_json"])
        for key, value in (("proof_sha256", "a" * 64), ("reviewer_actor", "foreign"), ("workflow_run_id", "802"), ("review_scope", "partial")):
            with self.subTest(key=key), closing(self.db()) as db:
                db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps({**original, key: value}), row["event_id"]))
                db.commit()
                before = len(self.http.calls)
                with self.assertRaises(runtime.RuntimeError):
                    self.project()
                self.assertEqual(len(self.http.calls), before)
        with closing(self.db()) as db:
            original.pop("proof_sha256")
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(original), row["event_id"]))
            db.commit()
        self.project()
        self.assertIn("legacy summary only", self.http.comments[0]["body"])
        self.assertNotIn("rating: 🐚 platinum hermit", self.http.labels)

    def test_later_leftover_terminal_keeps_exact_request_rich_publication(self):
        self.prepare()
        with closing(self.db()) as db:
            row = db.execute("SELECT event_id,payload_json FROM events WHERE kind='clawsweeper.terminal'").fetchone()
            original = json.loads(row["payload_json"])
            leftover = {**original, "workflow_run_id": "802"}
            core.insert_event(
                db, event_id="leftover-clawsweeper-802", kind="clawsweeper.terminal", stale=False,
                repository=original["repository"], pr_number=original["pr_number"],
                base_sha=original["base_sha"], head_sha=original["head_sha"], payload=leftover,
            )
            db.commit()
        first = self.project()
        self.assertEqual(first["projected"][0]["publication"]["result"], "published")
        self.assertFalse(first["merge_authorized"])
        body = self.http.comments[0]["body"]
        self.assertIn("## What This Changes", body)
        self.assertIn("/actions/runs/801", body)
        self.assertNotIn("/actions/runs/802", body)
        self.assertIn(hashlib.sha256(self.report.encode()).hexdigest(), body)

    def test_waiting_human_owner_adjudication_keeps_rich_bind_without_merge(self):
        self.prepare(proof_status="insufficient")
        with closing(self.db()) as db:
            head = db.execute("SELECT * FROM heads WHERE is_current=1").fetchone()
            self.assertEqual(head["state"], "waiting_human")
            self.assertEqual(head["rail"], "clawsweeper")
            self.assertEqual(str(head["review_request_id"]), "801")
            row = db.execute("SELECT payload_json FROM events WHERE kind='clawsweeper.terminal'").fetchone()
            leftover = {**json.loads(row["payload_json"]), "workflow_run_id": "802"}
            core.insert_event(
                db, event_id="leftover-waiting-human-802", kind="clawsweeper.terminal", stale=False,
                repository=leftover["repository"], pr_number=leftover["pr_number"],
                base_sha=leftover["base_sha"], head_sha=leftover["head_sha"], payload=leftover,
            )
            db.commit()
        with self.assertRaisesRegex(core.ContractError, "supports only defer and reject_false_positive"):
            core.ingest_internal_event(
                config_path=Path(self.config["core_config"]),
                state_root=Path(self.config["paths"]["state_root"]),
                event_payload={
                    "schema": core.INTERNAL_EVENT_SCHEMA, "event_id": "rich-waiting-human-required-fix",
                    "type": "adjudication.completed", "repository": profiles.REPO, "pr_number": 7,
                    "base_sha": profiles.BASE, "head_sha": profiles.HEAD, "request_id": "801",
                    "rail": "clawsweeper", "classifications": ["required_fix"],
                    "reviewer_actor": "fixture-suite-clawsweeper",
                    "proof_ref": "proof/adjudication/rich-waiting-human/ADJUDICATION.md",
                    "review_epoch": 0,
                },
            )
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT state FROM heads WHERE is_current=1").fetchone()[0], "waiting_human")
        outcome = core.ingest_internal_event(
            config_path=Path(self.config["core_config"]),
            state_root=Path(self.config["paths"]["state_root"]),
            event_payload={
                "schema": core.INTERNAL_EVENT_SCHEMA, "event_id": "rich-waiting-human-defer",
                "type": "adjudication.completed", "repository": profiles.REPO, "pr_number": 7,
                "base_sha": profiles.BASE, "head_sha": profiles.HEAD, "request_id": "801",
                "rail": "clawsweeper", "classifications": ["defer", "reject_false_positive"],
                "reviewer_actor": "fixture-suite-clawsweeper",
                "proof_ref": "proof/adjudication/rich-waiting-human/ADJUDICATION.md",
                "review_epoch": 0,
            },
        )
        self.assertEqual(outcome["state"], "ready_for_human_merge")
        self.assertFalse(outcome["merge_dispatched"])
        self.assertFalse(outcome["action_created"])
        first = self.project()
        self.assertFalse(first["merge_authorized"])
        self.assertEqual(first["projected"][0]["publication"]["result"], "published")
        self.assertIn(runtime.READY_LABEL, self.http.labels)
        body = self.http.comments[0]["body"]
        digest = hashlib.sha256(self.report.encode()).hexdigest()
        for expected in ("## What This Changes", "Native proof status: insufficient",
                         "Original review content: proof_deficient", digest, "/actions/runs/801",
                         "human_only", "Merge authorized: no"):
            self.assertIn(expected, body)
        self.assertNotIn("/actions/runs/802", body)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM actions WHERE kind='clawsweeper.dispatch'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM actions WHERE kind IN ('repair.route','merge')").fetchone()[0], 0)

    def test_wrong_comment_app_not_replaced_and_closed_ownership_retraction(self):
        self.prepare()
        self.project()
        owned = copy.deepcopy(self.http.comments[0])
        foreign = {**copy.deepcopy(owned), "id": 1900, "performed_via_github_app": {"id": 123}}
        self.http.comments.insert(0, foreign)
        self.http.labels.update({"proof: override", "rating: manual", "merge-risk: custom"})
        self.project()
        self.assertEqual(self.http.comments[0], foreign)
        payload = self.fixture.payload(legacy.pr_payload(7))
        payload["action"] = "closed"
        payload["pull_request"]["updated_at"] = "2026-08-29T20:22:00Z"
        self.fixture.ingest(self.config, "pull_request", "close-rich", payload)
        self.project()
        self.assertEqual(self.http.labels, {"docs", "proof: override", "rating: manual", "merge-risk: custom"})
        self.assertEqual(self.http.comments[0], foreign)
        self.assertTrue(self.http.comments[1]["body"].startswith("> **Historical review"))

    def test_changed_head_retires_old_native_labels_and_comment(self):
        self.prepare()
        self.project()
        payload = self.fixture.payload(legacy.pr_payload(7))
        payload["action"] = "synchronize"
        payload["pull_request"]["head"]["sha"] = "3" * 40
        payload["pull_request"]["updated_at"] = "2026-08-29T20:22:00Z"
        self.fixture.ingest(self.config, "pull_request", "new-rich-head", payload)
        self.project()
        self.assertFalse(self.http.labels & native.NATIVE_LABELS)
        self.assertTrue(self.http.comments[0]["body"].startswith("> **Historical review"))

    def test_native_label_add_missing_and_already_absent_remove_fail_honestly(self):
        path = f"/repos/{profiles.REPO}/issues/7/labels"
        label = "rating: 🐚 platinum hermit"
        self.client.add_owned_label(7, label)
        self.client.remove_owned_label(7, label)
        self.client.remove_owned_label(7, label)
        for response in ((200, b'[]'), (422, b'{"message":"Validation Failed"}')):
            self.http.overrides[("POST", path)] = response
            with self.assertRaises(runtime.GitHubApiError):
                self.client.add_owned_label(7, label)
        self.assertFalse(any(method == "PUT" or path.endswith("/repos/" + profiles.REPO + "/labels")
                             for method, path, _body in self.http.calls))

    def test_interrupted_publication_retains_cleanup_ownership_and_retries_one_comment(self):
        self.prepare()
        path = f"/repos/{profiles.REPO}/issues/7/labels"
        original = self.client.add_owned_label
        def fail_rating(pr, name, **kwargs):
            if name.startswith("rating:"):
                raise runtime.GitHubApiError("synthetic unavailable label")
            return original(pr, name, **kwargs)
        with patch.object(self.client, "add_owned_label", side_effect=fail_rating):
            with self.assertRaises(runtime.GitHubApiError):
                self.project()
        self.assertEqual(len(self.http.comments), 1)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='projection.native_publication'").fetchone()[0], 1)
        self.project()
        self.assertEqual(len(self.http.comments), 1)
        self.assertIn("rating: 🐚 platinum hermit", self.http.labels)
        self.assertTrue(any(method == "POST" and endpoint == path for method, endpoint, _ in self.http.calls))

    def test_invalid_terminal_digest_never_enters_accepted_event(self):
        self.prepare()
        with closing(self.db()) as db:
            terminal = json.loads(db.execute("SELECT payload_json FROM events WHERE kind='clawsweeper.terminal'").fetchone()[0])
        config = core.load_config(Path(self.config["core_config"]))
        for digest in (True, "UPPER", "a" * 63, "a" * 65, ["a" * 64]):
            with self.subTest(digest=digest), self.assertRaises(core.ContractError):
                core.validate_internal_event(config, {**terminal, "proof_sha256": digest})


if __name__ == "__main__":
    unittest.main(verbosity=2)
