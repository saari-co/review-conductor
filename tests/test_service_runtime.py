"""Qualified service-owned admission, ingress and GitHub App adapter boundary."""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import http.client
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_conductor as core
import review_conductor_runtime as runtime
import review_conductor_userland as userland
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
            reviewers={"openclaw": "fixture-openclaw", "clawsweeper": "fixture-clawsweeper"},
        )
        self.config_path.write_text(json.dumps(config) + "\n")
        self.reads: list[tuple[str, str]] = []

    def registry(self, *, policy=None):
        raw = policy if policy is not None else self.policy
        document = {
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [
                {
                    "repository": REPOSITORY,
                    "repository_id": REPOSITORY_ID,
                    "github_app": {
                        "id": APP_ID,
                        "installation_id": INSTALLATION_ID,
                        "installation_account": "saari-co",
                    },
                    "approved_policy": {
                        "commit": POLICY_COMMIT,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                }
            ],
        }
        return admission.load_registry(json.dumps(document).encode())

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

    def binding_rows(self):
        connection = core.open_database(self.state, REPOSITORY)
        try:
            return [
                dict(row) for row in connection.execute(
                    "SELECT * FROM service_policy_bindings ORDER BY created_at"
                ).fetchall()
            ]
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
            binding = service.binding_for_current_head(connection, self.fx.registry(), REPOSITORY, 7)
            self.assertEqual(binding["app_id"], APP_ID)
            self.assertEqual(binding["installation_id"], INSTALLATION_ID)
            self.assertEqual(binding["binding_id"], receipt["admission_binding_id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM service_policy_bindings").fetchone()[0], 1)
        finally:
            connection.close()
        duplicate = self.fx.ingest("delivery-one", self.fx.payload())
        self.assertEqual(duplicate["result"], "duplicate_delivery")
        self.assertEqual(duplicate["admission_binding_id"], receipt["admission_binding_id"])

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
                service.binding_for_current_head(connection, promoted_registry, REPOSITORY, 7)
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
                service._persist_binding(connection, conflicting)
            connection.rollback()
        finally:
            connection.close()
        ready = self.fx.payload()
        ready["action"] = "ready_for_review"
        ready["pull_request"]["updated_at"] = "2026-08-30T20:11:00Z"
        receipt = self.fx.ingest("ready", ready, registry=promoted_registry)
        self.assertEqual((receipt["result"], receipt["review_epoch"]), ("accepted", 1))
        self.assertEqual([(row["review_epoch"], row["policy_sha256"]) for row in self.fx.binding_rows()],
                         [(0, hashlib.sha256(promoted[:-1]).hexdigest()), (1, hashlib.sha256(promoted).hexdigest())])
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
            }],
        }
        path = self.fx.root / "registry.json"
        path.write_text(json.dumps(document))
        path.chmod(0o600)
        loaded = entrypoint.read_service_registry(path)
        self.assertEqual(loaded.enrollments[0].app_id, APP_ID)
        # The documented contract is exactly 0600: not merely owner-only bits.
        for mode in (0o400, 0o700, 0o640, 0o604, 0o660, 0o644, 0o666, 0o610, 0o601, 0o200):
            path.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(core.ContractError) as ctx:
                entrypoint.read_service_registry(path)
            self.assertIn("mode 0600 exactly", str(ctx.exception))
        path.chmod(0o600)
        link = self.fx.root / "registry-link.json"
        link.symlink_to(path)
        relative = Path("registry.json")
        for candidate in (link, relative, self.fx.root, self.fx.root / "missing.json", str(path)):
            with self.subTest(candidate=str(candidate)), self.assertRaises(core.ContractError):
                entrypoint.read_service_registry(candidate)
        path.write_text(json.dumps({**document, "enrollments": []}))
        with self.assertRaises(core.ContractError):
            entrypoint.read_service_registry(path)

    def test_registry_cannot_live_in_source_checkout_state_or_proof_roots(self):
        document = json.loads(json.dumps({
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
                "repository": REPOSITORY, "repository_id": REPOSITORY_ID,
                "github_app": {"id": APP_ID, "installation_id": INSTALLATION_ID,
                               "installation_account": "saari-co"},
                "approved_policy": {"commit": POLICY_COMMIT,
                                    "sha256": hashlib.sha256(self.fx.policy).hexdigest()},
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
                }],
            }
            path.write_text(json.dumps(document))
            path.chmod(0o600)

        write_registry(self.fx.policy)
        provider = entrypoint.registry_provider(path, {
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
            "quiet_seconds": lambda m: m["review"].__setitem__("quiet_seconds", 900),
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
            self.assertEqual(sorted(self.fx.reads), [(REPOSITORY, POLICY_COMMIT)] * 2)
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

    def test_shipped_candidate_profile_is_refused_before_registry_or_credentials(self):
        missing_registry = self.root / "never-created.json"
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(missing_registry)
        self.assertEqual(code, 2)
        self.assertIn("repository profile is inactive", stderr)
        self.assertIn("Contents: read", stderr)
        self.assertFalse(missing_registry.exists())
        self.assertFalse((self.home / ".local").exists())

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
            }],
        }
        registry.write_text(json.dumps(document))
        registry.chmod(0o600)
        with patch.object(userland, "read_inherited_value", side_effect=AssertionError("credentials were read")):
            code, stderr = self.run_main(registry)
        self.assertEqual(code, 2)
        self.assertIn("runtime profile is not enrolled in the service registry", stderr)
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

    def test_apply_flag_and_arguments_are_mandatory(self):
        for argv in [[], ["--profile", str(self.profile), "--registry", str(self.root)],
                     ["--profile", str(self.profile), "--apply"]]:
            with self.subTest(argv=argv), patch.object(sys, "stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as ctx:
                entrypoint.main(argv)
            self.assertEqual(ctx.exception.code, 2)


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
        '    app_id = core.require_positive_int(app.get("app_id"), "service GitHub App id")\n',
        "    app_id = registry.enrollments[0].app_id\n",
        "AdmissionIngressTests.test_configured_app_identity_must_match_the_registry",
    ),
    (
        "admit a foreign installation",
        "tools/service_runtime.py",
        "    installation_id = _installation_id(payload)\n",
        "    installation_id = registry.enrollments[0].installation_id\n",
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
        "        except admission.AdmissionError as exc:\n            raise service.ServiceError(\n                \"runtime profile is not enrolled in the service registry\"\n            ) from exc\n",
        "        except admission.AdmissionError:\n            pass\n",
        "EntrypointTests.test_enabled_profile_still_requires_registry_agreement_before_credentials",
    ),
    (
        "serve an inactive profile",
        "tools/service_entrypoint.py",
        "    profiles.require_enabled(config)\n",
        "    pass\n",
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
        "        if resolved == root or resolved.is_relative_to(root):\n",
        "        if False:\n",
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
        "    server = None\n    try:\n",
        "    server = None\n    worker.start()\n    try:\n",
        "EntrypointTests.test_failed_ingress_bind_never_starts_the_worker",
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
                        completed = subprocess.run(
                            [sys.executable, "-m", "unittest", "-q", f"test_service_runtime.{test_id}"],
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
            calls.append((method, url, json.loads(body) if body else None))
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
        self.assertEqual(token_call[0], "POST")
        self.assertTrue(token_call[1].endswith(f"/app/installations/{INSTALLATION_ID}/access_tokens"))
        self.assertEqual(token_call[2]["repositories"], ["openclaw-smcbd-suite"])
        self.assertEqual(
            token_call[2]["permissions"],
            {"actions": "write", "checks": "write", "contents": "read", "pull_requests": "write"},
        )
        self.assertTrue(content_call[1].endswith(f"/.review-conductor.json?ref={POLICY_COMMIT}"))

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
