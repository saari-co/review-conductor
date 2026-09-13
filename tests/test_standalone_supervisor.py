"""Synthetic qualification of the standalone service supervisor boundary."""
from __future__ import annotations

import contextlib
import copy
import errno
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import standalone_supervisor as supervisor


REAL_KILLPG = os.killpg
WEBHOOK = b"synthetic-webhook-" + b"w" * (1024 * 1024 - 18)
PRIVATE_KEY = (
    b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-only\n-----END PRIVATE KEY-----"
)


class FakeProcess:
    next_pid = 4100

    def __init__(self, returncode=None):
        FakeProcess.next_pid += 1
        self.pid = FakeProcess.next_pid
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.reaped = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        if self.returncode is None and timeout == 0:
            raise subprocess.TimeoutExpired("fake", timeout)
        self.reaped = True
        return self.returncode


def config_fixture(root: Path):
    return {
        "profile_id": "openclaw-smcbd-suite",
        "enrollment": {"enabled": True, "blockers": []},
        "home": str(root),
        "paths": {
            "state_root": str(root / "state"),
            "proof_root": str(root / "proof"),
            "blocks_checkout": str(root / "checkout"),
        },
        "ingress": {"bind_host": "127.0.0.1", "bind_port": 9444},
        "github_app": {
            "repository": "saari-co/openclaw-smcbd-suite",
            "repository_id": 1366416798,
            "app_id": 4916376,
            "installation_id": 161027021,
        },
        "credentials": {
            "webhook_secret_fd_env": "TEST_WEBHOOK_SECRET_FD",
            "github_private_key_fd_env": "TEST_GITHUB_PRIVATE_KEY_FD",
        },
        "notifications": {
            "discord_target_env": "TEST_DISCORD_TARGET",
            "signal_target_env": "TEST_SIGNAL_TARGET",
        },
    }


@contextlib.contextmanager
def source_credentials():
    with tempfile.TemporaryFile("w+b") as webhook, tempfile.TemporaryFile("w+b") as key:
        webhook.write(WEBHOOK)
        webhook.seek(0)
        key.write(PRIVATE_KEY)
        key.seek(0)
        inherited = (os.dup(webhook.fileno()), os.dup(key.fileno()))
        with patch.dict(
            os.environ,
            {
                "TEST_WEBHOOK_SECRET_FD": str(inherited[0]),
                "TEST_GITHUB_PRIVATE_KEY_FD": str(inherited[1]),
                "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-must-not-propagate",
            },
            clear=False,
        ):
            try:
                yield inherited
            finally:
                for descriptor in inherited:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


class StandaloneSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        killpg = patch.object(supervisor.os, "killpg", side_effect=ProcessLookupError)
        killpg.start()
        self.addCleanup(killpg.stop)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text("{}")
        self.registry = self.root / "registry.json"
        self.config = config_fixture(self.root)

    def assert_closed(self, descriptors):
        for descriptor in descriptors:
            with self.assertRaises(OSError) as error:
                os.fstat(descriptor)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def test_descriptor_transport_rewinds_for_restart_without_secret_exposure(self):
        calls = []

        def popen(command, **kwargs):
            self.assertEqual(kwargs["pass_fds"], credentials)
            for descriptor in credentials:
                self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
            rendered = repr(command) + repr(kwargs["env"])
            self.assertNotIn(WEBHOOK[:80].decode(), rendered)
            self.assertNotIn(PRIVATE_KEY.decode(), rendered)
            self.assertFalse(any(name.startswith("OP_") for name in kwargs["env"]))
            program = """
import os, sys
expected = [int(value) for value in sys.argv[1:]]
values = []
for descriptor in expected:
    with open('/dev/fd/' + str(descriptor), 'rb') as stream:
        values.append(stream.read())
assert len(values[0]) == 1024 * 1024
assert values[0].startswith(b'synthetic-webhook-')
assert values[1].startswith(b'-----BEGIN ' + b'PRIVATE KEY-----')
"""
            result = subprocess.run(
                [sys.executable, "-c", program, *map(str, credentials)],
                pass_fds=credentials,
                env=kwargs["env"],
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for descriptor in credentials:
                os.lseek(descriptor, 1, os.SEEK_SET)
            process = FakeProcess()
            calls.append(process)
            return process

        with source_credentials(), contextlib.ExitStack() as stack:
            credentials = supervisor.prepare_credentials(self.config, stack)
            first = supervisor.spawn_service(
                self.config, self.profile, self.registry, credentials, popen=popen
            )
            first.returncode = 7
            second = supervisor.spawn_service(
                self.config, self.profile, self.registry, credentials, popen=popen
            )
            self.assertEqual(len(calls), 2)
            descriptors = credentials
        self.assert_closed(descriptors)

    def test_partial_credential_preparation_closes_first_anonymous_descriptor(self):
        captured = []
        real = supervisor.legacy_launcher.credential_descriptor

        def prepare(value):
            if captured:
                raise supervisor.SupervisorError("synthetic second preparation failure")
            descriptor = real(value)
            captured.append(descriptor)
            return descriptor

        with source_credentials(), contextlib.ExitStack() as stack, patch.object(
            supervisor.legacy_launcher, "credential_descriptor", side_effect=prepare
        ):
            with self.assertRaises(supervisor.SupervisorError):
                supervisor.prepare_credentials(self.config, stack)
        self.assert_closed(captured)

    def test_partial_source_descriptor_acquisition_closes_first_source(self):
        with tempfile.TemporaryFile("w+b") as source:
            inherited = os.dup(source.fileno())
            with patch.dict(
                os.environ,
                {
                    "TEST_WEBHOOK_SECRET_FD": str(inherited),
                    "TEST_GITHUB_PRIVATE_KEY_FD": "999999999",
                },
                clear=False,
            ), contextlib.ExitStack() as stack:
                with self.assertRaises(supervisor.SupervisorError):
                    supervisor.prepare_credentials(self.config, stack)
                self.assert_closed([inherited])

    def test_spawn_argv_and_environment_contain_only_paths_and_descriptor_numbers(self):
        with tempfile.TemporaryFile("w+b") as webhook, tempfile.TemporaryFile("w+b") as key:
            webhook.write(b"one")
            key.write(b"two")
            credentials = (webhook.fileno(), key.fileno())
            captured = {}

            def popen(command, **kwargs):
                captured.update(command=command, kwargs=kwargs)
                return FakeProcess()

            supervisor.spawn_service(
                self.config, self.profile, self.registry, credentials, popen=popen
            )
        command = captured["command"]
        environment = captured["kwargs"]["env"]
        self.assertEqual(
            command[1:],
            [
                str(supervisor.SERVICE_ENTRYPOINT),
                "--profile",
                str(self.profile),
                "--registry",
                str(self.registry),
                "--apply",
                "serve",
            ],
        )
        self.assertEqual(set(captured["kwargs"]["pass_fds"]), set(credentials))
        self.assertIs(captured["kwargs"]["start_new_session"], True)
        self.assertEqual(environment["TEST_WEBHOOK_SECRET_FD"], str(credentials[0]))
        self.assertEqual(environment["TEST_GITHUB_PRIVATE_KEY_FD"], str(credentials[1]))
        self.assertNotIn("synthetic-webhook-secret-value", repr(command) + repr(environment))
        self.assertNotIn("synthetic-private-key-value", repr(command) + repr(environment))

    def test_configuration_identity_covers_profile_registry_and_isolation_boundary(self):
        baseline = supervisor.configuration_identity(
            self.config, self.profile, self.registry
        )
        other_profile = self.root / "other-profile.json"
        other_profile.write_text("{}")
        variants = [
            (self.config, other_profile, self.registry),
            (self.config, self.profile, self.root / "other-registry.json"),
        ]
        for path in ("state_root", "proof_root", "blocks_checkout"):
            changed = copy.deepcopy(self.config)
            changed["paths"][path] += "-other"
            variants.append((changed, self.profile, self.registry))
        for field in ("repository_id", "app_id", "installation_id"):
            changed = copy.deepcopy(self.config)
            changed["github_app"][field] += 1
            variants.append((changed, self.profile, self.registry))
        changed = copy.deepcopy(self.config)
        changed["ingress"]["bind_port"] += 1
        variants.append((changed, self.profile, self.registry))
        changed = copy.deepcopy(self.config)
        changed["credentials"]["webhook_secret_fd_env"] = "OTHER_WEBHOOK_FD"
        variants.append((changed, self.profile, self.registry))
        changed = copy.deepcopy(self.config)
        changed["enrollment"]["enabled"] = False
        variants.append((changed, self.profile, self.registry))
        for config, profile, registry in variants:
            with self.subTest(config=config, profile=profile, registry=registry):
                self.assertNotEqual(
                    baseline,
                    supervisor.configuration_identity(config, profile, registry),
                )

    def test_crash_stays_failed_until_explicit_restart(self):
        processes = []

        def popen(*_args, **_kwargs):
            process = FakeProcess()
            processes.append(process)
            return process

        with tempfile.TemporaryFile("w+b") as webhook, tempfile.TemporaryFile("w+b") as key:
            webhook.write(b"one")
            key.write(b"two")
            item = supervisor.Supervisor(
                self.config,
                self.profile,
                self.registry,
                (webhook.fileno(), key.fileno()),
                "identity",
                popen=popen,
            )
            item.start_child()
            processes[0].returncode = 9
            self.assertEqual(item.snapshot()["status"], "failed")
            self.assertFalse(item.snapshot()["automatic_restart"])
            self.assertEqual(len(processes), 1)
            item.restart_child()
            self.assertEqual(item.snapshot()["status"], "running")
            self.assertEqual(item.snapshot()["generation"], 2)
            self.assertEqual(len(processes), 2)
            item.stop_child()

    def test_stop_targets_service_process_group(self):
        process = FakeProcess()
        signals = []

        def kill_group(pid, signum):
            if signum == 0:
                if any(sent == signal.SIGTERM for _, sent in signals):
                    raise ProcessLookupError
                return
            signals.append((pid, signum))
            process.returncode = -signum

        supervisor.stop_service_process(process, kill_group=kill_group)
        self.assertEqual(signals, [(process.pid, signal.SIGTERM)])
        self.assertEqual(process.returncode, -signal.SIGTERM)

    def test_stop_targets_process_group_after_leader_exit(self):
        process = FakeProcess(returncode=9)
        signals = []

        def kill_group(pid, signum):
            if signum == 0:
                if any(sent == signal.SIGTERM for _, sent in signals):
                    raise ProcessLookupError
                return
            signals.append((pid, signum))

        supervisor.stop_service_process(process, kill_group=kill_group)
        self.assertEqual(signals, [(process.pid, signal.SIGTERM)])

    def test_exited_leader_does_not_leave_sigterm_ignoring_descendant(self):
        program = """
import os, signal, time
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
    os._exit(0)
os._exit(0)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", program],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def cleanup_group():
            with contextlib.suppress(ProcessLookupError, OSError):
                REAL_KILLPG(proc.pid, signal.SIGKILL)

        self.addCleanup(cleanup_group)
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())
        deadline = time.monotonic() + 2
        while True:
            try:
                REAL_KILLPG(proc.pid, 0)
                break
            except ProcessLookupError:
                if time.monotonic() >= deadline:
                    self.fail("descendant never appeared in the service process group")
                time.sleep(0.01)
        with patch.object(supervisor.os, "killpg", REAL_KILLPG):
            supervisor.stop_service_process(proc, timeout=1)
        with self.assertRaises(ProcessLookupError):
            REAL_KILLPG(proc.pid, 0)

    def test_unreaped_leader_is_reaped_before_group_probe_completes(self):
        process = FakeProcess(returncode=0)
        signals = []

        def kill_group(_pid, signum):
            if signum == 0:
                if process.reaped:
                    raise ProcessLookupError
                return
            signals.append(signum)

        supervisor.stop_service_process(process, kill_group=kill_group, timeout=0.2)
        self.assertEqual(signals, [signal.SIGTERM])
        self.assertTrue(process.reaped)

    def test_surviving_process_group_fails_closed_before_restart(self):
        process = FakeProcess()
        spawned = []

        def kill_group(_pid, _signum):
            return

        with self.assertRaisesRegex(
            supervisor.SupervisorError, "did not terminate"
        ):
            supervisor.stop_service_process(
                process, kill_group=kill_group, timeout=0.15
            )
        item = supervisor.Supervisor(
            self.config,
            self.profile,
            self.registry,
            (3, 4),
            "expected",
            popen=lambda *_args, **_kwargs: spawned.append(True) or FakeProcess(),
        )
        item.child = process
        with patch.object(supervisor, "GROUP_SHUTDOWN_SECONDS", 0.12), patch.object(
            supervisor.os, "killpg", kill_group
        ):
            with self.assertRaisesRegex(
                supervisor.SupervisorError, "did not terminate"
            ):
                item.restart_child()
        self.assertEqual(spawned, [])
        self.assertIs(item.child, process)
        self.assertEqual(item.generation, 0)

    def test_control_rejects_wrong_identity_before_restart_or_stop(self):
        with tempfile.TemporaryFile("w+b") as webhook, tempfile.TemporaryFile("w+b") as key:
            webhook.write(b"x")
            key.write(b"y")
            webhook.flush()
            key.flush()
            process = FakeProcess()
            item = supervisor.Supervisor(
                self.config,
                self.profile,
                self.registry,
                (webhook.fileno(), key.fileno()),
                "expected",
                popen=lambda *_args, **_kwargs: FakeProcess(),
            )
            item.child = process
            left, right = socket.socketpair()
            with left, right:
                right.sendall(b'{"command":"restart","identity":"wrong"}\n')
                self.assertTrue(supervisor._handle_control(left, item))
                response = json.loads(right.recv(4096))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(item.generation, 0)
            self.assertIs(item.child, process)

    def test_control_rejects_non_string_command_without_stopping(self):
        process = FakeProcess()
        item = supervisor.Supervisor(
            self.config, self.profile, self.registry, (3, 4), "expected"
        )
        item.child = process
        left, right = socket.socketpair()
        with left, right:
            right.sendall(b'{"command":["stop"],"identity":"expected"}\n')
            self.assertTrue(supervisor._handle_control(left, item))
            response = json.loads(right.recv(4096))
        self.assertEqual(response["status"], "rejected")
        self.assertFalse(item.stopping)
        self.assertIs(item.child, process)
        self.assertFalse(process.terminated)

    def test_fragmented_health_control_request_is_read_to_its_bound(self):
        process = FakeProcess()
        item = supervisor.Supervisor(
            self.config, self.profile, self.registry, (3, 4), "expected"
        )
        item.child = process
        left, right = socket.socketpair()
        result = []
        with left, right:
            thread = threading.Thread(
                target=lambda: result.append(supervisor._handle_control(left, item))
            )
            thread.start()
            right.sendall(b'{"command":"health",')
            right.sendall(b'"identity":"expected"}\n')
            response = json.loads(supervisor._recv_line(right, "test response"))
            thread.join(2)
        self.assertEqual(response["status"], "running")
        self.assertEqual(result, [True])

    def test_control_transport_failure_cannot_stop_supervisor(self):
        class BrokenConnection:
            def recv(self, _size):
                raise TimeoutError("synthetic stalled client")

            def sendall(self, _value):
                raise BrokenPipeError("synthetic disconnected client")

        process = FakeProcess()
        item = supervisor.Supervisor(
            self.config, self.profile, self.registry, (3, 4), "expected"
        )
        item.child = process
        self.assertTrue(supervisor._handle_control(BrokenConnection(), item))
        self.assertFalse(item.stopping)
        self.assertFalse(process.terminated)

    def test_control_timeout_is_not_reported_as_stopped(self):
        class TimedOutSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                pass

            def connect(self, _path):
                raise TimeoutError("synthetic control timeout")

        with patch.object(supervisor.socket, "socket", return_value=TimedOutSocket()):
            with self.assertRaisesRegex(
                supervisor.SupervisorError, "control request failed"
            ):
                supervisor.request_control(
                    self.config, self.profile, self.registry, "health"
                )

    def test_restart_control_timeout_exceeds_group_shutdown(self):
        captured = []

        class CaptureTimeout:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, timeout):
                captured.append(timeout)

            def connect(self, _path):
                raise TimeoutError("synthetic control timeout")

        with patch.object(supervisor.socket, "socket", return_value=CaptureTimeout()):
            with self.assertRaisesRegex(
                supervisor.SupervisorError, "control request failed"
            ):
                supervisor.request_control(
                    self.config, self.profile, self.registry, "restart"
                )
            with self.assertRaisesRegex(
                supervisor.SupervisorError, "control request failed"
            ):
                supervisor.request_control(
                    self.config, self.profile, self.registry, "health"
                )
        self.assertGreater(
            captured[0], 2 * supervisor.GROUP_SHUTDOWN_SECONDS
        )
        self.assertEqual(captured[1], supervisor.CONTROL_REQUEST_SECONDS)
        self.assertNotEqual(captured[0], captured[1])

    def test_control_response_requires_matching_identity(self):
        class ResponseSocket:
            def __init__(self, response):
                self.response = response

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                pass

            def connect(self, _path):
                pass

            def sendall(self, _value):
                pass

            def recv(self, size):
                result, self.response = self.response[:size], self.response[size:]
                return result

        invalid = (
            (b'{"status":"running","identity":"wrong"}\n', "mismatched identity"),
            (b'{"status":"invented","identity":"wrong"}\n', "invalid response"),
            (b'{"status":"rejected"}\n', "invalid response"),
        )
        for payload, message in invalid:
            with self.subTest(payload=payload), patch.object(
                supervisor.socket, "socket", return_value=ResponseSocket(payload)
            ):
                with self.assertRaisesRegex(supervisor.SupervisorError, message):
                    supervisor.request_control(
                        self.config, self.profile, self.registry, "health"
                    )

    def test_supervisor_lock_rejects_insecure_state_root_and_symlink_lock(self):
        lock_path = supervisor.supervisor_paths(self.config)[0]
        lock_path.parent.mkdir(mode=0o700)
        os.chmod(lock_path.parent, 0o777)
        with self.assertRaises(supervisor.SupervisorError):
            with supervisor.supervisor_lock(lock_path):
                self.fail("insecure state root was accepted")
        os.chmod(lock_path.parent, 0o700)
        target = self.root / "outside-lock"
        target.write_text("")
        lock_path.symlink_to(target)
        with self.assertRaises(supervisor.SupervisorError):
            with supervisor.supervisor_lock(lock_path):
                self.fail("symlink lock was followed")

    def test_foreground_lifecycle_health_stop_and_cleanup(self):
        process = FakeProcess()
        captured = []
        real_prepare = supervisor.prepare_credentials

        def prepare(config, stack):
            descriptors = real_prepare(config, stack)
            captured.extend(descriptors)
            return descriptors

        result = []
        with source_credentials() as sources, patch.object(
            supervisor, "prepare_credentials", side_effect=prepare
        ):
            thread = threading.Thread(
                target=lambda: result.append(
                    supervisor.run_supervisor(
                        self.config,
                        self.profile,
                        self.registry,
                        popen=lambda *_args, **_kwargs: process,
                        install_signals=False,
                    )
                )
            )
            thread.start()
            socket_path = supervisor.supervisor_paths(self.config)[1]
            for _ in range(200):
                if socket_path.exists():
                    break
                threading.Event().wait(0.01)
            self.assertTrue(socket_path.exists())
            health = supervisor.request_control(
                self.config, self.profile, self.registry, "health"
            )
            self.assertEqual(health["status"], "running")
            stopped = supervisor.request_control(
                self.config, self.profile, self.registry, "stop"
            )
            self.assertEqual(stopped["status"], "stopping")
            thread.join(5)
            self.assert_closed(sources)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])
        self.assertTrue(process.reaped)
        self.assertFalse(socket_path.exists())
        self.assert_closed(captured)

    def test_initial_spawn_failure_cleans_socket_and_credentials(self):
        captured = []
        real_prepare = supervisor.prepare_credentials

        def prepare(config, stack):
            descriptors = real_prepare(config, stack)
            captured.extend(descriptors)
            return descriptors

        with source_credentials() as sources, patch.object(
            supervisor, "prepare_credentials", side_effect=prepare
        ):
            with self.assertRaises(supervisor.SupervisorError):
                supervisor.run_supervisor(
                    self.config,
                    self.profile,
                    self.registry,
                    popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        OSError("synthetic spawn failure")
                    ),
                    install_signals=False,
                )
            self.assert_closed(sources)
        self.assertFalse(supervisor.supervisor_paths(self.config)[1].exists())
        self.assert_closed(captured)

    def test_chmod_failure_unlinks_bound_control_socket(self):
        socket_path = supervisor.supervisor_paths(self.config)[1]
        with source_credentials(), patch.object(
            supervisor.os, "chmod", side_effect=OSError("synthetic chmod failure")
        ):
            with self.assertRaises(supervisor.SupervisorError):
                supervisor.run_supervisor(
                    self.config,
                    self.profile,
                    self.registry,
                    popen=lambda *_args, **_kwargs: FakeProcess(),
                    install_signals=False,
                )
        self.assertFalse(socket_path.exists())

    def test_stop_signals_are_installed_before_child_spawn_and_restored(self):
        events = []

        def install(signum, handler):
            events.append(("signal", signum, handler))
            return f"previous-{signum}"

        def fail_spawn(*_args, **_kwargs):
            events.append(("spawn",))
            raise OSError("synthetic spawn failure")

        with source_credentials(), patch.object(
            supervisor.signal, "signal", side_effect=install
        ):
            with self.assertRaises(supervisor.SupervisorError):
                supervisor.run_supervisor(
                    self.config,
                    self.profile,
                    self.registry,
                    popen=fail_spawn,
                    install_signals=True,
                )
        self.assertEqual(
            [event[0] for event in events],
            ["signal", "signal", "spawn", "signal", "signal"],
        )
        self.assertEqual(events[3][2], f"previous-{signal.SIGINT}")
        self.assertEqual(events[4][2], f"previous-{signal.SIGTERM}")


class MutationTests(unittest.TestCase):
    MUTANTS = (
        (
            '            pass_fds=credentials,\n',
            '',
            'test_spawn_argv_and_environment_contain_only_paths_and_descriptor_numbers',
        ),
        (
            '            start_new_session=True,\n',
            '',
            'test_spawn_argv_and_environment_contain_only_paths_and_descriptor_numbers',
        ),
        (
            '        "registry_path": str(require_registry_path(registry_path)),\n',
            '',
            'test_configuration_identity_covers_profile_registry_and_isolation_boundary',
        ),
        (
            '            "service_returncode": returncode,\n            "generation": self.generation,\n            "automatic_restart": False,\n',
            '            "service_returncode": returncode,\n            "generation": self.generation,\n            "automatic_restart": True,\n',
            'test_crash_stays_failed_until_explicit_restart',
        ),
        (
            '        if request["identity"] != supervisor.identity:\n            raise SupervisorError("standalone supervisor identity does not match")\n',
            '',
            'test_control_rejects_wrong_identity_before_restart_or_stop',
        ),
        (
            '            stack.callback(socket_path.unlink, missing_ok=True)\n',
            '',
            'test_foreground_lifecycle_health_stop_and_cleanup',
        ),
        (
            '            os.lseek(descriptor, 0, os.SEEK_SET)\n',
            '            pass\n',
            'test_descriptor_transport_rewinds_for_restart_without_secret_exposure',
        ),
        (
            '        kill_group(pgid, signal.SIGTERM)\n',
            '        if process.poll() is not None:\n            return\n        kill_group(pgid, signal.SIGTERM)\n',
            'test_exited_leader_does_not_leave_sigterm_ignoring_descendant',
        ),
        (
            '                kill_group(pgid, signal.SIGKILL)\n',
            '                pass\n',
            'test_exited_leader_does_not_leave_sigterm_ignoring_descendant',
        ),
        (
            '    _reap_leader(process)\n',
            '',
            'test_unreaped_leader_is_reaped_before_group_probe_completes',
        ),
        (
            '                    raise SupervisorError(\n                        "standalone service process group did not terminate"\n                    )\n',
            '                    break\n',
            'test_surviving_process_group_fails_closed_before_restart',
        ),
        (
            '        RESTART_CONTROL_SECONDS if command == "restart" else CONTROL_REQUEST_SECONDS\n',
            '        CONTROL_REQUEST_SECONDS\n',
            'test_restart_control_timeout_exceeds_group_shutdown',
        ),
        (
            '    if not isinstance(command, str) or command not in {"health", "stop", "restart"}:\n',
            '    if command not in {"health", "stop", "restart"}:\n',
            'test_control_rejects_non_string_command_without_stopping',
        ),
        (
            '            listener.bind(str(socket_path))\n            stack.callback(socket_path.unlink, missing_ok=True)\n            os.chmod(socket_path, 0o600)\n',
            '            listener.bind(str(socket_path))\n            os.chmod(socket_path, 0o600)\n            stack.callback(socket_path.unlink, missing_ok=True)\n',
            'test_chmod_failure_unlinks_bound_control_socket',
        ),
        (
            '            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,\n',
            '            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,\n',
            'test_supervisor_lock_rejects_insecure_state_root_and_symlink_lock',
        ),
        (
            '        "config_sha256": config_digest,\n',
            '',
            'test_configuration_identity_covers_profile_registry_and_isolation_boundary',
        ),
        (
            '            incoming.callback(_close_descriptor, descriptor)\n',
            '',
            'test_partial_source_descriptor_acquisition_closes_first_source',
        ),
        (
            '    except OSError:\n        # A stalled or disconnected local client cannot terminate the service.\n        with contextlib.suppress(OSError):\n            _send_response(\n                connection,\n                {"status": "rejected", "error": "control transport failed"},\n            )\n',
            '    except OSError:\n        raise\n',
            'test_control_transport_failure_cannot_stop_supervisor',
        ),
        (
            '    except OSError as exc:\n        raise SupervisorError("standalone supervisor control request failed") from exc\n',
            '    except OSError:\n        raise\n',
            'test_control_timeout_is_not_reported_as_stopped',
        ),
        (
            '    if status not in {"running", "failed", "stopping", "rejected"}:\n        raise SupervisorError("standalone supervisor returned an invalid response")\n',
            '',
            'test_control_response_requires_matching_identity',
        ),
        (
            '    elif (\n        response.get("schema") != "review-conductor.standalone-supervisor-health.v1"\n        or response.get("identity") != identity\n    ):\n        raise SupervisorError("standalone supervisor returned a mismatched identity")\n',
            '',
            'test_control_response_requires_matching_identity',
        ),
        (
            '            if install_signals:\n                for signum in (signal.SIGINT, signal.SIGTERM):\n                    previous_handlers[signum] = signal.signal(signum, request_stop)\n            # Install handlers before spawning so a startup-time signal cannot\n            # leave the service child running without its foreground supervisor.\n            supervisor.start_child()\n',
            '            supervisor.start_child()\n            if install_signals:\n                for signum in (signal.SIGINT, signal.SIGTERM):\n                    previous_handlers[signum] = signal.signal(signum, request_stop)\n',
            'test_stop_signals_are_installed_before_child_spawn_and_restored',
        ),
    )

    def test_precise_standalone_supervisor_mutants(self):
        for anchor, replacement, test_name in self.MUTANTS:
            with self.subTest(test_name=test_name), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp)
                for name in ("tools", "tests", "contracts", "examples"):
                    shutil.copytree(ROOT / name, destination / name)
                source = destination / "tools/standalone_supervisor.py"
                text = source.read_text()
                self.assertEqual(text.count(anchor), 1)
                source.write_text(text.replace(anchor, replacement, 1))
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        f"test_standalone_supervisor.StandaloneSupervisorTests.{test_name}",
                    ],
                    cwd=destination / "tests",
                    env={**os.environ, "PYTHONPATH": str(destination / "tests")},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("Ran 1 test", result.stderr)
                self.assertRegex(result.stderr, rf"(FAIL|ERROR): {test_name}")


if __name__ == "__main__":
    unittest.main()
