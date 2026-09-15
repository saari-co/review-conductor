"""Independent object-store proof; synthetic auth only, no live GitHub calls."""
import copy
import contextlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_conductor as core
import review_conductor_runtime as runtime
import review_conductor_userland as userland

REPOSITORY = "test-owner/private-fixture"
REMOTE = f"https://github.com/{REPOSITORY}.git"
SYNTHETIC = "synthetic-ephemeral-hydration-credential"


class ColdHydrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.remote = self.root / "remote"
        self.checkout = self.root / "checkout"
        for path in (self.remote, self.checkout):
            path.mkdir()
            self.git(path, "init", "--template=/dev/null")
            self.git(path, "config", "user.name", "Fixture")
            self.git(path, "config", "user.email", "fixture@example.invalid")
        self.base = self.commit(self.remote, "base")
        self.head = self.commit(self.remote, "head")
        self.git(self.remote, "update-ref", "refs/pull/4/head", self.head)
        self.original = self.commit(self.checkout, "independent")
        self.git(self.checkout, "remote", "add", "origin", REMOTE)
        self.config = {
            "paths": {"blocks_checkout": str(self.checkout)},
            "review_policy": {"enabled": True},
            "clawsweeper": {"workflow_id": "fixture.yml", "ref": "main"},
            "github_app": {
                "app_id": 123, "installation_id": 456,
                "repository": REPOSITORY, "api_base": "https://api.github.com",
                "permissions": copy.deepcopy(runtime.STANDALONE_APP_PERMISSIONS),
            },
        }
        self.action = {"repository": REPOSITORY, "pr_number": 4,
                       "head_sha": self.head, "base_sha": self.base,
                       "review_epoch": 0, "action_id": "fixture-action"}
        self.operations = []
        self.requests = []
        def transport(method, url, headers, body, timeout):
            self.requests.append((method, url, json.loads(body)))
            return 201, json.dumps({"token": SYNTHETIC,
                                   "expires_at": "2099-01-01T00:00:00Z"}).encode()
        self.client = runtime.GitHubAppClient(
            self.config, "synthetic-key", transport=transport, signer=lambda *_: "synthetic-jwt")
        self.client.set_authority_guard(self.guard)
        self.fetches = 0
        self.fetch_fd = None

    def guard(self, method, operation, authority):
        self.assertEqual(authority, runtime.tuple_authority(self.action))
        self.operations.append(operation)

    def git(self, directory, *args, check=True):
        return subprocess.run(userland.checkout_git_command(directory, *args),
                              env=userland.checkout_git_environment(), check=check,
                              capture_output=True, text=True)

    def commit(self, directory, name):
        (directory / name).write_text(name)
        self.git(directory, "add", name)
        self.git(directory, "commit", "-m", name)
        return self.git(directory, "rev-parse", "HEAD").stdout.strip()

    def runner(self, command, **kwargs):
        self.assertNotIn(SYNTHETIC, repr(command))
        self.assertNotIn(SYNTHETIC, repr(kwargs.get("env")))
        self.assertNotIn("GIT_TRACE", kwargs["env"])
        if "fetch" in command:
            self.fetches += 1
            self.assertNotEqual(command[command.index("-C") + 1], str(self.checkout))
            self.assertEqual(kwargs["env"]["HOME"], command[command.index("-C") + 1])
            self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", kwargs["env"])
            self.assertIn("http.followRedirects=false", command)
            self.assertIn("credential.helper=", command)
            helper = next(item.removeprefix("credential.helper=!") for item in command
                          if item.startswith("credential.helper=!"))
            self.fetch_fd = int(shlex.split(helper)[3])
            self.assertIn(self.fetch_fd, kwargs["pass_fds"])
            # Execute the actual fixed helper, using only synthetic bytes.
            response = subprocess.run(command[:command.index("fetch")] + ["credential", "fill"],
                input=f"protocol=https\nhost=github.com\npath={REPOSITORY}.git\n\n",
                text=True, capture_output=True, pass_fds=(self.fetch_fd,),
                env=kwargs["env"], check=True)
            self.assertEqual(response.stdout, f"protocol=https\nhost=github.com\npath={REPOSITORY}.git\nusername=x-access-token\npassword={SYNTHETIC}\n")
            self.assertEqual(response.stderr, "")
            # Only offline transport substitution: actual Git fetch, separate DB.
            command = list(command)
            command[command.index(REMOTE)] = str(self.remote)
            command[command.index("fetch"):command.index("fetch")] = ["-c", "protocol.file.allow=always"]
        return subprocess.run(command, **kwargs)

    def test_cold_independent_store_fetch_preserves_refs_and_uses_scoped_pipe(self):
        before = self.git(self.checkout, "for-each-ref").stdout
        self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)
        # Poison target config: authenticated staging must not read any of it.
        self.git(self.checkout, "config", "credential.helper", "!exit 99")
        self.git(self.checkout, "config", "http.proxy", "http://wrong.invalid")
        with patch.dict(os.environ, {"GIT_TRACE": "1", "GIT_CURL_VERBOSE": "1", "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-parent"}):
            result = userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=self.runner)
        self.assertEqual(result["result"], "fetched")
        self.assertEqual(self.fetches, 1)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.original)
        self.assertEqual(self.git(self.checkout, "for-each-ref").stdout, before)
        self.assertEqual(self.git(self.checkout, "status", "--porcelain").stdout, "")
        self.assertFalse((self.checkout / ".git/FETCH_HEAD").exists())
        self.git(self.checkout, "merge-base", "--is-ancestor", self.base, self.head)
        with self.assertRaises(OSError):
            os.fstat(self.fetch_fd)
        self.assertEqual(self.requests[0][2]["repositories"], ["private-fixture"])
        self.assertEqual(self.requests[0][2]["permissions"]["contents"], "read")
        warm = userland.hydrate_exact_pr_head(self.config, self.action,
            authority_client=self.client, runner=self.runner)
        self.assertEqual(warm["result"], "already_present")
        self.assertEqual(self.fetches, 1)

    def test_wrong_current_pr_ref_does_not_import(self):
        self.git(self.remote, "update-ref", "refs/pull/4/head", self.base)
        with self.assertRaisesRegex(userland.HydrationFailure, "fetched_tuple_mismatch"):
            userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=self.runner)
        self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_missing_auth_fails_without_transport(self):
        with self.assertRaisesRegex(userland.HydrationFailure, "service_auth_unavailable"):
            userland.hydrate_exact_pr_head(self.config, self.action, runner=self.runner)
        self.assertEqual(self.fetches, 0)

    def test_revocation_before_import_prevents_object_mutation(self):
        def guard(method, operation, authority):
            self.guard(method, operation, authority)
            if operation == "checkout-hydration:import":
                raise core.AuthorityDenied("synthetic revoked")
        self.client.set_authority_guard(guard)
        with self.assertRaises(core.AuthorityDenied):
            userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=self.runner)
        self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_wrong_repo_cannot_mint_a_credential(self):
        wrong = {**self.action, "repository": "other/repository"}
        with self.assertRaises(core.AuthorityDenied):
            with self.client.hydration_credentials(wrong):
                self.fail("wrong repository must not obtain helper")
        self.assertEqual(self.requests, [])

    def test_revocation_after_token_resolution_stops_fetch(self):
        def revoke(method, operation, authority):
            self.guard(method, operation, authority)
            if operation == "checkout-hydration:credential-ready":
                raise core.AuthorityDenied("synthetic revoked after token")
        self.client.set_authority_guard(revoke)
        with self.assertRaises(core.AuthorityDenied):
            userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=self.runner)
        self.assertEqual(self.fetches, 0)

    def test_missing_live_authority_cannot_obtain_credentials(self):
        self.client.set_authority_guard(None)
        with self.assertRaises(core.AuthorityDenied):
            with self.client.hydration_credentials(self.action):
                self.fail("missing admission must fail closed")
        self.assertEqual(self.requests, [])

    def test_generation_descriptor_survives_authenticated_git(self):
        read_fd, write_fd = os.pipe()
        try:
            def runner(command, **kwargs):
                self.assertIn(write_fd, kwargs["pass_fds"])
                self.assertEqual(kwargs["env"][core.GENERATION_FD_ENV], str(write_fd))
                return self.runner(command, **kwargs)
            with patch.dict(os.environ, {core.GENERATION_FD_ENV: str(write_fd)}):
                result = userland.hydrate_exact_pr_head(self.config, self.action,
                    authority_client=self.client, runner=runner)
            self.assertEqual(result["result"], "fetched")
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_wrong_protocol_or_path_never_reads_the_credential(self):
        with self.client.hydration_credentials(self.action) as (helper, fd):
            for protocol, path in (("http", REPOSITORY + ".git"),
                                   ("https", "other/private.git")):
                denied = subprocess.run(shlex.split(helper[1:]) + ["get"],
                    pass_fds=(fd,), text=True, capture_output=True,
                    input=f"protocol={protocol}\nhost=github.com\npath={path}\n\n")
                self.assertNotEqual(denied.returncode, 0)
                self.assertEqual((denied.stdout, denied.stderr), ("", ""))
            self.assertEqual(os.read(fd, 2048), SYNTHETIC.encode())

    def test_helper_rejects_wrong_host_without_consuming_pipe_and_is_one_use(self):
        with self.client.hydration_credentials(self.action) as (helper, fd):
            command = shlex.split(helper[1:]) + ["get"]
            def invoke(host):
                return subprocess.run(command, pass_fds=(fd,), text=True, capture_output=True,
                    input=f"protocol=https\nhost={host}\npath={REPOSITORY}.git\n\n")
            wrong = invoke("wrong.invalid")
            self.assertNotEqual(wrong.returncode, 0)
            self.assertEqual((wrong.stdout, wrong.stderr), ("", ""))
            self.assertEqual(invoke("github.com").returncode, 0)
            repeated = invoke("github.com")
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual((repeated.stdout, repeated.stderr), ("", ""))

    def test_failed_fetch_has_sanitized_class_and_closes_pipe(self):
        def fail(command, **kwargs):
            if "fetch" in command:
                self.fetch_fd = kwargs["pass_fds"][-1]
                return subprocess.CompletedProcess(command, 128)
            return self.runner(command, **kwargs)
        with self.assertRaisesRegex(userland.HydrationFailure, "authenticated_fetch_failed"):
            userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=fail)
        with self.assertRaises(OSError):
            os.fstat(self.fetch_fd)

    def test_revocation_after_credential_yield_prevents_fetch(self):
        original = self.client.hydration_credentials
        revoked = False
        @contextlib.contextmanager
        def credentials(authority):
            nonlocal revoked
            with original(authority) as pair:
                revoked = True
                yield pair
        def guard(method, operation, authority):
            if revoked:
                raise core.AuthorityDenied("synthetic revocation after yield")
        self.client.set_authority_guard(guard)
        with patch.object(self.client, "hydration_credentials", credentials):
            with self.assertRaises(core.AuthorityDenied):
                userland.hydrate_exact_pr_head(self.config, self.action,
                    authority_client=self.client, runner=self.runner)
        self.assertEqual(self.fetches, 0)

    def test_revocation_while_opening_pack_prevents_import(self):
        original = Path.open
        revoked = False
        def opening(path, mode="r", *args, **kwargs):
            nonlocal revoked
            result = original(path, mode, *args, **kwargs)
            if path.name == "objects.pack" and mode == "rb":
                revoked = True
            return result
        def guard(method, operation, authority):
            if revoked:
                raise core.AuthorityDenied("synthetic revocation at pack open")
        self.client.set_authority_guard(guard)
        with patch.object(Path, "open", opening):
            with self.assertRaises(core.AuthorityDenied):
                userland.hydrate_exact_pr_head(self.config, self.action,
                    authority_client=self.client, runner=self.runner)
        self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_pipe_resource_failures_are_closed_and_owned_fds_close(self):
        for operation in ("pipe", "fpathconf", "write"):
            with self.subTest(operation=operation):
                opened = []
                original_pipe = os.pipe
                def pipe():
                    descriptors = original_pipe()
                    opened.extend(descriptors)
                    return descriptors
                with contextlib.ExitStack() as stack:
                    if operation != "pipe":
                        stack.enter_context(patch.object(runtime.os, "pipe", pipe))
                    stack.enter_context(patch.object(runtime.os, operation,
                        side_effect=OSError("synthetic local resource detail")))
                    with self.assertRaisesRegex(core.ContractError, "hydration credential resource unavailable"):
                        with self.client.hydration_credentials(self.action):
                            self.fail("resource failure must not yield")
                for descriptor in opened:
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)

    def test_short_pipe_write_is_closed(self):
        with patch.object(runtime.os, "write", return_value=1):
            with self.assertRaisesRegex(core.ContractError, "pipe incomplete"):
                with self.client.hydration_credentials(self.action):
                    self.fail("short write must not yield")

    def test_temp_and_pack_resource_errors_are_closed(self):
        original_open = Path.open
        original_stat = Path.stat
        for operation in ("temporary", "mkdir", "pack_open", "pack_stat"):
            with self.subTest(operation=operation), contextlib.ExitStack() as stack:
                def opening(path, *args, **kwargs):
                    if path.name == "objects.pack":
                        raise OSError("synthetic resource detail")
                    return original_open(path, *args, **kwargs)
                def statting(path, *args, **kwargs):
                    if path.name == "objects.pack":
                        raise OSError("synthetic resource detail")
                    return original_stat(path, *args, **kwargs)
                if operation == "temporary":
                    stack.enter_context(patch.object(userland.tempfile, "TemporaryDirectory", side_effect=OSError("detail")))
                elif operation == "mkdir":
                    stack.enter_context(patch.object(Path, "mkdir", side_effect=OSError("detail")))
                elif operation == "pack_open":
                    stack.enter_context(patch.object(Path, "open", opening))
                else:
                    stack.enter_context(patch.object(Path, "stat", statting))
                with self.assertRaisesRegex(userland.HydrationFailure, "local_resource_unavailable"):
                    userland.hydrate_exact_pr_head(self.config, self.action,
                        authority_client=self.client, runner=self.runner)
                self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_stored_and_expanded_object_budgets_prevent_checkout_import(self):
        # Very compressible data: final pack bytes alone cannot bound this.
        (self.remote / "large").write_bytes(b"x" * 100_000)
        self.git(self.remote, "add", "large")
        self.git(self.remote, "commit", "-m", "large synthetic blob")
        self.head = self.git(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.action["head_sha"] = self.head
        self.git(self.remote, "update-ref", "refs/pull/4/head", self.head)
        for bound, value, error in (
            ("HYDRATION_PACK_BYTES", 32, "fetched_object_budget_exceeded"),
            ("HYDRATION_EXPANDED_BYTES", 1024, "expanded_object_budget_exceeded"),
            ("HYDRATION_OBJECT_LIMIT", 1, "expanded_object_budget_exceeded"),
        ):
            with self.subTest(bound=bound), patch.object(userland, bound, value, create=True):
                with self.assertRaisesRegex(userland.HydrationFailure, error):
                    userland.hydrate_exact_pr_head(self.config, self.action,
                        authority_client=self.client, runner=self.runner)
                self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_fetch_file_size_limit_applies_during_transport(self):
        (self.remote / "random").write_bytes(os.urandom(32_000))
        self.git(self.remote, "add", "random")
        self.git(self.remote, "commit", "-m", "incompressible synthetic blob")
        self.head = self.git(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.action["head_sha"] = self.head
        self.git(self.remote, "update-ref", "refs/pull/4/head", self.head)
        def bounded_runner(command, **kwargs):
            if "fetch" in command:
                self.assertIn("fetch.unpackLimit=0", command)
                helper = str(userland.TOOLS / "git_hydration_exec.py")
                self.assertEqual(command[:3], [sys.executable, "-I", helper])
                # Lower only this child limit, never the parent process.
                code = ("import runpy,sys; m=runpy.run_path(sys.argv[1]); "
                        "m['main'].__globals__['FILE_BYTES']=4096; "
                        "sys.argv=sys.argv[1:]; sys.exit(m['main']())")
                command = [sys.executable, "-I", "-c", code, *command[2:]]
            return self.runner(command, **kwargs)
        with self.assertRaisesRegex(userland.HydrationFailure, "authenticated_fetch_failed"):
            userland.hydrate_exact_pr_head(self.config, self.action,
                authority_client=self.client, runner=bounded_runner)
        self.assertEqual(self.fetches, 1)
        self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def pending_fixture(self, name):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_review_conductor_userland as fixture
        root = self.root / name
        root.mkdir()
        config = fixture.config_fixture(root)
        config["paths"]["blocks_checkout"] = str(self.checkout)
        self.git(self.checkout, "remote", "set-url", "origin", "https://github.com/dinkuskit/blocks.git")
        pr = fixture.pr_payload(4, self.head)
        pr["pull_request"]["base"]["sha"] = self.base
        fixture.ingress(config, "pull_request", "fixture-pr", pr)
        ci = fixture.ci_payload(4, 101, self.head)
        ci["workflow_run"]["pull_requests"][0]["base"]["sha"] = self.base
        fixture.ingress(config, "workflow_run", "fixture-ci", ci)
        client_config = copy.deepcopy(self.config)
        client_config["github_app"]["repository"] = "dinkuskit/blocks"
        return fixture, config, client_config

    def test_credential_guard_contract_errors_preserve_pending_action(self):
        for fence in ("credential", "credential-ready"):
            with self.subTest(fence=fence):
                fixture, config, client_config = self.pending_fixture(fence)
                client = runtime.GitHubAppClient(client_config, "synthetic-key",
                    signer=lambda *_: "synthetic-jwt")
                def guard(method, operation, authority):
                    if operation == "checkout-hydration:" + fence:
                        raise core.ContractError("synthetic stale binding")
                client.set_authority_guard(guard)
                with patch.object(client, "_installation_token", return_value=SYNTHETIC) as token:
                    with self.assertRaises(core.AuthorityDenied):
                        userland.hydrate_pending_openclaw_heads(
                            config, authority_client=client, dry_run=False)
                    self.assertEqual(token.call_count, int(fence == "credential-ready"))
                saved = fixture.action(config, 4, "openclaw.enqueue")
                self.assertEqual((saved["status"], saved["attempts"], saved["last_error"]),
                                 ("pending", 0, None))
                self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_transient_installation_errors_preserve_pending_action_across_ticks(self):
        for failure in (429, 503, "transport"):
            with self.subTest(failure=failure):
                fixture, config, client_config = self.pending_fixture(str(failure))
                calls = []
                def transport(*args):
                    calls.append(args[0])
                    if failure == "transport":
                        raise runtime.GitHubTransientError("synthetic transport timeout")
                    return failure, b"{}"
                client = runtime.GitHubAppClient(client_config, "synthetic-key",
                    transport=transport, signer=lambda *_: "synthetic-jwt")
                client.set_authority_guard(lambda *_: None)
                # Two explicit normal tick attempts: no inline retry loop.
                for attempt in range(2):
                    with self.assertRaises(runtime.GitHubTransientError):
                        userland.hydrate_pending_openclaw_heads(
                            config, authority_client=client, dry_run=False)
                    saved = fixture.action(config, 4, "openclaw.enqueue")
                    self.assertEqual((saved["status"], saved["attempts"], saved["last_error"]),
                                     ("pending", 0, None))
                    self.assertEqual(len(calls), attempt + 1)
                self.assertNotEqual(self.git(self.checkout, "cat-file", "-e", self.head, check=False).returncode, 0)

    def test_pending_failures_persist_once_and_revocation_stays_pending(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_review_conductor_userland as fixture
        for case in ("missing", "legacy", "fetch", "resource", "revoked"):
            with self.subTest(case=case):
                root = self.root / case
                root.mkdir()
                config = fixture.config_fixture(root)
                config["paths"]["blocks_checkout"] = str(self.checkout)
                self.git(self.checkout, "remote", "set-url", "origin", "https://github.com/dinkuskit/blocks.git")
                pr = fixture.pr_payload(4, self.head)
                pr["pull_request"]["base"]["sha"] = self.base
                fixture.ingress(config, "pull_request", "fixture-pr", pr)
                ci = fixture.ci_payload(4, 101, self.head)
                ci["workflow_run"]["pull_requests"][0]["base"]["sha"] = self.base
                fixture.ingress(config, "workflow_run", "fixture-ci", ci)
                legacy = runtime.GitHubAppClient(config, "synthetic-key")
                client = legacy if case == "legacy" else None
                original_hydrate = userland.hydrate_exact_pr_head
                def hydrating(cfg, action, **kwargs):
                    if case == "fetch":
                        raise userland.HydrationFailure("authenticated_fetch_failed")
                    if case == "resource":
                        raise userland.HydrationFailure("local_resource_unavailable")
                    if case == "revoked":
                        raise core.AuthorityDenied("synthetic revoked")
                    return original_hydrate(cfg, action, runner=self.runner, **kwargs)
                with patch.object(userland, "hydrate_exact_pr_head", hydrating):
                    if case == "revoked":
                        with self.assertRaises(core.AuthorityDenied):
                            userland.hydrate_pending_openclaw_heads(config, authority_client=client, dry_run=False)
                    else:
                        result = userland.hydrate_pending_openclaw_heads(config, authority_client=client, dry_run=False)
                        self.assertEqual(result[0]["result"], "failed")
                        self.assertEqual(userland.hydrate_pending_openclaw_heads(config, authority_client=client, dry_run=False), [])
                saved = fixture.action(config, 4, "openclaw.enqueue")
                self.assertEqual(saved["status"], "pending" if case == "revoked" else "failed")
                self.assertEqual(saved["attempts"], 0 if case == "revoked" else 1)
                if case != "revoked":
                    reason = {"missing": "service_auth_unavailable", "legacy": "checkout_validation_failed",
                              "fetch": "authenticated_fetch_failed", "resource": "local_resource_unavailable"}[case]
                    self.assertEqual(saved["last_error"], f"exact PR head hydration failed ({reason})")


if __name__ == "__main__":
    unittest.main()
