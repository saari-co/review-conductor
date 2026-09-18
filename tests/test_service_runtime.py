"""Qualified service-owned admission, ingress and GitHub App adapter boundary."""
from __future__ import annotations

import argparse
import base64
import copy
import dataclasses
import hashlib
import hmac
import http.client
import io
import os
import re
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_conductor as core
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import orchestration_outcome as orchestration
import service_runtime as service
import service_entrypoint as entrypoint
import trusted_admission as admission
import test_review_conductor_userland as legacy


REPOSITORY = "saari-co/openclaw-smcbd-suite"
REPOSITORY_ID = 1366416798
APP_ID = 4916376
INSTALLATION_ID = 161027021
POLICY_COMMIT = "a" * 40
BASE = legacy.BASE
HEAD = legacy.HEAD
SECRET = "fixture-service-webhook-secret"
REVIEWERS = {"openclaw": "fixture-openclaw", "clawsweeper": "fixture-clawsweeper"}


class ServiceFixture:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.config_path = root / "smcbd.json"
        self.policy = (ROOT / "examples/smcbd.review-conductor.json").read_bytes()
        config = json.loads(
            (ROOT / "contracts/review-conductor/openclaw-smcbd-suite.json").read_text()
        )
        config["review_policy"].update(
            enabled=True,
            reviewers=dict(REVIEWERS),
        )
        self.config_path.write_text(json.dumps(config) + "\n")
        self.reads: list[tuple[str, str]] = []
        service.clear_staged_policies()

    def registry(self, *, policy=None, app_id=APP_ID):
        return admission.load_registry(json.dumps(self.registry_document(policy=policy, app_id=app_id)).encode())

    def registry_document(self, *, policy=None, app_id=APP_ID):
        raw = policy if policy is not None else self.policy
        return {
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [
                {
                    "repository": REPOSITORY,
                    "repository_id": REPOSITORY_ID,
                    "github_app": {
                        "id": app_id,
                        "installation_id": INSTALLATION_ID,
                        "installation_account": "saari-co",
                    },
                    "approved_policy": {
                        "commit": POLICY_COMMIT,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    "reviewers": dict(REVIEWERS),
                }
            ],
        }

    def read(self, repository, commit):
        self.reads.append((repository, commit))
        if (repository, commit) == (REPOSITORY, POLICY_COMMIT):
            return self.policy
        return b""

    def app_config(self):
        return {
            "core_config": str(self.config_path),
            "paths": {
                "state_root": str(self.state),
                "action_wake": str(self.state / "wake/action.json"),
                "projection_wake": str(self.state / "wake/projection.json"),
            },
            "ingress": {
                "path": "/github/webhook",
                "max_body_bytes": 1024 * 1024,
                "request_timeout_seconds": 5,
            },
            "github_app": {
                "api_base": "https://api.github.com",
                "app_id": APP_ID,
                "installation_id": INSTALLATION_ID,
                "repository": REPOSITORY,
                "repository_id": REPOSITORY_ID,
                "permissions": runtime.STANDALONE_APP_PERMISSIONS,
            },
            "review_policy": {"enabled": True},
            "clawsweeper": {
                "workflow_id": "clawsweeper-exact-tuple.yml",
                "ref": "main",
            },
            "spark": {
                "target": "spark-2",
                "smoky_path": "bin/smoky",
                "ssh_path": "/usr/bin/ssh",
                "scp_path": "/usr/bin/scp",
            },
            "adapter": {"contract": "exact-tuple-comprehensive-v1", "artifact_prefix": "fixture-review"},
        }

    def payload(self, *, installation=INSTALLATION_ID, repository=REPOSITORY, repository_id=REPOSITORY_ID):
        payload = copy.deepcopy(legacy.pr_payload(7))
        payload["repository"] = {"full_name": repository, "id": repository_id}
        payload["installation"] = {"id": installation}
        payload["pull_request"]["draft"] = False
        return payload

    @staticmethod
    def signed(payload, secret=SECRET):
        body = core.canonical_json(payload).encode()
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, signature

    def ingest(self, delivery, payload, *, registry=None, signature=None, service_config=None):
        body, valid = self.signed(payload)
        return service.ingest_service_delivery(
            config_path=self.config_path,
            state_root=self.state,
            event_type="pull_request",
            delivery_id=delivery,
            signature=valid if signature is None else signature,
            body=body,
            secret=SECRET,
            registry=registry or self.registry(),
            read_policy=self.read,
            service_config=service_config or self.app_config(),
        )

    def core_config(self):
        return core.load_config(self.config_path)

    def binding_rows(self):
        connection = core.open_database(self.state, REPOSITORY)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "service_policy_bindings" not in tables:
                return []
            return [
                dict(row) for row in connection.execute(
                    "SELECT * FROM service_policy_bindings ORDER BY created_at"
                ).fetchall()
            ]
        finally:
            connection.close()


def _delivery_rows(config):
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        return list(
            connection.execute(
                "SELECT event_key, status FROM notification_deliveries ORDER BY channel, event_key"
            )
        )
    finally:
        connection.close()


class RegistrySpy(admission.Registry):
    """A real (empty) registry that fails the test if enrollment is consulted before auth."""

    def __init__(self, test):
        super().__init__(enrollments=())
        object.__setattr__(self, "test", test)
        object.__setattr__(self, "calls", 0)

    def lookup(self, *_args):
        object.__setattr__(self, "calls", self.calls + 1)
        self.test.fail("enrollment lookup ran before webhook authentication")


class AdmissionIngressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = ServiceFixture(Path(self.temp.name))

    def test_signed_exact_installation_persists_one_policy_binding_and_replays(self):
        receipt = self.fx.ingest("delivery-one", self.fx.payload())
        self.assertEqual(receipt["result"], "accepted")
        self.assertRegex(receipt["admission_binding_id"], r"^[0-9a-f]{64}$")
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            binding = service.binding_for_current_head(connection, self.fx.registry(), REPOSITORY, 7, self.fx.core_config(), self.fx.app_config())
            self.assertEqual(binding["app_id"], APP_ID)
            self.assertEqual(binding["installation_id"], INSTALLATION_ID)
            self.assertEqual(binding["binding_id"], receipt["admission_binding_id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM service_policy_bindings").fetchone()[0], 1)
        finally:
            connection.close()
        duplicate = self.fx.ingest("delivery-one", self.fx.payload())
        self.assertEqual(duplicate["result"], "duplicate_delivery")
        self.assertEqual(duplicate["admission_binding_id"], receipt["admission_binding_id"])

    def test_transaction_bound_maintenance_gate_rejects_policy_revocation(self):
        self.fx.ingest("maintenance-binding", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = service.require_current_binding(
                connection, self.fx.app_config(), self.fx.registry(), 7
            )
            self.assertEqual(current["policy_commit"], POLICY_COMMIT)
            with self.assertRaises(core.ContractError) as ctx:
                service.require_current_binding(
                    connection,
                    self.fx.app_config(),
                    self.fx.registry(policy=b"promoted-policy"),
                    7,
                )
            self.assertIn("lacks a current approved-policy binding", str(ctx.exception))
        finally:
            connection.rollback()
            connection.close()

    def test_mutating_maintenance_calls_the_transaction_authority_guard(self):
        calls = []

        def denied(connection):
            calls.append(connection)
            raise service.ServiceError("maintenance authority revoked")

        operations = (
            lambda: userland.retry_failed_openclaw(
                self.fx.app_config(), 7, apply=True, authority_guard=denied
            ),
            lambda: userland.reconcile_uncertain_notification(
                self.fx.app_config(),
                7,
                "discord",
                "retry",
                "provider-nondelivery-observed",
                apply=True,
                authority_guard=denied,
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(core.ContractError) as ctx:
                operation()
            self.assertIn("maintenance authority revoked", str(ctx.exception))
        self.assertEqual(len(calls), 2)

    def test_legacy_binding_schema_is_migrated_but_never_trusted(self):
        self.fx.ingest("legacy-schema", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            connection.execute("ALTER TABLE service_policy_bindings RENAME TO current_bindings")
            connection.execute(
                """CREATE TABLE service_policy_bindings AS
                   SELECT repository, pr_number, base_sha, head_sha, review_epoch,
                          app_id, installation_id, policy_commit, policy_sha256,
                          policy_id, binding_id, reviewer_openclaw,
                          reviewer_clawsweeper, created_at
                   FROM current_bindings"""
            )
            connection.execute("DROP TABLE current_bindings")
            connection.commit()
            self.assertIsNone(service.binding_for_current_head(
                connection, self.fx.registry(), REPOSITORY, 7,
                self.fx.core_config(), self.fx.app_config(),
            ))
            service._ensure_binding_table(connection)
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(service_policy_bindings)"
            )}
            self.assertIn("profile_digest", columns)
            self.assertIsNone(connection.execute(
                "SELECT profile_digest FROM service_policy_bindings"
            ).fetchone()[0])
        finally:
            connection.close()

    def test_authentication_precedes_payload_registry_and_policy_processing(self):
        # A well-formed, enrolled payload with a forged signature must be refused before
        # JSON parsing, enrollment lookup or policy transport can observe it.
        body, valid = self.fx.signed(self.fx.payload())
        forged = "sha256=" + format(int(valid[7:], 16) ^ 1, "064x")
        spy = RegistrySpy(self)
        for signature in [forged, "sha256=" + "0" * 64, "", "sha1=" + "0" * 40, valid.upper()]:
            with self.subTest(signature=signature), self.assertRaises(core.ContractError):
                service.ingest_service_delivery(
                    config_path=self.fx.config_path,
                    state_root=self.fx.state,
                    event_type="pull_request",
                    delivery_id="bad-signature",
                    signature=signature,
                    body=body,
                    secret=SECRET,
                    registry=spy,
                    read_policy=lambda *_: self.fail("policy transport ran before authentication"),
                    service_config=self.fx.app_config(),
                )
        self.assertEqual(spy.calls, 0)
        self.assertFalse(self.fx.state.exists())

    def test_configured_app_identity_must_match_the_registry(self):
        config = self.fx.app_config()
        for key, value in [("app_id", APP_ID + 1), ("installation_id", INSTALLATION_ID + 1),
                           ("repository_id", REPOSITORY_ID + 1), ("app_id", str(APP_ID)),
                           ("app_id", None), ("app_id", True)]:
            mutated = copy.deepcopy(config)
            mutated["github_app"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(core.ContractError):
                self.fx.ingest(f"config-{key}", self.fx.payload(), service_config=mutated)
            self.assertFalse(self.fx.state.exists())
        self.assertEqual(self.fx.reads, [])

    def test_reviewer_identity_mismatch_leaves_no_binding(self):
        service_config = self.fx.app_config()
        config = json.loads(self.fx.config_path.read_text())
        config["review_policy"]["reviewers"]["openclaw"] = "foreign-openclaw"
        self.fx.config_path.write_text(json.dumps(config) + "\n")
        with self.assertRaises(core.ContractError):
            self.fx.ingest("foreign-reviewer", self.fx.payload(), service_config=service_config)
        self.assertEqual(self.fx.binding_rows(), [])

    def test_unknown_installation_repository_and_duplicate_json_fail_before_state(self):
        candidates = [
            self.fx.payload(installation=INSTALLATION_ID + 1),
            self.fx.payload(repository="dinkuskit/blocks", repository_id=1306882611),
            self.fx.payload(repository_id=REPOSITORY_ID + 1),
        ]
        for index, payload in enumerate(candidates):
            with self.subTest(index=index), self.assertRaises(core.ContractError):
                self.fx.ingest(f"wrong-{index}", payload)
            self.assertFalse(self.fx.state.exists())
        duplicate = b'{"repository":{},"repository":{},"installation":{"id":161027021}}'
        signature = "sha256=" + hmac.new(SECRET.encode(), duplicate, hashlib.sha256).hexdigest()
        with self.assertRaises(core.ContractError):
            service.ingest_service_delivery(
                config_path=self.fx.config_path,
                state_root=self.fx.state,
                event_type="pull_request",
                delivery_id="duplicate-key",
                signature=signature,
                body=duplicate,
                secret=SECRET,
                registry=self.fx.registry(),
                read_policy=self.fx.read,
                service_config=self.fx.app_config(),
            )
        self.assertFalse(self.fx.state.exists())

    def test_policy_hash_failure_rolls_back_engine_state(self):
        registry = self.fx.registry(policy=self.fx.policy + b"\n")
        with self.assertRaises(core.ContractError):
            self.fx.ingest("wrong-policy", self.fx.payload(), registry=registry)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM heads").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
        finally:
            connection.close()

    def test_stale_delivery_cannot_bind_a_superseded_tuple(self):
        fresh = self.fx.payload()
        fresh["pull_request"]["head"]["sha"] = "e" * 40
        fresh["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        self.fx.ingest("fresh-first", fresh)
        stale = self.fx.ingest("stale-old-head", self.fx.payload())
        self.assertEqual(stale["result"], "stale")
        self.assertNotIn("admission_binding_id", stale)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            rows = connection.execute(
                "SELECT head_sha FROM service_policy_bindings"
            ).fetchall()
            self.assertEqual([row[0] for row in rows], ["e" * 40])
        finally:
            connection.close()

    def test_policy_promotion_invalidates_existing_binding(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        promoted = self.fx.policy + b"\n"
        promoted_registry = self.fx.registry(policy=promoted)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertIsNone(
                service.binding_for_current_head(connection, promoted_registry, REPOSITORY, 7, self.fx.core_config(), self.fx.app_config())
            )
        finally:
            connection.close()
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(self.fx.app_config(), promoted_registry)
        # A duplicate delivery of the same exact tuple never rebinds it: the stale binding
        # stays, the head stays blocked, and only a new head or review epoch admitted under
        # the promoted policy unblocks it.
        self.fx.policy = promoted
        redelivered = self.fx.payload()
        redelivered["pull_request"]["updated_at"] = "2026-08-30T20:10:30Z"
        self.assertEqual(self.fx.ingest("after-promotion", redelivered, registry=promoted_registry)["result"], "duplicate")
        rows = self.fx.binding_rows()
        self.assertEqual([(row["review_epoch"], row["policy_sha256"]) for row in rows],
                         [(0, hashlib.sha256(promoted[:-1]).hexdigest())])
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(self.fx.app_config(), promoted_registry)
        # The exact-tuple table itself refuses a conflicting rebinding even if a caller
        # constructs one directly.
        conflicting = admission.admit(promoted_registry, {
            "repository": REPOSITORY, "repository_id": REPOSITORY_ID, "app_id": APP_ID,
            "installation_id": INSTALLATION_ID, "pr_number": 7, "base_sha": BASE, "head_sha": HEAD,
            "review_epoch": 0, "policy_commit": POLICY_COMMIT}, self.fx.read)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            with self.assertRaises(core.ContractError):
                service._persist_binding(connection, conflicting, promoted_registry.enrollments[0],
                                         service.profile_policy_digest(self.fx.core_config(), self.fx.app_config()))
            connection.rollback()
        finally:
            connection.close()
        ready = self.fx.payload()
        ready["action"] = "synchronize"
        ready["pull_request"]["head"]["sha"] = "e" * 40
        ready["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        receipt = self.fx.ingest("new-head", ready, registry=promoted_registry)
        self.assertEqual((receipt["result"], receipt["review_epoch"]), ("accepted", 0))
        self.assertEqual([(row["review_epoch"], row["policy_sha256"]) for row in self.fx.binding_rows()],
                         [(0, hashlib.sha256(promoted[:-1]).hexdigest()), (0, hashlib.sha256(promoted).hexdigest())])
        service.require_current_bindings(self.fx.app_config(), promoted_registry)

    def test_registry_file_requires_same_user_mode_0600(self):
        document = {
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY,
                "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }
        path = self.fx.root / "registry.json"
        path.write_text(json.dumps(document))
        path.chmod(0o600)
        with self.assertRaises(TypeError):
            entrypoint.read_service_registry(path)
        loaded = entrypoint.read_service_registry(path, [])
        self.assertEqual(loaded.enrollments[0].app_id, APP_ID)
        # The documented contract is exactly 0600: not merely owner-only bits.
        for mode in (0o400, 0o700, 0o640, 0o604, 0o660, 0o644, 0o666, 0o610, 0o601):
            path.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path, [])
            self.assertIn("mode 0600 exactly", str(ctx.exception))
        path.chmod(0o200)
        with self.assertRaises(core.ContractError):
            entrypoint.read_service_registry(path, [])
        path.chmod(0o600)
        link = self.fx.root / "registry-link.json"
        link.symlink_to(path)
        relative = Path("registry.json")
        for candidate in (link, relative, self.fx.root, self.fx.root / "missing.json", str(path)):
            with self.subTest(candidate=str(candidate)), self.assertRaises(core.ContractError):
                entrypoint.read_service_registry(candidate, [])
        hard_link = self.fx.root / "registry-hard.json"
        os.link(path, hard_link)
        with self.assertRaises(core.ContractError) as ctx:
            entrypoint.read_service_registry(path, [])
        self.assertIn("exactly one link", str(ctx.exception))
        hard_link.unlink()
        nested = self.fx.root / "shared"
        nested.mkdir()
        nested_registry = nested / "registry.json"
        nested_registry.write_text(json.dumps(document))
        nested_registry.chmod(0o600)
        for parent_mode in (0o777, 0o775, 0o702, 0o720):
            nested.chmod(parent_mode)
            with self.subTest(parent_mode=oct(parent_mode)), self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(nested_registry, [])
            self.assertIn("parent", str(ctx.exception))
        nested.chmod(0o755)
        self.assertEqual(entrypoint.read_service_registry(nested_registry, []).enrollments[0].app_id, APP_ID)
        path.write_text(json.dumps({**document, "enrollments": []}))
        with self.assertRaises(core.ContractError):
            entrypoint.read_service_registry(path, [])

    def test_registry_is_read_from_the_validated_descriptor_not_the_pathname(self):
        def document(app_id):
            return json.dumps({
                "schema": admission.REGISTRY_SCHEMA,
                "enrollments": [{
                    "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                    "github_app": {"id": app_id, "installation_id": INSTALLATION_ID,
                                   "installation_account": "saari-co"},
                    "approved_policy": {"commit": POLICY_COMMIT,
                                        "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
                    "reviewers": dict(REVIEWERS),
                }],
            })

        path = self.fx.root / "registry.json"
        path.write_text(document(APP_ID))
        path.chmod(0o600)
        swapped = self.fx.root / "swapped.json"
        swapped.write_text(document(APP_ID + 1))
        swapped.chmod(0o600)
        real_fstat = os.fstat

        def racing_fstat(descriptor):
            # A same-user writer replaces the pathname right after the opened file
            # has been validated.
            metadata = real_fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode) and swapped.exists():
                os.replace(swapped, path)
            return metadata

        with patch.object(entrypoint.os, "fstat", racing_fstat):
            loaded = entrypoint.read_service_registry(path, [])
        # The bytes come from the descriptor that was validated, not from whatever
        # the pathname points at afterwards.
        self.assertEqual(loaded.enrollments[0].app_id, APP_ID)
        self.assertEqual(entrypoint.read_service_registry(path, []).enrollments[0].app_id, APP_ID + 1)

    def test_reviewer_rotation_invalidates_existing_bindings_until_readmission(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        rows = self.fx.binding_rows()
        self.assertEqual((rows[0]["reviewer_openclaw"], rows[0]["reviewer_clawsweeper"]),
                         (REVIEWERS["openclaw"], REVIEWERS["clawsweeper"]))
        # Rotate the authoritative actors consistently in registry and profile: the old
        # binding was admitted under authority that no longer exists, so projection is
        # blocked until a new head/epoch is admitted under the rotated actors.
        rotated = {"openclaw": "rotated-openclaw", "clawsweeper": REVIEWERS["clawsweeper"]}
        registry_doc = {"schema": admission.REGISTRY_SCHEMA, "enrollments": [{
            "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
            "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID, "installation_account": "saari-co"},
            "approved_policy": {"commit": POLICY_COMMIT, "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
            "reviewers": rotated}]}
        rotated_registry = admission.load_registry(json.dumps(registry_doc).encode())
        core_config = json.loads(self.fx.config_path.read_text())
        core_config["review_policy"]["reviewers"] = rotated
        self.fx.config_path.write_text(json.dumps(core_config) + "\n")
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(self.fx.app_config(), rotated_registry)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertIsNone(service.binding_for_current_head(
                connection, rotated_registry, REPOSITORY, 7, self.fx.core_config(), self.fx.app_config()))
        finally:
            connection.close()
        ready = self.fx.payload()
        ready["action"] = "synchronize"
        ready["pull_request"]["head"]["sha"] = "e" * 40
        ready["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        self.assertEqual(self.fx.ingest("readmit", ready, registry=rotated_registry)["result"], "accepted")
        self.assertEqual([(row["review_epoch"], row["reviewer_openclaw"]) for row in self.fx.binding_rows()],
                         [(0, REVIEWERS["openclaw"]), (0, "rotated-openclaw")])
        service.require_current_bindings(self.fx.app_config(), rotated_registry)

    def test_profile_change_after_admission_invalidates_existing_bindings(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        original = self.fx.config_path.read_text()
        digest = self.fx.binding_rows()[0]["profile_digest"]
        self.assertEqual(digest, service.profile_policy_digest(self.fx.core_config(), self.fx.app_config()))
        # Simulate a restart with an edited engine profile while the registry still
        # approves the same policy commit/hash: every policy-governed field change
        # blocks projection of the old binding.
        edits = {
            "clawsweeper_requires_ready": lambda c: c["review_policy"].__setitem__("clawsweeper_requires_ready", False),
            "default_branch": lambda c: c.__setitem__("default_branch", "release"),
            "workflow_name": lambda c: c["ci"].__setitem__("workflow_name", "Other pipeline"),
            "workflow_path": lambda c: c["ci"].__setitem__("workflow_path", ".github/workflows/other.yml"),
            "merge_policy": lambda c: c.__setitem__("merge_policy", "auto"),
            # Adapter authority: which workflow produces the review and from which ref.
            "clawsweeper_workflow_id": lambda c: c["clawsweeper"].update(
                workflow_id="other-review.yml", workflow_path=".github/workflows/other-review.yml"),
            "clawsweeper_ref": lambda c: c["clawsweeper"].__setitem__("ref", "release"),
            "clawsweeper_workflow_name": lambda c: c["clawsweeper"].__setitem__("workflow_name", "Other review"),
            # OpenClaw adapter authority: which operator account and worktree run the review.
            "openclaw_operator_id": lambda c: c["openclaw"].__setitem__("operator_id", "other-operator"),
            "openclaw_transport": lambda c: c["openclaw"].__setitem__("transport", "ssh"),
            "openclaw_shelf": lambda c: c["openclaw"].__setitem__("remote_worktree_shelf", "/tmp/other-shelf"),
            "openclaw_exact_tuple_contract": lambda c: c["openclaw"].pop("exact_tuple_contract"),
        }
        for label, edit in edits.items():
            changed = json.loads(original)
            edit(changed)
            if label == "clawsweeper_requires_ready":
                # The engine requires readiness-gated ClawSweeper dispatch; prove
                # the digest itself is sensitive to an invalid profile value.
                variant = json.loads(original)
                variant["review_policy"]["clawsweeper_requires_ready"] = False
                self.assertNotEqual(service.profile_policy_digest(variant, self.fx.app_config()), digest)
                continue
            self.fx.config_path.write_text(json.dumps(changed) + "\n")
            try:
                loaded = self.fx.core_config()
            except core.ContractError:
                # Profiles the engine itself refuses cannot unlock anything either.
                with self.subTest(label=label), self.assertRaises(core.ContractError):
                    service.require_current_bindings(self.fx.app_config(), self.fx.registry())
                continue
            self.assertNotEqual(service.profile_policy_digest(loaded, self.fx.app_config()), digest)
            with self.subTest(label=label), self.assertRaises(core.ContractError):
                service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        self.fx.config_path.write_text(original)
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        # The service profile's adapter contract/artifact prefix select which
        # artifact namespace the worker accepts; changing either blocks projection.
        for adapter in (
            {"contract": "exact-tuple-comprehensive-v1", "artifact_prefix": "other-prefix"},
            {"contract": "other-contract", "artifact_prefix": "fixture-review"},
            None,
        ):
            changed_service = {**self.fx.app_config()}
            if adapter is None:
                del changed_service["adapter"]
            else:
                changed_service["adapter"] = adapter
            with self.subTest(adapter=adapter), self.assertRaises(core.ContractError):
                service.require_current_bindings(changed_service, self.fx.registry())
        changed_service = copy.deepcopy(self.fx.app_config())
        changed_service["spark"]["target"] = "spark-2.swarm"
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(changed_service, self.fx.registry())
        for selector, replacement in (
            ("smoky_path", "/opt/review-conductor/bin/smoky"),
            ("ssh_path", "/opt/review-conductor/bin/ssh"),
            ("scp_path", "/opt/review-conductor/bin/scp"),
        ):
            changed_service = copy.deepcopy(self.fx.app_config())
            changed_service["spark"][selector] = replacement
            self.assertNotEqual(service.profile_policy_digest(self.fx.core_config(), changed_service), digest)
            with self.subTest(selector=selector), self.assertRaises(core.ContractError):
                service.require_current_bindings(changed_service, self.fx.registry())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        # Non-policy edits (for example reviewer-independent operator fields) do not
        # change the digest.
        cosmetic = json.loads(original)
        cosmetic["max_repair_cycles"] = 1
        self.assertEqual(service.profile_policy_digest(cosmetic, self.fx.app_config()), digest)

    def test_policy_transport_never_runs_under_the_write_lock(self):
        database = self.fx.state / "review-conductor.sqlite3"
        probes = []

        def lock_aware_read(repository, commit):
            # A second writer must be able to take the write lock while the policy
            # is fetched: remote I/O never runs inside BEGIN IMMEDIATE.
            probe = sqlite3.connect(database, timeout=0)
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
            except sqlite3.OperationalError as exc:
                raise AssertionError("policy transport ran under the engine write lock") from exc
            finally:
                probe.close()
            probes.append((repository, commit))
            return self.fx.read(repository, commit)

        core.open_database(self.fx.state, REPOSITORY).close()
        receipt = service.ingest_service_delivery(
            config_path=self.fx.config_path, state_root=self.fx.state, event_type="pull_request",
            delivery_id="outside-lock", signature=self.fx.signed(self.fx.payload())[1],
            body=self.fx.signed(self.fx.payload())[0], secret=SECRET, registry=self.fx.registry(),
            read_policy=lock_aware_read, service_config=self.fx.app_config(),
        )
        self.assertEqual(receipt["result"], "accepted")
        self.assertEqual(probes, [(REPOSITORY, POLICY_COMMIT)])
        self.assertEqual(len(self.fx.binding_rows()), 1)
        # The staged bytes are hash-verified before caching; a reader returning the
        # wrong content for the approved commit cannot stage anything.
        service.clear_staged_policies()
        with self.assertRaises(core.ContractError) as ctx:
            service.stage_approved_policy(self.fx.registry().enrollments[0], lambda *_: b"{}")
        self.assertIn("hash does not match", str(ctx.exception))
        self.assertEqual(service._STAGED_POLICIES, {})
        with self.assertRaises(core.ContractError):
            service._staged_reader(self.fx.registry().enrollments[0], self.fx.policy)(REPOSITORY, "e" * 40)

    def test_transient_policy_failures_answer_503_without_writing_state(self):
        attempts = {"count": 0}

        def flaky_read(repository, commit):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise runtime.GitHubTransientError("GitHub API transport failed")
            if attempts["count"] == 2:
                raise runtime.GitHubApiError("allowlisted GitHub API operation failed (policy)")
            return self.fx.read(repository, commit)

        config = self.fx.app_config()
        body, signature = self.fx.signed(self.fx.payload())
        request = dict(
            config=config, method="POST", path="/github/webhook",
            headers={"content-type": "application/json", "x-github-event": "pull_request",
                     "x-github-delivery": "retry-me", "x-hub-signature-256": signature},
            body=body, secret=SECRET,
        )

        def ingestor(**kwargs):
            return service.ingest_service_delivery(
                **kwargs, registry=self.fx.registry(), read_policy=flaky_read, service_config=config,
            )

        def persisted():
            connection = core.open_database(self.fx.state, REPOSITORY)
            try:
                deliveries = connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                bindings = 0
                if "service_policy_bindings" in tables:
                    bindings = connection.execute("SELECT COUNT(*) FROM service_policy_bindings").fetchone()[0]
                return deliveries, bindings
            finally:
                connection.close()

        # Transient transport failure: 503, nothing persisted; the delivery stays
        # redeliverable from GitHub's delivery log/API (GitHub does not auto-retry).
        status, payload = runtime.handle_webhook_request(**request, ingestor=ingestor)
        self.assertEqual((int(status), payload), (503, {"ok": False, "reason": "dependency_unavailable"}))
        self.assertEqual(persisted(), (0, 0))
        # Non-transient reader failure stays a rejected delivery.
        status, payload = runtime.handle_webhook_request(**request, ingestor=ingestor)
        self.assertEqual((int(status), payload), (400, {"ok": False, "reason": "request_rejected"}))
        self.assertEqual(persisted(), (0, 0))
        # The redelivered identical delivery then succeeds under the same delivery id.
        status, payload = runtime.handle_webhook_request(**request, ingestor=ingestor)
        self.assertEqual((int(status), payload["result"]), (202, "accepted"))
        self.assertEqual(len(self.fx.binding_rows()), 1)
        self.assertEqual(attempts["count"], 3)
        # Malformed and foreign deliveries never reach the policy transport at all.
        foreign_body, foreign_signature = self.fx.signed(self.fx.payload(installation=INSTALLATION_ID + 1))
        status, payload = runtime.handle_webhook_request(
            **{**request, "body": foreign_body,
               "headers": {**request["headers"], "x-hub-signature-256": foreign_signature,
                           "x-github-delivery": "foreign"}},
            ingestor=ingestor,
        )
        self.assertEqual((int(status), payload["reason"]), (400, "request_rejected"))
        self.assertEqual(attempts["count"], 3)

    def test_github_client_classifies_transient_failures_and_malformed_content(self):
        responses = []

        def transport(method, url, headers, body, timeout):
            if url.endswith("/access_tokens"):
                return 201, json.dumps({"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}).encode()
            return responses.pop(0)

        client = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        for status in sorted(runtime.TRANSIENT_STATUSES):
            responses.append((status, b""))
            with self.subTest(status=status), self.assertRaises(runtime.GitHubTransientError):
                client.read_policy(REPOSITORY, POLICY_COMMIT)
        for status in (401, 403, 404, 422):
            responses.append((status, b""))
            with self.subTest(status=status), self.assertRaises(runtime.GitHubApiError) as ctx:
                client.read_policy(REPOSITORY, POLICY_COMMIT)
            self.assertNotIsInstance(ctx.exception, runtime.GitHubTransientError)
        for headers in ({"Retry-After": "30"}, {"X-RateLimit-Remaining": "0"}):
            responses.append((403, headers, b""))
            with self.subTest(headers=headers), self.assertRaises(runtime.GitHubTransientError):
                client.read_policy(REPOSITORY, POLICY_COMMIT)
        # Base64 that passes the character allowlist but has invalid padding/length is
        # normalized to the adapter's error, never a leaked binascii.Error.
        for content in ("A", "QUJD\nRA", "===="):
            responses.append((200, json.dumps({"encoding": "base64", "content": content}).encode()))
            with self.subTest(content=content), self.assertRaises(runtime.GitHubApiError) as ctx:
                client.read_policy(REPOSITORY, POLICY_COMMIT)
            self.assertNotIsInstance(ctx.exception, runtime.GitHubTransientError)
        self.assertEqual(responses, [])
        # The real urllib transport classifies connection-level failures as transient.
        import urllib.error
        for failure in (urllib.error.URLError("unreachable"), TimeoutError(), OSError("reset")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(runtime.urllib.request, "urlopen", side_effect=failure), \
                    self.assertRaises(runtime.GitHubTransientError):
                runtime.urllib_transport("GET", "https://api.github.com/x", {}, None, 1.0)

    def test_registry_leaf_swapped_for_symlink_after_canonicalization_is_refused(self):
        document = json.dumps(self.fx.registry_document())
        root = self.fx.root.resolve()
        parent = root / "service"
        parent.mkdir()
        path = parent / "registry.json"
        path.write_text(document)
        path.chmod(0o600)
        elsewhere = root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "registry.json").write_text(document)
        (elsewhere / "registry.json").chmod(0o600)
        real_open = os.open
        swapped = threading.Event()

        def racing_open(name, flags, *args, **kwargs):
            # After the ancestors were canonicalized but before the leaf is opened, a
            # same-user writer replaces the regular leaf with a symlink to a valid
            # registry elsewhere.
            if not swapped.is_set() and "dir_fd" in kwargs:
                swapped.set()
                path.unlink()
                path.symlink_to(elsewhere / "registry.json")
            return real_open(name, flags, *args, **kwargs)

        with patch.object(entrypoint.os, "open", racing_open):
            with self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path, [])
        self.assertTrue(swapped.is_set())
        self.assertIn("unavailable or is a symlink", str(ctx.exception))
        # The leaf is now a genuine symlink: still refused, never canonicalized.
        with self.assertRaises(core.ContractError) as ctx:
            entrypoint.read_service_registry(path, [])
        self.assertIn("unavailable or is a symlink", str(ctx.exception))
        path.unlink()
        path.write_text(document)
        path.chmod(0o600)
        self.assertEqual(entrypoint.read_service_registry(path, []).enrollments[0].app_id, APP_ID)

    def test_registry_symlink_loop_is_a_clean_service_error(self):
        root = self.fx.root.resolve()
        (root / "loop-a").symlink_to(root / "loop-b")
        (root / "loop-b").symlink_to(root / "loop-a")
        with self.assertRaises(core.ContractError) as ctx:
            entrypoint.read_service_registry(root / "loop-a" / "registry.json", [])
        self.assertIn("registry is unavailable", str(ctx.exception))
        # Older CPython raises RuntimeError for loops instead of OSError; both are
        # normalized to the same fail-closed error.
        real_resolve = Path.resolve

        def looping_resolve(self, strict=False):
            if self.name == "loop-a":
                raise RuntimeError("Symlink loop from 'loop-a'")
            return real_resolve(self, strict=strict)

        with patch.object(Path, "resolve", looping_resolve):
            with self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(root / "loop-a" / "registry.json", [])
        self.assertIn("registry is unavailable", str(ctx.exception))

    def test_binding_identity_separates_apps_with_identical_review_and_policy(self):
        self.fx.ingest("initial", self.fx.payload())
        row = self.fx.binding_rows()[0]
        request = {
            "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
            "app_id": APP_ID, "installation_id": INSTALLATION_ID,
            "pr_number": row["pr_number"], "base_sha": row["base_sha"], "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"], "policy_commit": POLICY_COMMIT,
        }
        first = admission.admit(self.fx.registry(), request, self.fx.read)
        second = admission.admit(
            self.fx.registry(app_id=APP_ID + 1), {**request, "app_id": APP_ID + 1}, self.fx.read
        )
        self.assertEqual(first.binding_id, row["binding_id"])
        self.assertEqual((first.policy.policy_id, first.review), (second.policy.policy_id, second.review))
        self.assertNotEqual(first.binding_id, second.binding_id)

    def test_closed_duplicate_and_stale_deliveries_never_touch_policy_transport(self):
        def unavailable(repository, commit):
            self.fx.reads.append((repository, commit))
            raise runtime.GitHubTransientError("GitHub API transport failed")

        config = self.fx.app_config()

        def deliver(delivery, payload, *, reader):
            body, signature = self.fx.signed(payload)
            return runtime.handle_webhook_request(
                config=config, method="POST", path="/github/webhook",
                headers={"content-type": "application/json", "x-github-event": "pull_request",
                         "x-github-delivery": delivery, "x-hub-signature-256": signature},
                body=body, secret=SECRET,
                ingestor=lambda **kwargs: service.ingest_service_delivery(
                    **kwargs, registry=self.fx.registry(), read_policy=reader, service_config=config),
            )

        # A close arriving on a cold cache while the policy dependency is down is
        # still a valid, state-recording delivery: no policy read, no 503.
        closed = self.fx.payload()
        closed["action"] = "closed"
        status, payload = deliver("cold-close", closed, reader=unavailable)
        self.assertEqual(int(status), 202)
        self.assertEqual(self.fx.reads, [])
        self.assertEqual(self.fx.binding_rows(), [])
        # A binding candidate needs the policy: exactly one read, staged outside the
        # transaction, then the binding exists.
        opened = self.fx.payload()
        opened["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        status, payload = deliver("open", opened, reader=self.fx.read)
        self.assertEqual((int(status), payload["result"]), (202, "accepted"))
        self.assertEqual(self.fx.reads, [(REPOSITORY, POLICY_COMMIT)])
        self.assertEqual(len(self.fx.binding_rows()), 1)
        # With the cache cold again and the dependency down, an idempotent replay, a
        # stale delivery and a close all succeed without any policy read.
        service.clear_staged_policies()
        self.fx.reads.clear()
        status, payload = deliver("open", opened, reader=unavailable)
        self.assertEqual((int(status), payload["result"]), (202, "duplicate_delivery"))
        stale = self.fx.payload()
        stale["pull_request"]["updated_at"] = "2026-08-30T20:09:00Z"
        status, payload = deliver("stale", stale, reader=unavailable)
        self.assertEqual((int(status), payload["result"]), (202, "stale"))
        closed["pull_request"]["updated_at"] = "2026-08-30T20:12:00Z"
        status, payload = deliver("close", closed, reader=unavailable)
        self.assertEqual(int(status), 202)
        self.assertEqual(self.fx.reads, [])
        self.assertEqual(len(self.fx.binding_rows()), 1)
        # Only a genuinely new binding candidate reports the dependency outage, and
        # it does so without writing anything.
        fresh = self.fx.payload()
        fresh["action"] = "reopened"
        fresh["pull_request"]["head"]["sha"] = "e" * 40
        fresh["pull_request"]["updated_at"] = "2026-08-30T20:13:00Z"
        status, payload = deliver("fresh", fresh, reader=unavailable)
        self.assertEqual((int(status), payload), (503, {"ok": False, "reason": "dependency_unavailable"}))
        self.assertEqual(self.fx.reads, [(REPOSITORY, POLICY_COMMIT)])
        self.assertEqual(len(self.fx.binding_rows()), 1)
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE delivery_id='fresh'").fetchone()[0], 0)
        finally:
            connection.close()
        status, payload = deliver("fresh", fresh, reader=self.fx.read)
        self.assertEqual((int(status), payload["result"]), (202, "accepted"))
        self.assertEqual(len(self.fx.binding_rows()), 2)

    def test_staged_policy_cache_is_bounded_and_deduplicated(self):
        base = self.fx.registry().enrollments[0]
        enrollments = []
        for index in range(2 * service.MAX_STAGED_POLICIES + 1):
            content = json.dumps({"index": index}).encode()
            enrollments.append((dataclasses.replace(
                base, approved_policy_commit=f"{index:040x}", approved_policy_sha256=hashlib.sha256(content).hexdigest(),
            ), content))
        errors = []

        def stage(enrolled, content):
            try:
                self.assertEqual(service.stage_approved_policy(enrolled, lambda *_: content), content)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=stage, args=item) for item in enrollments]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertLessEqual(len(service._STAGED_POLICIES), service.MAX_STAGED_POLICIES)
        # A concurrent stager that lands first wins; the later fetch is discarded and
        # the bound is re-checked after the fetch, not before it.
        service.clear_staged_policies()
        enrolled, content = enrollments[0]
        first = bytes(content)

        def racing_reader(*_):
            service._STAGED_POLICIES[service._staged_key(enrolled)] = first
            return bytes(content)

        self.assertIs(service.stage_approved_policy(enrolled, racing_reader), first)
        self.assertEqual(len(service._STAGED_POLICIES), 1)

    def test_registry_parent_replaced_by_real_directory_after_canonicalization_is_refused(self):
        root = self.fx.root.resolve()
        parent = root / "service"
        parent.mkdir()
        path = parent / "registry.json"
        path.write_text(json.dumps(self.fx.registry_document()))
        path.chmod(0o600)
        replacement = root / "replacement"
        replacement.mkdir()
        (replacement / "registry.json").write_text(json.dumps(self.fx.registry_document(app_id=APP_ID + 1)))
        (replacement / "registry.json").chmod(0o600)
        real_open = os.open
        swapped = threading.Event()

        def racing_open(name, flags, *args, **kwargs):
            # A same-user writer renames a different real directory (not a symlink)
            # into the canonicalized parent's place; it passes owner/mode checks.
            if not swapped.is_set() and "dir_fd" in kwargs:
                swapped.set()
                os.rename(parent, root / "service-old")
                os.rename(replacement, parent)
            return real_open(name, flags, *args, **kwargs)

        with patch.object(entrypoint.os, "open", racing_open):
            with self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path, [])
        self.assertTrue(swapped.is_set())
        self.assertIn("parent changed after canonicalization", str(ctx.exception))
        # The directory now at that path is a legitimate canonical parent on its own.
        self.assertEqual(entrypoint.read_service_registry(path, []).enrollments[0].app_id, APP_ID + 1)

    def test_client_permissions_snapshot_ignores_later_caller_mutation(self):
        token_requests = []

        def transport(method, url, headers, body, timeout):
            if url.endswith("/access_tokens"):
                token_requests.append(json.loads(body))
                return 201, json.dumps({"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}).encode()
            content = base64.b64encode(self.fx.policy).decode()
            return 200, json.dumps({"encoding": "base64", "content": content}).encode()

        config = self.fx.app_config()
        config["github_app"]["permissions"] = dict(runtime.STANDALONE_APP_PERMISSIONS)
        client = runtime.GitHubAppClient(config, "fixture-private-key", transport=transport,
                                         signer=lambda *_: "fixture-jwt")
        # The caller widens its own map after construction; the adapter must keep
        # requesting exactly the validated allowlist.
        config["github_app"]["permissions"]["contents"] = "write"
        config["github_app"]["permissions"]["administration"] = "write"
        config["github_app"]["repository"] = "dinkuskit/blocks"
        self.assertEqual(client.read_policy(REPOSITORY, POLICY_COMMIT), self.fx.policy)
        expected = {name: level for name, level in runtime.STANDALONE_APP_PERMISSIONS.items() if name != "metadata"}
        self.assertEqual([request["permissions"] for request in token_requests], [expected])
        self.assertEqual(client.repository, REPOSITORY)

    def test_client_clawsweeper_snapshot_and_profile_selected_permission_allowlist(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            if url.endswith("/access_tokens"):
                return 201, json.dumps({"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}).encode()
            return 204, b""

        config = self.fx.app_config()
        client = runtime.GitHubAppClient(config, "fixture-private-key", transport=transport,
                                         signer=lambda *_: "fixture-jwt")
        # Widening the caller's ClawSweeper map after construction must not widen the
        # workflow-dispatch allowlist the client derived from it.
        config["clawsweeper"]["workflow_id"] = "other-workflow.yml"
        config["clawsweeper"]["ref"] = "release"
        with self.assertRaises(core.ContractError):
            client._call("POST", f"/repos/{REPOSITORY}/actions/workflows/other-workflow.yml/dispatches",
                         {"ref": "release"}, expected={204})
        self.assertEqual(calls, [])
        client._call("POST", f"/repos/{REPOSITORY}/actions/workflows/clawsweeper-exact-tuple.yml/dispatches",
                     {"ref": "main"}, expected={204})
        self.assertEqual([url.rsplit("/", 2)[-2] for _method, url in calls if "dispatches" in url],
                         ["clawsweeper-exact-tuple.yml"])
        # The permission allowlist is selected by profile kind: a generalized profile
        # must carry exactly the standalone map and a legacy profile exactly the
        # legacy map; neither may present the other's.
        legacy = {k: v for k, v in self.fx.app_config().items() if k not in ("review_policy", "clawsweeper", "adapter")}
        legacy["github_app"]["permissions"] = dict(runtime.APP_PERMISSIONS)
        runtime.GitHubAppClient(legacy, "fixture-private-key", transport=transport, signer=lambda *_: "fixture-jwt")
        for label, bad in (
            ("legacy-with-standalone", {**legacy, "github_app": {**legacy["github_app"], "permissions": dict(runtime.STANDALONE_APP_PERMISSIONS)}}),
            ("generalized-with-legacy", {**self.fx.app_config(), "github_app": {**self.fx.app_config()["github_app"], "permissions": dict(runtime.APP_PERMISSIONS)}}),
        ):
            with self.subTest(label=label), self.assertRaises(core.ContractError):
                runtime.GitHubAppClient(bad, "fixture-private-key", transport=transport, signer=lambda *_: "fixture-jwt")

    def test_registry_fifo_fails_closed_instead_of_blocking_startup(self):
        root = self.fx.root.resolve()
        parent = root / "service"
        parent.mkdir()
        path = parent / "registry.json"
        os.mkfifo(path, 0o600)
        outcome = {}

        def attempt():
            try:
                entrypoint.read_service_registry(path, [])
                outcome["result"] = "loaded"
            except core.ContractError as exc:
                outcome["result"] = str(exc)

        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "opening a FIFO registry path blocked startup")
        self.assertIn("regular file", outcome["result"])

    def test_hook_revalidates_the_reloaded_core_profile_before_persisting(self):
        original = json.loads(self.fx.config_path.read_text())
        rotated = copy.deepcopy(original)
        rotated["review_policy"]["reviewers"] = {"openclaw": "rotated-openclaw", "clawsweeper": REVIEWERS["clawsweeper"]}
        loads = []
        real_load = core.load_config

        def racing_load(path, *args, **kwargs):
            # Preflight sees the enrolled profile; the engine's own reload inside the
            # delivery transaction sees an edited one.
            loads.append(path)
            if len(loads) >= 2:
                self.fx.config_path.write_text(json.dumps(rotated) + "\n")
            try:
                return real_load(path, *args, **kwargs)
            finally:
                self.fx.config_path.write_text(json.dumps(original) + "\n")

        with patch.object(core, "load_config", racing_load):
            with self.assertRaises(core.ContractError) as ctx:
                self.fx.ingest("racing-profile", self.fx.payload())
        self.assertGreaterEqual(len(loads), 2)
        self.assertIn("reviewer identities contradict", str(ctx.exception))
        self.assertEqual(self.fx.binding_rows(), [])
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
        finally:
            connection.close()
        self.assertEqual(self.fx.ingest("steady-profile", self.fx.payload())["result"], "accepted")

    def test_tick_side_effects_revalidate_admission_before_each_mutating_call(self):
        self.fx.ingest("initial", self.fx.payload())
        calls = []
        resolutions = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            if url.endswith("/access_tokens"):
                return 201, json.dumps({"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}).encode()
            return 201, json.dumps({"id": 11}).encode()

        client = runtime.GitHubAppClient(self.fx.app_config(), "fixture-private-key", transport=transport,
                                         signer=lambda *_: "fixture-jwt")
        current = self.fx.registry()
        revoked = self.fx.registry(policy=self.fx.policy + b"\n")

        def provider():
            # The opening gate sees the current registry; the registry is revoked
            # before the tick's first mutating GitHub call.
            resolutions.append(len(resolutions))
            return current if len(resolutions) == 1 else revoked

        def fake_tick(config, tick_client, notifier, *, dry_run, enrollment=None):
            tick_client.create_check("OpenClaw Review Rail", HEAD, "external", "queued")
            return {"result": "unreachable"}

        with patch.object(userland, "run_tick", fake_tick):
            with self.assertRaises(core.ContractError):
                service.run_service_tick(self.fx.app_config(), provider, client, object(), dry_run=False)
        self.assertEqual(len(resolutions), 2)
        self.assertEqual([url for method, url in calls if method != "GET" and "check-runs" in url], [])
        # With authority intact the same side effect proceeds, and the guard is
        # consulted once more for it.
        resolutions.clear()
        with patch.object(userland, "run_tick", fake_tick):
            service.run_service_tick(self.fx.app_config(), lambda: current, client, object(), dry_run=False)
        self.assertEqual(len([url for method, url in calls if method == "POST" and "check-runs" in url]), 1)
        # Read-only calls are not side effects and pass without the guard; a client
        # that cannot carry the guard cannot run a live tick at all.
        self.assertIsNotNone(client._authority_guard)
        with self.assertRaises(core.ContractError):
            service.run_service_tick(self.fx.app_config(), lambda: current, object(), object(), dry_run=False)

        class InstallOnlyClient:
            def set_authority_guard(self, _guard):
                pass

        with self.assertRaises(core.ContractError) as ctx:
            service.run_service_tick(
                self.fx.app_config(), lambda: current,
                InstallOnlyClient(), object(), dry_run=False,
            )
        self.assertIn("install and assert", str(ctx.exception))

    def test_tuple_guard_rejects_superseded_work_under_a_new_valid_binding(self):
        self.fx.ingest("initial", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            old_head = connection.execute(
                "SELECT * FROM heads WHERE repository=? AND is_current=1",
                (REPOSITORY,),
            ).fetchone()
            old_authority = runtime.tuple_authority(old_head)
        finally:
            connection.close()

        newer = self.fx.payload()
        newer["action"] = "synchronize"
        newer["pull_request"]["head"]["sha"] = "3" * 40
        newer["pull_request"]["updated_at"] = "2026-08-29T20:01:00Z"
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            if url.endswith("/access_tokens"):
                return 201, json.dumps(
                    {
                        "token": "fixture-token",
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ).encode()
            return 201, json.dumps({"id": 11}).encode()

        client = runtime.GitHubAppClient(
            self.fx.app_config(),
            "fixture-private-key",
            transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        registry = self.fx.registry()

        def fake_tick(config, tick_client, notifier, *, dry_run, enrollment=None):
            # Tuple B is admitted after tuple A was selected. The repository-wide
            # gate is healthy for B, but it must not authorize A's pending write.
            self.assertEqual(
                self.fx.ingest("superseding", newer, registry=registry)["result"],
                "accepted",
            )
            service.require_current_bindings(config, registry)
            tick_client.create_check(
                "OpenClaw Review Rail",
                old_authority["head_sha"],
                "external",
                "queued",
                authority=old_authority,
            )
            return {"result": "unreachable"}

        with patch.object(userland, "run_tick", fake_tick):
            with self.assertRaises(core.AuthorityDenied):
                service.run_service_tick(
                    self.fx.app_config(), registry, client, object(), dry_run=False
                )
        self.assertEqual(
            [url for method, url in calls if method != "GET" and "check-runs" in url],
            [],
        )
        with self.assertRaises(core.ContractError):
            service.require_exact_current_binding(
                self.fx.app_config(), registry, old_authority
            )
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            new_head = connection.execute(
                "SELECT * FROM heads WHERE repository=? AND is_current=1",
                (REPOSITORY,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(new_head["head_sha"], "3" * 40)
        self.assertIsNotNone(
            service.require_exact_current_binding(
                self.fx.app_config(), registry, runtime.tuple_authority(new_head)
            )
        )

    def test_superseded_review_notification_cannot_use_the_new_heads_binding(self):
        self.fx.ingest("initial", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            userland.ensure_userland_tables(connection)
            old_head = connection.execute(
                "SELECT * FROM heads WHERE repository=? AND is_current=1",
                (REPOSITORY,),
            ).fetchone()
            payload = {
                "schema": userland.NOTIFICATION_SCHEMA,
                "event_key": "stale-review",
                "repository": old_head["repository"],
                "pr_number": old_head["pr_number"],
                "base_sha": old_head["base_sha"],
                "head_sha": old_head["head_sha"],
                "review_epoch": old_head["review_epoch"],
                "state": old_head["state"],
                "repair_cycle": old_head["repair_cycle"],
                "message": "stale review result",
                "merge_authorized": False,
            }
            connection.execute(
                """
                INSERT INTO notification_deliveries(
                  event_key, channel, repository, pr_number, base_sha, head_sha,
                  review_epoch, state, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_key"],
                    "discord",
                    payload["repository"],
                    payload["pr_number"],
                    payload["base_sha"],
                    payload["head_sha"],
                    payload["review_epoch"],
                    payload["state"],
                    core.canonical_json(payload),
                    core.utc_now(),
                    core.utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        newer = self.fx.payload()
        newer["action"] = "synchronize"
        newer["pull_request"]["head"]["sha"] = "3" * 40
        newer["pull_request"]["updated_at"] = "2026-08-29T20:01:00Z"
        registry = self.fx.registry()
        client = runtime.GitHubAppClient(
            self.fx.app_config(),
            "fixture-private-key",
            transport=lambda *_: (500, b""),
            signer=lambda *_: "fixture-jwt",
        )
        sent = []

        class Notifier:
            def send(self, channel, message):
                sent.append((channel, message))

        def fake_tick(config, tick_client, notifier, *, dry_run, enrollment=None):
            self.fx.ingest("superseding-notification", newer, registry=registry)
            return userland.deliver_notifications(
                config,
                notifier,
                dry_run=False,
                authority_client=tick_client,
            )

        with patch.object(userland, "run_tick", fake_tick), patch.object(
            userland, "queue_notifications", lambda _config, enrollment=None, authoritative=False: 0
        ):
            service.run_service_tick(
                self.fx.app_config(), registry, client, Notifier(), dry_run=False
            )
        self.assertEqual(sent, [])
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM notification_deliveries WHERE event_key='stale-review'"
                ).fetchone()[0],
                "retired",
            )
        finally:
            connection.close()

    def _load_route_registry(self, *, enroll=True, legacy=None, app_id=APP_ID):
        if enroll:
            doc = self.fx.registry_document(app_id=app_id)
        else:
            doc = {"schema": admission.REGISTRY_SCHEMA, "enrollments": []}
        if legacy is not None:
            doc = {**doc, "legacy_xapi": legacy}
        return admission.load_registry(json.dumps(doc).encode())

    def test_service_tick_wires_registry_owned_enrollment_routes(self):
        self.fx.ingest("initial", self.fx.payload())
        enabled = {"enabled": True, "blockers": []}
        blocked = {"enabled": True, "blockers": ["userland-blocker"]}
        this_profile = {"repository": REPOSITORY, "status": "present"}
        other_profile = {"repository": "dinkuskit/blocks", "status": "present"}
        explicit_absent = {"repository": REPOSITORY, "status": "absent"}

        cases = (
            (
                "conductor",
                self._load_route_registry(),
                {"review_conductor": "present", "legacy_xapi": "absent"},
                "review_conductor",
                True,
                False,
            ),
            (
                "legacy-only",
                self._load_route_registry(enroll=False, legacy=this_profile),
                {"review_conductor": "absent", "legacy_xapi": "present"},
                "legacy_xapi",
                False,
                False,
            ),
            (
                "none",
                self._load_route_registry(enroll=False),
                {"review_conductor": "absent", "legacy_xapi": "absent"},
                "none",
                False,
                False,
            ),
            (
                "none-other-profile-marker",
                self._load_route_registry(enroll=False, legacy=other_profile),
                {"review_conductor": "absent", "legacy_xapi": "absent"},
                "none",
                False,
                False,
            ),
            (
                "dual",
                self._load_route_registry(legacy=this_profile),
                {"review_conductor": "present", "legacy_xapi": "present"},
                "review_conductor",
                True,
                False,
            ),
            (
                "conductor-other-profile-marker",
                self._load_route_registry(legacy=other_profile),
                {"review_conductor": "present", "legacy_xapi": "absent"},
                "review_conductor",
                True,
                False,
            ),
            (
                "conductor-explicit-absent",
                self._load_route_registry(legacy=explicit_absent),
                {"review_conductor": "present", "legacy_xapi": "absent"},
                "review_conductor",
                True,
                False,
            ),
            (
                "broken",
                self._load_route_registry(app_id=APP_ID + 1),
                {"review_conductor": "broken", "legacy_xapi": "broken"},
                "fail_closed",
                False,
                True,
            ),
        )
        for name, registry, expected_pair, route, runs_stages, notifies in cases:
            with self.subTest(route=name):
                self.assertEqual(
                    service.trusted_enrollment_from_registry(self.fx.app_config(), registry),
                    expected_pair,
                )
                self.assertEqual(orchestration.enrollment_route(expected_pair)[0], route)
                decision = orchestration.decide_orchestration_outcome({
                    "schema": orchestration.INPUT_SCHEMA,
                    "enrollment": expected_pair,
                    "state": "ci_running",
                    "rail": None,
                    "repair_cycle": 0,
                    "head_changed": False,
                    "openclaw_result": "absent",
                    "clawsweeper_result": "absent",
                    "ready_qualified": None,
                    "adjudication_dispositions": None,
                    "human_gate": False,
                })
                self.assertFalse(decision["legacy_dispatch"])
                self.assertEqual(decision["route"], route)
                if route == "legacy_xapi":
                    self.assertEqual(decision["reason"], "legacy_xapi_handoff_required")
                    self.assertFalse(decision["review_dispatch"])
                if route == "fail_closed":
                    self.assertEqual(decision["reason"], "ambiguous_or_broken_enrollment")
                    self.assertFalse(decision["review_dispatch"])
                enabled_config = {**self.fx.app_config(), "enrollment": dict(enabled)}
                self.assertEqual(
                    service.trusted_enrollment_from_registry(enabled_config, registry),
                    expected_pair,
                )
                blocked_config = {**self.fx.app_config(), "enrollment": dict(blocked)}
                self.assertEqual(
                    service.trusted_enrollment_from_registry(blocked_config, registry),
                    expected_pair,
                )
                self.assertEqual(
                    orchestration.resolve_trusted_enrollment(blocked_config),
                    {"review_conductor": "broken", "legacy_xapi": "broken"},
                )
                captured = {}
                real_tick = userland.run_tick

                def wrapping_tick(config, client, notifier, *, dry_run, enrollment=None):
                    captured["enrollment"] = enrollment
                    captured["inferred"] = orchestration.resolve_trusted_enrollment(config)
                    return real_tick(
                        config, client, notifier, dry_run=True, enrollment=enrollment
                    )

                notifier = legacy.FakeNotifier()
                with patch.object(userland, "run_tick", wrapping_tick), patch.object(
                    userland, "hydrate_pending_openclaw_heads", lambda *a, **k: []
                ), patch.object(
                    runtime, "drain_actions",
                    lambda *a, **k: {
                        "schema": "smoky.review-conductor.worker-wake.v1",
                        "result": "planned",
                        "recovered": [],
                        "actions": ["planned"],
                        "failed_actions": [],
                        "github_ci_polled": False,
                        "merge_dispatched": False,
                    },
                ), patch.object(
                    runtime, "drain_bridge_inboxes",
                    lambda *a, **k: {
                        "schema": "smoky.review-conductor.bridge-drain.v1",
                        "result": "planned",
                        "artifacts": [],
                    },
                ), patch.object(
                    runtime, "reconcile_projection",
                    lambda *a, **k: {
                        "schema": "smoky.review-conductor.projection.v1",
                        "result": "planned",
                        "projected": [],
                        "merge_authorized": False,
                    },
                ), patch.object(
                    userland, "collect_openclaw_terminals", lambda *a, **k: []
                ), patch.object(
                    userland, "collect_clawsweeper_terminals", lambda *a, **k: []
                ):
                    tick = service.run_service_tick(
                        enabled_config, registry, object(), notifier, dry_run=True
                    )
                self.assertEqual(captured["enrollment"], expected_pair)
                self.assertEqual(
                    captured["inferred"],
                    {"review_conductor": "present", "legacy_xapi": "absent"},
                )
                if runs_stages:
                    self.assertEqual(tick["worker"]["result"], "planned")
                    self.assertEqual(tick["worker"]["actions"], ["planned"])
                    self.assertEqual(tick["bridges_before"]["result"], "planned")
                else:
                    self.assertEqual(tick["worker"]["result"], "skipped")
                    self.assertEqual(tick["worker"]["actions"], [])
                    self.assertEqual(tick["hydration"], [])
                    self.assertEqual(tick["openclaw"], [])
                    self.assertEqual(tick["clawsweeper"], [])
                    self.assertEqual(tick["bridges_before"]["result"], "skipped")
                    self.assertEqual(tick["projection"]["result"], "skipped")
                if notifies:
                    self.assertTrue(tick["notifications"]["deliveries"])
                    for item in tick["notifications"]["deliveries"]:
                        self.assertEqual(item["result"], "planned")
                else:
                    self.assertEqual(tick["notifications"]["deliveries"], [])

    def test_registry_legacy_xapi_malformed_and_synthetic_attributes_fail_closed(self):
        self.fx.ingest("initial", self.fx.payload())
        doc = self.fx.registry_document()
        malformed = [
            {**doc, "legacy_xapi": "present"},
            {**doc, "legacy_xapi": True},
            {**doc, "legacy_xapi": {"repository": REPOSITORY}},
            {**doc, "legacy_xapi": {"repository": REPOSITORY, "status": "broken"}},
            {**doc, "legacy_xapi": {"repository": "saari-co/x-api", "status": "present"}},
            {**{"schema": admission.REGISTRY_SCHEMA, "enrollments": []}, "legacy_xapi": "present"},
        ]
        enrollment_marker = copy.deepcopy(doc)
        enrollment_marker["enrollments"][0]["legacy_xapi"] = {
            "repository": REPOSITORY,
            "status": "present",
        }
        malformed.append(enrollment_marker)
        for item in malformed:
            with self.subTest(item=item), self.assertRaises(admission.AdmissionError):
                admission.load_registry(json.dumps(item).encode())

        forged = admission.Registry(())
        object.__setattr__(forged, "legacy_xapi", "present")
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), forged),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        with self.assertRaises(admission.AdmissionError):
            admission.Registry((), legacy_xapi="present")

        class ExtraAttrRegistry(admission.Registry):
            synthetic_legacy_xapi = "present"

        extra = ExtraAttrRegistry(())
        self.assertEqual(extra.synthetic_legacy_xapi, "present")
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), extra),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                self._load_route_registry(enroll=False),
            ),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )

        def broken_provider():
            admission.load_registry(json.dumps({**doc, "legacy_xapi": "present"}).encode())
            return self.fx.registry()

        with self.assertRaises(admission.AdmissionError):
            service.run_service_tick(
                self.fx.app_config(), broken_provider, object(), object(), dry_run=True
            )

    def test_registry_subclass_overrides_cannot_synthesize_routes(self):
        loaded = self._load_route_registry()
        empty = self._load_route_registry(enroll=False)
        dual = self._load_route_registry(
            legacy={"repository": REPOSITORY, "status": "present"}
        )
        fake = loaded.enrollments[0]

        class OverrideEmpty(admission.Registry):
            def legacy_status_for(self, repository):
                return "present"

            def lookup(self, *args):
                return fake

            def __getattribute__(self, name):
                if name == "enrollments":
                    return (fake,)
                return object.__getattribute__(self, name)

        forged_empty = OverrideEmpty(())
        self.assertEqual(forged_empty.legacy_status_for(REPOSITORY), "present")
        self.assertEqual(forged_empty.enrollments, (fake,))
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), forged_empty),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )

        class DenyLoaded(admission.Registry):
            def legacy_status_for(self, repository):
                raise admission.AdmissionError("forged legacy")

            def lookup(self, *args):
                raise admission.AdmissionError("forged lookup")

        denied = DenyLoaded(loaded.enrollments, loaded.legacy_xapi)
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), denied),
            {"review_conductor": "present", "legacy_xapi": "absent"},
        )
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                DenyLoaded(dual.enrollments, dual.legacy_xapi),
            ),
            {"review_conductor": "present", "legacy_xapi": "present"},
        )
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                OverrideEmpty(empty.enrollments, empty.legacy_xapi),
            ),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )

    def test_nested_enrollment_virtual_authority_fails_closed(self):
        loaded = self._load_route_registry()
        real = loaded.enrollments[0]
        admitted = {
            name: object.__getattribute__(real, name)
            for name in admission.Enrollment.__dataclass_fields__
        }

        class VirtualEnrollment(admission.Enrollment):
            def __post_init__(self):
                return

            def __getattribute__(self, name):
                if name in admitted:
                    return admitted[name]
                return object.__getattribute__(self, name)

        forged = VirtualEnrollment(
            "saari-co/x-api",
            1,
            1,
            1,
            "attacker",
            "0" * 40,
            "0" * 64,
            "forged-openclaw",
            "forged-clawsweeper",
        )
        self.assertEqual(forged.repository, REPOSITORY)
        self.assertEqual(forged.repository_id, REPOSITORY_ID)
        self.assertEqual(forged.app_id, APP_ID)
        self.assertEqual(forged.installation_id, INSTALLATION_ID)
        self.assertEqual(forged.reviewers, real.reviewers)
        wrapper = admission.Registry((forged,))
        self.assertEqual(
            wrapper.lookup(REPOSITORY, REPOSITORY_ID, APP_ID, INSTALLATION_ID),
            forged,
        )
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), wrapper),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        class SubclassEnrollment(admission.Enrollment):
            pass

        subclassed = SubclassEnrollment(
            *(object.__getattribute__(real, name) for name in admission.Enrollment.__dataclass_fields__)
        )
        self.assertEqual(subclassed.repository, REPOSITORY)
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((subclassed,)),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        extra = admission.Enrollment(
            *(object.__getattribute__(real, name) for name in admission.Enrollment.__dataclass_fields__)
        )
        object.__getattribute__(extra, "__dict__")["synthetic"] = REPOSITORY
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((extra,)),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        missing = admission.Enrollment(
            *(object.__getattribute__(real, name) for name in admission.Enrollment.__dataclass_fields__)
        )
        missing_wrapper = admission.Registry((missing,))
        del object.__getattribute__(missing_wrapper.enrollments[0], "__dict__")["repository"]
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), missing_wrapper),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        mutated = admission.Enrollment(
            *(object.__getattribute__(real, name) for name in admission.Enrollment.__dataclass_fields__)
        )
        object.__setattr__(mutated, "repository", "saari-co/x-api")
        self.assertEqual(mutated.repository, "saari-co/x-api")
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((mutated,)),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

    def test_nested_legacy_xapi_marker_virtual_authority_fails_closed(self):
        class VirtualMarker(admission.LegacyXapiMarker):
            def __post_init__(self):
                return

            def __getattribute__(self, name):
                if name == "repository":
                    return REPOSITORY
                if name == "status":
                    return "present"
                return object.__getattribute__(self, name)

        forged = VirtualMarker("saari-co/x-api", "broken")
        self.assertEqual(forged.repository, REPOSITORY)
        self.assertEqual(forged.status, "present")
        wrapper = admission.Registry((), legacy_xapi=forged)
        self.assertEqual(wrapper.legacy_status_for(REPOSITORY), "present")
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), wrapper),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                self._load_route_registry(enroll=False),
            ),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )

        class SubclassMarker(admission.LegacyXapiMarker):
            pass

        subclassed = SubclassMarker(REPOSITORY, "present")
        self.assertEqual(subclassed.status, "present")
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((), legacy_xapi=subclassed),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        extra = admission.LegacyXapiMarker(REPOSITORY, "present")
        object.__getattribute__(extra, "__dict__")["synthetic"] = "present"
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((), legacy_xapi=extra),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        missing = admission.LegacyXapiMarker(REPOSITORY, "present")
        missing_wrapper = admission.Registry((), legacy_xapi=missing)
        del object.__getattribute__(missing_wrapper.legacy_xapi, "__dict__")["status"]
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), missing_wrapper),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

        mutated = admission.LegacyXapiMarker(REPOSITORY, "present")
        object.__setattr__(mutated, "status", "broken")
        self.assertEqual(mutated.status, "broken")
        self.assertEqual(
            service.trusted_enrollment_from_registry(
                self.fx.app_config(),
                admission.Registry((), legacy_xapi=mutated),
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

    def test_malformed_service_profile_repository_is_broken_not_unenrolled(self):
        empty = self._load_route_registry(enroll=False)
        self.assertEqual(
            service.trusted_enrollment_from_registry(self.fx.app_config(), empty),
            {"review_conductor": "absent", "legacy_xapi": "absent"},
        )
        cases = (
            None,
            1,
            True,
            "",
            "saari-co/x-api",
            "Saari-Co/openclaw-smcbd-suite",
        )
        for repository in cases:
            with self.subTest(repository=repository):
                config = copy.deepcopy(self.fx.app_config())
                config["github_app"]["repository"] = repository
                self.assertEqual(
                    service.trusted_enrollment_from_registry(config, empty),
                    {"review_conductor": "broken", "legacy_xapi": "broken"},
                )
        missing = copy.deepcopy(self.fx.app_config())
        del missing["github_app"]["repository"]
        self.assertEqual(
            service.trusted_enrollment_from_registry(missing, empty),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        not_a_map = copy.deepcopy(self.fx.app_config())
        not_a_map["github_app"] = REPOSITORY
        self.assertEqual(
            service.trusted_enrollment_from_registry(not_a_map, empty),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

    def test_notification_event_identity_distinguishes_route_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            pr = 215
            legacy.ingress(config, "pull_request", "identity-pr", legacy.pr_payload(pr))
            legacy.ingress(
                config,
                "workflow_run",
                "identity-ci",
                legacy.ci_payload(pr, 2215, conclusion="failure"),
            )
            first = userland.deliver_notifications(
                config, legacy.UnavailableNotifier(), dry_run=False
            )
            self.assertEqual(
                [item["result"] for item in first["deliveries"]],
                ["not_ready", "not_ready", "not_ready"],
            )
            original_rows = _delivery_rows(config)
            self.assertEqual(len(original_rows), 3)
            original = {row["event_key"] for row in original_rows}
            self.assertEqual(len(original), 1)
            legacy.set_trusted_enrollment(config, "broken", "absent")
            notifier = legacy.FakeNotifier()
            changed = userland.deliver_notifications(config, notifier, dry_run=False)
            results = [item["result"] for item in changed["deliveries"]]
            self.assertEqual(results.count("retired"), 3)
            self.assertEqual(results.count("sent"), 3)
            sent = {row["event_key"] for row in _delivery_rows(config) if row["status"] == "sent"}
            self.assertTrue(sent)
            self.assertTrue(sent.isdisjoint(original))
            again = userland.deliver_notifications(config, legacy.FakeNotifier(), dry_run=False)
            self.assertEqual(again["deliveries"], [])
            self.assertEqual(len(_delivery_rows(config)), 6)

    # --- Invariant 1: enrollment/profile identity -------------------------------

    def test_profile_disabled_after_startup_fails_every_gate_closed(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        original = self.fx.config_path.read_text()
        disabled = json.loads(original)
        disabled["review_policy"]["enabled"] = False
        self.fx.config_path.write_text(json.dumps(disabled) + "\n")
        with self.assertRaises(core.ContractError) as ctx:
            service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        self.assertIn("not enabled", str(ctx.exception))
        with self.assertRaises(core.ContractError):
            service.require_profile_enrolled(self.fx.app_config(), self.fx.registry())
        ready = self.fx.payload()
        ready["action"] = "ready_for_review"
        ready["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        with self.assertRaises(core.ContractError):
            self.fx.ingest("while-disabled", ready)
        self.assertEqual(len(self.fx.binding_rows()), 1)
        self.fx.config_path.write_text(original)
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())

    # --- Invariant 2: registry filesystem authority -------------------------------

    def test_forbidden_root_created_during_walk_is_refused(self):
        root = self.fx.root.resolve()
        parent = root / "service"
        parent.mkdir()
        path = parent / "registry.json"
        path.write_text(json.dumps(self.fx.registry_document()))
        path.chmod(0o600)
        state_root = root / "state-root"  # does not exist when identities are snapshotted
        real_open = os.open
        created = threading.Event()

        def racing_open(name, flags, *args, **kwargs):
            # After the forbidden-root snapshot, the state root appears and points at
            # the registry's parent; by path the registry is now inside it.
            if not created.is_set() and "dir_fd" in kwargs:
                created.set()
                state_root.symlink_to(parent)
            return real_open(name, flags, *args, **kwargs)

        with patch.object(entrypoint.os, "open", racing_open):
            with self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path, [state_root])
        self.assertTrue(created.is_set())
        self.assertIn("entered a forbidden root", str(ctx.exception))
        state_root.unlink()
        self.assertEqual(entrypoint.read_service_registry(path, [state_root]).enrollments[0].app_id, APP_ID)

    # --- Invariant 3: policy fetch / replay / transaction -------------------------

    def test_hook_reresolves_registry_inside_the_transaction_before_admission(self):
        current = self.fx.registry()
        revoked = self.fx.registry(app_id=APP_ID + 1)
        resolutions = []

        def revoking_provider():
            # Preflight sees the enrollment; the hook, inside the transaction, sees it
            # revoked for this App.
            resolutions.append(len(resolutions))
            return current if len(resolutions) == 1 else revoked

        with self.assertRaises(core.ContractError):
            self.fx.ingest("revoked-in-flight", self.fx.payload(), registry=revoking_provider)
        self.assertEqual(len(resolutions), 2)
        self.assertEqual(self.fx.binding_rows(), [])
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
        finally:
            connection.close()
        # A promotion observed by the hook is staged for the promoted enrollment and
        # the binding lands under the promoted policy, never the preflight one.
        promoted_policy = self.fx.policy + b"\n"
        promoted = self.fx.registry(policy=promoted_policy)
        resolutions.clear()

        def promoting_provider():
            resolutions.append(len(resolutions))
            if len(resolutions) >= 3:
                self.fx.policy = promoted_policy
                return promoted
            return current

        receipt = self.fx.ingest("promoted-in-flight", self.fx.payload(), registry=promoting_provider)
        self.assertEqual(receipt["result"], "accepted")
        rows = self.fx.binding_rows()
        self.assertEqual([row["policy_sha256"] for row in rows], [hashlib.sha256(promoted_policy).hexdigest()])
        self.assertEqual(self.fx.reads, [(REPOSITORY, POLICY_COMMIT)] * 2)

    # --- Invariant 4: adapter least privilege / immutable config -------------------

    def test_legacy_client_cannot_read_repository_contents(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            return 200, b"{}"

        legacy = {k: v for k, v in self.fx.app_config().items() if k not in ("review_policy", "clawsweeper", "adapter")}
        legacy["github_app"]["permissions"] = dict(runtime.APP_PERMISSIONS)
        client = runtime.GitHubAppClient(legacy, "fixture-private-key", transport=transport, signer=lambda *_: "fixture-jwt")
        with self.assertRaises(core.ContractError):
            client.read_policy(REPOSITORY, POLICY_COMMIT)
        with self.assertRaises(core.ContractError):
            client._call("GET", f"/repos/{REPOSITORY}/contents/.review-conductor.json?ref={POLICY_COMMIT}", None, expected={200})
        self.assertEqual(calls, [])

    # --- Invariant 5: worker/tick side-effect fencing ------------------------------

    def test_guard_runs_after_token_minting_immediately_before_the_mutating_request(self):
        self.fx.ingest("initial", self.fx.payload())
        current = self.fx.registry()
        revoked = self.fx.registry(policy=self.fx.policy + b"\n")
        state = {"registry": current}
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            if url.endswith("/access_tokens"):
                # Authority is revoked while the token is being minted.
                state["registry"] = revoked
                return 201, json.dumps({"token": "***", "expires_at": "2099-01-01T00:00:00Z"}).encode()
            return 201, json.dumps({"id": 11}).encode()

        client = runtime.GitHubAppClient(self.fx.app_config(), "fixture-private-key", transport=transport,
                                         signer=lambda *_: "fixture-jwt")
        config = self.fx.app_config()
        client.set_authority_guard(
            lambda _m, _p, _authority: service.require_current_bindings(
                config, lambda: state["registry"]
            )
        )
        with self.assertRaises(core.ContractError):
            client.create_check("OpenClaw Review Rail", HEAD, "external", "queued")
        self.assertEqual([url.rsplit("/", 1)[-1] for _m, url in calls], ["access_tokens"])

    def test_openclaw_dispatch_and_notifications_are_fenced_by_the_authority_guard(self):
        self.fx.ingest("initial", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE heads SET state='openclaw_queued' WHERE is_current=1")
            row = connection.execute("SELECT * FROM heads WHERE is_current=1").fetchone()
            action_id, idempotency_key = core.action_identity(
                "openclaw.enqueue", row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
                core.review_action_suffix(int(row["review_epoch"])),
            )
            connection.execute(
                """INSERT INTO actions(action_id, idempotency_key, kind, repository, pr_number, base_sha, head_sha,
                   review_epoch, status, payload_json, created_at, updated_at)
                   VALUES (?, ?, 'openclaw.enqueue', ?, ?, ?, ?, ?, 'pending', '{}', ?, ?)""",
                (action_id, idempotency_key, row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
                 row["review_epoch"], core.utc_now(), core.utc_now()),
            )
            connection.commit()
        finally:
            connection.close()
        config = {**self.fx.app_config(), "worker": {"max_actions_per_wake": 5, "claim_lease_seconds": 60},
                  "paths": {**self.fx.app_config()["paths"], "blocks_checkout": str(self.fx.root / "checkout")}}
        current = self.fx.registry()
        revoked = self.fx.registry(policy=self.fx.policy + b"\n")
        resolutions = []

        def provider():
            resolutions.append(len(resolutions))
            return current if len(resolutions) == 1 else revoked

        client = runtime.GitHubAppClient(self.fx.app_config(), "fixture-private-key",
                                         transport=lambda *_: (500, b""), signer=lambda *_: "fixture-jwt")
        dispatched = []
        neutral = lambda *args, **kwargs: {}
        with patch.object(core, "dispatch_action", lambda args: dispatched.append(args.action_id) or {}), \
                patch.object(runtime, "recover_abandoned_actions", lambda _config: []), \
                patch.object(runtime, "service_transport_environment", lambda _config: {}), \
                patch.object(runtime, "drain_bridge_inboxes", neutral), \
                patch.object(userland, "hydrate_pending_openclaw_heads", neutral):
            with self.assertRaises(core.ContractError):
                service.run_service_tick(config, provider, client, object(), dry_run=False)
        # The opening gate passed; the per-dispatch fence saw the revocation and no
        # OpenClaw dispatch ran. Without the fence the pending action is dispatched.
        self.assertEqual(len(resolutions), 2)
        self.assertEqual(dispatched, [])
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            self.assertEqual([row["status"] for row in connection.execute("SELECT status FROM actions")], ["pending"])
        finally:
            connection.close()

    def test_openclaw_dispatch_uses_an_isolated_subprocess_environment(self):
        self.fx.ingest("environment-isolation", self.fx.payload())
        connection = core.open_database(self.fx.state, REPOSITORY)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE heads SET state='openclaw_queued' WHERE is_current=1")
            row = connection.execute("SELECT * FROM heads WHERE is_current=1").fetchone()
            action_id, idempotency_key = core.action_identity(
                "openclaw.enqueue",
                row["repository"],
                row["pr_number"],
                row["base_sha"],
                row["head_sha"],
                core.review_action_suffix(int(row["review_epoch"])),
            )
            connection.execute(
                """INSERT INTO actions(action_id, idempotency_key, kind, repository, pr_number, base_sha, head_sha,
                   review_epoch, status, payload_json, created_at, updated_at)
                   VALUES (?, ?, 'openclaw.enqueue', ?, ?, ?, ?, ?, 'pending', '{}', ?, ?)""",
                (
                    action_id,
                    idempotency_key,
                    row["repository"],
                    row["pr_number"],
                    row["base_sha"],
                    row["head_sha"],
                    row["review_epoch"],
                    core.utc_now(),
                    core.utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        config = {
            **self.fx.app_config(),
            "worker": {"max_actions_per_wake": 5, "claim_lease_seconds": 60},
            "paths": {
                **self.fx.app_config()["paths"],
                "blocks_checkout": str(self.fx.root / "checkout"),
            },
            "spark": {
                "target": "fixture-spark",
                "smoky_path": "bin/smoky",
                "ssh_path": "/usr/bin/ssh",
                "scp_path": "/usr/bin/scp",
            },
        }
        started = threading.Event()
        release = threading.Event()
        captured = []
        failures = []

        class AllowClient:
            @staticmethod
            def assert_authority(_operation):
                return None

        def dispatch(args):
            captured.append(args.command_environment)
            started.set()
            if not release.wait(5):
                raise AssertionError("test dispatch was not released")
            return {"result": "fixture"}

        def drain():
            try:
                runtime.drain_actions(config, AllowClient(), dry_run=False)
            except BaseException as exc:
                failures.append(exc)

        sentinel = "REVIEW_CONDUCTOR_THREAD_SENTINEL"
        concurrent = "REVIEW_CONDUCTOR_CONCURRENT_SENTINEL"
        old_sentinel = os.environ.get(sentinel)
        old_concurrent = os.environ.get(concurrent)
        os.environ[sentinel] = "parent-thread"
        try:
            with patch.object(core, "dispatch_action", side_effect=dispatch), patch.object(
                runtime, "recover_abandoned_actions", return_value=[]
            ):
                thread = threading.Thread(target=drain)
                thread.start()
                self.assertTrue(started.wait(5), "adapter dispatch did not start")
                self.assertEqual(os.environ.get(sentinel), "parent-thread")
                os.environ[concurrent] = "written-during-dispatch"
                release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive(), "adapter dispatch did not finish")
            self.assertEqual(failures, [])
            self.assertEqual(os.environ.get(sentinel), "parent-thread")
            self.assertEqual(os.environ.get(concurrent), "written-during-dispatch")
            self.assertEqual(len(captured), 1)
            self.assertNotIn(sentinel, captured[0])
            self.assertNotIn(concurrent, captured[0])
            self.assertEqual(captured[0]["SMOKY_REVIEW_CONDUCTOR_SMOKY"], "bin/smoky")
            self.assertEqual(captured[0]["SPARK_OPENCLAW_AUTOREVIEW_TARGET"], "fixture-spark")
        finally:
            release.set()
            if old_sentinel is None:
                os.environ.pop(sentinel, None)
            else:
                os.environ[sentinel] = old_sentinel
            if old_concurrent is None:
                os.environ.pop(concurrent, None)
            else:
                os.environ[concurrent] = old_concurrent

    def test_checkout_hydration_is_fenced_before_the_checkout_is_touched(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            legacy.ingress(config, "pull_request", "hydrate-pr", legacy.pr_payload(78))
            legacy.ingress(config, "workflow_run", "hydrate-ci", legacy.ci_payload(78, 1078))
            touched = []

            class RevokedClient:
                def assert_authority(self, operation):
                    self.operation = operation
                    raise service.ServiceError("revoked before hydration")

            client = RevokedClient()
            with patch.object(
                userland, "hydrate_exact_pr_head",
                lambda *_args, **_kwargs: touched.append(True),
            ):
                with self.assertRaises(core.ContractError):
                    userland.hydrate_pending_openclaw_heads(
                        config, dry_run=False, authority_client=client
                    )
            self.assertEqual(touched, [])
            self.assertRegex(client.operation, r"^checkout-hydration:act-[0-9a-f]{32}$")

            connection = core.open_database(Path(config["paths"]["state_root"]), config["repository"])
            try:
                self.assertEqual(connection.execute(
                    "SELECT status FROM actions WHERE kind='openclaw.enqueue'"
                ).fetchone()[0], "pending")
            finally:
                connection.close()

    def test_authority_denial_inside_checkout_hydration_keeps_action_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            legacy.ingress(config, "pull_request", "hydrate-pr", legacy.pr_payload(80))
            legacy.ingress(config, "workflow_run", "hydrate-ci", legacy.ci_payload(80, 1080))

            with patch.object(
                userland, "hydrate_exact_pr_head",
                side_effect=core.AuthorityDenied("revoked during hydration"),
            ):
                with self.assertRaises(core.AuthorityDenied):
                    userland.hydrate_pending_openclaw_heads(
                        config, dry_run=False, authority_client=None
                    )
            connection = core.open_database(Path(config["paths"]["state_root"]), config["repository"])
            try:
                row = connection.execute(
                    "SELECT status, claim_owner FROM actions WHERE kind='openclaw.enqueue'"
                ).fetchone()
                self.assertEqual((row["status"], row["claim_owner"]), ("pending", None))
            finally:
                connection.close()

    def test_each_openclaw_external_command_has_a_fresh_authority_fence(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            legacy.ingress(config, "pull_request", "dispatch-pr", legacy.pr_payload(79))
            legacy.ingress(config, "workflow_run", "dispatch-ci", legacy.ci_payload(79, 1079))
            action = legacy.action(config, 79, "openclaw.enqueue")
            fences = []
            commands = []
            environments = []

            def fence(index, _command):
                fences.append(index)
                if index == 1:
                    raise core.AuthorityDenied("revoked between OpenClaw commands")

            def run(command, _label, *, environment=None):
                commands.append(command)
                environments.append(environment)
                receipt = {"result": "completed", "commit_sha": legacy.HEAD}
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(receipt) + "\n", stderr=""
                )

            args = argparse.Namespace(
                config=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                action_id=action["action_id"],
                source_checkout=Path(config["paths"]["blocks_checkout"]),
                apply=True,
                retry=False,
                claim_owner="fixture-worker",
                claim_lease_seconds=60,
                before_external_command=fence,
                command_environment={
                    "PATH": "/usr/bin:/bin",
                    "SMOKY_REVIEW_CONDUCTOR_SMOKY": "fixture-smoky",
                },
            )
            with patch.object(core, "run_command", run):
                with self.assertRaises(core.ContractError):
                    core.dispatch_action(args)
            self.assertEqual(fences, [0, 1])
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][0], "fixture-smoky")
            self.assertEqual(environments, [args.command_environment])
            connection = core.open_database(Path(config["paths"]["state_root"]), config["repository"])
            try:
                row = connection.execute(
                    "SELECT status, claim_owner FROM actions WHERE action_id=?",
                    (action["action_id"],),
                ).fetchone()
                self.assertEqual((row["status"], row["claim_owner"]), ("pending", None))
            finally:
                connection.close()

    def test_each_notification_send_has_a_fresh_authority_fence(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            userland.queue_operator_alert(config, "fixture-alert", "fixture message")
            fences = []
            sent = []

            class FenceClient:
                def assert_authority(self, operation):
                    fences.append(operation)
                    if len(fences) == 2:
                        raise service.ServiceError("revoked between notifications")

            class Notifier:
                def send(self, channel, message):
                    sent.append((channel, message))

            with patch.object(userland, "queue_notifications", lambda _config, enrollment=None, authoritative=False: 0):
                with self.assertRaises(core.ContractError):
                    userland.deliver_notifications(
                        config, Notifier(), dry_run=False,
                        authority_client=FenceClient(),
                    )
            self.assertEqual(len(fences), 2)
            self.assertEqual(len(sent), 1)

    def test_notification_is_uncertain_before_transport_and_survives_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            userland.queue_operator_alert(config, "crash-safe", "fixture message")
            observed = []

            class CrashingNotifier:
                def send(_self, channel, _message):
                    connection = core.open_database(Path(config["paths"]["state_root"]))
                    try:
                        row = connection.execute(
                            """
                            SELECT status, attempts FROM notification_deliveries
                            WHERE channel = ?
                            """,
                            (channel,),
                        ).fetchone()
                        observed.append((channel, row["status"], row["attempts"]))
                    finally:
                        connection.close()
                    raise KeyboardInterrupt("simulated process death")

            with patch.object(userland, "queue_notifications", lambda _config, enrollment=None, authoritative=False: 0):
                with self.assertRaises(KeyboardInterrupt):
                    userland.deliver_notifications(
                        config, CrashingNotifier(), dry_run=False
                    )

            self.assertEqual(observed, [("openclaw_context", "uncertain", 1)])
            connection = core.open_database(Path(config["paths"]["state_root"]))
            try:
                rows = connection.execute(
                    """
                    SELECT channel, status, attempts FROM notification_deliveries
                    ORDER BY channel
                    """
                ).fetchall()
                self.assertEqual(
                    [(row["channel"], row["status"], row["attempts"]) for row in rows],
                    [
                        ("discord", "pending", 0),
                        ("openclaw_context", "uncertain", 1),
                        ("signal", "pending", 0),
                    ],
                )
            finally:
                connection.close()

            sent = []

            class RecordingNotifier:
                def send(_self, channel, message):
                    sent.append((channel, message))

            with patch.object(userland, "queue_notifications", lambda _config, enrollment=None, authoritative=False: 0):
                userland.deliver_notifications(
                    config, RecordingNotifier(), dry_run=False
                )
            self.assertEqual([channel for channel, _message in sent], ["discord", "signal"])

            connection = core.open_database(Path(config["paths"]["state_root"]))
            try:
                rows = connection.execute(
                    """
                    SELECT channel, status, attempts FROM notification_deliveries
                    ORDER BY channel
                    """
                ).fetchall()
                self.assertEqual(
                    [(row["channel"], row["status"], row["attempts"]) for row in rows],
                    [
                        ("discord", "sent", 1),
                        ("openclaw_context", "uncertain", 1),
                        ("signal", "sent", 1),
                    ],
                )
            finally:
                connection.close()

    def test_notification_subprocess_uses_an_allowlisted_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = legacy.config_fixture(Path(temporary))
            observed = {}

            def run(command, **kwargs):
                observed["command"] = command
                observed["environment"] = kwargs["env"]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"result": "sent"}) + "\n",
                    stderr="",
                )

            environment = {
                "HOME": str(Path(temporary) / "home"),
                "USER": "fixture-user",
                "LOGNAME": "fixture-user",
                "PATH": "/fixture/bin:/usr/bin:/bin",
                "TMPDIR": "/tmp",
                "LC_ALL": "C",
                config["notifications"]["discord_target_env"]: "channel:fixture",
                config["credentials"]["webhook_secret_fd_env"]: "41",
                config["credentials"]["github_private_key_fd_env"]: "42",
                "OP_SERVICE_ACCOUNT_TOKEN": "must-not-reach-child",
            }
            notifier = userland.OpenClawNotifier(
                config, runner=run, environment=environment
            )
            notifier.send("discord", "fixture message")

            self.assertIn("channel:fixture", observed["command"])
            self.assertEqual(
                observed["environment"],
                {
                    "HOME": environment["HOME"],
                    "USER": "fixture-user",
                    "LOGNAME": "fixture-user",
                    "PATH": "/fixture/bin:/usr/bin:/bin",
                    "TMPDIR": "/tmp",
                    "LC_ALL": "C",
                    "PYTHONUNBUFFERED": "1",
                },
            )

    def test_gate_database_is_read_only_and_disappearance_fails_closed(self):
        self.fx.ingest("initial", self.fx.payload())
        database = self.fx.state / "review-conductor.sqlite3"
        connection = service._gate_connection(self.fx.state)
        self.assertIsNotNone(connection)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE authority_bypass(value TEXT)")
        finally:
            connection.close()

        real_connect = sqlite3.connect

        def disappear_then_open(*args, **kwargs):
            database.unlink()
            return real_connect(*args, **kwargs)

        with patch.object(service.sqlite3, "connect", disappear_then_open):
            with self.assertRaises(core.ContractError) as ctx:
                service._gate_connection(self.fx.state)
        self.assertIn("could not be opened read-only", str(ctx.exception))

    # --- Invariant 6: evidence accuracy --------------------------------------------

    def test_evidence_never_claims_automatic_github_redelivery(self):
        root = Path(__file__).resolve().parents[1]
        claim = re.compile(r"GitHub\s+(automatically\s+)?redelivers|automatically\s+redeliver|GitHub\s+retries", re.IGNORECASE)
        offenders = []
        for file in [*root.glob("docs/*.md"), *root.glob("proof/*/PROOF.md"), *root.glob("tools/*.py"), root / "README.md"]:
            if file.exists() and claim.search(file.read_text()):
                offenders.append(str(file.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_registry_ancestor_swapped_for_symlink_after_canonicalization_is_refused(self):
        document = json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        })
        root = self.fx.root.resolve()
        parent = root / "service"
        parent.mkdir()
        path = parent / "registry.json"
        path.write_text(document)
        path.chmod(0o600)
        elsewhere = root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "registry.json").write_text(document)
        (elsewhere / "registry.json").chmod(0o600)
        real_open = os.open
        swapped = threading.Event()

        def racing_open(name, flags, *args, **kwargs):
            # After canonicalization but before the walk reaches it, a same-user
            # writer replaces the validated parent directory with a symlink.
            if not swapped.is_set() and "dir_fd" in kwargs:
                swapped.set()
                os.rename(parent, root / "service-moved")
                parent.symlink_to(elsewhere)
            return real_open(name, flags, *args, **kwargs)

        with patch.object(entrypoint.os, "open", racing_open):
            with self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path, [])
        self.assertTrue(swapped.is_set())
        self.assertIn("component is unavailable or is a symlink", str(ctx.exception))
        # Once the parent is a genuine directory again the same path loads normally.
        parent.unlink()
        os.rename(root / "service-moved", parent)
        self.assertEqual(entrypoint.read_service_registry(path, []).enrollments[0].app_id, APP_ID)
        # Forbidden roots are recognised by device/inode on the held directories,
        # independent of the pathname comparison.
        with self.assertRaises(core.ContractError) as ctx:
            entrypoint._registry_bytes(path.parent, path.name, [parent])
        self.assertIn("service-owned", str(ctx.exception))
        with self.assertRaises(core.ContractError) as ctx:
            entrypoint._registry_bytes(path.parent, path.name, [root])
        self.assertIn("service-owned", str(ctx.exception))

    def test_worker_gate_requires_the_profile_to_be_the_registry_enrollment(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        for key, value in [("app_id", APP_ID + 1), ("installation_id", INSTALLATION_ID + 1),
                           ("repository_id", REPOSITORY_ID + 1), ("repository", "dinkuskit/blocks"),
                           ("app_id", str(APP_ID)), ("app_id", None)]:
            config = self.fx.app_config()
            config["github_app"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(core.ContractError) as ctx:
                service.require_current_bindings(config, self.fx.registry())
            self.assertIn("not enrolled", str(ctx.exception))
        mismatched = {
            **self.fx.app_config(),
            "github_app": {**self.fx.app_config()["github_app"], "app_id": APP_ID + 1},
        }
        captured = {}
        real_tick = userland.run_tick

        def wrapping_tick(config, client, notifier, *, dry_run, enrollment=None):
            captured["enrollment"] = enrollment
            return real_tick(
                config, client, notifier, dry_run=dry_run, enrollment=enrollment
            )

        with patch.object(userland, "run_tick", wrapping_tick):
            tick = service.run_service_tick(
                mismatched, self.fx.registry(), object(), object(), dry_run=True
            )
        self.assertEqual(
            captured["enrollment"],
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        self.assertEqual(tick["worker"]["result"], "skipped")
        self.assertEqual(tick["hydration"], [])
        self.assertEqual(tick["openclaw"], [])
        self.assertEqual(tick["clawsweeper"], [])

        original = json.loads(self.fx.config_path.read_text())
        for key, value in (
            ("repository", "dinkuskit/blocks"),
            ("repository_id", REPOSITORY_ID + 1),
        ):
            changed = copy.deepcopy(original)
            changed[key] = value
            self.fx.config_path.write_text(json.dumps(changed) + "\n")
            with self.subTest(core_identity=key), self.assertRaises(core.ContractError) as ctx:
                service.require_profile_enrolled(self.fx.app_config(), self.fx.registry())
            self.assertIn("core repository identity contradicts", str(ctx.exception))
        self.fx.config_path.write_text(json.dumps(original) + "\n")

    def test_reviewer_actors_come_from_enrollment_not_the_profile(self):
        self.fx.ingest("initial", self.fx.payload())
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        core_config = json.loads(self.fx.config_path.read_text())
        for reviewers in [
            {"openclaw": "attacker-openclaw", "clawsweeper": REVIEWERS["clawsweeper"]},
            {"openclaw": REVIEWERS["openclaw"], "clawsweeper": "attacker-clawsweeper"},
            {"openclaw": REVIEWERS["clawsweeper"], "clawsweeper": REVIEWERS["openclaw"]},
        ]:
            changed = copy.deepcopy(core_config)
            changed["review_policy"]["reviewers"] = reviewers
            self.fx.config_path.write_text(json.dumps(changed) + "\n")
            with self.subTest(reviewers=reviewers, gate="tick"), self.assertRaises(core.ContractError) as ctx:
                service.require_current_bindings(self.fx.app_config(), self.fx.registry())
            self.assertIn("reviewer identities contradict", str(ctx.exception))
            fresh = self.fx.payload()
            fresh["pull_request"]["head"]["sha"] = "e" * 40
            fresh["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
            with self.subTest(reviewers=reviewers, gate="delivery"), self.assertRaises(core.ContractError):
                self.fx.ingest("changed-reviewers", fresh)
            self.assertEqual([row["head_sha"] for row in self.fx.binding_rows()], [HEAD])
        self.fx.config_path.write_text(json.dumps(core_config) + "\n")
        service.require_current_bindings(self.fx.app_config(), self.fx.registry())
        # Registry documents without reviewers, with duplicate or malformed actors, are refused.
        base = json.loads(json.dumps({"schema": admission.REGISTRY_SCHEMA, "enrollments": [{
            "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
            "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID, "installation_account": "saari-co"},
            "approved_policy": {"commit": POLICY_COMMIT, "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
            "reviewers": dict(REVIEWERS)}]}))
        bad = []
        item = copy.deepcopy(base); del item["enrollments"][0]["reviewers"]; bad.append(item)
        item = copy.deepcopy(base); item["enrollments"][0]["reviewers"] = {"openclaw": "same", "clawsweeper": "same"}; bad.append(item)
        item = copy.deepcopy(base); item["enrollments"][0]["reviewers"] = {"openclaw": "", "clawsweeper": "x"}; bad.append(item)
        item = copy.deepcopy(base); item["enrollments"][0]["reviewers"] = {"openclaw": 12, "clawsweeper": "x"}; bad.append(item)
        item = copy.deepcopy(base); item["enrollments"][0]["reviewers"] = {"openclaw": "a", "clawsweeper": "b", "human": "c"}; bad.append(item)
        item = copy.deepcopy(base); item["enrollments"][0]["reviewers"] = {"openclaw": " a", "clawsweeper": "b"}; bad.append(item)
        for index, item in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(admission.AdmissionError):
                admission.load_registry(json.dumps(item).encode())

    def test_registry_cannot_live_in_source_checkout_state_or_proof_roots(self):
        document = json.loads(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }))
        root = self.fx.root.resolve()
        checkout = root / "Developer/review-conductor/openclaw-smcbd-suite"
        state = root / ".local/state/review-conductor"
        proof = root / ".local/share/review-conductor/proof"
        config = {"paths": {
            "blocks_checkout": str(checkout), "state_root": str(state), "proof_root": str(proof),
        }}
        roots = entrypoint.forbidden_registry_roots(config)
        self.assertIn(entrypoint.ROOT, roots)
        forbidden = [
            checkout / ".review-conductor-registry.json",
            checkout / "config/registry.json",
            state / "registry.json",
            proof / "registry.json",
            proof / "inbox/openclaw/registry.json",
            checkout / "nested/../registry.json",
        ]
        for candidate in forbidden:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(json.dumps(document))
            candidate.chmod(0o600)
            with self.subTest(candidate=str(candidate.relative_to(root))), \
                    self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(candidate, roots)
            self.assertIn("service-owned", str(ctx.exception))
        # This repository's own tree is never a registry location, even with a valid file.
        with tempfile.NamedTemporaryFile("w", dir=ROOT, prefix=".tmp-registry-", suffix=".json",
                                         delete=False) as handle:
            handle.write(json.dumps(document))
        in_source = Path(handle.name)
        self.addCleanup(in_source.unlink)
        in_source.chmod(0o600)
        with self.assertRaises(core.ContractError):
            entrypoint.read_service_registry(in_source, roots)
        # A sibling service-owned location outside every root is accepted.
        allowed = root / ".config/review-conductor/registry.json"
        allowed.parent.mkdir(parents=True)
        allowed.write_text(json.dumps(document))
        allowed.chmod(0o600)
        self.assertEqual(entrypoint.read_service_registry(allowed, roots).enrollments[0].app_id, APP_ID)

    def test_registry_provider_observes_promotion_and_revocation_without_restart(self):
        path = self.fx.root.resolve() / "registry.json"

        def write_registry(policy):
            document = {
                "schema": admission.REGISTRY_SCHEMA,
                "enrollments": [{
                    "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                    "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                                   "installation_account": "saari-co"},
                    "approved_policy": {"commit": POLICY_COMMIT,
                                        "sha256": hashlib.sha256(policy).hexdigest()},
                    "reviewers": dict(REVIEWERS),
                }],
            }
            path.write_text(json.dumps(document))
            path.chmod(0o600)

        write_registry(self.fx.policy)
        provider = entrypoint.registry_provider(path, {
            "core_config": str(self.fx.config_path),
            "paths": {key: str(self.fx.root.resolve() / key) for key in (
                "blocks_checkout", "state_root", "proof_root")},
            "github_app": self.fx.app_config()["github_app"],
        })
        receipt = self.fx.ingest("initial", self.fx.payload(), registry=provider)
        self.assertEqual(receipt["result"], "accepted")
        service.require_current_bindings(self.fx.app_config(), provider)
        # Promotion written to the registry file is observed by the next tick and delivery.
        promoted = self.fx.policy + b"\n"
        write_registry(promoted)
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(self.fx.app_config(), provider)
        self.fx.policy = promoted
        fresh = self.fx.payload()
        fresh["pull_request"]["head"]["sha"] = "e" * 40
        fresh["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        receipt = self.fx.ingest("promoted-head", fresh, registry=provider)
        self.assertEqual(receipt["result"], "accepted")
        self.assertEqual(self.fx.binding_rows()[-1]["policy_sha256"], hashlib.sha256(promoted).hexdigest())
        service.require_current_bindings(self.fx.app_config(), provider)
        # Revocation or corruption of the registry fails every later delivery and tick closed,
        # never falling back to the previously loaded approval.
        path.chmod(0o644)
        with self.assertRaises(core.ContractError):
            service.require_current_bindings(self.fx.app_config(), provider)
        newer = self.fx.payload()
        newer["pull_request"]["head"]["sha"] = "f" * 40
        newer["pull_request"]["updated_at"] = "2026-08-30T20:12:00Z"
        with self.assertRaises(core.ContractError):
            self.fx.ingest("revoked", newer, registry=provider)
        self.assertEqual([row["head_sha"] for row in self.fx.binding_rows()], [HEAD, "e" * 40])
        for bad in (object(), lambda: {"enrollments": []}, None):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(core.ContractError):
                service.resolve_registry(bad)

    def test_workflow_run_deliveries_never_create_bindings(self):
        self.fx.ingest("pr", self.fx.payload())
        first = self.fx.binding_rows()
        self.assertEqual(len(first), 1)
        reads = len(self.fx.reads)
        core_config = json.loads(self.fx.config_path.read_text())

        def ci_run(run_id, head, *, created="2026-08-29T20:15:00Z"):
            payload = copy.deepcopy(legacy.ci_payload(7, run_id, head))
            payload["repository"] = {"full_name": REPOSITORY, "id": REPOSITORY_ID}
            payload["installation"] = {"id": INSTALLATION_ID}
            payload["workflow"] = {"name": core_config["ci"]["workflow_name"], "path": core_config["ci"]["workflow_path"]}
            payload["workflow_run"]["created_at"] = created
            payload["workflow_run"]["updated_at"] = "2026-08-29T20:20:00Z"
            return payload

        def claw_run(run_id, head):
            payload = copy.deepcopy(legacy.claw_workflow_payload(run_id))
            payload["repository"] = {"full_name": REPOSITORY, "id": REPOSITORY_ID}
            payload["installation"] = {"id": INSTALLATION_ID}
            payload["workflow"] = {"name": core_config["clawsweeper"]["workflow_name"],
                                  "path": core_config["clawsweeper"]["workflow_path"]}
            payload["workflow_run"]["head_sha"] = head
            payload["workflow_run"]["pull_requests"] = [{"number": 7, "base": {"ref": "main", "sha": BASE},
                                                       "head": {"sha": head}}]
            return payload

        def ingest(delivery, payload):
            body, signature = self.fx.signed(payload)
            return service.ingest_service_delivery(
                config_path=self.fx.config_path, state_root=self.fx.state, event_type="workflow_run",
                delivery_id=delivery, signature=signature, body=body, secret=SECRET,
                registry=self.fx.registry(), read_policy=self.fx.read, service_config=self.fx.app_config(),
            )

        # Exact-head CI, a ClawSweeper dispatch naming the current head, a ClawSweeper
        # dispatch naming an unrelated head, and stale CI are all engine evidence only.
        results = [
            ingest("ci-exact", ci_run(1001, HEAD))["result"],
            ingest("claw-current", claw_run(2001, HEAD))["result"],
            ingest("claw-unrelated", claw_run(2002, "9" * 40))["result"],
            ingest("ci-stale", ci_run(1002, "9" * 40))["result"],
        ]
        self.assertEqual(results, ["accepted", "accepted", "accepted", "stale"])
        self.assertEqual(self.fx.binding_rows(), first)
        self.assertEqual(len(self.fx.reads), reads)
        # Closing the PR is recorded even when the registry has since promoted the policy,
        # and a closed head is not a live head demanding a binding.
        promoted_registry = self.fx.registry(policy=self.fx.policy + b"\n")
        self.fx.policy = self.fx.policy + b"\n"
        closed = self.fx.payload()
        closed["action"] = "closed"
        closed["pull_request"]["state"] = "closed"
        closed["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        self.assertEqual(self.fx.ingest("closed", closed, registry=promoted_registry)["result"], "accepted")
        self.assertEqual(self.fx.binding_rows(), first)
        service.require_current_bindings(self.fx.app_config(), promoted_registry)

    def test_admitted_policy_must_match_the_engine_profile(self):
        manifest = json.loads(self.fx.policy)
        variants = {
            "default_branch": lambda m: m.__setitem__("default_branch", "release"),
            "workflow_name": lambda m: m["ci"].__setitem__("workflow_name", "Other pipeline"),
            "workflow_path": lambda m: m["ci"].__setitem__("workflow_path", ".github/workflows/other.yml"),
        }
        for label, mutate in variants.items():
            mutated = copy.deepcopy(manifest)
            mutate(mutated)
            raw = json.dumps(mutated, indent=2).encode() + b"\n"
            self.fx.policy = raw
            with self.subTest(label=label), self.assertRaises(core.ContractError) as ctx:
                self.fx.ingest(f"mismatch-{label}", self.fx.payload(), registry=self.fx.registry(policy=raw))
            self.assertIn("contradicts the engine profile", str(ctx.exception))
            connection = core.open_database(self.fx.state, REPOSITORY)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM heads").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
            finally:
                connection.close()
        # Whitespace-only reformatting of an agreeing manifest still binds.
        agreeing = json.dumps(manifest, indent=4).encode()
        self.fx.policy = agreeing
        self.assertEqual(self.fx.ingest("agreeing", self.fx.payload(), registry=self.fx.registry(policy=agreeing))["result"], "accepted")
        with self.assertRaises(core.ContractError):
            service.require_policy_matches_profile(
                admission.load_approved_policy(self.fx.registry().enrollments[0], POLICY_COMMIT, self.fx.read),
                {**json.loads(self.fx.config_path.read_text()), "merge_policy": "auto"},
            )

    def test_real_bounded_http_ingress_admits_replays_and_rejects_bad_deliveries(self):
        config = self.fx.app_config()
        handler = service.build_service_http_handler(
            config,
            secret=SECRET,
            registry=self.fx.registry(),
            read_policy=self.fx.read,
        )
        server = runtime.BoundedHTTPServer(
            ("127.0.0.1", 0), handler, request_timeout_seconds=5
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(delivery, body, signature, *, event="pull_request", path="/github/webhook",
                 content_type="application/json"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("POST", path, body, {
                    "Content-Type": content_type,
                    "X-GitHub-Event": event,
                    "X-GitHub-Delivery": delivery,
                    "X-Hub-Signature-256": signature,
                })
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            body, signature = self.fx.signed(self.fx.payload())
            status, result = post("http-delivery", body, signature)
            self.assertEqual((status, result["ok"], result["result"]), (202, True, "accepted"))
            self.assertEqual(result["merge_dispatched"], False)
            first = self.fx.binding_rows()
            self.assertEqual(len(first), 1)
            # Replay of the identical delivery is idempotent and creates no second binding.
            status, result = post("http-delivery", body, signature)
            self.assertEqual((status, result["result"]), (202, "duplicate_delivery"))
            # Delivery-ID reuse with different content, forged/missing signatures, foreign
            # installations, other repositories, malformed JSON, wrong paths and event types
            # are all refused without leaking detail or touching bindings.
            changed = self.fx.payload()
            changed["pull_request"]["updated_at"] = "2026-08-30T20:10:30Z"
            changed_body, changed_signature = self.fx.signed(changed)
            other_body, other_signature = self.fx.signed(self.fx.payload(installation=INSTALLATION_ID + 1))
            foreign_body, foreign_signature = self.fx.signed(
                self.fx.payload(repository="dinkuskit/blocks", repository_id=1306882611)
            )
            garbage = b'{"repository": '
            garbage_signature = "sha256=" + hmac.new(SECRET.encode(), garbage, hashlib.sha256).hexdigest()
            rejected = [
                dict(delivery="http-delivery", body=changed_body, signature=changed_signature),
                dict(delivery="forged", body=body, signature="sha256=" + "0" * 64),
                dict(delivery="unsigned", body=body, signature=""),
                dict(delivery="cross-install", body=other_body, signature=other_signature),
                dict(delivery="cross-repo", body=foreign_body, signature=foreign_signature),
                dict(delivery="garbage", body=garbage, signature=garbage_signature),
                dict(delivery="bad event", body=body, signature=signature),
                dict(delivery="issue-event", body=body, signature=signature, event="issues"),
            ]
            for item in rejected:
                with self.subTest(item=item["delivery"]):
                    status, result = post(**item)
                    self.assertEqual((status, result), (400, {"ok": False, "reason": "request_rejected"}))
            status, result = post("wrong-path", body, signature, path="/")
            self.assertEqual(status, 404)
            status, result = post("wrong-type", body, signature, content_type="text/plain")
            self.assertEqual(status, 415)
            self.assertEqual(self.fx.binding_rows(), first)
            # A newer head supersedes the tuple; the old head's delivery is then stale and
            # cannot bind, while the new head gets exactly one binding.
            fresh = self.fx.payload()
            fresh["pull_request"]["head"]["sha"] = "e" * 40
            fresh["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
            status, result = post("fresh-head", *self.fx.signed(fresh))
            self.assertEqual((status, result["result"]), (202, "accepted"))
            status, result = post("stale-head", body, signature)
            self.assertEqual((status, result["result"]), (202, "stale"))
            self.assertEqual([row["head_sha"] for row in self.fx.binding_rows()], [HEAD, "e" * 40])
            # One hash-verified read staged the approved bytes; later deliveries reuse them.
            self.assertEqual(self.fx.reads, [(REPOSITORY, POLICY_COMMIT)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class EntrypointTests(unittest.TestCase):
    """The service entrypoint stays inert unless every external precondition holds."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "source"
        contracts = self.source / "contracts/review-conductor"
        contracts.mkdir(parents=True)
        for file in (ROOT / "contracts/review-conductor").glob("*.json"):
            shutil.copyfile(file, contracts / file.name)
        self.profile = contracts / "openclaw-smcbd-suite-userland.json"
        self.core_profile = contracts / "openclaw-smcbd-suite.json"
        real_load = userland.load_config
        patcher = patch.object(
            userland, "load_config",
            lambda path, **_: real_load(path, home=self.home, source_root=self.source),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, registry_path):
        stderr = io.StringIO()
        with patch.object(sys, "stderr", stderr):
            code = entrypoint.main(["--profile", str(self.profile), "--registry", str(registry_path), "--apply"])
        return code, stderr.getvalue()

    def enable_profile(self):
        core_config = json.loads(self.core_profile.read_text())
        core_config["review_policy"].update(
            enabled=True, reviewers={"openclaw": "fixture-openclaw", "clawsweeper": "fixture-clawsweeper"}
        )
        self.core_profile.write_text(json.dumps(core_config) + "\n")
        profile = json.loads(self.profile.read_text())
        profile["enrollment"] = {"enabled": True, "blockers": []}
        profile["tunnel"].update(tunnel_id="00000000-0000-4000-8000-000000000001")
        self.profile.write_text(json.dumps(profile) + "\n")

    def test_service_holds_restrictive_umask_for_threaded_lifecycle(self):
        self.enable_profile()
        expected_config = userland.load_config(self.profile)
        current_mask = {"value": 0o022}
        transitions = []

        def fake_umask(value):
            previous = current_mask["value"]
            current_mask["value"] = value
            transitions.append((previous, value))
            return previous

        def exercise_lifecycle(config, registry_path):
            self.assertEqual(current_mask["value"], 0o077)
            self.assertEqual(config, expected_config)
            self.assertEqual(registry_path, self.root / "registry.json")

        with patch.object(entrypoint.os, "umask", side_effect=fake_umask), patch.object(
            entrypoint, "registry_provider", return_value=lambda: object()
        ), patch.object(
            entrypoint,
            "_serve_with_restrictive_umask",
            side_effect=exercise_lifecycle,
        ):
            entrypoint.serve(self.profile, self.root / "registry.json")

        self.assertEqual(current_mask["value"], 0o022)
        self.assertEqual(transitions, [(0o022, 0o077), (0o077, 0o022)])

    def test_service_and_maintenance_are_mutually_exclusive(self):
        config = userland.load_config(self.profile)
        registry = self.root / "registry.json"
        with entrypoint.exclusive_service_lock(config, registry):
            with self.assertRaises(service.ServiceError):
                with entrypoint.exclusive_service_lock(config, self.root / "registry-copy.json"):
                    self.fail("a concurrent maintenance lock must not be acquired")

    def test_service_uses_the_config_that_selected_the_tenant_lock(self):
        self.enable_profile()
        loaded = userland.load_config(self.profile)
        seen = []
        with patch.object(userland, "load_config", side_effect=lambda _path: seen.append(True) or loaded), \
                patch.object(entrypoint, "registry_provider", return_value=lambda: object()), \
                patch.object(entrypoint, "_serve_with_restrictive_umask") as serve_inner:
            entrypoint.serve(self.profile, self.root / "registry.json")
        self.assertEqual(len(seen), 1)
        self.assertIs(serve_inner.call_args.args[0], loaded)
        seen.clear()
        with patch.object(userland, "load_config", side_effect=lambda _path: seen.append(True) or loaded), \
                patch.object(entrypoint, "registry_provider", return_value=lambda: object()), \
                patch.object(entrypoint, "_run_maintenance_unlocked", return_value={}) as maintenance_inner:
            entrypoint.run_maintenance(
                self.profile, self.root / "registry-copy.json", "retry-openclaw", 7, apply=True
            )
        self.assertEqual(len(seen), 1)
        self.assertIs(maintenance_inner.call_args.args[0], loaded)

    def test_shipped_candidate_profile_is_refused_before_registry_or_credentials(self):
        missing_registry = self.root / "never-created.json"
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(missing_registry)
        self.assertEqual(code, 2)
        self.assertIn("repository profile is inactive", stderr)
        self.assertIn("Contents: read", stderr)
        self.assertFalse(missing_registry.exists())
        self.assertFalse((self.home / ".local").exists())

    def test_symlink_loop_registry_path_exits_two_without_a_traceback(self):
        self.enable_profile()
        root = self.root.resolve()
        (root / "loop-a").symlink_to(root / "loop-b")
        (root / "loop-b").symlink_to(root / "loop-a")
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(root / "loop-a" / "registry.json")
        self.assertEqual(code, 2)
        self.assertIn("service enrollment registry is unavailable", stderr)
        self.assertNotIn("Traceback", stderr)
        real_resolve = Path.resolve

        def looping_resolve(self, strict=False):
            if self.name == "loop-a":
                raise RuntimeError("Symlink loop from 'loop-a'")
            return real_resolve(self, strict=strict)

        with patch.object(Path, "resolve", looping_resolve), \
                patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(root / "loop-a" / "registry.json")
        self.assertEqual(code, 2)
        self.assertIn("service enrollment registry is unavailable", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_enabled_profile_still_requires_registry_agreement_before_credentials(self):
        self.enable_profile()
        registry = self.root / "registry.json"
        document = {
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID + 1, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT, "sha256": "f" * 64},
                "reviewers": dict(REVIEWERS),
            }],
        }
        registry.write_text(json.dumps(document))
        registry.chmod(0o600)
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("runtime profile is not enrolled in the service registry", stderr)
        # The registry, not the profile, names the reviewer actors the engine trusts.
        document["enrollments"][0]["github_app"]["id"] = APP_ID
        document["enrollments"][0]["reviewers"] = {"openclaw": "someone-else", "clawsweeper": REVIEWERS["clawsweeper"]}
        registry.write_text(json.dumps(document))
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("reviewer identities contradict service enrollment", stderr)
        registry.chmod(0o640)
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("mode 0600 exactly", stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_failed_ingress_bind_never_starts_the_worker(self):
        self.enable_profile()
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256((ROOT / "examples/smcbd.review-conductor.json").read_bytes()).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }))
        registry.chmod(0o600)
        ticked = threading.Event()

        class Client:
            def read_policy(self, *_):
                raise AssertionError("policy transport ran during failed startup")

        def bind_failure(*_args, **_kwargs):
            raise OSError("address already in use")

        with patch.object(userland, "read_inherited_value", return_value="fixture-secret"), \
                patch.object(userland, "build_client", return_value=Client()), \
                patch.object(userland, "OpenClawNotifier", return_value=object()), \
                patch.object(service, "run_service_tick", side_effect=lambda *a, **k: ticked.set()), \
                patch.object(runtime, "BoundedHTTPServer", side_effect=bind_failure):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("address already in use", stderr)
        self.assertFalse(ticked.wait(1.0), "worker ran a tick although ingress never bound")
        self.assertNotIn("review-conductor-service", [thread.name for thread in threading.enumerate()])

    def test_worker_operational_failure_stops_the_whole_service(self):
        self.enable_profile()
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256((ROOT / "examples/smcbd.review-conductor.json").read_bytes()).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }))
        registry.chmod(0o600)
        closed = threading.Event()

        class FakeServer:
            def __init__(self, *_args, **_kwargs):
                self.stopped = threading.Event()

            def serve_forever(self, poll_interval=0.5):
                # Returns when shut down by the worker, or after a bounded wait as if
                # an operator stopped it, so a silently dead worker is observable.
                self.stopped.wait(3.0)

            def shutdown(self):
                self.stopped.set()

            def server_close(self):
                closed.set()

        class Client:
            def read_policy(self, *_):
                raise AssertionError("policy transport ran")

        with patch.object(userland, "read_inherited_value", return_value="fixture-secret"), \
                patch.object(userland, "build_client", return_value=Client()), \
                patch.object(userland, "OpenClawNotifier", return_value=object()), \
                patch.object(service, "run_service_tick", side_effect=sqlite3.OperationalError("database is locked")), \
                patch.object(runtime, "BoundedHTTPServer", FakeServer):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("worker failed; service stopped", stderr)
        self.assertTrue(closed.is_set())
        self.assertNotIn("review-conductor-service", [thread.name for thread in threading.enumerate()])

    def test_shutdown_waits_for_the_non_daemon_worker_without_tick_timeout(self):
        self.enable_profile()
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256((ROOT / "examples/smcbd.review-conductor.json").read_bytes()).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }))
        registry.chmod(0o600)
        observed = {}

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                observed.update(target=target, name=name, daemon=daemon)

            def start(self):
                observed["started"] = True

            def is_alive(self):
                return True

            def join(self, *args, **kwargs):
                observed["join"] = (args, kwargs)

        class FakeServer:
            def __init__(self, *_args, **_kwargs):
                pass

            def serve_forever(self, poll_interval=0.5):
                return None

            def server_close(self):
                observed["closed"] = True

        class Client:
            def read_policy(self, *_args):
                raise AssertionError("policy transport ran")

        with patch.object(userland, "read_inherited_value", return_value="fixture-secret"), \
                patch.object(userland, "build_client", return_value=Client()), \
                patch.object(userland, "OpenClawNotifier", return_value=object()), \
                patch.object(entrypoint.threading, "Thread", FakeThread), \
                patch.object(runtime, "BoundedHTTPServer", FakeServer):
            entrypoint.serve(self.profile, registry)
        self.assertEqual(observed["daemon"], False)
        self.assertEqual(observed["join"], ((), {}))
        self.assertTrue(observed["started"])
        self.assertTrue(observed["closed"])

    def test_apply_flag_and_arguments_are_mandatory(self):
        for argv in [[], ["--profile", str(self.profile), "--registry", str(self.root)],
                     ["--profile", str(self.profile), "--apply"]]:
            with self.subTest(argv=argv), patch.object(sys, "stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as ctx:
                entrypoint.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_maintenance_sqlite_failure_is_a_controlled_service_error(self):
        argv = [
            "--profile",
            str(self.profile),
            "--registry",
            str(self.root / "registry.json"),
            "--dry-run",
            "retry-openclaw",
            "--pr",
            "7",
        ]
        with patch.object(
            entrypoint,
            "run_maintenance",
            side_effect=sqlite3.OperationalError("database is locked"),
        ), patch.object(sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(entrypoint.main(argv), 2)
        self.assertIn("review-conductor-service: database is locked", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_maintenance_modes_use_outer_and_transaction_binding_gates(self):
        self.enable_profile()
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256((ROOT / "examples/smcbd.review-conductor.json").read_bytes()).hexdigest()},
                "reviewers": dict(REVIEWERS),
            }],
        }))
        registry.chmod(0o600)
        transaction_connections = []

        def retry(_config, pr_number, *, apply, authority_guard):
            connection = object()
            authority_guard(connection)
            transaction_connections.append(("retry", connection, pr_number, apply))
            return {"result": "planned"}

        def reconcile(
            _config, pr_number, channel, disposition, confirmation, *, apply, authority_guard
        ):
            connection = object()
            authority_guard(connection)
            transaction_connections.append(
                ("reconcile", connection, pr_number, channel, disposition, confirmation, apply)
            )
            return {"result": "reconciled"}

        target_checks = []

        def require_target(connection, _config, _registry, pr_number):
            target_checks.append((connection, pr_number))
            return {}

        base = ["--profile", str(self.profile), "--registry", str(registry)]
        with patch.object(service, "require_current_bindings") as outer, \
                patch.object(service, "require_current_binding", side_effect=require_target), \
                patch.object(userland, "retry_failed_openclaw", side_effect=retry), \
                patch.object(userland, "reconcile_uncertain_notification", side_effect=reconcile), \
                patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(
                entrypoint.main(base + ["--dry-run", "retry-openclaw", "--pr", "7"]), 0
            )
            self.assertEqual(
                entrypoint.main(base + [
                    "--apply", "reconcile-notification", "--pr", "7",
                    "--channel", "discord", "--disposition", "retry",
                    "--confirm", "provider-nondelivery-observed",
                ]),
                0,
            )
        self.assertEqual(outer.call_count, 2)
        self.assertEqual([item[0] for item in transaction_connections], ["retry", "reconcile"])
        self.assertEqual(target_checks, [
            (transaction_connections[0][1], 7),
            (transaction_connections[1][1], 7),
        ])

        with patch.object(
            service, "require_current_bindings",
            side_effect=service.ServiceError("current binding revoked"),
        ), patch.object(
            userland, "retry_failed_openclaw",
            side_effect=AssertionError("mutation ran after a denied opening gate"),
        ), patch.object(sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(
                entrypoint.main(base + ["--dry-run", "retry-openclaw", "--pr", "7"]), 2
            )
        self.assertIn("current binding revoked", stderr.getvalue())


MUTANTS = [
    # (label, relative file, exact old substring, replacement, test id that must fail)
    (
        "bypass HMAC verification before parsing",
        "tools/service_runtime.py",
        "    core.verify_github_signature(body, signature, secret)\n",
        "    pass\n",
        "AdmissionIngressTests.test_authentication_precedes_payload_registry_and_policy_processing",
    ),
    (
        "ignore the configured App identity",
        "tools/service_runtime.py",
        '            app.get("repository"), app.get("repository_id"), app.get("app_id"), app.get("installation_id")\n',
        '            app.get("repository"), app.get("repository_id"), registry.enrollments[0].app_id, app.get("installation_id")\n',
        "AdmissionIngressTests.test_configured_app_identity_must_match_the_registry",
    ),
    (
        "admit a foreign installation",
        "tools/service_runtime.py",
        "    installation_id = _installation_id(payload)\n",
        "    installation_id = enrolled.installation_id\n",
        "AdmissionIngressTests.test_unknown_installation_repository_and_duplicate_json_fail_before_state",
    ),
    (
        "rebind an exact tuple to a conflicting policy",
        "tools/service_runtime.py",
        "    if prior is not None and tuple(prior) != expected:\n",
        "    if False:\n",
        "AdmissionIngressTests.test_policy_promotion_invalidates_existing_binding",
    ),
    (
        "bypass current-policy binding enforcement before worker projection",
        "tools/service_runtime.py",
        '                raise ServiceError("current review tuple lacks a current approved-policy binding")\n',
        "                continue\n",
        "AdmissionIngressTests.test_policy_promotion_invalidates_existing_binding",
    ),
    (
        "request Contents write instead of read-only",
        "tools/review_conductor_runtime.py",
        '    "contents": "read",\n}\n',
        '    "contents": "write",\n}\n',
        "GitHubAdapterTests.test_token_is_exact_installation_repository_and_permission_scoped",
    ),
    (
        "allow reading any repository content path",
        "tools/review_conductor_runtime.py",
        '                rf"/repos/{repository}/contents/\\.review-conductor\\.json\\?ref=[0-9a-f]{{40}}",\n',
        '                rf"/repos/{repository}/contents/[^?]+\\?ref=[0-9a-f]{{40}}",\n',
        "GitHubAdapterTests.test_policy_reader_and_check_publisher_cannot_cross_repository_or_merge",
    ),
    (
        "skip the service enrollment check in the entrypoint",
        "tools/service_entrypoint.py",
        "        service.require_profile_enrolled(config, registry)\n        return registry\n",
        "        return registry\n",
        "EntrypointTests.test_enabled_profile_still_requires_registry_agreement_before_credentials",
    ),
    (
        "serve an inactive profile",
        "tools/service_entrypoint.py",
        "    profiles.require_enabled(config)\n    registry_provider(registry_path, config)()\n    previous_umask = os.umask(0o077)\n",
        "    pass\n    registry_provider(registry_path, config)()\n    previous_umask = os.umask(0o077)\n",
        "EntrypointTests.test_shipped_candidate_profile_is_refused_before_registry_or_credentials",
    ),
    (
        "accept any owner-only registry mode",
        "tools/service_entrypoint.py",
        "    if stat.S_IMODE(metadata.st_mode) != REGISTRY_MODE:\n",
        "    if metadata.st_mode & 0o077:\n",
        "AdmissionIngressTests.test_registry_file_requires_same_user_mode_0600",
    ),
    (
        "accept a registry inside a checkout or state root",
        "tools/service_entrypoint.py",
        "            if (metadata.st_dev, metadata.st_ino) in forbidden:\n",
        "            if False:\n",
        "AdmissionIngressTests.test_registry_cannot_live_in_source_checkout_state_or_proof_roots",
    ),
    (
        "cache the registry instead of re-reading it",
        "tools/service_entrypoint.py",
        "    def provide() -> admission.Registry:\n        registry = read_service_registry(path, roots)\n",
        "    cached = read_service_registry(path, roots)\n\n    def provide() -> admission.Registry:\n        registry = cached\n",
        "AdmissionIngressTests.test_registry_provider_observes_promotion_and_revocation_without_restart",
    ),
    (
        "bind on workflow_run deliveries",
        "tools/service_runtime.py",
        '            event_type != "pull_request"\n',
        '            event_type not in {"pull_request", "workflow_run"}\n',
        "AdmissionIngressTests.test_workflow_run_deliveries_never_create_bindings",
    ),
    (
        "bind without checking the policy against the engine profile",
        "tools/service_runtime.py",
        "        require_policy_matches_profile(bound.policy, config)\n",
        "        pass\n",
        "AdmissionIngressTests.test_admitted_policy_must_match_the_engine_profile",
    ),
    (
        "start the worker before ingress binds",
        "tools/service_entrypoint.py",
        "    try:\n        # Bind ingress before any worker exists",
        "    worker.start()\n    try:\n        # Bind ingress before any worker exists",
        "EntrypointTests.test_failed_ingress_bind_never_starts_the_worker",
    ),
    (
        "mint tokens from an unvalidated permission map",
        "tools/review_conductor_runtime.py",
        '        if app.get("permissions") != expected_permissions:\n',
        "        if False:\n",
        "GitHubAdapterTests.test_token_is_exact_installation_repository_and_permission_scoped",
    ),
    (
        "read the registry by pathname after validating the descriptor",
        "tools/service_entrypoint.py",
        '        raw = b"".join(chunks)\n',
        '        raw = path.read_bytes()\n',
        "AdmissionIngressTests.test_registry_is_read_from_the_validated_descriptor_not_the_pathname",
    ),
    (
        "gate the worker without checking the profile enrollment",
        "tools/service_runtime.py",
        "    require_profile_enrolled(config, registry, core_config)\n",
        "    pass\n",
        "AdmissionIngressTests.test_worker_gate_requires_the_profile_to_be_the_registry_enrollment",
    ),
    (
        "follow a symlinked ancestor while walking the registry path",
        "tools/service_entrypoint.py",
        "                following = os.open(component, directory_flags, dir_fd=held)\n",
        "                following = os.open(component, directory_flags & ~os.O_NOFOLLOW, dir_fd=held)\n",
        "AdmissionIngressTests.test_registry_ancestor_swapped_for_symlink_after_canonicalization_is_refused",
    ),
    (
        "trust the profile's reviewer actors instead of enrollment",
        "tools/service_runtime.py",
        '    if review_policy.get("reviewers") != enrolled.reviewers:\n        raise ServiceError("runtime reviewer identities contradict service enrollment")\n',
        "    pass\n",
        "AdmissionIngressTests.test_reviewer_actors_come_from_enrollment_not_the_profile",
    ),
    (
        "keep bindings current after reviewer rotation",
        "tools/service_runtime.py",
        '        enrolled.reviewer_openclaw != row["reviewer_openclaw"]\n        or enrolled.reviewer_clawsweeper != row["reviewer_clawsweeper"]\n',
        "        False\n",
        "AdmissionIngressTests.test_reviewer_rotation_invalidates_existing_bindings_until_readmission",
    ),
    (
        "keep bindings current after the engine profile changes",
        "tools/service_runtime.py",
        '    if profile_policy_digest(core_config, service_config) != row["profile_digest"]:\n',
        "    if False:\n",
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "stage policy for every pull_request delivery before the engine classifies it",
        "tools/service_runtime.py",
        "    for _attempt in range(3):\n        try:\n            return core.ingest_github_delivery(\n",
        "    stage_approved_policy(enrolled, read_policy)\n    for _attempt in range(3):\n        try:\n            return core.ingest_github_delivery(\n",
        "AdmissionIngressTests.test_closed_duplicate_and_stale_deliveries_never_touch_policy_transport",
    ),
    (
        "accept a real directory renamed into the canonical parent's place",
        "tools/service_entrypoint.py",
        "        if (parent_metadata.st_dev, parent_metadata.st_ino) != parent_identity:\n",
        "        if False:\n",
        "AdmissionIngressTests.test_registry_parent_replaced_by_real_directory_after_canonicalization_is_refused",
    ),
    (
        "keep the caller's GitHub App map by reference",
        "tools/review_conductor_runtime.py",
        '        app = copy.deepcopy(config["github_app"])\n',
        '        app = config["github_app"]\n',
        "AdmissionIngressTests.test_client_permissions_snapshot_ignores_later_caller_mutation",
    ),
    (
        "ignore ClawSweeper workflow authority in the profile digest",
        "tools/service_runtime.py",
        '            "clawsweeper": {\n                key: clawsweeper.get(key)\n                for key in ("workflow_id", "workflow_name", "workflow_path", "ref", "publish")\n            },\n',
        '            "clawsweeper": None,\n',
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "ignore the adapter artifact namespace in the profile digest",
        "tools/service_runtime.py",
        '            "adapter": None if adapter is None else {\n                "contract": adapter.get("contract"), "artifact_prefix": adapter.get("artifact_prefix")\n            },\n',
        '            "adapter": None,\n',
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "reject transient policy failures instead of asking for redelivery",
        "tools/service_runtime.py",
        '        raise runtime.RetryableIngestError("approved policy transport failed transiently") from exc\n',
        '        raise ServiceError("approved policy transport failed transiently") from exc\n',
        "AdmissionIngressTests.test_transient_policy_failures_answer_503_without_writing_state",
    ),
    (
        "answer 400 for a retryable dependency failure",
        "tools/review_conductor_runtime.py",
        '    except RetryableIngestError:\n        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "reason": "dependency_unavailable"}\n',
        '    except RetryableIngestError:\n        return HTTPStatus.BAD_REQUEST, {"ok": False, "reason": "request_rejected"}\n',
        "AdmissionIngressTests.test_transient_policy_failures_answer_503_without_writing_state",
    ),
    (
        "classify transient GitHub statuses as rejected operations",
        "tools/review_conductor_runtime.py",
        "            if status in TRANSIENT_STATUSES or rate_limited:\n",
        "            if False:\n",
        "AdmissionIngressTests.test_github_client_classifies_transient_failures_and_malformed_content",
    ),
    (
        "follow a symlinked registry leaf",
        "tools/service_entrypoint.py",
        "            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=held)\n",
        "            return os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=held)\n",
        "AdmissionIngressTests.test_registry_leaf_swapped_for_symlink_after_canonicalization_is_refused",
    ),
    (
        "let a symlink loop escape as a traceback",
        "tools/service_entrypoint.py",
        "    except (OSError, RuntimeError) as exc:\n        raise service.ServiceError(\"service enrollment registry is unavailable\") from exc\n",
        "    except OSError as exc:\n        raise service.ServiceError(\"service enrollment registry is unavailable\") from exc\n",
        "AdmissionIngressTests.test_registry_symlink_loop_is_a_clean_service_error",
    ),
    (
        "drop the App id from binding identity",
        "tools/trusted_admission.py",
        '             "review_epoch": self.review.review_epoch, "app_id": self.app_id,\n',
        '             "review_epoch": self.review.review_epoch,\n',
        "AdmissionIngressTests.test_binding_identity_separates_apps_with_identical_review_and_policy",
    ),
    (
        "keep the caller's ClawSweeper map by reference",
        "tools/review_conductor_runtime.py",
        "        self._clawsweeper = copy.deepcopy(\n            config.get(\"clawsweeper\", {\"workflow_id\": \"clawsweeper-native-canary.yml\", \"ref\": \"main\"})\n        )\n",
        "        self._clawsweeper = config.get(\"clawsweeper\", {\"workflow_id\": \"clawsweeper-native-canary.yml\", \"ref\": \"main\"})\n",
        "AdmissionIngressTests.test_client_clawsweeper_snapshot_and_profile_selected_permission_allowlist",
    ),
    (
        "accept either permission allowlist regardless of profile kind",
        "tools/review_conductor_runtime.py",
        '        expected_permissions = STANDALONE_APP_PERMISSIONS if config.get("review_policy") else APP_PERMISSIONS\n        if app.get("permissions") != expected_permissions:\n',
        '        if app.get("permissions") not in (APP_PERMISSIONS, STANDALONE_APP_PERMISSIONS):\n',
        "AdmissionIngressTests.test_client_clawsweeper_snapshot_and_profile_selected_permission_allowlist",
    ),
    (
        "open the registry leaf without O_NONBLOCK",
        "tools/service_entrypoint.py",
        "            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=held)\n",
        "            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=held)\n",
        "AdmissionIngressTests.test_registry_fifo_fails_closed_instead_of_blocking_startup",
    ),
    (
        "persist a binding under a reloaded profile that is no longer enrolled",
        "tools/service_runtime.py",
        "        enrolled = preflight_enrollment(service_config, registry, payload, config)\n",
        "        enrolled = registry.enrollments[0]\n",
        "AdmissionIngressTests.test_hook_revalidates_the_reloaded_core_profile_before_persisting",
    ),
    (
        "keep serving a profile disabled after startup",
        "tools/service_runtime.py",
        '    if review_policy.get("enabled") is not True:\n        raise ServiceError("runtime core profile is not enabled")\n',
        "",
        "AdmissionIngressTests.test_profile_disabled_after_startup_fails_every_gate_closed",
    ),
    (
        "ignore the OpenClaw adapter authority in the profile digest",
        "tools/service_runtime.py",
        '            "openclaw": {\n                key: openclaw.get(key) for key in ("operator_id", "transport", "remote_worktree_shelf", "exact_tuple_contract")\n            },\n',
        '            "openclaw": None,\n',
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "trust the pre-walk forbidden-root snapshot",
        "tools/service_entrypoint.py",
        "        if _forbidden_identities(forbidden_roots) & set(walked):\n",
        "        if False:\n",
        "AdmissionIngressTests.test_forbidden_root_created_during_walk_is_refused",
    ),
    (
        "admit under the preflight registry snapshot instead of re-reading in the transaction",
        "tools/service_runtime.py",
        "                admission_hook=_admission_hook(registry_source, service_config),\n",
        "                admission_hook=_admission_hook(registry, service_config),\n",
        "AdmissionIngressTests.test_hook_reresolves_registry_inside_the_transaction_before_admission",
    ),
    (
        "stage policy for the preflight enrollment instead of the one the hook resolved",
        "tools/service_runtime.py",
        "            stage_approved_policy(pending.enrolled, read_policy)\n",
        "            stage_approved_policy(enrolled, read_policy)\n",
        "AdmissionIngressTests.test_hook_reresolves_registry_inside_the_transaction_before_admission",
    ),
    (
        "expose repository contents to the legacy allowlist",
        "tools/review_conductor_runtime.py",
        "                if self._strict_adapter\n                else ()\n",
        "                if True\n                else ()\n",
        "AdmissionIngressTests.test_legacy_client_cannot_read_repository_contents",
    ),
    (
        "dispatch OpenClaw work without the authority fence",
        "tools/review_conductor_runtime.py",
        '            assert_authority(\n                client,\n                f"openclaw.enqueue:{action[\'action_id\']}",\n                authority,\n            )\n',
        "",
        "AdmissionIngressTests.test_openclaw_dispatch_and_notifications_are_fenced_by_the_authority_guard",
    ),
    (
        "deliver notifications without the authority fence",
        "tools/review_conductor_userland.py",
        '                runtime.assert_authority(\n                    authority_client,\n                    f"notification:{row[\'event_key\']}:{row[\'channel\']}",\n                    authority,\n                )\n',
        "                pass\n",
        "AdmissionIngressTests.test_each_notification_send_has_a_fresh_authority_fence",
    ),
    (
        "skip pending notification revalidation before send",
        "tools/review_conductor_userland.py",
        "            if not pending_review_notification_still_eligible(\n                connection, row, trusted_enrollment\n            ):\n",
        "            if False and not pending_review_notification_still_eligible(\n                connection, row, trusted_enrollment\n            ):\n",
        "AdmissionIngressTests.test_superseded_review_notification_cannot_use_the_new_heads_binding",
    ),
    (
        "assert automatic redelivery of a 503",
        "tools/review_conductor_runtime.py",
        "    foreign, so the HTTP edge answers 503 and leaves the delivery redeliverable\n    from GitHub's delivery log or API (GitHub does not retry automatically).\n",
        "    foreign, so the HTTP edge answers 503 and GitHub redelivers it.\n",
        "AdmissionIngressTests.test_evidence_never_claims_automatic_github_redelivery",
    ),
    (
        "skip the authority guard before mutating GitHub calls",
        "tools/review_conductor_runtime.py",
        '        if (\n            self._authority_guard is not None\n            and method != "GET"\n            and operation != "installation-token"\n        ):\n            try:\n                self._authority_guard(method, path, authority)\n            except core.AuthorityDenied:\n                raise\n            except Exception as exc:\n                raise core.AuthorityDenied(\n                    "authority revoked before GitHub request"\n                ) from exc\n',
        "        pass\n",
        "AdmissionIngressTests.test_tick_side_effects_revalidate_admission_before_each_mutating_call",
    ),
    (
        "run a live tick without arming the authority guard",
        "tools/service_runtime.py",
        "        install(authority_guard)\n",
        "        pass\n",
        "AdmissionIngressTests.test_tick_side_effects_revalidate_admission_before_each_mutating_call",
    ),
    (
        "allow concurrent installation-token cache replacement",
        "tools/review_conductor_runtime.py",
        "        with self._token_lock:\n            if self._token is not None and self._clock() < self._token_expires - 60:\n",
        "        if True:\n            if self._token is not None and self._clock() < self._token_expires - 60:\n",
        "GitHubAdapterTests.test_token_cache_is_synchronized_and_policy_fetch_is_not_binding_fenced",
    ),
    (
        "fence installation-token minting under the stale binding",
        "tools/review_conductor_runtime.py",
        '            and operation != "installation-token"\n',
        "",
        "GitHubAdapterTests.test_token_cache_is_synchronized_and_policy_fetch_is_not_binding_fenced",
    ),
    (
        "ignore the reloaded core repository identity",
        "tools/service_runtime.py",
        '    if (\n        core_config.get("repository") != enrolled.repository\n        or core_config.get("repository_id") != enrolled.repository_id\n    ):\n        raise ServiceError("runtime core repository identity contradicts service enrollment")\n',
        "    if False:\n        pass\n",
        "AdmissionIngressTests.test_worker_gate_requires_the_profile_to_be_the_registry_enrollment",
    ),
    (
        "open the authority gate database read-write",
        "tools/service_runtime.py",
        '        connection = sqlite3.connect(\n            f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10\n        )\n',
        "        connection = sqlite3.connect(database, timeout=10)\n",
        "AdmissionIngressTests.test_gate_database_is_read_only_and_disappearance_fails_closed",
    ),
    (
        "treat a disappearing authority database as empty state",
        "tools/service_runtime.py",
        '        raise ServiceError("admission-gate database could not be opened read-only") from exc\n',
        "        return None\n",
        "AdmissionIngressTests.test_gate_database_is_read_only_and_disappearance_fails_closed",
    ),
    (
        "touch the checkout before a fresh authority fence",
        "tools/review_conductor_userland.py",
        '        runtime.assert_authority(\n            authority_client,\n            f"checkout-hydration:{row[\'action_id\']}",\n            authority,\n        )\n',
        "",
        "AdmissionIngressTests.test_checkout_hydration_is_fenced_before_the_checkout_is_touched",
    ),
    (
        "authorize superseded work under a newer valid binding",
        "tools/service_runtime.py",
        "                require_exact_current_binding(config, registry, authority)\n",
        "                require_current_bindings(config, registry)\n",
        "AdmissionIngressTests.test_tuple_guard_rejects_superseded_work_under_a_new_valid_binding",
    ),
    (
        "run both OpenClaw commands under one stale fence",
        "tools/review_conductor.py",
        '                before_external_command = getattr(args, "before_external_command", None)\n                if before_external_command is not None:\n                    before_external_command(index, command)\n',
        "",
        "AdmissionIngressTests.test_each_openclaw_external_command_has_a_fresh_authority_fence",
    ),
    (
        "drop the isolated environment before starting an OpenClaw subprocess",
        "tools/review_conductor.py",
        "                        environment=command_environment,\n",
        "                        environment=None,\n",
        "AdmissionIngressTests.test_each_openclaw_external_command_has_a_fresh_authority_fence",
    ),
    (
        "mutate the process environment while preparing an adapter subprocess",
        "tools/review_conductor_runtime.py",
        '    return {\n        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),\n',
        '    os.environ.pop("REVIEW_CONDUCTOR_THREAD_SENTINEL", None)\n    return {\n        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),\n',
        "AdmissionIngressTests.test_openclaw_dispatch_uses_an_isolated_subprocess_environment",
    ),
    (
        "accept a live client that cannot assert authority",
        "tools/service_runtime.py",
        "        if not callable(install) or not callable(assertion):\n",
        "        if not callable(install):\n",
        "AdmissionIngressTests.test_tick_side_effects_revalidate_admission_before_each_mutating_call",
    ),
    (
        "omit the effective Spark dispatch target from the binding digest",
        "tools/service_runtime.py",
        '            "spark_target": spark_target,\n',
        '            "spark_target": None,\n',
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "omit effective Spark executable selectors from the binding digest",
        "tools/service_runtime.py",
        '            "spark_executables": spark_executables,\n',
        '            "spark_executables": None,\n',
        "AdmissionIngressTests.test_profile_change_after_admission_invalidates_existing_bindings",
    ),
    (
        "unpack a header-aware artifact fallback as a two-tuple",
        "tools/review_conductor_runtime.py",
        '                if len(response) == 2:\n                    return response\n                status, _response_headers, raw = response\n                return status, raw\n',
        '                status, raw = response\n                return status, raw\n',
        "GitHubAdapterTests.test_header_aware_transport_fallback_normalizes_artifact_downloads",
    ),
    (
        "run standalone maintenance without the whole-profile authority gate",
        "tools/service_entrypoint.py",
        "    service.require_current_bindings(config, provide_registry)\n",
        "    pass\n",
        "EntrypointTests.test_maintenance_modes_use_outer_and_transaction_binding_gates",
    ),
    (
        "run standalone maintenance without the transaction-bound target gate",
        "tools/service_entrypoint.py",
        "        service.require_current_binding(\n            connection, config, provide_registry, pr_number\n        )\n",
        "        return None\n",
        "EntrypointTests.test_maintenance_modes_use_outer_and_transaction_binding_gates",
    ),
    (
        "treat rate-limited GitHub 403 responses as permanent authorization failures",
        "tools/review_conductor_runtime.py",
        '            rate_limited = status == 403 and (\n                header_value(response_headers, "Retry-After") is not None\n                or header_value(response_headers, "X-RateLimit-Remaining") == "0"\n            )\n',
        "            rate_limited = False\n",
        "AdmissionIngressTests.test_github_client_classifies_transient_failures_and_malformed_content",
    ),
    (
        "detach the service worker during shutdown",
        "tools/service_entrypoint.py",
        "        daemon=False,\n",
        "        daemon=True,\n",
        "EntrypointTests.test_shutdown_waits_for_the_non_daemon_worker_without_tick_timeout",
    ),
    (
        "bound shutdown waiting to the short tick interval",
        "tools/service_entrypoint.py",
        "            worker.join()\n",
        '            worker.join(timeout=config["worker"]["tick_seconds"] + 1)\n',
        "EntrypointTests.test_shutdown_waits_for_the_non_daemon_worker_without_tick_timeout",
    ),
    (
        "let the worker die silently on operational failure",
        "tools/service_entrypoint.py",
        "        except BaseException as exc:  # sqlite3.Error, OSError, or anything unexpected\n            failure.append(exc)\n            stop.set()\n",
        "        except BaseException:\n            return\n            stop.set()\n",
        "EntrypointTests.test_worker_operational_failure_stops_the_whole_service",
    ),
    (
        "start the threaded service under the inherited broad umask",
        "tools/service_entrypoint.py",
        "    previous_umask = os.umask(0o077)\n",
        "    previous_umask = os.umask(0o022)\n",
        "EntrypointTests.test_service_holds_restrictive_umask_for_threaded_lifecycle",
    ),
    (
        "send a notification while its durable row still appears pending",
        "tools/review_conductor_userland.py",
        "                SET status = 'uncertain', attempts = attempts + 1,\n                  last_error = 'delivery in progress; reconcile if interrupted',\n",
        "                SET status = 'pending', attempts = attempts + 1,\n                  last_error = 'delivery in progress; reconcile if interrupted',\n",
        "AdmissionIngressTests.test_notification_is_uncertain_before_transport_and_survives_crash",
    ),
    (
        "inherit the complete service environment in a notification subprocess",
        "tools/review_conductor_userland.py",
        "                env=self.subprocess_environment(),\n",
        "                env=self.environment,\n",
        "AdmissionIngressTests.test_notification_subprocess_uses_an_allowlisted_environment",
    ),
    (
        "let a maintenance SQLite failure escape the CLI error boundary",
        "tools/service_entrypoint.py",
        "    except (core.ContractError, OSError, sqlite3.Error) as exc:\n",
        "    except (core.ContractError, OSError) as exc:\n",
        "EntrypointTests.test_maintenance_sqlite_failure_is_a_controlled_service_error",
    ),
    (
        "invalidate in-flight CI on a same-head status transition",
        "tools/review_conductor.py",
        '    if event["source_created_at"] <= row["head_started_at"]:\n',
        '    if event["source_created_at"] <= row["source_updated_at"]:\n',
        "test_review_conductor_profiles.ProfilesTest.test_status_transition_preserves_inflight_ci_and_first_draft_value",
    ),
    (
        "leave a draft-obsoleted ClawSweeper action inert after returning ready",
        "tools/review_conductor.py",
        "                    SET status='pending', last_error=NULL, claim_owner=NULL,\n",
        "                    SET status='obsolete', last_error=NULL, claim_owner=NULL,\n",
        "test_review_conductor_profiles.ProfilesTest.test_draft_runs_openclaw_and_ready_enables_clawsweeper_without_new_epoch",
    ),
    (
        "dispatch ClawSweeper after clean OpenClaw adjudication while draft",
        "tools/review_conductor.py",
        '    if rail == "openclaw":\n        if bool(row["is_draft"]):\n',
        '    if rail == "openclaw":\n        if False:\n',
        "test_review_conductor_profiles.ProfilesTest.test_clean_adjudication_while_draft_never_dispatches_clawsweeper",
    ),
    (
        "retain a ready label when closing a projected pull request",
        "tools/review_conductor_runtime.py",
        '        for closed_head in closed:\n            if not dry_run:\n                remove_projected_status_labels(client, closed_head["pr_number"])\n',
        '        for closed_head in closed:\n            if False:\n                remove_projected_status_labels(client, closed_head["pr_number"])\n',
        "test_review_conductor_profiles.ProfilesTest.test_closed_projection_filter_removes_only_target_ready_label",
    ),
    (
        "retain merge-ready state when a fully cleared pull request returns to draft",
        "tools/review_conductor.py",
        '        if is_draft and state == "ready_for_human_merge":\n',
        '        if False:\n',
        "test_review_conductor_profiles.ProfilesTest.test_clawsweeper_finishing_after_return_to_draft_cannot_clear_merge",
    ),
    (
        "redispatch consumed ClawSweeper work after returning ready",
        "tools/review_conductor.py",
        '        elif not is_draft and state == "clawsweeper_clean_draft":\n',
        '        elif False:\n',
        "test_review_conductor_profiles.ProfilesTest.test_clawsweeper_finishing_after_return_to_draft_cannot_clear_merge",
    ),
    (
        "key the service lock to a registry copy instead of tenant state",
        "tools/service_entrypoint.py",
        '    lock_path = core.ensure_state_root(Path(config["paths"]["state_root"])) / ".service-operation.lock"\n',
        '    lock_path = registry_path.parent / ".service-operation.lock"\n',
        "EntrypointTests.test_service_and_maintenance_are_mutually_exclusive",
    ),
    (
        "skip closed heads that have no projection row",
        "tools/review_conductor_runtime.py",
        '            SELECT heads.* FROM heads\n',
        '            SELECT heads.* FROM heads JOIN projections USING (repository, pr_number, base_sha, head_sha, review_epoch)\n',
        "test_review_conductor_profiles.ProfilesTest.test_closed_head_without_projection_still_removes_ready_label",
    ),
    (
        "pause a ClawSweeper action that was already dispatched",
        "tools/review_conductor.py",
        "            if paused == 1:\n",
        "            if True:\n",
        "test_review_conductor_profiles.ProfilesTest.test_draft_transition_preserves_dispatched_clawsweeper_until_terminal",
    ),
    (
        "reload the service profile after selecting its tenant lock",
        "tools/service_entrypoint.py",
        "            _serve_with_restrictive_umask(config, registry_path)\n",
        "            _serve_with_restrictive_umask(userland.load_config(profile_path), registry_path)\n",
        "EntrypointTests.test_service_uses_the_config_that_selected_the_tenant_lock",
    ),
    (
        "reload the maintenance profile after selecting its tenant lock",
        "tools/service_entrypoint.py",
        "            config, registry_path, command, pr_number, apply=apply,\n",
        "            userland.load_config(profile_path), registry_path, command, pr_number, apply=apply,\n",
        "EntrypointTests.test_service_uses_the_config_that_selected_the_tenant_lock",
    ),
    (
        "call run_tick without the registry-owned enrollment pair",
        "tools/service_runtime.py",
        "    return userland.run_tick(\n        config, client, notifier, dry_run=dry_run, enrollment=trusted_enrollment\n    )\n",
        "    return userland.run_tick(config, client, notifier, dry_run=dry_run)\n",
        "AdmissionIngressTests.test_service_tick_wires_registry_owned_enrollment_routes",
    ),
    (
        "infer conductor enrollment from userland activation flags",
        "tools/service_runtime.py",
        "    app = config.get(\"github_app\")\n",
        "    inferred = orchestration.resolve_trusted_enrollment(config)\n    if inferred.get(\"review_conductor\") == \"present\":\n        return inferred\n    app = config.get(\"github_app\")\n",
        "AdmissionIngressTests.test_service_tick_wires_registry_owned_enrollment_routes",
    ),
    (
        "omit decision identity from the notification event key",
        "tools/review_conductor_userland.py",
        '        f"{decision[\'route\']}|{decision[\'reason\']}|{eligibility}"\n',
        '        f"{row[\'state\']}|{eligibility}"\n',
        "AdmissionIngressTests.test_notification_event_identity_distinguishes_route_changes",
    ),
    (
        "let a Registry subclass synthesize legacy routing",
        "tools/service_runtime.py",
        "    snapshot = _registry_from_stored_fields(registry)\n    if snapshot is None:\n        return dict(broken)\n",
        "    snapshot = registry\n    if snapshot is None:\n        return dict(broken)\n",
        "AdmissionIngressTests.test_registry_subclass_overrides_cannot_synthesize_routes",
    ),
    (
        "let nested Enrollment virtual attributes grant registry authority",
        "tools/service_runtime.py",
        "    nested = _nested_registry_values_from_stored_fields(stored)\n    if nested is None:\n        return None\n",
        "    nested = (stored[\"enrollments\"], stored.get(\"legacy_xapi\"))\n    if nested is None:\n        return None\n",
        "AdmissionIngressTests.test_nested_enrollment_virtual_authority_fails_closed",
    ),
    (
        "treat an out-of-scope service profile as unenrolled",
        "tools/service_runtime.py",
        "    if type(repository) is not str or repository not in admission.INITIAL_ENROLLMENT_SCOPE:\n        return dict(broken)\n",
        '    if type(repository) is not str or repository not in admission.INITIAL_ENROLLMENT_SCOPE:\n        return {"review_conductor": "absent", "legacy_xapi": "absent"}\n',
        "AdmissionIngressTests.test_malformed_service_profile_repository_is_broken_not_unenrolled",
    ),
]


class MutationTests(unittest.TestCase):
    """Each guard bites: a precise disposable-copy mutant makes its intended test fail."""

    def test_precise_mutants_fail_their_intended_tests(self):
        for label, relative, old, new, test_id in MUTANTS:
            with self.subTest(mutant=label):
                with tempfile.TemporaryDirectory() as temp:
                    copy_root = Path(temp) / "copy"
                    for name in ("tools", "tests", "contracts", "examples"):
                        shutil.copytree(ROOT / name, copy_root / name)
                    target = copy_root / relative
                    source = target.read_text()
                    self.assertEqual(source.count(old), 1, f"mutant anchor drifted: {label}")
                    target.write_text(source.replace(old, new))
                    try:
                        target_id = test_id if test_id.startswith("test_") else f"test_service_runtime.{test_id}"
                        completed = subprocess.run(
                            [sys.executable, "-m", "unittest", "-q", target_id],
                            cwd=copy_root / "tests", capture_output=True, text=True, timeout=180,
                            env={"PATH": "/usr/bin:/bin", "HOME": temp, "PYTHONDONTWRITEBYTECODE": "1"},
                        )
                    except subprocess.TimeoutExpired:
                        self.fail(f"mutant run deadlocked: {label}")
                self.assertNotEqual(completed.returncode, 0, f"mutant survived: {label}\n{completed.stderr}")
                self.assertIn("Ran 1 test", completed.stderr, f"intended test did not run: {label}\n{completed.stderr}")
                self.assertRegex(completed.stderr, r"(FAIL|ERROR): " + test_id.rsplit(".", 1)[1],
                                 f"failure was not the intended test: {label}\n{completed.stderr}")


class GitHubAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = ServiceFixture(Path(self.temp.name))

    def test_token_is_exact_installation_repository_and_permission_scoped(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, dict(headers), json.loads(body) if body else None))
            if url.endswith("/access_tokens"):
                return 201, json.dumps(
                    {"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}
                ).encode()
            content = base64.b64encode(self.fx.policy).decode()
            content = "\n".join(content[index:index + 60] for index in range(0, len(content), 60))
            return 200, json.dumps({"encoding": "base64", "content": content}).encode()

        client = runtime.GitHubAppClient(
            self.fx.app_config(),
            "fixture-private-key",
            transport=transport,
            signer=lambda app_id, _key, _now: f"fixture-jwt-{app_id}",
        )
        self.assertEqual(client.read_policy(REPOSITORY, POLICY_COMMIT), self.fx.policy)
        token_call, content_call = calls
        # A directly constructed client cannot request permissions outside the two
        # closed allowlists, whatever map it is handed.
        for permissions in [
            {**runtime.STANDALONE_APP_PERMISSIONS, "contents": "write"},
            {**runtime.STANDALONE_APP_PERMISSIONS, "administration": "write"},
            {**runtime.APP_PERMISSIONS, "workflows": "write"},
            {name: level for name, level in runtime.STANDALONE_APP_PERMISSIONS.items() if name != "metadata"},
            {}, None, "contents:read",
        ]:
            config = self.fx.app_config()
            config["github_app"]["permissions"] = permissions
            with self.subTest(permissions=permissions), self.assertRaises(core.ContractError):
                runtime.GitHubAppClient(config, "fixture-private-key", transport=transport,
                                        signer=lambda *_: "fixture-jwt")
        legacy_config = {k: v for k, v in self.fx.app_config().items()
                         if k not in ("review_policy", "clawsweeper", "adapter")}
        legacy_config["github_app"]["permissions"] = dict(runtime.APP_PERMISSIONS)
        runtime.GitHubAppClient(legacy_config, "fixture-private-key", transport=transport,
                                signer=lambda *_: "fixture-jwt")
        self.assertEqual(len(calls), 2)
        self.assertEqual(token_call[0], "POST")
        self.assertTrue(token_call[1].endswith(f"/app/installations/{INSTALLATION_ID}/access_tokens"))
        self.assertEqual(token_call[2]["Authorization"], f"Bearer fixture-jwt-{APP_ID}")
        self.assertEqual(content_call[2]["Authorization"], "Bearer fixture-token")
        self.assertEqual(token_call[3]["repositories"], ["openclaw-smcbd-suite"])
        self.assertEqual(
            token_call[3]["permissions"],
            {"actions": "write", "checks": "write", "contents": "read", "pull_requests": "write"},
        )
        self.assertTrue(content_call[1].endswith(f"/.review-conductor.json?ref={POLICY_COMMIT}"))
        artifact_headers = []
        client._artifact_transport = lambda _url, headers, _timeout: (
            artifact_headers.append(dict(headers)) or (200, b"fixture-zip")
        )
        self.assertEqual(client.download_artifact(9), b"fixture-zip")
        self.assertEqual(artifact_headers[0]["Authorization"], "Bearer fixture-token")

    def test_header_aware_transport_fallback_normalizes_artifact_downloads(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, dict(headers)))
            if url.endswith("/access_tokens"):
                return 201, {}, json.dumps({
                    "token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"
                }).encode()
            return 200, {"X-Fixture": "artifact"}, b"fixture-zip"

        client = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        self.assertEqual(client.download_artifact(9), b"fixture-zip")
        self.assertEqual(calls[-1][2]["Authorization"], "Bearer fixture-token")

    def test_token_cache_is_synchronized_and_policy_fetch_is_not_binding_fenced(self):
        token_calls = []
        call_lock = threading.Lock()

        def transport(method, url, headers, body, timeout):
            if url.endswith("/access_tokens"):
                with call_lock:
                    token_calls.append(url)
                # Hold the first mint long enough for every competing caller to
                # reach the cache boundary; without the lock they all mint.
                time.sleep(0.05)
                return 201, json.dumps(
                    {"token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}
                ).encode()
            content = base64.b64encode(self.fx.policy).decode()
            return 200, json.dumps({"encoding": "base64", "content": content}).encode()

        client = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        start = threading.Barrier(9)
        results = []

        def get_token():
            start.wait()
            results.append(client._installation_token())

        workers = [threading.Thread(target=get_token) for _ in range(8)]
        for worker in workers:
            worker.start()
        start.wait()
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["fixture-token"] * 8)
        self.assertEqual(len(token_calls), 1)

        # A stale old binding must not fence the installation-token POST or the
        # read-only policy fetch needed to admit the promoted policy. A later
        # repository mutation is still fenced after token minting.
        guarded = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        guarded.set_authority_guard(
            lambda _method, _path, _authority: (_ for _ in ()).throw(
                service.ServiceError("old binding is stale")
            )
        )
        self.assertEqual(guarded.read_policy(REPOSITORY, POLICY_COMMIT), self.fx.policy)
        with self.assertRaises(core.ContractError):
            guarded.create_check("OpenClaw Review Rail", HEAD, "fixture", "queued")

    def test_policy_reader_and_check_publisher_cannot_cross_repository_or_merge(self):
        # The transport would happily answer anything; the allowlist must stop the call.
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            return 200, json.dumps({"encoding": "base64", "content": base64.b64encode(self.fx.policy).decode(),
                                    "token": "fixture-token", "expires_at": "2099-01-01T00:00:00Z"}).encode()

        client = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        client._token = "fixture-token"
        client._token_expires = 9999999999
        forbidden = [
            lambda: client.read_policy("dinkuskit/blocks", POLICY_COMMIT),
            lambda: client.read_policy(REPOSITORY, "main"),
            lambda: client.read_policy(REPOSITORY, POLICY_COMMIT.upper()),
            lambda: client.read_policy(REPOSITORY, POLICY_COMMIT[:39]),
            lambda: client._call("POST", f"/repos/{REPOSITORY}/pulls/3/merge", {}, expected={200}),
            lambda: client._call("PUT", f"/repos/{REPOSITORY}/pulls/3/merge", {}, expected={200}),
            lambda: client._call("GET", f"/repos/{REPOSITORY}/contents/AGENTS.md?ref={POLICY_COMMIT}", None, expected={200}),
            lambda: client._call("GET", f"/repos/{REPOSITORY}/contents/.review-conductor.json", None, expected={200}),
            lambda: client._call("GET", f"/repos/{REPOSITORY}/contents/.review-conductor.json?ref=main", None, expected={200}),
            lambda: client._call("GET", f"/repos/dinkuskit/blocks/contents/.review-conductor.json?ref={POLICY_COMMIT}", None, expected={200}),
            lambda: client._call("PUT", f"/repos/{REPOSITORY}/contents/.review-conductor.json", {}, expected={200}),
            lambda: client._call("PUT", f"/repos/{REPOSITORY}/branches/main/protection", {}, expected={200}),
            lambda: client._call("POST", "/app/installations/999/access_tokens", {}, expected={201}),
        ]
        for index, operation in enumerate(forbidden):
            with self.subTest(index=index), self.assertRaises(core.ContractError):
                operation()
        self.assertEqual(calls, [])
        self.assertEqual(client.read_policy(REPOSITORY, POLICY_COMMIT), self.fx.policy)
        self.assertEqual(len(calls), 1)

    def test_exact_head_check_create_and_update_are_the_only_check_writes(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, json.loads(body) if body else None))
            return (201, b'{"id":771}') if method == "POST" else (200, b'{}')

        client = runtime.GitHubAppClient(
            self.fx.app_config(), "fixture-private-key", transport=transport,
            signer=lambda *_: "fixture-jwt",
        )
        client._token = "fixture-token"
        client._token_expires = 9999999999
        check_id = client.create_check(
            "OpenClaw Review Rail", HEAD, "review-conductor:fixture", "queued"
        )
        client.update_check(
            check_id, "OpenClaw Review Rail", HEAD,
            "review-conductor:fixture", "success"
        )
        self.assertEqual(check_id, 771)
        self.assertEqual(calls[0][2]["head_sha"], HEAD)
        self.assertEqual(calls[1][2]["conclusion"], "success")
        with self.assertRaises(core.ContractError):
            client.create_check("CI", HEAD, "review-conductor:fixture", "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
