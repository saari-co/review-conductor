"""Synthetic-only descriptor transport, watchdog and cleanup regression contracts."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import review_conductor_userland_launcher as launcher
import test_review_conductor_userland as legacy


class LauncherTransportTests(unittest.TestCase):
    def assert_closed(self, descriptors):
        for fd in descriptors:
            with self.assertRaises(OSError) as error:
                os.fstat(fd)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def test_maximum_payload_before_any_reader_has_bounded_completion(self):
        # Watchdog isolates the old pre-reader pipe write, which would deadlock.
        program = '''
import hashlib, os, stat, sys
sys.path.insert(0, sys.argv[1])
import review_conductor_userland_launcher as launcher
value = b"synthetic-only-" * 74900
value = (value + b"x" * (1024 * 1024))[:1024 * 1024]
fd = launcher.credential_descriptor(value)
try:
    metadata = os.fstat(fd)
    assert stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 0
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not os.get_inheritable(fd)
    with os.fdopen(os.dup(fd), "rb") as reader:
        assert reader.read() == value
finally:
    os.close(fd)
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT / 'tools')],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')

    def test_shape_rejection_creates_no_resource(self):
        with patch.object(launcher.tempfile, 'TemporaryFile') as factory:
            for value in (b'', b'x' * (1024 * 1024 + 1), 'not bytes'):
                with self.assertRaises(launcher.LauncherError):
                    launcher.credential_descriptor(value)
            factory.assert_not_called()

    def test_named_storage_rejected_before_secret_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'empty-synthetic-fixture'
            stream = path.open('w+b')
            with patch.object(launcher.tempfile, 'TemporaryFile', return_value=stream):
                with self.assertRaises(launcher.LauncherError):
                    launcher.credential_descriptor(b'synthetic-do-not-write')
            self.assertTrue(stream.closed)
            self.assertEqual(path.read_bytes(), b'')

    def test_creation_and_io_failures_close_resources_without_value_output(self):
        value = b'synthetic-private-value'
        with patch.object(launcher.tempfile, 'TemporaryFile', side_effect=OSError(value.decode())):
            with self.assertRaises(launcher.LauncherError) as error:
                launcher.credential_descriptor(value)
            self.assertNotIn(value.decode(), str(error.exception))
        for operation in ('write', 'flush', 'seek', 'dup'):
            with self.subTest(operation=operation):
                stream = tempfile.TemporaryFile(mode='w+b')
                fd = stream.fileno()
                # Delegate actual file behavior except the injected failure.
                from unittest.mock import MagicMock
                wrapper = MagicMock(wraps=stream)
                wrapper.__enter__.return_value = wrapper
                wrapper.__exit__.side_effect = lambda *args: stream.close()
                with patch.object(launcher.tempfile, 'TemporaryFile', return_value=wrapper):
                    if operation == 'dup':
                        with patch.object(launcher.os, 'dup', side_effect=OSError('synthetic failure')):
                            with self.assertRaises(launcher.LauncherError):
                                launcher.credential_descriptor(value)
                    else:
                        getattr(wrapper, operation).side_effect = OSError('synthetic failure')
                        with self.assertRaises(launcher.LauncherError):
                            launcher.credential_descriptor(value)
                self.assertTrue(stream.closed)
                self.assert_closed([fd])
        stream = tempfile.TemporaryFile(mode='w+b')
        from unittest.mock import MagicMock
        wrapper = MagicMock(wraps=stream)
        wrapper.__enter__.return_value = wrapper
        wrapper.__exit__.side_effect = lambda *args: stream.close()
        wrapper.write.return_value = 1
        with patch.object(launcher.tempfile, 'TemporaryFile', return_value=wrapper):
            with self.assertRaises(launcher.LauncherError):
                launcher.credential_descriptor(value)
        self.assertTrue(stream.closed)

    def run_start(self, popen, *, fail_prepare=None, fail_resolve=None):
        with tempfile.TemporaryDirectory() as temp:
            config = legacy.config_fixture(Path(temp))
            original = launcher.credential_descriptor
            descriptors = []
            def prepare(value):
                if fail_prepare == len(descriptors):
                    raise launcher.LauncherError('synthetic preparation failure')
                fd = original(value)
                descriptors.append(fd)
                return fd
            resolutions = 0
            def resolve(*args):
                nonlocal resolutions
                if fail_resolve == resolutions:
                    raise launcher.LauncherError('synthetic resolver failure')
                resolutions += 1
                return b'x' * (1024 * 1024)
            with patch.object(launcher, 'bootstrap_status', return_value={'result': 'ready'}), \
                 patch.object(launcher, 'resolve_runtime_value', side_effect=resolve), \
                 patch.object(launcher, 'credential_descriptor', side_effect=prepare):
                try:
                    return launcher.start(config, config_path=Path(temp) / 'config.json', popen=popen)
                finally:
                    self.assert_closed(descriptors)

    def test_real_child_reads_large_descriptors_and_dev_fd_without_value_in_args_env(self):
        calls = []
        def popen(command, **kwargs):
            fds = kwargs['pass_fds']
            self.assertEqual(len(fds), 2 if not calls else 1)
            self.assertNotIn('x' * 100, repr(command) + repr(kwargs['env']))
            self.assertFalse(any(name.startswith('OP_') for name in kwargs['env']))
            # Actual isolated Python child consumes inherited descriptors; second
            # uses /dev/fd exactly as the tunnel's --token-file transport does.
            program = '''
import hashlib, os, sys
for fd in sys.argv[1:]:
    with open('/dev/fd/' + fd, 'rb') as stream:
        data = stream.read()
    assert len(data) == 1024 * 1024
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(b'x' * len(data)).hexdigest()
'''
            result = subprocess.run([sys.executable, '-c', program, *map(str, fds)],
                                    pass_fds=fds, env=kwargs['env'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b'')
            process = legacy.FakeProcess(command, kwargs)
            calls.append(process)
            return process
        self.assertEqual(self.run_start(popen), 0)
        self.assertEqual(len(calls), 2)

    def test_partial_preparation_closes_fds_without_starting_children(self):
        for fail_at in (0, 1, 2):
            with self.subTest(fail_at=fail_at), patch('subprocess.Popen') as popen:
                with self.assertRaises(launcher.LauncherError):
                    self.run_start(popen, fail_prepare=fail_at)
                popen.assert_not_called()

    def test_partial_resolution_failure_closes_prepared_fds(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at), patch('subprocess.Popen') as popen:
                with self.assertRaises(launcher.LauncherError):
                    self.run_start(popen, fail_resolve=fail_at)
                popen.assert_not_called()

    def test_first_spawn_failure_closes_all_fds(self):
        with self.assertRaises(OSError):
            self.run_start(lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic failure')))

    def test_second_spawn_failure_kills_and_reaps_first_child(self):
        class StubbornChild(legacy.FakeProcess):
            returncode = None
            def __init__(self):
                self.returncode = None
                self.waits = 0
                self.terminated = False
            def wait(self, timeout):
                self.waits += 1
                if self.returncode is None:
                    raise subprocess.TimeoutExpired('synthetic child', timeout)
                return self.returncode
        child = StubbornChild()
        count = 0
        def popen(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('synthetic second spawn failure')
            return child
        with self.assertRaises(OSError):
            self.run_start(popen)
        self.assertTrue(child.terminated)
        self.assertEqual(child.returncode, -9)
        self.assertEqual(child.waits, 2)

    def test_legacy_start_stop_keeps_ten_second_default(self):
        class TimedChild(legacy.FakeProcess):
            def __init__(self):
                super().__init__(['synthetic'], {})
                self.timeouts = []

            def wait(self, timeout):
                self.timeouts.append(timeout)
                return self.returncode

        conductor = TimedChild()
        tunnel = TimedChild()
        calls = []

        def popen(*args, **kwargs):
            calls.append(1)
            return conductor if len(calls) == 1 else tunnel

        self.assertEqual(self.run_start(popen), 0)
        self.assertEqual(conductor.timeouts, [launcher.LEGACY_CHILD_STOP_SECONDS])
        self.assertEqual(tunnel.timeouts, [launcher.LEGACY_CHILD_STOP_SECONDS])


if __name__ == '__main__':
    unittest.main()
