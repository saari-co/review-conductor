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
SERVE_THREAD_SHUTDOWN_ANCHOR = (
    "            threading.Thread(\n"
    "                target=current.shutdown,\n"
    "                name=\"review-conductor-shutdown\",\n"
    "                daemon=True,\n"
    "            ).start()\n"
)
SERVE_THREAD_SHUTDOWN_DEFECT = "            current.shutdown()\n"
PARENT_LOSS_DRAIN_ANCHOR = (
    "        if parent_lost.is_set():\n"
    "            # Parent-loss drain is independent of the worker. An in-flight adapter\n"
    "            # or TERM-resistant descendant must not delay the session reap after\n"
    "            # the original supervisor is already gone.\n"
    "            drain_owned_session()\n"
    "        elif worker.is_alive():\n"
    "            # A tick may be inside a bounded external adapter operation far longer\n"
    "            # than the polling interval. Never return while that worker still owns\n"
    "            # a claim or subprocess; its adapter timeout remains the upper bound.\n"
    "            worker.join()\n"
)
PARENT_LOSS_JOIN_FIRST_DEFECT = (
    "        if worker.is_alive():\n"
    "            worker.join()\n"
    "        if parent_lost.is_set():\n"
    "            drain_owned_session()\n"
)
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

ENTRYPOINT_SERVICE = r"""
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import service_entrypoint as entry
import service_runtime as service

state_root = sys.argv[2]
status_fd = int(sys.argv[3])
blocked = sys.argv[4] == "blocked"
resistant = sys.argv[5] == "resistant"
entry.OWNED_GENERATION_SHUTDOWN_SECONDS = 0.35
state = Path(state_root)
state.mkdir(parents=True, exist_ok=True)
config = {
    "enrollment": {"enabled": True, "blockers": []},
    "credentials": {"webhook_secret_fd_env": "TEST_WEBHOOK_SECRET_FD"},
    "ingress": {
        "bind_host": "127.0.0.1",
        "bind_port": 0,
        "request_timeout_seconds": 1,
        "max_body_bytes": 4096,
    },
    "worker": {"tick_seconds": 0.05},
    "paths": {
        "state_root": str(state),
        "proof_root": str(state / "proof"),
        "blocks_checkout": str(state / "checkout"),
    },
}

class Client:
    def read_policy(self, *_args, **_kwargs):
        raise AssertionError("synthetic client must not read policy")

class ReportingServer(runtime.BoundedHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _host, port = self.server_address
        lock_path = state.resolve() / ".service-operation.lock"
        os.write(status_fd, f"{os.getpid()} {os.getpgrp()} {port} {lock_path}\n".encode())
        os.close(status_fd)

def idle_tick(*_args, **_kwargs):
    return None

def hung_tick(*_args, **_kwargs):
    (state / "tick-started").write_text("1")
    if resistant:
        child = os.fork()
        if child == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(1)
    time.sleep(3600)
    (state / "tick-finished").write_text("1")

with patch.object(entry, "load_supervised_profile", return_value=config), patch.object(
    entry, "registry_provider", return_value=lambda: object()
), patch.object(
    userland, "read_inherited_value", return_value="fixture-secret"
), patch.object(
    userland, "build_client", return_value=Client()
), patch.object(
    userland, "OpenClawNotifier", return_value=object()
), patch.object(
    service, "run_service_tick", side_effect=hung_tick if blocked else idle_tick
), patch.object(
    runtime, "BoundedHTTPServer", ReportingServer
):
    entry.serve(state / "profile.json", state / "registry.json")
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

    def _mutated_tools(self, source_name, anchor, replacement):
        destination = Path(
            tempfile.mkdtemp(prefix="review-conductor-baseline-", dir=self.root)
        )
        shutil.copytree(ROOT / "tools", destination / "tools")
        source = destination / source_name
        text = source.read_text()
        self.assertEqual(text.count(anchor), 1, f"baseline anchor drifted: {anchor!r}")
        source.write_text(text.replace(anchor, replacement, 1))
        return destination / "tools"

    def spawn_entrypoint_service(self, *, blocked: bool, resistant: bool = False, tools=None):
        parent_read, parent_write = os.pipe()
        status_read, status_write = os.pipe()
        state_root = self.root / "entrypoint-state"
        state_root.mkdir()
        error_path = self.root / "entrypoint.err"
        error_file = error_path.open("wb")
        self.addCleanup(error_file.close)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    ENTRYPOINT_SERVICE,
                    str(tools or TOOLS),
                    str(state_root),
                    str(status_write),
                    "blocked" if blocked else "idle",
                    "resistant" if resistant else "plain",
                ],
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    entrypoint.PARENT_LIFETIME_FD_ENV: str(parent_read),
                },
                pass_fds=(parent_read, status_write),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_file,
            )
        finally:
            os.close(parent_read)
            os.close(status_write)
        try:
            readable, _, _ = select_ready(status_read, 5)
            self.assertEqual(
                readable,
                [status_read],
                msg=error_path.read_text() if error_path.exists() else "no child stderr",
            )
            identity = os.read(status_read, 256).decode().split(maxsplit=3)
        finally:
            os.close(status_read)
        pid, pgid, port = map(int, identity[:3])
        lock_path = Path(identity[3].strip())
        self.addCleanup(lambda: self._reap_group(process, pgid, parent_write))
        return process, parent_write, pid, pgid, port, lock_path, state_root

    def _assert_listener_accepts(self, port: int):
        probe = socket.create_connection(("127.0.0.1", port), timeout=1)
        probe.close()

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
        legacy_start = (
            Path(launcher.__file__).read_text().split("def start(")[1].split("def inherit_standalone")[0]
        )
        self.assertNotIn("STANDALONE_STOP_SIGNALS", legacy_start)
        self.assertNotIn("stop_standalone_child", legacy_start)
        self.assertIn("stop_child(conductor)", legacy_start)
        self.assertIn("stop_child(process)", legacy_start)

    def test_standalone_stop_helper_uses_qualified_budget_blocks_keeps_default(self):
        recorded = []

        class Child:
            def __init__(self):
                self.returncode = None

            def poll(self):
                return None

            def terminate(self):
                return None

            def wait(self, timeout):
                recorded.append(timeout)
                self.returncode = 0
                return 0

            def kill(self):
                return None

        launcher.stop_child(Child())
        self.assertEqual(recorded, [launcher.LEGACY_CHILD_STOP_SECONDS])
        recorded.clear()
        launcher.stop_standalone_child(Child())
        self.assertEqual(recorded, [launcher.STANDALONE_CHILD_STOP_SECONDS])

    def test_baseline_actual_serving_loop_sigterm_deadlocks_on_serve_thread_shutdown(self):
        tools = self._mutated_tools(
            "tools/service_entrypoint.py",
            SERVE_THREAD_SHUTDOWN_ANCHOR,
            SERVE_THREAD_SHUTDOWN_DEFECT,
        )
        process, parent_write, pid, _pgid, port, lock_path, _state = (
            self.spawn_entrypoint_service(blocked=False, tools=tools)
        )
        self._assert_listener_accepts(port)
        self.assertFalse(_lock_released(lock_path))
        os.kill(pid, signal.SIGTERM)
        time.sleep(1.0)
        self.assertIsNone(process.poll())
        self.assertFalse(_port_free(port))
        self.assertFalse(_lock_released(lock_path))
        with contextlib.suppress(OSError):
            os.close(parent_write)

    def test_baseline_actual_serving_loop_parent_loss_holds_lock_when_join_precedes_drain(
        self,
    ):
        tools = self._mutated_tools(
            "tools/service_entrypoint.py",
            PARENT_LOSS_DRAIN_ANCHOR,
            PARENT_LOSS_JOIN_FIRST_DEFECT,
        )
        process, parent_write, pid, pgid, port, lock_path, state_root = (
            self.spawn_entrypoint_service(blocked=True, resistant=True, tools=tools)
        )
        self._assert_listener_accepts(port)
        self.assertFalse(_lock_released(lock_path))
        _wait_until(
            lambda: (state_root / "tick-started").exists(),
            message="blocked worker entered a tick",
        )
        os.close(parent_write)
        time.sleep(1.0)
        self.assertIsNone(process.poll())
        self.assertFalse(_lock_released(lock_path))
        self.assertFalse(_group_absent(pgid))
        self.assertFalse((state_root / "tick-finished").exists())
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 0)

    def test_entrypoint_sigterm_stops_real_serving_loop(self):
        self._assert_entrypoint_signal_stops(signal.SIGTERM)

    def test_entrypoint_sighup_stops_real_serving_loop(self):
        self._assert_entrypoint_signal_stops(signal.SIGHUP)

    def _assert_entrypoint_signal_stops(self, signum):
        process, parent_write, pid, pgid, port, lock_path, _state = (
            self.spawn_entrypoint_service(blocked=False)
        )
        self._assert_listener_accepts(port)
        self.assertFalse(_lock_released(lock_path))
        os.kill(pid, signum)
        _wait_until(lambda: process.poll() is not None, message=f"entrypoint {signum} exit")
        _wait_until(lambda: _port_free(port), message="listener released after signal")
        _wait_until(lambda: _lock_released(lock_path), message="lock released after signal")
        self.assertIsNotNone(process.poll())
        with contextlib.suppress(OSError):
            os.close(parent_write)
            parent_write = -1

    def test_entrypoint_parent_loss_drains_blocked_worker_listener_and_lock(self):
        process, parent_write, pid, pgid, port, lock_path, state_root = (
            self.spawn_entrypoint_service(blocked=True, resistant=True)
        )
        self._assert_listener_accepts(port)
        self.assertFalse(_lock_released(lock_path))
        _wait_until(
            lambda: (state_root / "tick-started").exists(),
            message="blocked worker entered a tick",
        )
        os.close(parent_write)
        _wait_until(lambda: process.poll() is not None, timeout=4, message="entrypoint parent-loss exit")
        _wait_until(lambda: _group_absent(pgid), timeout=4, message="blocked session drain")
        self.assertTrue(_port_free(port))
        self.assertTrue(_lock_released(lock_path))
        self.assertFalse((state_root / "tick-finished").exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

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
            "tools/review_conductor_userland_launcher.py",
            "    stop_child(process, timeout=STANDALONE_CHILD_STOP_SECONDS)\n",
            "    stop_child(process)\n",
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_standalone_stop_helper_uses_qualified_budget_blocks_keeps_default",
        ),
        (
            "tools/service_entrypoint.py",
            "    if pgid != pid:\n        raise service.ServiceError(\"service is not the owned session leader\")\n",
            "    if False:\n        raise service.ServiceError(\"service is not the owned session leader\")\n",
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_drain_refuses_non_leader_without_signaling_guessed_pgid",
        ),
        (
            "tools/service_entrypoint.py",
            SERVE_THREAD_SHUTDOWN_ANCHOR,
            SERVE_THREAD_SHUTDOWN_DEFECT,
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_entrypoint_sigterm_stops_real_serving_loop",
        ),
        (
            "tools/service_entrypoint.py",
            PARENT_LOSS_DRAIN_ANCHOR,
            PARENT_LOSS_JOIN_FIRST_DEFECT,
            "test_runtime_lifecycle.RuntimeLifecycleTests.test_entrypoint_parent_loss_drains_blocked_worker_listener_and_lock",
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
