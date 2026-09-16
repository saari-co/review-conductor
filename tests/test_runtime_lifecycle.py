"""Synthetic process-level qualification of standalone parent-loss drain."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_conductor_userland_launcher as launcher
import service_entrypoint as entrypoint
import standalone_supervisor as supervisor


TOOLS = str(ROOT / "tools")
FAKE_SERVICE = r"""
import fcntl
import os
import select
import signal
import socket
import sys
import time

sys.path.insert(0, sys.argv[1])
import service_entrypoint as entry

watch = sys.argv[2] == "watch"
resistant = sys.argv[3] == "resistant"
lock_path = sys.argv[4]
status_fd = int(sys.argv[5])
parent_fd = int(os.environ["REVIEW_CONDUCTOR_PARENT_LIFETIME_FD"])
os.set_blocking(parent_fd, False)
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
port = listener.getsockname()[1]
os.write(
    status_fd,
    f"{os.getpid()} {os.getpgrp()} {port}\n".encode(),
)
os.close(status_fd)
if resistant:
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(1)
stop = False

def request_stop(_signum, _frame):
    global stop
    stop = True

for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(signum, request_stop)
deadline = time.monotonic() + 8
while not stop and time.monotonic() < deadline:
    if watch:
        try:
            if entry.parent_lifetime_lost(parent_fd):
                break
        except entry.service.ServiceError:
            break
    readable, _, _ = select.select([listener], [], [], 0.05)
    if readable:
        connection, _ = listener.accept()
        connection.close()
listener.close()
if watch:
    entry.drain_owned_session(timeout=0.35)
else:
    while time.monotonic() < deadline:
        time.sleep(0.05)
os.close(lock_fd)
"""


def _wait_until(predicate, timeout=3.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {message}")


def _group_absent(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError as exc:
        return exc.errno == errno.ESRCH
    return False


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _lock_released(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return True
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(descriptor)
    return True


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def spawn_fake_service(self, *, watch: bool, resistant: bool = False):
        parent_read, parent_write = os.pipe()
        status_read, status_write = os.pipe()
        lock_path = self.root / "service.lock"
        lock_path.write_bytes(b"")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    FAKE_SERVICE,
                    TOOLS,
                    "watch" if watch else "orphan",
                    "resistant" if resistant else "plain",
                    str(lock_path),
                    str(status_write),
                ],
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    entrypoint.PARENT_LIFETIME_FD_ENV: str(parent_read),
                },
                pass_fds=(parent_read, status_write),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            os.close(parent_read)
            os.close(status_write)
        try:
            readable, _, _ = select_ready(status_read, 3)
            self.assertEqual(readable, [status_read])
            identity = os.read(status_read, 64).decode().split()
        finally:
            os.close(status_read)
        pid, pgid, port = map(int, identity)
        self.addCleanup(lambda: self._reap_group(process, pgid, parent_write))
        return process, parent_write, pid, pgid, port, lock_path

    def _reap_group(self, process, pgid, parent_write):
        with contextlib.suppress(OSError):
            os.close(parent_write)
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(pgid, signal.SIGKILL)
            process.wait(timeout=2)

    def test_baseline_supervisor_disappearance_leaves_listening_orphan(self):
        process, parent_write, _pid, pgid, port, lock_path = self.spawn_fake_service(
            watch=False
        )
        os.close(parent_write)
        time.sleep(0.25)
        self.assertIsNone(process.poll())
        self.assertFalse(_port_free(port))
        self.assertFalse(_lock_released(lock_path))
        self.assertFalse(_group_absent(pgid))

    def test_parent_write_close_drains_listener_lock_and_session(self):
        process, parent_write, _pid, pgid, port, lock_path = self.spawn_fake_service(
            watch=True
        )
        os.close(parent_write)
        _wait_until(lambda: process.poll() is not None, message="service exit")
        _wait_until(lambda: _group_absent(pgid), message="session drain")
        self.assertTrue(_port_free(port))
        self.assertTrue(_lock_released(lock_path))

    def test_abrupt_owner_death_drains_term_resistant_descendant(self):
        process, parent_write, pid, pgid, port, lock_path = self.spawn_fake_service(
            watch=True, resistant=True
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", "import signal; signal.pause()"],
            pass_fds=(parent_write,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(parent_write)
        self.addCleanup(holder.kill)
        holder.kill()
        holder.wait(timeout=2)
        _wait_until(lambda: process.poll() is not None, timeout=4, message="service exit")
        _wait_until(lambda: _group_absent(pgid), timeout=4, message="resistant session drain")
        self.assertTrue(_port_free(port))
        self.assertTrue(_lock_released(lock_path))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_missing_parent_descriptor_fails_before_registry(self):
        profile = self.root / "profile.json"
        profile.write_text("{}")
        generation_read, generation_write = os.pipe()
        leader_read, leader_write = os.pipe()
        self.addCleanup(lambda: supervisor._close_descriptor(generation_read))
        self.addCleanup(lambda: supervisor._close_descriptor(generation_write))
        self.addCleanup(lambda: supervisor._close_descriptor(leader_read))
        self.addCleanup(lambda: supervisor._close_descriptor(leader_write))
        environment = {
            entrypoint.PROFILE_DIGEST_ENV: "a" * 64,
            entrypoint.GENERATION_FD_ENV: str(generation_write),
            entrypoint.LEADER_FD_ENV: str(leader_write),
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            entrypoint.userland, "load_config", return_value={"profile_id": "x"}
        ), patch.object(entrypoint, "registry_provider") as registry:
            with self.assertRaisesRegex(
                entrypoint.service.ServiceError, "parent-lifetime descriptor"
            ):
                entrypoint.load_supervised_profile(profile)
        registry.assert_not_called()

    def test_closed_parent_descriptor_fails_closed(self):
        profile = self.root / "profile.json"
        profile.write_text("{}")
        generation_read, generation_write = os.pipe()
        leader_read, leader_write = os.pipe()
        parent_read, parent_write = os.pipe()
        os.close(parent_read)
        os.close(parent_write)
        self.addCleanup(lambda: supervisor._close_descriptor(generation_read))
        self.addCleanup(lambda: supervisor._close_descriptor(generation_write))
        self.addCleanup(lambda: supervisor._close_descriptor(leader_read))
        self.addCleanup(lambda: supervisor._close_descriptor(leader_write))
        environment = {
            entrypoint.PROFILE_DIGEST_ENV: "a" * 64,
            entrypoint.GENERATION_FD_ENV: str(generation_write),
            entrypoint.LEADER_FD_ENV: str(leader_write),
            entrypoint.PARENT_LIFETIME_FD_ENV: str(parent_read),
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            entrypoint.userland, "load_config", return_value={"profile_id": "x"}
        ):
            with self.assertRaisesRegex(
                entrypoint.service.ServiceError, "parent-lifetime descriptor"
            ):
                entrypoint.load_supervised_profile(profile)

    def test_partial_lifetime_pipe_setup_closes_prior_descriptors(self):
        created = []
        real_pipe = os.pipe

        def failing_pipe():
            if len(created) >= 2:
                raise OSError("synthetic parent pipe failure")
            pair = real_pipe()
            created.append(pair)
            return pair

        spawned = []
        with tempfile.TemporaryFile("w+b") as webhook, tempfile.TemporaryFile(
            "w+b"
        ) as key, patch.object(supervisor.os, "pipe", side_effect=failing_pipe):
            with self.assertRaisesRegex(
                supervisor.SupervisorError, "lifetime handles"
            ):
                supervisor.spawn_service(
                    {
                        "profile_id": "openclaw-smcbd-suite",
                        "home": str(self.root),
                        "paths": {
                            "state_root": str(self.root / "state"),
                            "proof_root": str(self.root / "proof"),
                            "blocks_checkout": str(self.root / "checkout"),
                        },
                        "ingress": {"bind_host": "127.0.0.1", "bind_port": 9444},
                        "github_app": {
                            "repository": "saari-co/openclaw-smcbd-suite",
                            "repository_id": 1,
                            "app_id": 1,
                            "installation_id": 1,
                        },
                        "credentials": {
                            "webhook_secret_fd_env": "TEST_WEBHOOK_SECRET_FD",
                            "github_private_key_fd_env": "TEST_GITHUB_PRIVATE_KEY_FD",
                        },
                        "notifications": {
                            "discord_target_env": "TEST_DISCORD_TARGET",
                            "signal_target_env": "TEST_SIGNAL_TARGET",
                        },
                    },
                    self.root / "profile.json",
                    self.root / "registry.json",
                    (webhook.fileno(), key.fileno()),
                    popen=lambda *_args, **_kwargs: spawned.append(True),
                )
        self.assertEqual(spawned, [])
        for pair in created:
            for descriptor in pair:
                with self.assertRaises(OSError) as error:
                    os.fstat(descriptor)
                self.assertEqual(error.exception.errno, errno.EBADF)

    def test_drain_refuses_non_leader_without_signaling_guessed_pgid(self):
        program = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import service_entrypoint as entry
signals = []
def kill_group(pgid, signum):
    signals.append((pgid, signum))
try:
    entry.drain_owned_session(timeout=0.05, kill_group=kill_group)
except entry.service.ServiceError as exc:
    if "session leader" in str(exc) and signals == []:
        os._exit(0)
os._exit(1)
"""
        result = subprocess.run(
            [sys.executable, "-c", program, TOOLS],
            check=False,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0)

    def test_standalone_outer_budget_exceeds_supervisor_drain_blocks_stays_legacy(self):
        self.assertEqual(launcher.LEGACY_CHILD_STOP_SECONDS, 10)
        self.assertEqual(
            launcher.STANDALONE_CHILD_STOP_SECONDS,
            supervisor.SHUTDOWN_CONTROL_SECONDS,
        )
        self.assertGreater(
            launcher.STANDALONE_CHILD_STOP_SECONDS,
            supervisor.GROUP_SHUTDOWN_SECONDS * 2,
        )
        self.assertEqual(entrypoint.OWNED_GENERATION_SHUTDOWN_SECONDS, 10)
        self.assertIn(signal.SIGHUP, supervisor.STOP_SIGNALS)
        self.assertIn(signal.SIGHUP, launcher.STANDALONE_STOP_SIGNALS)
        self.assertNotIn(
            "STANDALONE_STOP_SIGNALS",
            Path(launcher.__file__).read_text().split("def start(")[1].split("def inherit_standalone")[0],
        )

    def test_parent_lifetime_eof_is_not_data(self):
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.addCleanup(lambda: supervisor._close_descriptor(read_fd))
        self.addCleanup(lambda: supervisor._close_descriptor(write_fd))
        self.assertFalse(entrypoint.parent_lifetime_lost(read_fd))
        os.write(write_fd, b"x")
        with self.assertRaisesRegex(entrypoint.service.ServiceError, "corrupted"):
            entrypoint.parent_lifetime_lost(read_fd)
        os.close(write_fd)
        write_fd = -1
        read_fd2, write_fd2 = os.pipe()
        os.set_blocking(read_fd2, False)
        self.addCleanup(lambda: supervisor._close_descriptor(read_fd2))
        self.addCleanup(lambda: supervisor._close_descriptor(write_fd2))
        os.close(write_fd2)
        self.assertTrue(entrypoint.parent_lifetime_lost(read_fd2))


def select_ready(descriptor, timeout):
    import select as selector

    return selector.select([descriptor], [], [], timeout)


class MutationTests(unittest.TestCase):
    MUTANTS = (
        (
            "tools/service_entrypoint.py",
            "        if not readable:\n            return False\n        value = os.read(parent_fd, 1)\n",
            "        if not readable:\n            return False\n        return False\n        value = os.read(parent_fd, 1)\n",
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_parent_write_close_drains_listener_lock_and_session",
        ),
        (
            "tools/standalone_supervisor.py",
            "    environment[PARENT_LIFETIME_FD_ENV] = str(parent_lifetime_fd)\n",
            "",
            "test_standalone_supervisor.StandaloneSupervisorTests.test_spawn_argv_and_environment_contain_only_paths_and_descriptor_numbers",
        ),
        (
            "tools/review_conductor_userland_launcher.py",
            "STANDALONE_CHILD_STOP_SECONDS = 22\n",
            "STANDALONE_CHILD_STOP_SECONDS = 10\n",
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_standalone_outer_budget_exceeds_supervisor_drain_blocks_stays_legacy",
        ),
        (
            "tools/service_entrypoint.py",
            "    if pgid != pid:\n        raise service.ServiceError(\"service is not the owned session leader\")\n",
            "    if False:\n        raise service.ServiceError(\"service is not the owned session leader\")\n",
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_drain_refuses_non_leader_without_signaling_guessed_pgid",
        ),
    )

    def test_precise_runtime_lifecycle_mutants(self):
        for source_name, anchor, replacement, test_id in self.MUTANTS:
            with self.subTest(test_id=test_id, source_name=source_name):
                with tempfile.TemporaryDirectory(prefix="review-conductor-mutant-") as temp:
                    destination = Path(temp)
                    for name in ("tools", "tests", "contracts", "examples"):
                        shutil.copytree(ROOT / name, destination / name)
                    source = destination / source_name
                    text = source.read_text()
                    self.assertEqual(text.count(anchor), 1, f"mutant anchor drifted: {anchor!r}")
                    source.write_text(text.replace(anchor, replacement, 1))
                    result = subprocess.run(
                        [sys.executable, "-m", "unittest", "-q", test_id],
                        cwd=destination / "tests",
                        env={**os.environ, "PYTHONPATH": str(destination / "tests")},
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Ran 1 test", result.stderr)
                    self.assertRegex(result.stderr, r"(FAIL|ERROR): ")


if __name__ == "__main__":
    unittest.main()
