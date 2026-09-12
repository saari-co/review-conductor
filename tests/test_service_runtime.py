"""Inactive service-core admission, registry and webhook qualification."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import review_conductor as core  # noqa: E402
import review_conductor_runtime as runtime  # noqa: E402
import service_entrypoint as registry_io  # noqa: E402
import service_runtime as service  # noqa: E402
import trusted_admission as admission  # noqa: E402
import test_review_conductor as engine_fixture  # noqa: E402


REPOSITORY = "saari-co/openclaw-smcbd-suite"
REPOSITORY_ID = 1366416798
APP_ID = 4916376
INSTALLATION_ID = 161027021
POLICY_COMMIT = "a" * 40
HEAD = "b" * 40
SECRET = "synthetic-service-webhook-secret"
REVIEWERS = {"openclaw": "synthetic-openclaw", "clawsweeper": "synthetic-clawsweeper"}


class ServiceFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.config_path = root / "smcbd.json"
        self.policy = (ROOT / "examples/smcbd.review-conductor.json").read_bytes()
        config = json.loads(
            (ROOT / "contracts/review-conductor/openclaw-smcbd-suite.json").read_text()
        )
        config["review_policy"].update(enabled=True, reviewers=dict(REVIEWERS))
        self.write_config(config)
        self.reads: list[tuple[str, str]] = []
        service.clear_staged_policies()

    def write_config(self, config: dict) -> None:
        self.config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")

    def registry_document(self, *, policy: bytes | None = None, app_id: int = APP_ID) -> dict:
        raw = self.policy if policy is None else policy
        return {
            "schema": admission.REGISTRY_SCHEMA,
            "enrollments": [{
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
            }],
        }

    def registry(self, *, policy: bytes | None = None, app_id: int = APP_ID) -> admission.Registry:
        return admission.load_registry(
            json.dumps(self.registry_document(policy=policy, app_id=app_id)).encode()
        )

    def read(self, repository: str, commit: str) -> bytes:
        self.reads.append((repository, commit))
        if (repository, commit) == (REPOSITORY, POLICY_COMMIT):
            return self.policy
        return b""

    def service_config(self) -> dict:
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
                "app_id": APP_ID,
                "installation_id": INSTALLATION_ID,
                "repository": REPOSITORY,
                "repository_id": REPOSITORY_ID,
            },
        }

    def payload(
        self,
        *,
        action: str = "opened",
        head: str = HEAD,
        repository: str = REPOSITORY,
        repository_id: int = REPOSITORY_ID,
        installation_id: int = INSTALLATION_ID,
        updated_at: str = "2026-09-12T12:00:00Z",
    ) -> dict:
        payload = engine_fixture.pr_payload(7, head, action=action, updated_at=updated_at)
        payload["repository"] = {"full_name": repository, "id": repository_id}
        payload["installation"] = {"id": installation_id}
        payload["pull_request"]["draft"] = False
        return payload

    @staticmethod
    def signed(payload: dict, secret: str = SECRET) -> tuple[bytes, str]:
        body = core.canonical_json(payload).encode()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, f"sha256={digest}"

    def ingest(
        self,
        delivery_id: str,
        payload: dict,
        *,
        registry=None,
        read_policy=None,
        service_config=None,
        event_type: str = "pull_request",
    ) -> dict:
        body, signature = self.signed(payload)
        return service.ingest_service_delivery(
            config_path=self.config_path,
            state_root=self.state,
            event_type=event_type,
            delivery_id=delivery_id,
            signature=signature,
            body=body,
            secret=SECRET,
            registry=self.registry() if registry is None else registry,
            read_policy=self.read if read_policy is None else read_policy,
            service_config=self.service_config() if service_config is None else service_config,
        )

    def binding_rows(self) -> list[dict]:
        connection = core.open_database(self.state, REPOSITORY)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_policy_bindings'"
            ).fetchone()
            if table is None:
                return []
            return [dict(row) for row in connection.execute(
                "SELECT * FROM service_policy_bindings ORDER BY created_at"
            )]
        finally:
            connection.close()


class RegistrySpy(admission.Registry):
    def __init__(self, test: unittest.TestCase) -> None:
        super().__init__(enrollments=())
        object.__setattr__(self, "test", test)
        object.__setattr__(self, "calls", 0)

    def lookup(self, *_args):
        object.__setattr__(self, "calls", self.calls + 1)
        self.test.fail("enrollment lookup ran before webhook authentication")


class ServiceCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = ServiceFixture(Path(self.temp.name))

    def test_signed_exact_installation_persists_one_binding_and_replays(self):
        receipt = self.fx.ingest("delivery-one", self.fx.payload())
        self.assertEqual(receipt["result"], "accepted")
        self.assertRegex(receipt["admission_binding_id"], r"^[0-9a-f]{64}$")
        rows = self.fx.binding_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["app_id"], APP_ID)
        self.assertEqual(rows[0]["installation_id"], INSTALLATION_ID)
        self.assertEqual(rows[0]["binding_id"], receipt["admission_binding_id"])
        duplicate = self.fx.ingest("delivery-one", self.fx.payload())
        self.assertEqual(duplicate["result"], "duplicate_delivery")
        self.assertEqual(len(self.fx.binding_rows()), 1)

    def test_authentication_precedes_payload_registry_and_policy_processing(self):
        body, valid = self.fx.signed(self.fx.payload())
        forged = "sha256=" + format(int(valid[7:], 16) ^ 1, "064x")
        spy = RegistrySpy(self)
        for signature in (forged, "", "sha1=" + "0" * 40, valid.upper()):
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
                    read_policy=lambda *_: self.fail("policy read preceded authentication"),
                    service_config=self.fx.service_config(),
                )
        self.assertEqual(spy.calls, 0)
        self.assertEqual(self.fx.binding_rows(), [])

    def test_app_installation_and_repository_identity_are_exact(self):
        cases = [
            {"installation_id": INSTALLATION_ID + 1},
            {"repository": "dinkuskit/blocks"},
            {"repository_id": REPOSITORY_ID + 1},
        ]
        for index, changes in enumerate(cases):
            with self.subTest(changes=changes), self.assertRaises(service.ServiceError):
                self.fx.ingest(f"foreign-{index}", self.fx.payload(**changes))
        changed = self.fx.service_config()
        changed["github_app"]["app_id"] = APP_ID + 1
        with self.assertRaises(service.ServiceError):
            self.fx.ingest("foreign-app", self.fx.payload(), service_config=changed)
        self.assertEqual(self.fx.binding_rows(), [])

    def test_reviewer_identity_mismatch_leaves_no_binding(self):
        changed = self.fx.service_config()
        config = json.loads(self.fx.config_path.read_text())
        config["review_policy"]["reviewers"]["openclaw"] = "foreign-openclaw"
        self.fx.write_config(config)
        with self.assertRaises(service.ServiceError):
            self.fx.ingest("foreign-reviewer", self.fx.payload(), service_config=changed)
        self.assertEqual(self.fx.binding_rows(), [])

    def test_policy_hash_and_profile_mismatch_roll_back_all_state(self):
        wrong_registry = self.fx.registry(policy=b"different approved bytes")
        with self.assertRaises(service.ServiceError):
            self.fx.ingest("bad-hash", self.fx.payload(), registry=wrong_registry)
        self.assertIsNone(service.staged_policy(wrong_registry.enrollments[0]))
        self.assertEqual(self.fx.binding_rows(), [])

        config = json.loads(self.fx.config_path.read_text())
        config["ci"]["workflow_name"] = "Different exact-head pipeline"
        self.fx.write_config(config)
        service.clear_staged_policies()
        with self.assertRaises(service.ServiceError):
            self.fx.ingest("bad-profile", self.fx.payload())
        self.assertEqual(self.fx.binding_rows(), [])

    def test_policy_transport_runs_outside_the_engine_write_lock(self):
        lock_observed = []

        def read_policy(repository: str, commit: str) -> bytes:
            connection = core.open_database(self.fx.state, REPOSITORY)
            try:
                connection.execute("BEGIN IMMEDIATE")
                lock_observed.append((repository, commit))
                connection.rollback()
            finally:
                connection.close()
            return self.fx.policy

        self.fx.ingest("outside-lock", self.fx.payload(), read_policy=read_policy)
        self.assertEqual(lock_observed, [(REPOSITORY, POLICY_COMMIT)])

    def test_registry_is_reresolved_inside_transaction_before_binding(self):
        valid = self.fx.registry()
        revoked = self.fx.registry(app_id=APP_ID + 1)
        calls = 0

        def provider() -> admission.Registry:
            nonlocal calls
            calls += 1
            return valid if calls < 3 else revoked

        with self.assertRaises(service.ServiceError):
            self.fx.ingest("revoked-during-admission", self.fx.payload(), registry=provider)
        self.assertGreaterEqual(calls, 3)
        self.assertEqual(self.fx.binding_rows(), [])

    def test_duplicate_closed_and_workflow_deliveries_do_not_read_policy(self):
        self.fx.ingest("opened", self.fx.payload())
        service.clear_staged_policies()

        def unavailable(*_args):
            raise AssertionError("non-binding delivery read policy")

        duplicate = self.fx.ingest("opened", self.fx.payload(), read_policy=unavailable)
        self.assertEqual(duplicate["result"], "duplicate_delivery")
        closed = self.fx.payload(action="closed", updated_at="2026-09-12T12:01:00Z")
        self.fx.ingest("closed", closed, read_policy=unavailable)

        workflow = engine_fixture.workflow_payload(7, HEAD, "success", 91)
        workflow["repository"] = {"full_name": REPOSITORY, "id": REPOSITORY_ID}
        workflow["installation"] = {"id": INSTALLATION_ID}
        workflow["workflow"]["name"] = "Exact-head review pipeline"
        self.fx.ingest(
            "workflow", workflow, event_type="workflow_run", read_policy=unavailable
        )
        self.assertEqual(len(self.fx.binding_rows()), 1)

    def test_staged_policy_cache_is_bounded_and_thread_safe(self):
        enrollments = []
        for index in range(service.MAX_STAGED_POLICIES + 2):
            policy = self.fx.policy + (b"\n" * index)
            document = self.fx.registry_document(policy=policy)
            document["enrollments"][0]["approved_policy"]["commit"] = f"{index + 1:040x}"
            enrolled = admission.load_registry(json.dumps(document).encode()).enrollments[0]
            enrollments.append((enrolled, policy))
        for enrolled, policy in enrollments:
            service.stage_approved_policy(enrolled, lambda *_args, raw=policy: raw)
        self.assertLessEqual(len(service._STAGED_POLICIES), service.MAX_STAGED_POLICIES)

        service.clear_staged_policies()
        enrolled, policy = enrollments[0]
        calls = 0
        lock = threading.Lock()

        def reader(*_args):
            nonlocal calls
            with lock:
                calls += 1
            return policy

        threads = [threading.Thread(target=service.stage_approved_policy, args=(enrolled, reader))
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(service.staged_policy(enrolled), policy)
        self.assertGreaterEqual(calls, 1)

    def test_registry_file_is_descriptor_validated_and_outside_forbidden_roots(self):
        root = Path(self.temp.name)
        allowed_dir = root / "service-config"
        allowed_dir.mkdir(mode=0o700)
        path = allowed_dir / "registry.json"
        path.write_text(json.dumps(self.fx.registry_document()) + "\n", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(TypeError):
            registry_io.read_service_registry(path)
        self.assertEqual(registry_io.read_service_registry(path, []).enrollments[0].app_id, APP_ID)

        path.chmod(0o640)
        with self.assertRaises(service.ServiceError):
            registry_io.read_service_registry(path, [])
        path.chmod(0o600)

        link = allowed_dir / "registry-link.json"
        link.symlink_to(path)
        with self.assertRaises(service.ServiceError):
            registry_io.read_service_registry(link, [])
        with self.assertRaises(service.ServiceError):
            registry_io.read_service_registry(path, [allowed_dir])

    def test_bounded_loopback_handler_uses_service_admission(self):
        config = self.fx.service_config()
        handler = service.build_service_http_handler(
            config, secret=SECRET, registry=self.fx.registry(), read_policy=self.fx.read
        )
        server = runtime.BoundedHTTPServer(
            ("127.0.0.1", 0), handler, request_timeout_seconds=5
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        body, signature = self.fx.signed(self.fx.payload())
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/github/webhook", body=body, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "http-valid",
            "X-Hub-Signature-256": signature,
        })
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 202)
        connection.close()
        self.assertEqual(len(self.fx.binding_rows()), 1)


MUTANTS = [
    (
        "bypass webhook authentication",
        "tools/service_runtime.py",
        "    core.verify_github_signature(body, signature, secret)\n",
        "    pass\n",
        "ServiceCoreTests.test_authentication_precedes_payload_registry_and_policy_processing",
    ),
    (
        "ignore installation identity",
        "tools/service_runtime.py",
        "        or installation_id != enrolled.installation_id\n",
        "        or False\n",
        "ServiceCoreTests.test_app_installation_and_repository_identity_are_exact",
    ),
    (
        "skip approved-policy hash check",
        "tools/service_runtime.py",
        "    if hashlib.sha256(raw).hexdigest() != enrolled.approved_policy_sha256:\n",
        "    if False:\n",
        "ServiceCoreTests.test_policy_hash_and_profile_mismatch_roll_back_all_state",
    ),
    (
        "skip policy-versus-profile check",
        "tools/service_runtime.py",
        "        require_policy_matches_profile(bound.policy, config)\n",
        "        pass\n",
        "ServiceCoreTests.test_policy_hash_and_profile_mismatch_roll_back_all_state",
    ),
]


class MutationTests(unittest.TestCase):
    def test_core_guards_kill_precise_mutants(self):
        for label, relative, old, new, test_id in MUTANTS:
            with self.subTest(mutant=label), tempfile.TemporaryDirectory() as temp:
                copy_root = Path(temp) / "copy"
                for name in ("tools", "tests", "contracts", "examples"):
                    shutil.copytree(ROOT / name, copy_root / name)
                target = copy_root / relative
                source = target.read_text()
                self.assertEqual(source.count(old), 1, f"mutant anchor drifted: {label}")
                target.write_text(source.replace(old, new))
                completed = subprocess.run(
                    [sys.executable, "-m", "unittest", "-q", f"test_service_runtime.{test_id}"],
                    cwd=copy_root / "tests",
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={"PATH": "/usr/bin:/bin", "HOME": temp, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertNotEqual(completed.returncode, 0, f"mutant survived: {label}")
                self.assertIn("Ran 1 test", completed.stderr)
                self.assertRegex(completed.stderr, r"(FAIL|ERROR): " + test_id.rsplit(".", 1)[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
