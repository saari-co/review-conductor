"""Synthetic qualification of the SMCBD standalone launcher boundary."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
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

    def start(self, config, profile_path, popen, **kwargs):
        original = launcher.credential_descriptor
        descriptors = []

        def prepare(value):
            descriptor = original(value)
            descriptors.append(descriptor)
            return descriptor

        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve), \
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
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.validate_standalone_enrollment(config, self.registry)
            with self.assertRaises(launcher.LauncherError):
                launcher.standalone_preflight(config, path, self.registry)
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry
                )
        self.assertEqual(self.resolutions, [])
        self.write_registry()
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.root / "missing-registry.json"
                )
        self.assertEqual(self.resolutions, [])
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
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve):
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config, profile_path=path, registry_path=self.registry
                )
        self.assertEqual(self.resolutions, [])

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
        self.assertEqual(result["reviewers"], REVIEWERS)
        self.assertFalse(result["credentials_resolved"])
        self.assertFalse(result["tunnel_started"])
        self.assertFalse(result["activation_authorized"])
        self.assertIsNone(config["tunnel"]["tunnel_id"])

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
        self.assertEqual(self.resolutions, list(launcher.standalone_capabilities(config)))
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

                with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
                     patch.object(launcher, "resolve_runtime_value", side_effect=self.resolve), \
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
        self.resolutions.clear()
        with patch.object(launcher, "bootstrap_status", return_value={"result": "ready"}), \
             patch.object(
                 launcher,
                 "resolve_runtime_value",
                 side_effect=launcher.LauncherError("synthetic resolver failure"),
             ), \
             patch("subprocess.Popen") as popen:
            with self.assertRaises(launcher.LauncherError):
                launcher.start_standalone(
                    config,
                    profile_path=path,
                    registry_path=self.registry,
                    popen=popen,
                )
            popen.assert_not_called()

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
            "    child = None\n"
            "    with contextlib.ExitStack() as descriptors:\n",
            "    if (\n"
            "        bootstrap_status(config, capabilities=standalone_capabilities(config))[\"result\"]\n"
            "        != \"ready\"\n"
            "    ):\n"
            "        raise LauncherError(\"current-user service-account bootstrap is not ready\")\n"
            "    child = None\n"
            "    with contextlib.ExitStack() as descriptors:\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_inactive_or_absent_enrollment_rejects_before_any_resolution",
        ),
        (
            "resolve the Cloudflare tunnel capability during standalone start",
            "tools/review_conductor_userland_launcher.py",
            "        for capability in standalone_capabilities(config):\n",
            "        for capability in profiles.capabilities(config):\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_standalone_start_invokes_supervisor_with_inherited_descriptors_only",
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
            "            if ingress[\"public_hostname\"].endswith(\".invalid\"):\n"
            "                raise UserlandError(\"enabled profile requires an isolated public hostname\")\n",
            "            if (not tunnel[\"tunnel_id\"] or ingress[\"public_hostname\"].endswith(\".invalid\")):\n"
            "                raise UserlandError(\"enabled profile requires an isolated public hostname\")\n",
            "test_review_conductor_profiles.ProfilesTest.test_enabled_standalone_profile_does_not_require_a_tunnel_id",
        ),
        (
            "omit immediate descriptor cleanup after standalone preparation",
            "tools/review_conductor_userland_launcher.py",
            "            descriptors.callback(os.close, descriptor)\n"
            "            prepared.append(descriptor)\n"
            "        webhook_fd, github_fd = prepared\n",
            "            prepared.append(descriptor)\n"
            "        webhook_fd, github_fd = prepared\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_partial_resolution_and_preparation_cleanup",
        ),
        (
            "allow legacy start of the SMCBD suite profile",
            "tools/review_conductor_userland_launcher.py",
            "    if config.get(\"profile_id\") == STANDALONE_PROFILE_ID:\n"
            "        raise LauncherError(\n"
            "            \"SMCBD suite uses standalone start; legacy start remains the Blocks 9443 consumer\"\n"
            "        )\n",
            "    if False:\n"
            "        raise LauncherError(\n"
            "            \"SMCBD suite uses standalone start; legacy start remains the Blocks 9443 consumer\"\n"
            "        )\n",
            "test_suite_activation_launcher.SuiteActivationLauncherTests.test_legacy_start_keeps_blocks_9443_and_rejects_suite",
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
