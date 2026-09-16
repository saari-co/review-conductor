"""Synthetic qualification of the SMCBD standalone launcher boundary."""
from __future__ import annotations

import contextlib
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_conductor as core
import review_conductor_userland as userland
import review_conductor_userland_launcher as launcher
import trusted_admission as admission
import test_review_conductor_userland as legacy


REVIEWERS = {"openclaw": "spark-openclaw", "clawsweeper": "saari-clawsweeper"}
WEBHOOK = b"synthetic-suite-webhook-value"
PRIVATE_KEY = (
    b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-suite-only\n-----END PRIVATE KEY-----"
)
TUNNEL = b"synthetic-suite-tunnel-must-not-resolve"
POLICY_COMMIT = "a" * 40
POLICY_BYTES = b'{"schema":"review-conductor.synthetic-policy.v1"}'


class LiveChild:
    """Stay running until the launcher stop path terminates and reaps it."""

    def __init__(self, command=None, kwargs=None):
        self.command = command
        self.kwargs = kwargs
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = -signal.SIGTERM

    def kill(self):
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout):
        self.waits += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic-standalone-child", timeout)
        return self.returncode


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")
    return path


class SuiteActivationLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "source"
        self.contracts = self.source / "contracts/review-conductor"
        self.contracts.mkdir(parents=True)
        for file in (ROOT / "contracts/review-conductor").glob("*.json"):
            shutil.copyfile(file, self.contracts / file.name)
        self.enrollment = self.root / "enrollment"
        self.enrollment.mkdir(mode=0o700)
        self.registry = self.enrollment / "registry.json"
        self.resolutions: list[str] = []
        self.tracked = self.root / "tracked-proof.txt"
        self.tracked.write_text("source-only tracked fixture\n")
        previous_handlers = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        }

        def restore_handlers():
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

        self.addCleanup(restore_handlers)

    def enable_suite_source(self, *, reviewers=None, app_id=4916376, installation_id=161027021):
        core_path = self.contracts / "openclaw-smcbd-suite.json"
        runtime_path = self.contracts / "openclaw-smcbd-suite-userland.json"
        core_profile = json.loads(core_path.read_text())
        runtime_profile = json.loads(runtime_path.read_text())
        core_profile["review_policy"].update(
            enabled=True, reviewers=dict(reviewers or REVIEWERS)
        )
        runtime_profile["enrollment"] = {"enabled": True, "blockers": []}
        runtime_profile["github_app"].update(
            app_id=app_id, installation_id=installation_id
        )
        write_json(core_path, core_profile)
        write_json(runtime_path, runtime_profile)
        return runtime_path

    def load_enabled(self, **overrides):
        path = self.enable_suite_source(**overrides)
        return path, userland.load_config(path, home=self.home, source_root=self.source)

    def registry_document(self, **overrides):
        item = {
            "repository": "saari-co/openclaw-smcbd-suite",
            "repository_id": 1366416798,
            "github_app": {
                "id": 4916376,
                "installation_id": 161027021,
                "installation_account": "saari-co",
            },
            "approved_policy": {
                "commit": POLICY_COMMIT,
                "sha256": hashlib.sha256(POLICY_BYTES).hexdigest(),
            },
            "reviewers": dict(REVIEWERS),
        }
        item.update(overrides)
        if "github_app" in overrides:
            item["github_app"] = {**{
                "id": 4916376,
                "installation_id": 161027021,
                "installation_account": "saari-co",
            }, **overrides["github_app"]}
        return {"schema": admission.REGISTRY_SCHEMA, "enrollments": [item]}

    def write_registry(self, document=None, *, mode=0o600):
        payload = document if document is not None else self.registry_document()
        self.registry.write_text(json.dumps(payload))
        os.chmod(self.registry, mode)
        return self.registry

    def assert_closed(self, descriptors):
        for descriptor in descriptors:
            with self.assertRaises(OSError) as error:
                os.fstat(descriptor)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def resolve(self, _config, capability):
        self.resolutions.append(capability)
        values = {
            launcher.standalone_capabilities(_config)[0]: WEBHOOK,
            launcher.standalone_capabilities(_config)[1]: PRIVATE_KEY,
            f"review-conductor.{_config.get('profile_id')}.cloudflare-tunnel": TUNNEL,
        }
        return values[capability]

    def refuse_resolve(self, _config, capability):
        self.resolutions.append(capability)
        raise AssertionError("standalone start must not resolve profile credential refs")

    def inherited_env(self, config):
        webhook = tempfile.TemporaryFile("w+b")
        github = tempfile.TemporaryFile("w+b")
        self.addCleanup(webhook.close)
        self.addCleanup(github.close)
        webhook.write(WEBHOOK)
        webhook.seek(0)
        github.write(PRIVATE_KEY)
        github.seek(0)
        inherited = (os.dup(webhook.fileno()), os.dup(github.fileno()))

        def close_inherited():
            for descriptor in inherited:
                with contextlib.suppress(OSError):
                    os.close(descriptor)

        self.addCleanup(close_inherited)
        return {
            config["credentials"]["webhook_secret_fd_env"]: str(inherited[0]),
            config["credentials"]["github_private_key_fd_env"]: str(inherited[1]),
        }

    def start(self, config, profile_path, popen, **kwargs):
        original = launcher.credential_descriptor
        descriptors = []

        def prepare(value):
            descriptor = original(value)
            descriptors.append(descriptor)
            return descriptor

        with patch.dict(os.environ, self.inherited_env(config), clear=False), \
             patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.refuse_resolve), \
             patch.object(launcher, "credential_descriptor", side_effect=prepare):
            try:
                return launcher.start_standalone(
                    config,
                    profile_path=profile_path,
                    registry_path=self.registry,
                    popen=popen,
                )
            finally:
                self.assert_closed(descriptors)

    def test_committed_profile_binds_authoritative_reviewers_and_stays_inactive(self):
        core_profile = json.loads(
            (ROOT / "contracts/review-conductor/openclaw-smcbd-suite.json").read_text()
        )
        runtime_profile = json.loads(
            (ROOT / "contracts/review-conductor/openclaw-smcbd-suite-userland.json").read_text()
        )
        self.assertEqual(core_profile["review_policy"]["reviewers"], REVIEWERS)
        self.assertFalse(core_profile["review_policy"]["enabled"])
        self.assertFalse(runtime_profile["enrollment"]["enabled"])
        self.assertIsNone(runtime_profile["tunnel"]["tunnel_id"])
        path = ROOT / "contracts/review-conductor/openclaw-smcbd-suite-userland.json"
        config = userland.load_config(path, home=self.home, source_root=ROOT)
        with patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(core.ContractError):
                launcher.start(config, config_path=path)
            with self.assertRaises(core.ContractError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry
                )
        self.assertEqual(self.resolutions, [])

    def test_inactive_or_absent_enrollment_rejects_before_any_resolution(self):
        path, config = self.load_enabled()
        started = []

        def popen(command, **kwargs):
            started.append(command)
            return legacy.FakeProcess(command, kwargs)

        with patch.dict(os.environ, self.inherited_env(config), clear=False), \
             patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.validate_standalone_enrollment(config, self.registry)
            with self.assertRaises(launcher.LauncherError):
                launcher.standalone_preflight(config, path, self.registry)
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry, popen=popen
                )
        self.assertEqual(self.resolutions, [])
        self.assertEqual(started, [])
        self.write_registry()
        with patch.dict(os.environ, self.inherited_env(config), clear=False), \
             patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config,
                    profile_path=path,
                    registry_path=self.root / "missing-registry.json",
                    popen=popen,
                )
        self.assertEqual(self.resolutions, [])
        self.assertEqual(started, [])
        inactive = json.loads(path.read_text())
        inactive["enrollment"] = {
            "enabled": False,
            "blockers": ["synthetic inactive enrollment"],
        }
        write_json(self.contracts / "openclaw-smcbd-suite.json", {
            **json.loads((self.contracts / "openclaw-smcbd-suite.json").read_text()),
            "review_policy": {
                **json.loads((self.contracts / "openclaw-smcbd-suite.json").read_text())["review_policy"],
                "enabled": False,
            },
        })
        write_json(path, inactive)
        config = userland.load_config(path, home=self.home, source_root=self.source)
        with patch.dict(os.environ, self.inherited_env(config), clear=False), \
             patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry, popen=popen
                )
        self.assertEqual(self.resolutions, [])
        self.assertEqual(started, [])

    def test_permission_wrong_registry_rejects_before_resolution(self):
        path, config = self.load_enabled()
        self.write_registry(mode=0o644)
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError) as error:
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry
                )
        self.assertIn("mode 0600 exactly", str(error.exception))
        self.assertEqual(self.resolutions, [])

    def test_foreign_and_mismatched_enrollment_reject_before_resolution(self):
        path, config = self.load_enabled()
        cases = {
            "repo": self.registry_document(
                repository="dinkuskit/blocks", repository_id=1306882611,
                github_app={"id": 1, "installation_id": 2, "installation_account": "dinkuskit"},
                reviewers={"openclaw": "blocks-openclaw", "clawsweeper": "blocks-clawsweeper"},
            ),
            "app": self.registry_document(github_app={"id": 12}),
            "install": self.registry_document(github_app={"installation_id": 99}),
            "actor": self.registry_document(
                reviewers={"openclaw": "foreign-openclaw", "clawsweeper": "saari-clawsweeper"}
            ),
        }
        for label, document in cases.items():
            with self.subTest(mismatch=label):
                self.resolutions.clear()
                self.write_registry(document)
                with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
                     patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
                    with self.assertRaises(launcher.LauncherError):
                        launcher.start_standalone(
                            config, profile_path=path, registry_path=self.registry
                        )
                self.assertEqual(self.resolutions, [])
        self.write_registry()
        core_path = self.contracts / "openclaw-smcbd-suite.json"
        core_profile = json.loads(core_path.read_text())
        core_profile["review_policy"]["reviewers"] = {
            "openclaw": "spark-openclaw",
            "clawsweeper": "policy-mismatch-clawsweeper",
        }
        write_json(core_path, core_profile)
        config = userland.load_config(path, home=self.home, source_root=self.source)
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry
                )
        self.assertEqual(self.resolutions, [])

    def test_preflight_validates_enrollment_without_resolving_or_starting_a_tunnel(self):
        path, config = self.load_enabled()
        self.write_registry()
        with patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            result = launcher.standalone_preflight(config, path, self.registry)
        self.assertEqual(self.resolutions, [])
        self.assertEqual(result["result"], "waiting_for_human")
        self.assertEqual(result["bootstrap"]["result"], "waiting_for_human")
        self.assertEqual(
            result["bootstrap"]["auth_mode"],
            "selected_webhook_verify_and_github_installation",
        )
        self.assertEqual(result["reviewers"], REVIEWERS)
        self.assertFalse(result["credentials_resolved"])
        self.assertFalse(result["tunnel_started"])
        self.assertFalse(result["activation_authorized"])
        self.assertIsNone(config["tunnel"]["tunnel_id"])

    def test_preflight_ready_requires_bootstrap_and_does_not_hard_code_auth_mode(self):
        path, config = self.load_enabled()
        self.write_registry()
        ready = {
            "schema": "smoky.review-conductor.userland-bootstrap-status.v1",
            "result": "ready",
            "auth_mode": launcher.selected_auth_mode(
                launcher.standalone_capabilities(config)
            ),
            "domains": {},
            "values_exposed": False,
            "merge_authorized": False,
        }
        with patch.object(launcher, "bootstrap_status", return_value=ready), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            result = launcher.standalone_preflight(config, path, self.registry)
        self.assertEqual(self.resolutions, [])
        self.assertEqual(result["result"], "ready")
        self.assertEqual(
            result["bootstrap"]["auth_mode"],
            "selected_webhook_verify_and_github_installation",
        )
        self.assertNotEqual(
            result["bootstrap"]["auth_mode"], "three_distinct_service_accounts"
        )
        self.assertEqual(
            launcher.selected_auth_mode(launcher.CAPABILITIES),
            "three_distinct_service_accounts",
        )

    def test_standalone_start_invokes_supervisor_with_inherited_descriptors_only(self):
        path, config = self.load_enabled()
        self.write_registry()
        calls = []

        def popen(command, **kwargs):
            process = legacy.FakeProcess(command, kwargs)
            calls.append(process)
            webhook_fd, github_fd = kwargs["pass_fds"]
            self.assertEqual(len(kwargs["pass_fds"]), 2)
            self.assertEqual(
                kwargs["env"][config["credentials"]["webhook_secret_fd_env"]],
                str(webhook_fd),
            )
            self.assertEqual(
                kwargs["env"][config["credentials"]["github_private_key_fd_env"]],
                str(github_fd),
            )
            with os.fdopen(os.dup(webhook_fd), "rb") as stream:
                self.assertEqual(stream.read(), WEBHOOK)
            with os.fdopen(os.dup(github_fd), "rb") as stream:
                self.assertEqual(stream.read(), PRIVATE_KEY)
            return process

        self.assertEqual(self.start(config, path, popen), 0)
        self.assertEqual(len(calls), 1)
        command = calls[0].command
        self.assertEqual(command[1], str(launcher.STANDALONE_SUPERVISOR))
        self.assertEqual(command[command.index("--profile") + 1], str(path))
        self.assertEqual(command[command.index("--registry") + 1], str(self.registry))
        self.assertEqual(command[-2:], ["start", "--apply"])
        self.assertNotIn("cloudflared", " ".join(command))
        self.assertNotIn("review_conductor_userland.py", " ".join(command))
        serialized = json.dumps(command) + json.dumps(calls[0].kwargs["env"])
        for value in (WEBHOOK, PRIVATE_KEY, TUNNEL):
            self.assertNotIn(value.decode(), serialized)
        self.assertFalse(any(name.startswith("OP_") for name in calls[0].kwargs["env"]))
        self.assertEqual(self.resolutions, [])
        self.assertNotIn(
            f"review-conductor.{config['profile_id']}.cloudflare-tunnel",
            self.resolutions,
        )
        self.assertNotIn(WEBHOOK.decode(), self.tracked.read_text())
        self.assertNotIn(PRIVATE_KEY.decode(), self.tracked.read_text())

    def test_real_child_reads_descriptors_without_values_in_argv_env_or_logs(self):
        path, config = self.load_enabled()
        self.write_registry()
        log = self.root / "child.log"

        def popen(command, **kwargs):
            program = """
import os, sys
webhook, github = sys.argv[1], sys.argv[2]
assert os.environ[sys.argv[3]] == webhook
assert os.environ[sys.argv[4]] == github
with open('/dev/fd/' + webhook, 'rb') as stream:
    webhook_value = stream.read()
with open('/dev/fd/' + github, 'rb') as stream:
    github_value = stream.read()
assert len(webhook_value) == int(sys.argv[5])
assert len(github_value) == int(sys.argv[6])
print('ok', file=sys.stderr)
"""
            fds = kwargs["pass_fds"]
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    program,
                    str(fds[0]),
                    str(fds[1]),
                    config["credentials"]["webhook_secret_fd_env"],
                    config["credentials"]["github_private_key_fd_env"],
                    str(len(WEBHOOK)),
                    str(len(PRIVATE_KEY)),
                ],
                pass_fds=fds,
                env=kwargs["env"],
                cwd=kwargs["cwd"],
                capture_output=True,
                timeout=5,
            )
            log.write_bytes(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            return legacy.FakeProcess(command, kwargs)

        self.assertEqual(self.start(config, path, popen), 0)
        output = log.read_bytes()
        for value in (WEBHOOK, PRIVATE_KEY, TUNNEL):
            self.assertNotIn(value, output)
            self.assertNotIn(value, json.dumps(os.environ.copy()).encode())

    def test_partial_resolution_and_preparation_cleanup(self):
        path, config = self.load_enabled()
        self.write_registry()
        original = launcher.credential_descriptor
        for fail_at in (0, 1):
            with self.subTest(fail_prepare=fail_at):
                descriptors = []

                def prepare(value):
                    if len(descriptors) == fail_at:
                        raise launcher.LauncherError("synthetic preparation failure")
                    descriptor = original(value)
                    descriptors.append(descriptor)
                    return descriptor

                with patch.dict(os.environ, self.inherited_env(config), clear=False), \
                     patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
                     patch.object(launcher, "resolve_runtime_value", side_effect=self.refuse_resolve), \
                     patch.object(launcher, "credential_descriptor", side_effect=prepare), \
                     patch("subprocess.Popen") as popen:
                    with self.assertRaises(launcher.LauncherError):
                        launcher.start_standalone(
                            config,
                            profile_path=path,
                            registry_path=self.registry,
                            popen=popen,
                        )
                    popen.assert_not_called()
                self.assert_closed(descriptors)
                self.assertEqual(self.resolutions, [])
        self.resolutions.clear()
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.refuse_resolve), \
             patch("subprocess.Popen") as popen:
            with self.assertRaises(launcher.LauncherError) as error:
                launcher.start_standalone(
                    config,
                    profile_path=path,
                    registry_path=self.registry,
                    popen=popen,
                )
            self.assertIn("inherited credential descriptor is unavailable", str(error.exception))
            popen.assert_not_called()
        self.assertEqual(self.resolutions, [])

    def test_spawn_failure_closes_descriptors(self):
        path, config = self.load_enabled()
        self.write_registry()
        with self.assertRaises(OSError):
            self.start(
                config,
                path,
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic spawn failure")),
            )

    def test_health_invokes_supervisor_without_resolving_credentials(self):
        path, config = self.load_enabled()
        self.write_registry()
        observed = {}

        def runner(command, **kwargs):
            observed["command"] = command
            observed["env"] = kwargs["env"]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b'{"status":"stopped","automatic_restart":false}\n',
                stderr=b"",
            )

        with patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            payload = launcher.standalone_health(
                config, path, self.registry, runner=runner
            )
        self.assertEqual(self.resolutions, [])
        self.assertEqual(payload["status"], "stopped")
        self.assertEqual(observed["command"][1], str(launcher.STANDALONE_SUPERVISOR))
        self.assertEqual(observed["command"][-1], "health")
        self.assertFalse(any(name.startswith("OP_") for name in observed["env"]))

    def test_legacy_start_keeps_blocks_9443_and_rejects_suite(self):
        blocks_root = self.root / "blocks"
        blocks_root.mkdir()
        blocks = legacy.config_fixture(blocks_root)
        self.assertEqual(blocks["ingress"]["bind_port"], 9443)
        calls = []

        def fake_popen(command, **kwargs):
            process = legacy.FakeProcess(command, kwargs)
            calls.append(process)
            return process

        values = {
            launcher.CAPABILITIES[0]: b"fixture-webhook-value",
            launcher.CAPABILITIES[1]: PRIVATE_KEY,
            launcher.CAPABILITIES[2]: TUNNEL,
        }
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=lambda _c, capability: values[capability]):
            self.assertEqual(
                launcher.start(
                    blocks,
                    config_path=userland.DEFAULT_CONFIG,
                    popen=fake_popen,
                ),
                0,
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("review_conductor_userland.py", calls[0].command[1])
        self.assertEqual(calls[1].command[1:4], ["tunnel", "--no-autoupdate", "run"])
        path, config = self.load_enabled()
        with self.assertRaises(launcher.LauncherError) as error:
            launcher.start(config, config_path=path, popen=fake_popen)
        self.assertIn("standalone start", str(error.exception))
        foreign = dict(config)
        foreign["profile_id"] = "fixture-other-generalized"
        self.resolutions.clear()
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError) as other:
                launcher.start(foreign, config_path=path, popen=fake_popen)
        self.assertIn("standalone start", str(other.exception))
        self.assertEqual(self.resolutions, [])
        self.assertEqual(len(calls), 2)

    def test_main_standalone_start_uses_the_selected_profile_and_registry(self):
        path, config = self.load_enabled()
        self.write_registry()
        observed = {}

        def fake_start(loaded, *, profile_path, registry_path):
            observed["config"] = loaded
            observed["profile_path"] = profile_path
            observed["registry_path"] = registry_path
            return 0

        with patch.object(launcher.userland, "load_config", return_value=config), \
             patch.object(launcher, "start_standalone", side_effect=fake_start):
            self.assertEqual(
                launcher.main(
                    [
                        "--config",
                        str(path),
                        "standalone",
                        "--registry",
                        str(self.registry),
                        "start",
                        "--apply",
                    ]
                ),
                0,
            )
        self.assertEqual(observed["profile_path"], path.resolve())
        self.assertEqual(observed["registry_path"], self.registry)

    def test_changed_profile_credential_refs_cannot_redirect_resolution(self):
        path, config = self.load_enabled()
        self.write_registry()
        attacker = "/tmp/attacker-op-must-not-run"
        mutated = copy.deepcopy(config)
        mutated["onepassword"]["op_path"] = attacker
        for domain in mutated["onepassword"]["domains"].values():
            domain["runtime_reference"] = "op://attacker/vault/secret"
            domain["runtime_vault"] = "attacker-vault"
            domain["runtime_item"] = "attacker-item"
            domain["runtime_field"] = "attacker-field"
        calls = []

        def popen(command, **kwargs):
            process = legacy.FakeProcess(command, kwargs)
            calls.append(process)
            webhook_fd, github_fd = kwargs["pass_fds"]
            with os.fdopen(os.dup(webhook_fd), "rb") as stream:
                self.assertEqual(stream.read(), WEBHOOK)
            with os.fdopen(os.dup(github_fd), "rb") as stream:
                self.assertEqual(stream.read(), PRIVATE_KEY)
            serialized = json.dumps(command) + json.dumps(kwargs["env"])
            self.assertNotIn(attacker, serialized)
            self.assertNotIn("op://attacker/vault/secret", serialized)
            self.assertNotIn(WEBHOOK.decode(), serialized)
            self.assertNotIn(PRIVATE_KEY.decode(), serialized)
            return process

        self.assertEqual(self.start(mutated, path, popen), 0)
        self.assertEqual(self.resolutions, [])
        self.assertEqual(len(calls), 1)
        self.assertNotIn(attacker, json.dumps(os.environ.copy()))
        self.assertNotIn(WEBHOOK.decode(), self.tracked.read_text())
        self.assertNotIn(PRIVATE_KEY.decode(), self.tracked.read_text())

    def test_signal_during_popen_stops_and_reaps_child(self):
        path, config = self.load_enabled()
        self.write_registry()
        previous = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGHUP: signal.getsignal(signal.SIGHUP),
        }
        events = []
        child = LiveChild()
        real_signal = launcher.signal.signal

        def install(signum, handler):
            events.append(("signal", signum, handler))
            return real_signal(signum, handler)

        def popen(command, **kwargs):
            events.append(("spawn",))
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler))
            self.assertNotIn(handler, (signal.SIG_DFL, signal.SIG_IGN, None))
            handler(signal.SIGTERM, None)
            child.command = command
            child.kwargs = kwargs
            return child

        with patch.object(launcher.signal, "signal", side_effect=install):
            self.assertEqual(self.start(config, path, popen), -signal.SIGTERM)
        self.assertEqual(
            [event[0] for event in events],
            ["signal", "signal", "signal", "spawn", "signal", "signal", "signal"],
        )
        self.assertTrue(child.terminated)
        self.assertEqual(child.waits, 1)
        self.assertFalse(child.killed)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous[signal.SIGINT])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous[signal.SIGTERM])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous[signal.SIGHUP])

    def test_signal_immediately_after_popen_stops_child_before_wait(self):
        path, config = self.load_enabled()
        self.write_registry()
        previous = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGHUP: signal.getsignal(signal.SIGHUP),
        }
        child = LiveChild()
        spawned = []
        real_stack = contextlib.ExitStack

        class PostSpawnStack(real_stack):
            def __exit__(self, *exc):
                result = super().__exit__(*exc)
                if spawned and child.returncode is None:
                    handler = signal.getsignal(signal.SIGINT)
                    if callable(handler) and handler not in (signal.SIG_DFL, signal.SIG_IGN):
                        handler(signal.SIGINT, None)
                return result

        def popen(command, **kwargs):
            child.command = command
            child.kwargs = kwargs
            spawned.append(child)
            return child

        with patch.object(launcher.contextlib, "ExitStack", PostSpawnStack):
            self.assertEqual(self.start(config, path, popen), -signal.SIGTERM)
        self.assertTrue(child.terminated)
        self.assertEqual(child.waits, 1)
        self.assertFalse(child.killed)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous[signal.SIGINT])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous[signal.SIGTERM])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous[signal.SIGHUP])

    def test_spawn_exception_restores_handlers(self):
        path, config = self.load_enabled()
        self.write_registry()
        previous = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGHUP: signal.getsignal(signal.SIGHUP),
        }
        events = []
        real_signal = launcher.signal.signal

        def install(signum, handler):
            events.append(("signal", signum, handler))
            return real_signal(signum, handler)

        def fail_spawn(*_args, **_kwargs):
            events.append(("spawn",))
            raise OSError("synthetic spawn failure")

        with patch.object(launcher.signal, "signal", side_effect=install):
            with self.assertRaises(OSError):
                self.start(config, path, fail_spawn)
        self.assertEqual(
            [event[0] for event in events],
            ["signal", "signal", "signal", "spawn", "signal", "signal", "signal"],
        )
        self.assertEqual(events[4][2], previous[signal.SIGINT])
        self.assertEqual(events[5][2], previous[signal.SIGTERM])
        self.assertEqual(events[6][2], previous[signal.SIGHUP])
        self.assertEqual(signal.getsignal(signal.SIGINT), previous[signal.SIGINT])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous[signal.SIGTERM])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous[signal.SIGHUP])

    def test_normal_exit_restores_handlers_and_reaps_child_once(self):
        path, config = self.load_enabled()
        self.write_registry()
        previous = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGHUP: signal.getsignal(signal.SIGHUP),
        }
        child = LiveChild()
        stops = []
        timeouts = []
        real_stop = launcher.stop_standalone_child

        def stop(process):
            stops.append(process)
            timeouts.append(launcher.STANDALONE_CHILD_STOP_SECONDS)
            return real_stop(process)

        def popen(command, **kwargs):
            child.command = command
            child.kwargs = kwargs
            child.returncode = 0
            return child

        with patch.object(launcher, "stop_standalone_child", side_effect=stop):
            self.assertEqual(self.start(config, path, popen), 0)
        self.assertEqual(stops, [child])
        self.assertEqual(timeouts, [launcher.STANDALONE_CHILD_STOP_SECONDS])
        self.assertEqual(child.waits, 1)
        self.assertFalse(child.terminated)
        self.assertFalse(child.killed)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous[signal.SIGINT])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous[signal.SIGTERM])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous[signal.SIGHUP])

    def test_command_behavior_docs_and_proof_are_consistent(self):
        files = {
            ROOT / "README.md": (
                "standalone preflight` only validates",
                "standalone start` forwards inherited",
                "standalone health` only queries",
            ),
            ROOT / "docs/integration-contract.md": (
                "does not resolve credentials or invoke",
                "forwards already-prepared webhook",
                "only queries the supervisor",
            ),
            ROOT / "docs/migration.md": (
                "preflight` validates registry/bootstrap only",
                "start` forwards inherited",
                "health` only queries",
            ),
            ROOT / "docs/smcbd-pilot-handoff.md": (
                "preflight` reports registry and",
                "forwards already-prepared webhook",
                "health` only queries",
            ),
            ROOT / "proof/smcbd-suite-activation-launcher-20260914/PROOF.md": (
                "preflight only validates",
                "start forwards already-prepared",
                "health only queries",
                "direct supervisor health",
            ),
        }
        forbidden = (
            "standalone {preflight,start,health}` invokes the SMCBD supervisor",
            "The committed profile remains inactive, so these commands fail closed",
        )
        for path, phrases in files.items():
            text = path.read_text()
            for phrase in phrases:
                self.assertIn(phrase, text, f"{path.name} missing {phrase!r}")
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{path.name} still overstates {phrase!r}")


class SuiteActivationLauncherMutationTests(unittest.TestCase):
    MUTANTS = (
        (
            "resolve credentials before enrollment validation",
            "tools/review_conductor_userland_launcher.py",
            "    validate_standalone_enrollment(config, registry_path)\n"
            "    if (\n"
            "        bootstrap_status(config, capabilities=standalone_capabilities(config))[\"result\"]\n"
            "        != \"ready\"\n"
            "    ):\n"
            "        raise LauncherError(\"current-user service-account bootstrap is not ready\")\n"
            "    child = None\n",
            "    if (\n"
            "        bootstrap_status(config, capabilities=standalone_capabilities(config))[\"result\"]\n"
            "        != \"ready\"\n"
            "    ):\n"
            "        raise LauncherError(\"current-user service-account bootstrap is not ready\")\n"
            "    child = None\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_inactive_or_absent_enrollment_rejects_before_any_resolution",
        ),
        (
            "resolve standalone credentials from reviewed-profile selectors",
            "tools/review_conductor_userland_launcher.py",
            "        webhook_fd, github_fd = inherit_standalone_credentials(config, descriptors)\n",
            "        webhook_fd, github_fd = [\n"
            "            credential_descriptor(resolve_runtime_value(config, capability))\n"
            "            for capability in standalone_capabilities(config)\n"
            "        ]\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_changed_profile_credential_refs_cannot_redirect_resolution",
        ),
        (
            "omit inherited credential descriptors from supervisor pass_fds",
            "tools/review_conductor_userland_launcher.py",
            "                    \"start\",\n"
            "                    \"--apply\",\n"
            "                ],\n"
            "                cwd=config[\"source_root\"],\n"
            "                env=child_environment(config, webhook_fd, github_fd),\n"
            "                pass_fds=(webhook_fd, github_fd),\n",
            "                    \"start\",\n"
            "                    \"--apply\",\n"
            "                ],\n"
            "                cwd=config[\"source_root\"],\n"
            "                env=child_environment(config, webhook_fd, github_fd),\n"
            "                pass_fds=(),\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_standalone_start_invokes_supervisor_with_inherited_descriptors_only",
        ),
        (
            "launch the legacy userland consumer instead of the standalone supervisor",
            "tools/review_conductor_userland_launcher.py",
            "                    str(STANDALONE_SUPERVISOR),\n",
            "                    str(ROOT / \"tools/review_conductor_userland.py\"),\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_standalone_start_invokes_supervisor_with_inherited_descriptors_only",
        ),
        (
            "couple standalone source readiness to an invented tunnel ID",
            "tools/review_conductor_userland.py",
            "            if not tunnel[\"tunnel_name\"]:\n"
            "                raise UserlandError(\"enabled profile requires an isolated tunnel name\")\n"
            "            if ingress[\"public_hostname\"].endswith(\".invalid\"):\n"
            "                raise UserlandError(\"enabled profile requires an isolated public hostname\")\n",
            "            if not tunnel[\"tunnel_name\"]:\n"
            "                raise UserlandError(\"enabled profile requires an isolated tunnel name\")\n"
            "            if (not tunnel[\"tunnel_id\"] or ingress[\"public_hostname\"].endswith(\".invalid\")):\n"
            "                raise UserlandError(\"enabled profile requires an isolated public hostname\")\n",
            "test_review_conductor_profiles.ProfilesTest.test_enabled_standalone_profile_does_not_require_a_tunnel_id",
        ),
        (
            "admit an enabled generalized profile without a tunnel name",
            "tools/review_conductor_userland.py",
            "            if not tunnel[\"tunnel_name\"]:\n"
            "                raise UserlandError(\"enabled profile requires an isolated tunnel name\")\n"
            "            if ingress[\"public_hostname\"].endswith(\".invalid\"):\n",
            "            if ingress[\"public_hostname\"].endswith(\".invalid\"):\n",
            "test_review_conductor_profiles.ProfilesTest.test_enabled_standalone_profile_rejects_empty_tunnel_name",
        ),
        (
            "omit immediate descriptor cleanup after standalone preparation",
            "tools/review_conductor_userland_launcher.py",
            "            stack.callback(os.close, copied)\n"
            "            prepared.append(copied)\n",
            "            prepared.append(copied)\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_partial_resolution_and_preparation_cleanup",
        ),
        (
            "allow legacy start of any generalized profile",
            "tools/review_conductor_userland_launcher.py",
            "    if config.get(\"profile_id\"):\n"
            "        raise LauncherError(\n"
            "            \"generalized profiles use standalone start; legacy start remains the Blocks 9443 consumer\"\n"
            "        )\n",
            "    if config.get(\"profile_id\") == STANDALONE_PROFILE_ID:\n"
            "        raise LauncherError(\n"
            "            \"generalized profiles use standalone start; legacy start remains the Blocks 9443 consumer\"\n"
            "        )\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_legacy_start_keeps_blocks_9443_and_rejects_suite",
        ),
        (
            "report standalone preflight ready while bootstrap is waiting",
            "tools/review_conductor_userland_launcher.py",
            "        \"result\": bootstrap[\"result\"],\n",
            "        \"result\": \"ready\",\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_preflight_validates_enrollment_without_resolving_or_starting_a_tunnel",
        ),
        (
            "hard-code three-account auth metadata for standalone capabilities",
            "tools/review_conductor_userland_launcher.py",
            "        \"auth_mode\": selected_auth_mode(tuple(selected)),\n",
            "        \"auth_mode\": \"three_distinct_service_accounts\",\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_preflight_validates_enrollment_without_resolving_or_starting_a_tunnel",
        ),
        (
            "install standalone stop handlers after supervisor spawn",
            "tools/review_conductor_userland_launcher.py",
            "        for signum in STANDALONE_STOP_SIGNALS:\n"
            "            previous_handlers[signum] = signal.signal(signum, request_stop)\n"
            "        # Own stop handlers before spawn so a startup-time signal cannot\n"
            "        # leave the supervisor child running without its foreground launcher.\n"
            "        with contextlib.ExitStack() as descriptors:\n"
            "            webhook_fd, github_fd = inherit_standalone_credentials(config, descriptors)\n"
            "            child = popen(\n"
            "                [\n"
            "                    sys.executable,\n"
            "                    str(STANDALONE_SUPERVISOR),\n"
            "                    \"--profile\",\n"
            "                    str(profile_path),\n"
            "                    \"--registry\",\n"
            "                    str(registry_path),\n"
            "                    \"start\",\n"
            "                    \"--apply\",\n"
            "                ],\n"
            "                cwd=config[\"source_root\"],\n"
            "                env=child_environment(config, webhook_fd, github_fd),\n"
            "                pass_fds=(webhook_fd, github_fd),\n"
            "            )\n"
            "        assert child is not None\n",
            "        with contextlib.ExitStack() as descriptors:\n"
            "            webhook_fd, github_fd = inherit_standalone_credentials(config, descriptors)\n"
            "            child = popen(\n"
            "                [\n"
            "                    sys.executable,\n"
            "                    str(STANDALONE_SUPERVISOR),\n"
            "                    \"--profile\",\n"
            "                    str(profile_path),\n"
            "                    \"--registry\",\n"
            "                    str(registry_path),\n"
            "                    \"start\",\n"
            "                    \"--apply\",\n"
            "                ],\n"
            "                cwd=config[\"source_root\"],\n"
            "                env=child_environment(config, webhook_fd, github_fd),\n"
            "                pass_fds=(webhook_fd, github_fd),\n"
            "            )\n"
            "        assert child is not None\n"
            "        for signum in STANDALONE_STOP_SIGNALS:\n"
            "            previous_handlers[signum] = signal.signal(signum, request_stop)\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_signal_during_popen_stops_and_reaps_child",
        ),
        (
            "keep standalone outer stop on the Blocks ten-second budget",
            "tools/review_conductor_userland_launcher.py",
            "            stop_standalone_child(child)\n",
            "            stop_child(child)\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_normal_exit_restores_handlers_and_reaps_child_once",
        ),
    )

    def test_precise_suite_activation_launcher_mutants(self):
        for label, relative, old, new, test_id in self.MUTANTS:
            with self.subTest(mutant=label):
                with tempfile.TemporaryDirectory(prefix="review-conductor-mutant-") as temp:
                    copy_root = Path(temp) / "copy"
                    for name in ("tools", "tests", "contracts", "examples"):
                        if (ROOT / name).exists():
                            shutil.copytree(ROOT / name, copy_root / name)
                    target = copy_root / relative
                    source = target.read_text()
                    self.assertEqual(source.count(old), 1, f"mutant anchor drifted: {label}")
                    target.write_text(source.replace(old, new, 1))
                    completed = subprocess.run(
                        [sys.executable, "-m", "unittest", "-q", test_id],
                        cwd=copy_root / "tests",
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env={
                            "PATH": "/usr/bin:/bin",
                            "HOME": temp,
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                    )
                self.assertNotEqual(
                    completed.returncode, 0, f"mutant survived: {label}\n{completed.stderr}"
                )
                self.assertIn(
                    "Ran 1 test",
                    completed.stderr,
                    f"intended test did not run: {label}\n{completed.stderr}",
                )
                test_name = test_id.rsplit(".", 1)[1]
                self.assertRegex(
                    completed.stderr,
                    rf"(FAIL|ERROR): {test_name}",
                    f"failure was not the intended test: {label}\n{completed.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
