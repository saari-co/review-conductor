#!/usr/bin/env python3
"""Shared current-user adapters for the exact-head Review Conductor.

The module owns authenticated ingress, bounded GitHub operations, durable
review-action dispatch, terminal bridge ingestion, and exact-head projection.
It has no merge endpoint or merge authority.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import contextlib
import datetime as dt
import hashlib
import inspect
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Callable


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402
import review_result_projection as result_projection  # noqa: E402


UTC = getattr(dt, "UTC", dt.timezone.utc)
OPENCLAW_ARTIFACT_SCHEMA = "smoky.review-conductor.openclaw-terminal.v1"
CLAWSWEEPER_ARTIFACT_SCHEMA = "smoky.review-conductor.clawsweeper-terminal.v1"
READY_LABEL = "status: 👀 ready for maintainer look"
CHECK_NAMES = ("OpenClaw Review Rail", "ClawSweeper Review Rail")
APP_PERMISSIONS = {
    "actions": "write",
    "checks": "write",
    "metadata": "read",
    "pull_requests": "write",
}
STANDALONE_APP_PERMISSIONS = {
    **APP_PERMISSIONS,
    "contents": "read",
}
DENIED_PERMISSIONS = {
    "administration",
    "contents",
    "deployments",
    "issues",
    "members",
    "merge_queue",
    "organization_administration",
    "secrets",
    "workflows",
}
STANDALONE_DENIED_PERMISSIONS = DENIED_PERMISSIONS - {"contents"}
APP_EVENTS = ["pull_request", "workflow_run", "issue_comment"]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
API_VERSION = "2022-11-28"
# GitHub statuses that describe a transient upstream condition rather than a
# rejected operation; callers may retry without changing the request.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_LIST_PAGES = 10
FIXED_GIT = Path("/usr/bin/git")



class RuntimeError(core.ContractError):
    """Activation adapter input, state, or policy is invalid."""


class GitHubApiError(RuntimeError):
    """A fixed GitHub API operation failed without exposing response material."""


class ArtifactRedirectDenied(GitHubApiError):
    """The signed redirect host is outside the fixed artifact allowlist.

    Admission-log collection treats this as no identity. Widening the
    allowlist for private runner logs requires a separate approval.
    """


class GitHubTransientError(GitHubApiError):
    """The transport or GitHub itself failed transiently; the same call may be retried."""


class RetryableIngestError(Exception):
    """A dependency failed transiently before any state was written.

    Deliberately not a ContractError: the delivery was neither malformed nor
    foreign, so the HTTP edge answers 503 and leaves the delivery redeliverable
    from GitHub's delivery log or API (GitHub does not retry automatically).
    """


def tuple_authority(record: Any) -> dict[str, Any]:
    """Return the canonical review tuple that owns a prospective side effect."""
    try:
        repository = record["repository"]
        pr_number = record["pr_number"]
        base_sha = record["base_sha"]
        head_sha = record["head_sha"]
        review_epoch = record["review_epoch"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("side-effect authority is missing exact tuple identity") from exc
    return {
        "repository": core.require_text(repository, "authority repository", 200),
        "pr_number": core.require_positive_int(pr_number, "authority PR number"),
        "base_sha": core.require_sha(base_sha, "authority base SHA"),
        "head_sha": core.require_sha(head_sha, "authority head SHA"),
        "review_epoch": require_review_epoch(review_epoch, "authority review epoch"),
    }


def guarded_client_kwargs(client: Any, record: Any) -> dict[str, Any]:
    """Pass tuple authority to clients that implement the live guard contract.

    Legacy and test-only adapters have no authority-guard installer and retain
    their historical signatures. A live service refuses such a client before a
    tick starts, so this compatibility path cannot weaken the standalone route.
    """
    if callable(getattr(client, "set_authority_guard", None)):
        return {"authority": tuple_authority(record)}
    return {}


def assert_authority(client: Any, operation: str, authority: Any | None = None) -> None:
    """Run the client's admission authority guard before a non-GitHub side effect.

    Worker phases that act outside the GitHub client (OpenClaw dispatch,
    notifications) call this so the same per-side-effect fence applies to them.
    Clients without a guard (legacy route, dry runs) are unaffected.
    """
    check = getattr(client, "assert_authority", None)
    if check is not None:
        try:
            if authority is not None and callable(
                getattr(client, "set_authority_guard", None)
            ):
                check(operation, tuple_authority(authority))
            else:
                check(operation)
        except core.AuthorityDenied:
            raise
        except Exception as exc:
            raise core.AuthorityDenied("authority revoked before side effect") from exc


def require_absolute_path(value: Any, label: str) -> Path:
    text = core.require_text(value, label, 900)
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{label} must be an absolute traversal-free path")
    return path


def require_review_epoch(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"{label} must be a non-negative integer")
    return value


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def sign_app_jwt(app_id: int, private_key_pem: str, now: int | None = None) -> str:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise RuntimeError("GitHub App JWT signer dependency is unavailable") from exc
    moment = int(time.time()) if now is None else now
    header = b64url(core.canonical_json({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64url(
        core.canonical_json({"iat": moment - 30, "exp": moment + 540, "iss": str(app_id)}).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("GitHub App private key shape is invalid") from exc
    return f"{header}.{claims}.{b64url(signature)}"


TransportResponse = tuple[int, bytes] | tuple[int, dict[str, str], bytes]
Transport = Callable[[str, str, dict[str, str], bytes | None, float], TransportResponse]
ArtifactTransport = Callable[[str, dict[str, str], float], tuple[int, bytes]]
ArtifactRequest = Callable[
    [str, dict[str, str], float], tuple[int, dict[str, str], bytes]
]
ARTIFACT_MAX_BYTES = 1024 * 1024
ARTIFACT_BLOB_HOST_RE = re.compile(
    r"^productionresultssa[0-9]+\.blob\.core\.windows\.net$", re.IGNORECASE
)
CLAWSWEEPER_ADMISSION_JOB_MARK = "Admit exact-tuple"
# Bytes above this bound are not an identity. A prefix parse would hide a
# conflicting tuple that begins later in the same admission log.
CLAWSWEEPER_DISPATCH_LOG_LIMIT = 64 * 1024
_DISPATCH_LOG_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DISPATCH_LOG_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+"
)
_DISPATCH_LOG_FIELD = re.compile(
    r"^(pr_number|expected_base_sha|expected_head_sha|review_epoch):\s*(\S+)\s*$",
    re.IGNORECASE,
)
_DISPATCH_LOG_FIELDS = frozenset(
    {"pr_number", "expected_base_sha", "expected_head_sha", "review_epoch"}
)


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                int(response.status),
                dict(response.headers.items()),
                response.read(1024 * 1024),
            )
    except urllib.error.HTTPError as exc:
        response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
        exc.close()
        return int(exc.code), response_headers, b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubTransientError("GitHub API transport failed") from exc


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: Any,
        _file_pointer: Any,
        _code: int,
        _message: str,
        _headers: Any,
        _new_url: str,
    ) -> None:
        return None


def urllib_request_without_redirects(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(ARTIFACT_MAX_BYTES + 1)
            return int(response.status), dict(response.headers.items()), raw
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_headers = dict(exc.headers.items())
        exc.close()
        return status, response_headers, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GitHubApiError("GitHub artifact transport failed") from None


def header_value(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    return next((value for key, value in headers.items() if key.lower() == target), None)


def validate_artifact_redirect(api_url: str, location: str) -> str:
    if not isinstance(location, str) or not 1 <= len(location) <= 4096:
        raise GitHubApiError("GitHub artifact redirect URL is invalid")
    try:
        api = urllib.parse.urlsplit(api_url)
        target = urllib.parse.urlsplit(location)
        api_port = api.port
        target_port = target.port
    except ValueError as exc:
        raise GitHubApiError("GitHub artifact redirect URL is invalid") from exc
    if (
        api.scheme != "https"
        or api.hostname != "api.github.com"
        or api.username is not None
        or api.password is not None
        or api_port not in {None, 443}
    ):
        raise GitHubApiError("GitHub artifact API origin is invalid")
    if (
        target.scheme != "https"
        or target.hostname is None
        or target.username is not None
        or target.password is not None
        or target_port not in {None, 443}
        or not target.path.startswith("/")
        or not target.query
        or target.fragment
    ):
        raise GitHubApiError("GitHub artifact redirect target is outside the fixed allowlist")
    if ARTIFACT_BLOB_HOST_RE.fullmatch(target.hostname) is None:
        raise ArtifactRedirectDenied(
            "GitHub artifact redirect target is outside the fixed allowlist"
        )
    return location


def urllib_artifact_transport(
    api_url: str,
    headers: dict[str, str],
    timeout: float,
    *,
    request_once: ArtifactRequest = urllib_request_without_redirects,
) -> tuple[int, bytes]:
    status, response_headers, _raw = request_once(api_url, headers, timeout)
    location = header_value(response_headers, "Location")
    if status != 302 or location is None:
        raise GitHubApiError("GitHub artifact download did not return one signed redirect")
    target = validate_artifact_redirect(api_url, location)
    storage_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "smoky-review-conductor/1",
    }
    final_status, _final_headers, raw = request_once(target, storage_headers, timeout)
    if final_status != 200:
        raise GitHubApiError("GitHub artifact signed download failed")
    if not raw or len(raw) > ARTIFACT_MAX_BYTES:
        raise GitHubApiError("ClawSweeper artifact download has an invalid bounded size")
    return final_status, raw


def parse_clawsweeper_dispatch_log(text: str) -> dict[str, Any] | None:
    """Return one consistent dispatch tuple echoed by the admission job.

    Repeated identical complete blocks are one identity. An incomplete block
    is not an identity by itself. A partial block that only repeats the
    complete tuple is ignored. A different value in any partial or complete
    block is not an identity.
    """
    if not isinstance(text, str):
        return None
    complete: list[tuple[tuple[str, Any], ...]] = []
    partials: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    def parse_field(key: str, value: str) -> Any:
        if key in {"pr_number", "review_epoch"}:
            if not re.fullmatch(r"[0-9]{1,10}", value):
                return None
            if key == "pr_number" and value == "0":
                return None
            return int(value)
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            return None
        return value

    def close_block() -> None:
        nonlocal current
        if not current:
            return
        if set(current) == _DISPATCH_LOG_FIELDS:
            complete.append(tuple(sorted(current.items())))
        else:
            partials.append(current)
        current = {}

    for raw_line in text.splitlines():
        line = _DISPATCH_LOG_ANSI.sub("", raw_line).strip()
        line = _DISPATCH_LOG_TIMESTAMP.sub("", line).strip()
        match = _DISPATCH_LOG_FIELD.fullmatch(line)
        if match is None:
            close_block()
            continue
        key = match.group(1).lower()
        parsed = parse_field(key, match.group(2))
        if parsed is None:
            return None
        prior = current.get(key)
        if prior is not None and prior != parsed:
            return None
        current[key] = parsed
        if set(current) == _DISPATCH_LOG_FIELDS:
            complete.append(tuple(sorted(current.items())))
            current = {}
    close_block()
    if len(set(complete)) != 1:
        return None
    identity = dict(complete[0])
    for partial in partials:
        for key, value in partial.items():
            if identity[key] != value:
                return None
    return identity


class GitHubAppClient:
    """One Blocks installation client with a mechanically closed API surface."""

    def __init__(
        self,
        config: dict[str, Any],
        private_key_pem: str,
        *,
        transport: Transport = urllib_transport,
        artifact_transport: ArtifactTransport | None = None,
        clock: Callable[[], float] = time.time,
        signer: Callable[[int, str, int], str] | None = None,
    ) -> None:
        core.require_enabled({"review_policy": config.get("review_policy", {})})
        # Snapshot the caller's map: the token request is derived from this object
        # later, so a caller mutating its own config after construction must not
        # be able to change the permissions the adapter validated here.
        app = copy.deepcopy(config["github_app"])
        # The adapter derives its token request from this map, so it must be exactly
        # the closed allowlist the profile kind authorizes: only generalized
        # (standalone) profiles may request Contents read for approved-policy
        # retrieval; a legacy profile is held to the legacy allowlist.
        expected_permissions = STANDALONE_APP_PERMISSIONS if config.get("review_policy") else APP_PERMISSIONS
        if app.get("permissions") != expected_permissions:
            raise GitHubApiError("GitHub App permissions are outside the profile's closed allowlist")
        if config.get("review_policy") and not config.get("clawsweeper"):
            raise RuntimeError("generalized profile requires its own ClawSweeper adapter")
        # Same for the ClawSweeper map: _allow() and dispatch derive the authorized
        # workflow endpoint from it.
        self._clawsweeper = copy.deepcopy(
            config.get("clawsweeper", {"workflow_id": "clawsweeper-native-canary.yml", "ref": "main"})
        )
        self._strict_adapter = bool(config.get("review_policy"))
        if app["app_id"] is None or app["installation_id"] is None:
            raise RuntimeError("GitHub App IDs are not configured")
        if not private_key_pem:
            raise RuntimeError("GitHub App private key is empty")
        self._app = app
        self._private_key = private_key_pem
        self._transport = transport
        if artifact_transport is not None:
            self._artifact_transport = artifact_transport
        elif transport is urllib_transport:
            self._artifact_transport = urllib_artifact_transport
        else:
            def transport_artifact(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
                response = transport("GET", url, headers, None, timeout)
                if len(response) == 2:
                    return response
                status, _response_headers, raw = response
                return status, raw

            self._artifact_transport = transport_artifact
        self._clock = clock
        self._signer = signer or sign_app_jwt
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = threading.Lock()
        self._authority_guard: Callable[[str, str, dict[str, Any] | None], None] | None = None

    def set_authority_guard(
        self,
        guard: Callable[[str, str, dict[str, Any] | None], None] | None,
    ) -> None:
        """Install a check that runs before every mutating GitHub call.

        The service uses it to re-validate current admission (registry, reviewer
        actors, profile digest) immediately before each side effect of a tick, so
        a revocation or profile edit after the tick's opening gate cannot leave
        the remainder of that tick dispatching or publishing.
        """
        self._authority_guard = guard

    def assert_authority(
        self, operation: str, authority: dict[str, Any] | None = None
    ) -> None:
        """Run the installed guard for a side effect that is not a GitHub call."""
        if self._authority_guard is not None:
            self._authority_guard("SIDE_EFFECT", operation, authority)

    @property
    def repository(self) -> str:
        return self._app["repository"]

    def _allow(self, method: str, path: str) -> str:
        repository = re.escape(self.repository)
        rules = (
            ("POST", rf"/app/installations/{self._app['installation_id']}/access_tokens", "installation-token"),
            ("POST", rf"/repos/{repository}/check-runs", "check-create"),
            ("PATCH", rf"/repos/{repository}/check-runs/[1-9][0-9]*", "check-update"),
            ("GET", rf"/repos/{repository}/check-runs/[1-9][0-9]*", "check-read"),
            # Approved-policy retrieval is a generalized-profile capability; the
            # legacy allowlist never reaches repository contents.
            *(
                (
                    (
                        "GET",
                        rf"/repos/{repository}/contents/\.review-conductor\.json\?ref=[0-9a-f]{{40}}",
                        "approved-policy-read",
                    ),
                )
                if self._strict_adapter
                else ()
            ),
            ("POST", rf"/repos/{repository}/issues/[1-9][0-9]*/labels", "label-add"),
            ("DELETE", rf"/repos/{repository}/issues/[1-9][0-9]*/labels/.+", "label-remove"),
            ("GET", rf"/repos/{repository}/issues/[1-9][0-9]*/labels(?:\?per_page=100(?:&page=[1-9][0-9]*)?)?", "label-list"),
            ("GET", rf"/repos/{repository}/issues/[1-9][0-9]*/comments(?:\?per_page=100(?:&page=[1-9][0-9]*)?)?", "comment-list"),
            ("POST", rf"/repos/{repository}/issues/[1-9][0-9]*/comments", "comment-create"),
            ("PATCH", rf"/repos/{repository}/issues/comments/[1-9][0-9]*", "comment-update"),
            (
                "POST",
                rf"/repos/{repository}/actions/workflows/{re.escape(self._clawsweeper['workflow_id'])}/dispatches",
                "clawsweeper-dispatch",
            ),
            (
                "GET",
                rf"/repos/{repository}/actions/runs/[1-9][0-9]*/artifacts",
                "clawsweeper-artifact-list",
            ),
            (
                "GET",
                rf"/repos/{repository}/actions/artifacts/[1-9][0-9]*/zip",
                "clawsweeper-artifact-download",
            ),
            (
                "GET",
                rf"/repos/{repository}/actions/runs/[1-9][0-9]*/jobs(?:\?per_page=100(?:&page=[1-9][0-9]*)?)?",
                "clawsweeper-job-list",
            ),
            (
                "GET",
                rf"/repos/{repository}/actions/jobs/[1-9][0-9]*/logs",
                "clawsweeper-admission-log",
            ),
        )
        for allowed_method, pattern, operation in rules:
            if method == allowed_method and re.fullmatch(pattern, path):
                return operation
        raise GitHubApiError("GitHub API operation is outside the fixed allowlist")

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        expected: set[int],
        app_jwt: str | None = None,
        authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _operation, raw = self._call_raw(
            method,
            path,
            payload,
            expected=expected,
            app_jwt=app_jwt,
            authority=authority,
        )
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubApiError("GitHub API returned malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise GitHubApiError("GitHub API returned an unexpected payload shape")
        return decoded

    def _call_raw(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        expected: set[int],
        app_jwt: str | None = None,
        authority: dict[str, Any] | None = None,
    ) -> tuple[str, bytes]:
        operation, _response_headers, raw = self._call_raw_response(
            method,
            path,
            payload,
            expected=expected,
            app_jwt=app_jwt,
            authority=authority,
        )
        return operation, raw

    def _call_raw_response(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        expected: set[int],
        app_jwt: str | None = None,
        authority: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        operation = self._allow(method, path)
        token = app_jwt if operation == "installation-token" else self._installation_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "smoky-review-conductor/1",
        }
        body = None
        if payload is not None:
            body = core.canonical_json(payload).encode()
            headers["Content-Type"] = "application/json"
        # Fence immediately before the mutating request itself: token minting above
        # is a separate call, and authority may have been revoked while it ran.
        if (
            self._authority_guard is not None
            and method != "GET"
            and operation != "installation-token"
        ):
            try:
                self._authority_guard(method, path, authority)
            except core.AuthorityDenied:
                raise
            except Exception as exc:
                raise core.AuthorityDenied(
                    "authority revoked before GitHub request"
                ) from exc
        response = self._transport(
            method, self._app["api_base"] + path, headers, body, 15.0
        )
        if len(response) == 2:
            status, raw = response
            response_headers: dict[str, str] = {}
        else:
            status, response_headers, raw = response
        if status not in expected:
            rate_limited = status == 403 and (
                header_value(response_headers, "Retry-After") is not None
                or header_value(response_headers, "X-RateLimit-Remaining") == "0"
            )
            if status in TRANSIENT_STATUSES or rate_limited:
                raise GitHubTransientError(
                    f"allowlisted GitHub API operation failed transiently ({operation})"
                )
            raise GitHubApiError(f"allowlisted GitHub API operation failed ({operation})")
        if operation == "label-remove" and status in {204, 404}:
            # Single-label removal is idempotent when already absent. Normalize
            # only these statuses, never an object/empty successful HTTP 200.
            raw = b"[]"
        return operation, response_headers, raw

    def _installation_token(self) -> str:
        if self._token is not None and self._clock() < self._token_expires - 60:
            return self._token
        # A shared service client may be used by ingress and the worker. Only one
        # thread may mint/replace an installation token; late contenders re-use
        # the first validated result instead of racing duplicate token requests.
        with self._token_lock:
            if self._token is not None and self._clock() < self._token_expires - 60:
                return self._token
            app_jwt = self._signer(
                self._app["app_id"], self._private_key, int(self._clock())
            )
            response = self._call(
                "POST",
                f"/app/installations/{self._app['installation_id']}/access_tokens",
                {
                    "repositories": [self.repository.split("/", 1)[1]],
                    "permissions": {
                        name: level
                        for name, level in self._app["permissions"].items()
                        if name != "metadata"
                    },
                },
                expected={201},
                app_jwt=app_jwt,
            )
            token = response.get("token")
            expires_at = response.get("expires_at")
            if not isinstance(token, str) or not token or not isinstance(expires_at, str):
                raise GitHubApiError("installation token response is incomplete")
            try:
                expiry = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
            except ValueError as exc:
                raise GitHubApiError("installation token expiry is invalid") from exc
            self._token = token
            self._token_expires = expiry
            return token

    @contextlib.contextmanager
    def hydration_credentials(self, authority: dict[str, Any]):
        """One-use pipe to the fixed Git helper; values never enter argv/env/files.

        Only the admitted standalone service has Contents-read authority. The
        helper is service source, never a helper selected by the target checkout.
        """
        authority = tuple_authority(authority)
        if authority["repository"] != self.repository:
            raise core.AuthorityDenied("authenticated hydration authority unavailable")
        if not self._strict_adapter or self._app["api_base"] != "https://api.github.com":
            raise core.ContractError("authenticated hydration capability unavailable")
        if self._authority_guard is None:
            raise core.AuthorityDenied("authenticated hydration authority unavailable")
        assert_authority(self, "checkout-hydration:credential", authority)
        token = self._installation_token()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,2048}", token):
            raise GitHubApiError("hydration credential has invalid bounded shape")
        assert_authority(self, "checkout-hydration:credential-ready", authority)
        read_fd = write_fd = -1
        try:
            read_fd, write_fd = os.pipe()
            # Below POSIX PIPE_BUF: no writer thread, tempfile, or environment.
            encoded = token.encode("ascii")
            if len(encoded) > os.fpathconf(write_fd, "PC_PIPE_BUF"):
                raise GitHubApiError("hydration credential exceeds atomic pipe bound")
            if os.write(write_fd, encoded) != len(encoded):
                raise core.ContractError("hydration credential pipe incomplete")
            os.close(write_fd)
            write_fd = -1
            helper = "!" + " ".join(shlex.quote(item) for item in (
                sys.executable, "-I", str(TOOLS / "git_hydration_credential.py"),
                str(read_fd), self.repository,
            ))
            yield helper, read_fd
        except OSError:
            raise core.ContractError("hydration credential resource unavailable") from None
        finally:
            for descriptor in (read_fd, write_fd):
                if descriptor != -1:
                    try:
                        os.close(descriptor)
                    except OSError:
                        # Preserve any admission denial already unwinding.
                        pass

    def read_policy(self, repository: str, commit: str) -> bytes:
        if repository != self.repository:
            raise GitHubApiError("approved policy repository is outside the installation")
        core.require_sha(commit, "approved policy commit")
        _operation, raw = self._call_raw(
            "GET",
            f"/repos/{self.repository}/contents/.review-conductor.json?ref={commit}",
            None,
            expected={200},
        )
        try:
            response = core.require_object(json.loads(raw), "approved policy response")
            if response.get("encoding") != "base64" or not isinstance(response.get("content"), str):
                raise GitHubApiError("approved policy response is not base64 file content")
            encoded = response["content"]
            if re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", encoded) is None:
                raise GitHubApiError("approved policy response is not canonical base64")
            content = base64.b64decode(
                encoded.replace("\r", "").replace("\n", ""), validate=True
            )
        except (binascii.Error, ValueError, UnicodeError, json.JSONDecodeError, core.ContractError) as exc:
            raise GitHubApiError("approved policy response is malformed") from exc
        if not content or len(content) > 16384:
            raise GitHubApiError("approved policy content has an invalid bounded size")
        return content

    def create_check(
        self,
        name: str,
        head_sha: str,
        external_id: str,
        state: str,
        *,
        authority: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
    ) -> int:
        if name not in CHECK_NAMES:
            raise GitHubApiError("check name is outside the fixed allowlist")
        payload = check_payload(name, head_sha, external_id, state, report=report)
        response = self._call(
            "POST",
            f"/repos/{self.repository}/check-runs",
            payload,
            expected={201},
            authority=authority,
        )
        return core.require_positive_int(response.get("id"), "GitHub check run id")

    def needs_skipped_check_replacement(
        self, check_id: int, name: str, head_sha: str, external_id: str, *,
        authority: dict[str, Any] | None = None,
    ) -> bool:
        """Read-only migration probe; never reset a terminal conclusion."""
        if name not in CHECK_NAMES:
            raise GitHubApiError("check name is outside the fixed allowlist")
        observed = self._call(
            "GET", f"/repos/{self.repository}/check-runs/{check_id}", None,
            expected={200}, authority=authority,
        )
        if (
            type(observed.get("id")) is not int or observed["id"] != check_id
            or observed.get("name") != name or observed.get("head_sha") != head_sha
            or observed.get("external_id") != external_id
            or not isinstance(observed.get("app"), dict)
            or type(observed["app"].get("id")) is not int
            or observed["app"]["id"] != self._app["app_id"]
        ):
            raise GitHubApiError("migration check does not match the owned exact check")
        if observed.get("conclusion") == "skipped" and observed.get("status") in {
            "completed", "queued", "in_progress",
        }:
            # A prior partial PATCH may already have changed status while leaving
            # the terminal skip. It needs the same replacement, not another PATCH.
            return True
        if observed.get("status") == "completed" or observed.get("conclusion") is not None:
            raise GitHubApiError("only a terminal skipped prerequisite check may be replaced")
        return False

    def update_check(
        self,
        check_id: int,
        name: str,
        head_sha: str,
        external_id: str,
        state: str,
        *,
        authority: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        if name not in CHECK_NAMES:
            raise GitHubApiError("check name is outside the fixed allowlist")
        payload = check_payload(name, head_sha, external_id, state, report=report)
        if name == "OpenClaw Review Rail" and report and "original_report" in report:
            # Use GitHub's observed page for this very check, never an invented
            # Actions run or caller-supplied report URL. PATCH remains idempotent.
            observed = self._call(
                "GET", f"/repos/{self.repository}/check-runs/{check_id}", None,
                expected={200}, authority=authority,
            )
            url = observed.get("html_url")
            if (
                type(observed.get("id")) is not int or observed["id"] != check_id
                or observed.get("name") != name or observed.get("head_sha") != head_sha
                or observed.get("external_id") != external_id
                or not isinstance(observed.get("app"), dict)
                or type(observed["app"].get("id")) is not int
                or observed["app"]["id"] != self._app["app_id"]
                or url != f"https://github.com/{self.repository}/runs/{check_id}"
            ):
                raise GitHubApiError("original report check page does not match the owned exact check")
            payload["details_url"] = url
        observed = self._call(
            "PATCH", f"/repos/{self.repository}/check-runs/{check_id}", payload,
            expected={200}, authority=authority,
        )
        if state in {"queued", "in_progress"} and (
            observed.get("status") != state or "conclusion" not in observed
            or observed["conclusion"] is not None
        ):
            # An HTTP 200 is not proof that a previously terminal check resumed.
            # Do not invent null-conclusion support or silently retain a skip.
            raise GitHubApiError("GitHub did not confirm the requested nonterminal check state; operator recovery required")

    def _call_list(
        self,
        method: str,
        path: str,
        *,
        authority: dict[str, Any] | None = None,
    ) -> list[Any]:
        items: list[Any] = []
        current = path
        seen: set[str] = set()
        for _page in range(MAX_LIST_PAGES + 1):
            if current in seen:
                raise GitHubApiError("GitHub list pagination looped")
            seen.add(current)
            operation, response_headers, raw = self._call_raw_response(
                method, current, None, expected={200}, authority=authority
            )
            del operation
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GitHubApiError("GitHub API returned malformed JSON") from exc
            if not isinstance(decoded, list):
                raise GitHubApiError("GitHub API returned an unexpected payload shape")
            items.extend(decoded)
            nxt = self._next_list_path(current, response_headers)
            if nxt is None:
                return items
            if _page == MAX_LIST_PAGES - 1:
                raise GitHubApiError("GitHub list pagination exceeded the bounded page count")
            current = nxt
        raise GitHubApiError("GitHub list pagination exceeded the bounded page count")

    def _next_list_path(self, current_path: str, response_headers: dict[str, str]) -> str | None:
        link = header_value(response_headers, "Link")
        if not link:
            return None
        next_url = None
        for part in link.split(","):
            piece = part.strip()
            if 'rel="next"' not in piece:
                continue
            match = re.fullmatch(r"<([^>]+)>;\s*rel=\"next\"", piece)
            if match is None:
                raise GitHubApiError("GitHub list pagination Link header is malformed")
            if next_url is not None:
                raise GitHubApiError("GitHub list pagination Link header is ambiguous")
            next_url = match.group(1)
        if next_url is None:
            return None
        try:
            parsed = urllib.parse.urlsplit(next_url)
        except ValueError as exc:
            raise GitHubApiError("GitHub list pagination URL is invalid") from exc
        api = urllib.parse.urlsplit(self._app["api_base"])
        if (
            parsed.scheme != api.scheme
            or parsed.netloc != api.netloc
            or parsed.fragment
            or not parsed.path
        ):
            raise GitHubApiError("GitHub list pagination left the allowlisted API origin")
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        self._allow("GET", path)
        if path == current_path:
            raise GitHubApiError("GitHub list pagination looped")
        return path

    def list_issue_comments(
        self, pr_number: int, *, authority: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        items = self._call_list(
            "GET",
            f"/repos/{self.repository}/issues/{pr_number}/comments?per_page=100",
            authority=authority,
        )
        comments: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise GitHubApiError("issue comment list is malformed")
            comments.append(item)
        return comments

    def create_issue_comment(
        self,
        pr_number: int,
        body: str,
        *,
        authority: dict[str, Any] | None = None,
    ) -> int:
        response = self._call(
            "POST",
            f"/repos/{self.repository}/issues/{pr_number}/comments",
            {"body": body},
            expected={201},
            authority=authority,
        )
        return core.require_positive_int(response.get("id"), "GitHub comment id")

    def update_issue_comment(
        self,
        comment_id: int,
        body: str,
        *,
        authority: dict[str, Any] | None = None,
    ) -> None:
        self._call(
            "PATCH",
            f"/repos/{self.repository}/issues/comments/{comment_id}",
            {"body": body},
            expected={200},
            authority=authority,
        )

    def list_issue_labels(
        self, pr_number: int, *, authority: dict[str, Any] | None = None
    ) -> list[str]:
        items = self._call_list(
            "GET",
            f"/repos/{self.repository}/issues/{pr_number}/labels?per_page=100",
            authority=authority,
        )
        names: list[str] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
            elif isinstance(item, str):
                names.append(item)
            else:
                raise GitHubApiError("issue label list is malformed")
        return names

    def _mutate_label(
        self, method: str, path: str, payload: dict[str, Any] | None, *,
        expected: set[int], authority: dict[str, Any] | None = None,
    ) -> list[str]:
        _operation, raw = self._call_raw(
            method, path, payload, expected=expected, authority=authority,
        )
        try:
            labels = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise GitHubApiError("GitHub label mutation returned malformed JSON") from exc
        if not isinstance(labels, list) or any(
            not isinstance(label, dict) or not isinstance(label.get("name"), str)
            for label in labels
        ):
            raise GitHubApiError("GitHub label mutation returned an unexpected payload shape")
        return [label["name"] for label in labels]

    def add_owned_label(
        self,
        pr_number: int,
        name: str,
        *,
        authority: dict[str, Any] | None = None,
    ) -> None:
        if name not in result_projection.PUBLICATION_LABELS:
            raise GitHubApiError("label is outside the Conductor-owned vocabulary")
        observed = self._mutate_label(
            "POST",
            f"/repos/{self.repository}/issues/{pr_number}/labels",
            {"labels": [name]},
            expected={200},
            authority=authority,
        )
        if name in result_projection.native.NATIVE_LABELS and name not in observed:
            raise GitHubApiError("native label add was not confirmed; repository label may be unavailable")

    def remove_owned_label(
        self,
        pr_number: int,
        name: str,
        *,
        authority: dict[str, Any] | None = None,
    ) -> None:
        if name not in result_projection.PUBLICATION_LABELS:
            raise GitHubApiError("label is outside the Conductor-owned vocabulary")
        encoded = urllib.parse.quote(name, safe="")
        observed = self._mutate_label(
            "DELETE",
            f"/repos/{self.repository}/issues/{pr_number}/labels/{encoded}",
            None,
            expected={200, 204, 404},
            authority=authority,
        )
        if name in result_projection.native.NATIVE_LABELS and name in observed:
            raise GitHubApiError("native label removal was not confirmed")

    def add_ready_label(
        self, pr_number: int, *, authority: dict[str, Any] | None = None
    ) -> None:
        self._mutate_label(
            "POST",
            f"/repos/{self.repository}/issues/{pr_number}/labels",
            {"labels": [READY_LABEL]},
            expected={200},
            authority=authority,
        )

    def remove_ready_label(
        self, pr_number: int, *, authority: dict[str, Any] | None = None
    ) -> None:
        encoded = urllib.parse.quote(READY_LABEL, safe="")
        self._mutate_label(
            "DELETE",
            f"/repos/{self.repository}/issues/{pr_number}/labels/{encoded}",
            None,
            expected={200, 204, 404},
            authority=authority,
        )

    def dispatch_clawsweeper(
        self, *, pr_number: int, base_sha: str, head_sha: str, publish: bool,
        review_epoch: int | None = None,
        authority: dict[str, Any] | None = None,
    ) -> int | None:
        core.require_sha(base_sha, "ClawSweeper dispatch base_sha")
        core.require_sha(head_sha, "ClawSweeper dispatch head_sha")
        if publish is not True:
            raise GitHubApiError("ClawSweeper dispatch publish must remain true")
        extra = {}
        if self._strict_adapter:
            require_review_epoch(review_epoch, "dispatch review epoch")
            extra = {"review_epoch": str(review_epoch), "repository": self.repository, "review_scope": "comprehensive"}
        # Changelog 2026-02-19: return_run_details true yields HTTP 200 with
        # workflow_run_id. Omitting the flag yields 204. A 204 host still
        # dispatches; the collector then has no stored run id for that attempt.
        response = self._call(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{self._clawsweeper['workflow_id']}/dispatches",
            {
                "ref": self._clawsweeper["ref"],
                "inputs": {
                    **extra,
                    "pr_number": str(pr_number),
                    "expected_base_sha": base_sha,
                    "expected_head_sha": head_sha,
                    "publish": "true",
                },
                "return_run_details": True,
            },
            expected={200, 204},
            authority=authority,
        )
        raw_run_id = response.get("workflow_run_id")
        if raw_run_id is None:
            return None
        return core.require_positive_int(raw_run_id, "dispatched ClawSweeper workflow run id")

    def list_run_artifacts(self, workflow_run_id: int) -> list[dict[str, Any]]:
        run_id = core.require_positive_int(workflow_run_id, "ClawSweeper workflow run id")
        response = self._call(
            "GET",
            f"/repos/{self.repository}/actions/runs/{run_id}/artifacts",
            None,
            expected={200},
        )
        artifacts = response.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) > 100:
            raise GitHubApiError("ClawSweeper artifact listing has an invalid shape")
        return [core.require_object(item, "ClawSweeper artifact") for item in artifacts]

    def download_artifact(self, artifact_id: int) -> bytes:
        identifier = core.require_positive_int(artifact_id, "ClawSweeper artifact id")
        path = f"/repos/{self.repository}/actions/artifacts/{identifier}/zip"
        operation = self._allow("GET", path)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._installation_token()}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "smoky-review-conductor/1",
        }
        status, raw = self._artifact_transport(
            self._app["api_base"] + path, headers, 15.0
        )
        if status != 200:
            raise GitHubApiError(f"allowlisted GitHub API operation failed ({operation})")
        if not raw or len(raw) > 1024 * 1024:
            raise GitHubApiError("ClawSweeper artifact download has an invalid bounded size")
        return raw

    def _list_clawsweeper_run_jobs(self, run_id: int) -> list[Any]:
        """Read every jobs page, or fail closed before calling the list complete.

        The first page is not the whole run. ``per_page=100`` and allowlisted
        ``Link`` rel=next pages stay inside ``MAX_LIST_PAGES``. A loop, a
        disallowed next URL, a full page with no next link and no matching
        ``total_count``, or a page past the cap is an incomplete listing.
        """
        current = f"/repos/{self.repository}/actions/runs/{run_id}/jobs?per_page=100"
        jobs: list[Any] = []
        seen: set[str] = set()
        reported_total: int | None = None
        for page_index in range(MAX_LIST_PAGES + 1):
            if current in seen:
                raise GitHubApiError("GitHub list pagination looped")
            seen.add(current)
            _operation, response_headers, raw = self._call_raw_response(
                "GET", current, None, expected={200}
            )
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GitHubApiError("GitHub API returned malformed JSON") from exc
            if not isinstance(decoded, dict):
                raise GitHubApiError("ClawSweeper jobs listing has an invalid shape")
            if "total_count" in decoded:
                total = decoded["total_count"]
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise GitHubApiError("ClawSweeper jobs listing has an invalid shape")
                if reported_total is not None and reported_total != total:
                    raise GitHubApiError("ClawSweeper jobs listing is incomplete")
                reported_total = total
            page_jobs = decoded.get("jobs")
            if not isinstance(page_jobs, list) or len(page_jobs) > 100:
                raise GitHubApiError("ClawSweeper jobs listing has an invalid shape")
            jobs.extend(page_jobs)
            nxt = self._next_list_path(current, response_headers)
            if nxt is None:
                if reported_total is not None and reported_total != len(jobs):
                    raise GitHubApiError("ClawSweeper jobs listing is incomplete")
                if reported_total is None and len(page_jobs) == 100:
                    raise GitHubApiError("ClawSweeper jobs listing is incomplete")
                return jobs
            if page_index == MAX_LIST_PAGES - 1:  # clawsweeper-job-page-cap
                raise GitHubApiError("ClawSweeper jobs listing exceeded the bounded page count")
            current = nxt  # clawsweeper-job-pages
        raise GitHubApiError("ClawSweeper jobs listing exceeded the bounded page count")

    def clawsweeper_dispatch_identity(self, workflow_run_id: int) -> dict[str, Any] | None:
        """Read one admission-job log for the dispatch inputs this client sent.

        GitHub's workflow run resource does not return workflow_dispatch inputs.
        The admission job log is the only retained exact tuple for a run whose
        dispatch receipt predates stored run ids. Download reuses the artifact
        redirect transport and its productionresultssa blob allowlist. Private
        runner log hosts stay unfetched until a separate approval. A missing,
        conflicting, oversize, non-admission, or disallowed-redirect log is no
        identity. Job lookup walks bounded allowlisted pages before deciding
        that the admission job is absent.
        """
        run_id = core.require_positive_int(workflow_run_id, "ClawSweeper workflow run id")
        jobs = self._list_clawsweeper_run_jobs(run_id)
        admission = [
            job for job in jobs
            if isinstance(job, dict)
            and isinstance(job.get("name"), str)
            and CLAWSWEEPER_ADMISSION_JOB_MARK in job["name"]
        ]
        if len(admission) != 1:
            return None
        job_id = core.require_positive_int(admission[0].get("id"), "ClawSweeper admission job id")
        path = f"/repos/{self.repository}/actions/jobs/{job_id}/logs"
        operation = self._allow("GET", path)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._installation_token()}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "smoky-review-conductor/1",
        }
        try:
            status, raw = self._artifact_transport(self._app["api_base"] + path, headers, 15.0)
        except ArtifactRedirectDenied:
            return None
        if status != 200:
            raise GitHubApiError(f"allowlisted GitHub API operation failed ({operation})")
        if len(raw) > CLAWSWEEPER_DISPATCH_LOG_LIMIT:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            return None
        parsed = parse_clawsweeper_dispatch_log(text)
        if parsed is None:
            return None
        return {
            "repository": self.repository,
            "pr_number": parsed["pr_number"],
            "base_sha": parsed["expected_base_sha"],
            "head_sha": parsed["expected_head_sha"],
            "review_epoch": parsed["review_epoch"],
        }


def check_payload(
    name: str,
    head_sha: str,
    external_id: str,
    state: str,
    *,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    core.require_sha(head_sha, "check head_sha")
    if state not in {
        "queued",
        "in_progress",
        "success",
        "failure",
        "action_required",
        "skipped",
        "cancelled",
    }:
        raise RuntimeError("check projection state is unsupported")
    fallback = {
        "queued": "Waiting for the exact prerequisite or Review Rail dispatch.",
        "in_progress": "The exact-head Review Rail is running.",
        "success": "The exact-head Review Rail completed cleanly.",
        "failure": "The exact-head Review Rail reached a terminal failure.",
        "action_required": "The exact-head Review Rail requires adjudication or recovery.",
        "skipped": "This rail was not dispatched because an exact prerequisite did not succeed or the tuple was superseded.",
        "cancelled": "This exact tuple was cancelled because the pull request closed.",
    }
    output = {
        "title": name,
        "summary": f"{fallback[state]} Exact head {head_sha}.",
    }
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "external_id": external_id,
    }
    if report:
        rendered = result_projection.check_output(
            name,
            state,
            repository=report["repository"],
            pr_number=int(report["pr_number"]),
            head_sha=head_sha,
            stage=report.get("stage"),
            content_verdict=report.get("content_verdict"),
            process_gates=report.get("process_gates"),
            reason=report.get("reason"),
            workflow_run_id=report.get("workflow_run_id"),
            artifact_digest=report.get("artifact_digest"),
            report_url=report.get("report_url"),
            request_id=report.get("request_id"),
            original_report=report.get("original_report"),
        )
        output = {"title": rendered["title"], "summary": rendered["summary"], "text": rendered["text"]}
        if rendered.get("details_url"):
            payload["details_url"] = rendered["details_url"]
    if state == "queued":
        payload["status"] = "queued"
        payload["output"] = output
        return payload
    if state == "in_progress":
        payload["status"] = "in_progress"
        payload["output"] = output
        return payload
    payload["status"] = "completed"
    payload["conclusion"] = state
    payload["output"] = output
    return payload


def accepted_quality_row(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "clawsweeper_quality" not in tables:
        return None
    quality = connection.execute(
        """
        SELECT * FROM clawsweeper_quality
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND review_epoch = ?
        """,
        (
            row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
            row["review_epoch"],
        ),
    ).fetchone()
    if quality is None:
        return None
    # Reconciliation may run before a collector migrates an old database.
    result = dict(quality)
    result.setdefault("content_verdict", None)
    result.setdefault("process_gates_json", None)
    bind_projected_clawsweeper_quality(row, result)
    return result


def bind_projected_clawsweeper_quality(row: sqlite3.Row, quality: Any) -> None:
    if quality is None:
        return
    request_id = row["review_request_id"]
    if row["rail"] != "clawsweeper" or not request_id:
        return
    core.require_bound_clawsweeper_quality_workflow_run(
        str(request_id), quality["workflow_run_id"]
    )


def effective_quality(connection: sqlite3.Connection, row: sqlite3.Row, quality: Any) -> Any:
    bind_projected_clawsweeper_quality(row, quality)
    if quality is None or row["state"] != "ready_for_human_merge":
        return quality
    events = connection.execute(
        "SELECT event_id, payload_json FROM events WHERE kind='adjudication.completed' "
        "AND repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND stale=0 "
        "ORDER BY sequence DESC",
        (row["repository"], row["pr_number"], row["base_sha"], row["head_sha"]),
    )
    for event in events:
        payload = json.loads(event["payload_json"])
        if (type(payload.get("review_epoch")) is not int
                or payload["review_epoch"] != row["review_epoch"]
                or payload.get("rail") != "clawsweeper"
                or payload.get("request_id") != row["review_request_id"]):
            continue
        dispositions = payload.get("classifications")
        if not isinstance(dispositions, list) or not dispositions or not set(dispositions) <= {"reject_false_positive", "defer"}:
            return quality
        result = dict(quality)
        result["adjudication_reason"] = (
            f"Original review content: {quality['content_verdict'] or 'unclassified'}; "
            f"effective disposition: {', '.join(dispositions)} (event {str(event['event_id'])[:48]})"
        )
        result["content_verdict"] = result_projection.CONTENT_CLEAN
        result["process_gates_json"] = json.dumps([result_projection.PROCESS_OWNER_MERGE])
        return result
    return quality


def accepted_clawsweeper_presentation(
    config: dict[str, Any], connection: sqlite3.Connection, row: sqlite3.Row, quality: Any,
) -> dict[str, Any] | None:
    """Load only the ingested native report, never a nearby report or PR comment."""
    # Legacy Blocks has no comprehensive epoch/actor contract. Do not backfill it.
    if quality is None or not config.get("review_policy"):
        return None
    identity = {key: row[key] for key in ("repository", "pr_number", "base_sha", "head_sha", "review_epoch")}
    if any(not core.same_typed_value(quality[key], value) for key, value in identity.items()):
        raise RuntimeError("native quality does not match the current exact tuple")
    run_id = str(quality["workflow_run_id"])
    if row["rail"] == "clawsweeper" and row["review_request_id"]:
        core.require_bound_clawsweeper_quality_workflow_run(str(row["review_request_id"]), run_id)
    result_projection.workflow_run_url(row["repository"], run_id)
    actor = config["review_policy"]["reviewers"]["clawsweeper"]
    events = connection.execute(
        "SELECT payload_json FROM events WHERE kind='clawsweeper.terminal' "
        "AND repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND stale=0 ORDER BY sequence DESC",
        tuple(identity[key] for key in ("repository", "pr_number", "base_sha", "head_sha")),
    )
    same_epoch = [
        payload for event in events
        if isinstance((payload := json.loads(event["payload_json"])), dict)
        and core.same_typed_value(payload.get("review_epoch"), row["review_epoch"])
    ]
    terminal = next((payload for payload in same_epoch if str(payload.get("workflow_run_id", "")) == run_id), None)
    if terminal is None:
        if same_epoch:
            raise RuntimeError("native accepted receipt conflicts with publication evidence")
        return None  # Older accepted receipts are explicitly presentation-unavailable.
    if "proof_sha256" not in terminal:
        return None  # Older accepted receipts are explicitly presentation-unavailable.
    if (str(terminal.get("workflow_run_id")) != run_id
            or terminal.get("proof_sha256") != quality["report_sha256"]
            or terminal.get("reviewer_actor") != actor
            or terminal.get("review_scope") != "comprehensive"):
        raise RuntimeError("native accepted receipt conflicts with publication evidence")
    rail = connection.execute(
        "SELECT * FROM rail_workflow_runs WHERE rail='clawsweeper' AND workflow_run_id=?", (run_id,),
    ).fetchone()
    if (rail is None or rail["status"] != "verdict_ingested"
            or any(not core.same_typed_value(rail["bound_" + key], value) for key, value in identity.items())
            or rail["proof_ref"] != terminal["proof_ref"]):
        raise RuntimeError("native report has no accepted exact-tuple workflow binding")
    root = Path(config["paths"]["proof_root"]).resolve(strict=True)
    filename = f"{row['pr_number']}.md"
    if Path(terminal["proof_ref"]) != root / "clawsweeper" / run_id / filename:
        raise RuntimeError("native report path is outside the accepted run")
    limit = result_projection.native.REPORT_MAX_BYTES
    try:
        with contextlib.ExitStack() as stack:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, flags)
            stack.callback(os.close, directory)
            for component in ("clawsweeper", run_id):
                directory = os.open(component, flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, fd)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise RuntimeError("native report is not a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(limit + 1)
        text = raw.decode("utf-8")
        return result_projection.native.parse_report(
            text, {**identity, "artifact_digest": quality["report_sha256"]}, actor=actor,
        )
    except (OSError, UnicodeError, result_projection.native.PresentationError) as exc:
        raise RuntimeError("native accepted report is missing, malformed, oversized or changed") from exc


def journal_native_publication(connection: sqlite3.Connection, row: sqlite3.Row, plan: dict[str, Any]) -> None:
    """Persist scoped ownership before writes, including interrupted publication."""
    if result_projection.native.RICH_MARKER not in plan["comment"]["body"]:
        return
    identity = {key: row[key] for key in ("repository", "pr_number", "base_sha", "head_sha")}
    event_id = "native-publication:" + hashlib.sha256(json.dumps(plan["identity"], sort_keys=True).encode()).hexdigest()
    if not connection.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
        core.insert_event(
            connection, event_id=event_id, kind="projection.native_publication", stale=False,
            **identity, payload={"review_epoch": row["review_epoch"],
                                 "artifact_digest": plan["identity"]["artifact_digest"],
                                 "families": plan["native_label_families"]},
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
    current = core.exact_current_head(connection, **identity)
    if current is None or any(current[key] != row[key] for key in ("review_epoch", "state")):
        raise RuntimeError("native publication changed while recording ownership")


def retract_native_publication(connection: sqlite3.Connection, row: sqlite3.Row, client: Any) -> None:
    """Retire only a journaled native projection; legacy/manual labels stay intact."""
    events = connection.execute(
        "SELECT payload_json FROM events WHERE kind='projection.native_publication' "
        "AND repository=? AND pr_number=? AND base_sha=? AND head_sha=?",
        tuple(row[key] for key in ("repository", "pr_number", "base_sha", "head_sha")),
    )
    families: set[str] = set()
    journaled = False
    for event in events:
        payload = json.loads(event["payload_json"])
        if not core.same_typed_value(payload.get("review_epoch"), row["review_epoch"]):
            continue
        journaled = True
        for family in payload["families"]:
            if family not in result_projection.native.FAMILIES:
                raise RuntimeError("native publication ownership is malformed")
            families.add(family)
    if not journaled:
        return
    labels = set().union(*(result_projection.native.FAMILIES[name] for name in families))
    # Existing repository maintenance guard remains on every cleanup operation.
    for name in sorted(labels & set(client.list_issue_labels(row["pr_number"]))):
        client.remove_owned_label(row["pr_number"], name)
    issuer = client._app["app_id"] if hasattr(client, "_app") else result_projection.CONDUCTOR_ISSUER_APP_ID
    for comment in client.list_issue_comments(row["pr_number"]):
        if not result_projection.trusted_app_author(comment, issuer):
            continue
        marker = result_projection.parse_projection_marker(comment.get("body", ""))
        if marker is None or marker["issuer_app_id"] != issuer or any(
            marker[key] != row[key] for key in ("repository", "pr_number", "base_sha", "head_sha", "review_epoch")
        ):
            continue
        notice = "> **Historical review — this tuple is closed or superseded, not current clearance.**\n\n"
        if not comment["body"].startswith(notice):
            client.update_issue_comment(comment["id"], notice + comment["body"])


def accepted_openclaw_terminal(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> dict[str, Any] | None:
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "events" not in tables:
        return None
    found = connection.execute(
        """
        SELECT payload_json FROM events
        WHERE kind = 'openclaw.terminal'
          AND repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND stale = 0
        ORDER BY sequence DESC
        """,
        (
            row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
        ),
    )
    payload = None
    for candidate in found:
        decoded = json.loads(candidate["payload_json"])
        if (isinstance(decoded, dict)
                and type(decoded.get("review_epoch")) is int
                and decoded["review_epoch"] == row["review_epoch"]):
            payload = decoded
            break
    if payload is None:
        return None
    terminal: dict[str, Any] = {}
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        terminal["request_id"] = request_id
    result = payload.get("result")
    if isinstance(result, str) and result:
        terminal["result"] = result
    proof_sha = payload.get("proof_sha256")
    if isinstance(proof_sha, str) and proof_sha:
        terminal["proof_sha256"] = proof_sha
    artifact_digest = payload.get("artifact_digest") or payload.get("artifact_sha256")
    if isinstance(artifact_digest, str) and artifact_digest:
        terminal["artifact_digest"] = artifact_digest
    if "original_report" in payload:
        terminal["original_report"] = core.validate_original_report(payload["original_report"])
    report_url = payload.get("report_url")
    if isinstance(report_url, str) and report_url:
        terminal["report_url"] = report_url
    return terminal or None


def projection_check_report(
    row: sqlite3.Row,
    quality: sqlite3.Row | None = None,
    *,
    check_name: str,
    check_state: str | None = None,
    openclaw_terminal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if check_name not in CHECK_NAMES:
        raise RuntimeError("check name is outside the fixed allowlist")
    report: dict[str, Any] = {
        "repository": row["repository"],
        "pr_number": row["pr_number"],
        "stage": check_state or row["state"],
        "reason": row["blocker"] or f"state {row['state']}",
    }
    if check_name == "OpenClaw Review Rail":
        report["stage"] = (
            "openclaw_completed"
            if check_state == "success"
            else (check_state or row["state"])
        )
        if openclaw_terminal:
            if "original_report" in openclaw_terminal:
                report["original_report"] = openclaw_terminal["original_report"]
            if openclaw_terminal.get("request_id"):
                report["request_id"] = openclaw_terminal["request_id"]
                report["reason"] = (
                    f"OpenClaw {openclaw_terminal.get('result') or check_state} "
                    f"for {openclaw_terminal['request_id']}"
                )
            digest = openclaw_terminal.get("artifact_digest") or openclaw_terminal.get("proof_sha256")
            if digest:
                report["artifact_digest"] = digest
            if openclaw_terminal.get("report_url"):
                report["report_url"] = openclaw_terminal["report_url"]
            result = openclaw_terminal.get("result")
            if result == "clean":
                report["content_verdict"] = result_projection.CONTENT_CLEAN
            elif result == "findings":
                report["content_verdict"] = result_projection.CONTENT_FINDINGS
            elif result == "failed":
                report["content_verdict"] = result_projection.CONTENT_FAILED
            elif result == "human_gate":
                report["content_verdict"] = result_projection.CONTENT_HUMAN_POLICY
        return report
    request_id = row["review_request_id"]
    if row["rail"] == "clawsweeper" and request_id and str(request_id).isdigit():
        report["workflow_run_id"] = str(request_id)
        report["report_url"] = result_projection.workflow_run_url(
            row["repository"], str(request_id)
        )
    if quality is not None:
        bind_projected_clawsweeper_quality(row, quality)
        if "adjudication_reason" in quality.keys():
            report["reason"] = quality["adjudication_reason"]
        report["artifact_digest"] = quality["report_sha256"]
        report["workflow_run_id"] = quality["workflow_run_id"]
        run_url = result_projection.workflow_run_url(
            row["repository"], quality["workflow_run_id"]
        )
        if run_url:
            report["report_url"] = run_url
        if quality["content_verdict"]:
            report["content_verdict"] = quality["content_verdict"]
        if quality["process_gates_json"]:
            report["process_gates"] = json.loads(quality["process_gates_json"])
    return report


def _invoke_check(
    client: Any,
    method: str,
    *positional: Any,
    report: dict[str, Any] | None,
    authority_kwargs: dict[str, Any],
) -> Any:
    target = getattr(client, method)
    # Bind before any side effect. An exception inside the client must never
    # trigger a second non-idempotent check creation.
    signature = inspect.signature(target)
    kwargs = dict(authority_kwargs)
    if "report" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        kwargs["report"] = report
    signature.bind(*positional, **kwargs)
    return target(*positional, **kwargs)


def publish_accepted_github_projection(
    client: Any,
    row: sqlite3.Row,
    quality: sqlite3.Row,
    *,
    dry_run: bool,
    authority_kwargs: dict[str, Any],
    native_report: dict[str, Any] | None = None,
    before_apply: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    if not all(
        hasattr(client, name)
        for name in (
            "list_issue_comments",
            "list_issue_labels",
            "create_issue_comment",
            "update_issue_comment",
            "add_owned_label",
            "remove_owned_label",
        )
    ):
        return None
    issuer = client._app.get("app_id") if hasattr(client, "_app") else result_projection.CONDUCTOR_ISSUER_APP_ID
    identity = result_projection.bind_accepted_artifact(
        repository=row["repository"],
        pr_number=int(row["pr_number"]),
        base_sha=row["base_sha"],
        head_sha=row["head_sha"],
        review_epoch=int(row["review_epoch"]),
        issuer_app_id=int(issuer),
        artifact_digest=quality["report_sha256"],
        expected={
            "repository": row["repository"],
            "pr_number": int(row["pr_number"]),
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": int(row["review_epoch"]),
            "issuer_app_id": int(issuer),
            "artifact_digest": quality["report_sha256"],
        },
    )
    gates = json.loads(quality["process_gates_json"] or "[]")
    classification = {
        "content_verdict": quality["content_verdict"] or CONTENT_FROM_STATE.get(row["state"], "human_policy"),
        "process_gates": gates,
        "rail_result": "clean" if row["state"] == "ready_for_human_merge" else "human_gate",
        "merge_authorized": False,
        "merge_policy": "human_only",
        "own_current_check_circular": result_projection.PROCESS_OWN_CHECK in gates,
        "native_decision": None,
    }
    if dry_run:
        return {
            "schema": "smoky.review-conductor.github-publication.v1",
            "result": "planned",
            "identity": identity,
            "classification": classification,
        }
    comments = client.list_issue_comments(row["pr_number"], **authority_kwargs)
    labels = client.list_issue_labels(row["pr_number"], **authority_kwargs)
    plan = result_projection.plan_github_publication(
        identity=identity,
        classification=classification,
        existing_comments=comments,
        existing_labels=labels,
        stage=row["state"],
        reason=(quality["adjudication_reason"] if "adjudication_reason" in quality.keys()
                else row["blocker"] or f"state {row['state']}"),
        workflow_run_id=quality["workflow_run_id"],
        native_report=native_report,
    )
    if not plan["writes"]:
        return {**plan, "result": "unchanged"}
    if before_apply is not None:
        before_apply(plan)
    receipt = result_projection.apply_github_publication(
        client, plan, authority_kwargs=authority_kwargs
    )
    return {**plan, "result": "published", "receipt": receipt}


CONTENT_FROM_STATE = {
    "ready_for_human_merge": result_projection.CONTENT_CLEAN,
    "awaiting_adjudication": result_projection.CONTENT_FINDINGS,
    "openclaw_failed": result_projection.CONTENT_FAILED,
    "clawsweeper_failed": result_projection.CONTENT_FAILED,
    "waiting_human": result_projection.CONTENT_HUMAN_POLICY,
}


def projection_external_id(row: sqlite3.Row, check_name: str) -> str:
    digest = hashlib.sha256(
        (
            f"{row['repository']}|{row['pr_number']}|{row['base_sha']}|"
            f"{row['head_sha']}|{row['review_epoch']}|{check_name}"
        ).encode()
    ).hexdigest()
    return f"review-conductor:{digest}"


def ensure_projection_row(connection: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    now = core.utc_now()
    connection.execute(
        """
        INSERT INTO projections(
          repository, pr_number, base_sha, head_sha, review_epoch, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository, pr_number, base_sha, head_sha, review_epoch) DO NOTHING
        """,
        (
            row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
            row["review_epoch"], now, now,
        ),
    )
    result = connection.execute(
        """
        SELECT * FROM projections
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND review_epoch = ?
        """,
        (
            row["repository"], row["pr_number"], row["base_sha"], row["head_sha"],
            row["review_epoch"],
        ),
    ).fetchone()
    if result is None:
        raise RuntimeError("failed to create exact-tuple projection state")
    return result


def superseded_projection_rows(
    connection: sqlite3.Connection,
    repository: str,
    pr_number: int | None,
) -> list[sqlite3.Row]:
    query = """
        SELECT projections.*
        FROM projections
        JOIN heads
          ON heads.repository = projections.repository
         AND heads.pr_number = projections.pr_number
         AND heads.base_sha = projections.base_sha
         AND heads.head_sha = projections.head_sha
        WHERE projections.repository = ?
          AND (heads.is_current = 0 OR heads.review_epoch != projections.review_epoch)
          AND COALESCE(projections.last_projected_state, '') != 'superseded'
    """
    params: list[Any] = [repository]
    if pr_number is not None:
        query += " AND projections.pr_number = ?"
        params.append(pr_number)
    query += " ORDER BY projections.pr_number, projections.review_epoch"
    return connection.execute(query, params).fetchall()


def remove_projected_status_labels(client: Any, pr_number: int) -> None:
    """Retract the reserved projection vocabulary as repository maintenance.

    This negative cleanup deliberately has no live-tuple authority: its owner
    may already be closed or superseded. The client's existing maintenance
    guard still checks current enrollment before each DELETE.
    """
    client.remove_ready_label(pr_number)
    remove = getattr(client, "remove_owned_label", None)
    if callable(remove):
        for name in sorted(result_projection.OWNED_LABELS):
            if name != READY_LABEL:
                remove(pr_number, name)


def reconcile_superseded_projection(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    client: GitHubAppClient | Any,
    *,
    dry_run: bool,
    conclusion: str = "skipped",
    ready_label_already_removed: bool = False,
) -> dict[str, Any]:
    item = {
        "pr_number": row["pr_number"],
        "base_sha": row["base_sha"],
        "head_sha": row["head_sha"],
        "review_epoch": row["review_epoch"],
        "visible_state": "closed" if conclusion == "cancelled" else "superseded",
        "checks": {name: conclusion for name in CHECK_NAMES},
        "ready_label": False,
        "merge_authorized": False,
    }
    if dry_run:
        return item
    check_columns = {
        "OpenClaw Review Rail": (
            "openclaw_check_run_id",
            "openclaw_check_create_state",
        ),
        "ClawSweeper Review Rail": (
            "clawsweeper_check_run_id",
            "clawsweeper_check_create_state",
        ),
    }
    check_ids: dict[str, int | None] = {}
    for name, (id_column, create_state_column) in check_columns.items():
        check_id = row[id_column]
        check_ids[name] = check_id
        if check_id is not None:
            client.update_check(
                check_id,
                name,
                row["head_sha"],
                projection_external_id(row, name),
                conclusion,
            )
        elif row[create_state_column] == "creating":
            raise RuntimeError(
                f"superseded {name} creation outcome is uncertain and requires reconciliation"
            )
    if not ready_label_already_removed:
        remove_projected_status_labels(client, row["pr_number"])
    retract_native_publication(connection, row, client)
    now = core.utc_now()
    connection.execute(
        """
        UPDATE projections
        SET ready_label_applied = 0,
            ready_label_reconcile_action = 'superseded_by_current_tuple',
            last_projected_state = 'superseded', last_error = NULL, updated_at = ?
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND review_epoch = ?
        """,
        (
            now,
            row["repository"],
            row["pr_number"],
            row["base_sha"],
            row["head_sha"],
            row["review_epoch"],
        ),
    )
    item["check_ids"] = check_ids
    return item


def reconcile_projection(
    config: dict[str, Any], client: GitHubAppClient | Any, *, pr_number: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    core_config = core.load_config(Path(config["core_config"]))
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    projected: list[dict[str, Any]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        for stale_projection in superseded_projection_rows(
            connection, core_config["repository"], pr_number
        ):
            projected.append(
                reconcile_superseded_projection(
                    connection, stale_projection, client, dry_run=dry_run
                )
            )
        closed_query = """
            SELECT heads.* FROM heads
            WHERE heads.repository=? AND heads.is_current=1
              AND heads.state IN ('closed','closed_merged')
              AND NOT EXISTS (
                  SELECT 1 FROM projections
                  WHERE projections.repository=heads.repository
                    AND projections.pr_number=heads.pr_number
                    AND projections.base_sha=heads.base_sha
                    AND projections.head_sha=heads.head_sha
                    AND projections.review_epoch=heads.review_epoch
                    AND projections.last_projected_state='superseded'
              )
        """
        closed_params: list[Any] = [core_config["repository"]]
        if pr_number is not None:
            closed_query += " AND heads.pr_number=?"
            closed_params.append(pr_number)
        closed_query += " ORDER BY heads.pr_number"
        closed = connection.execute(closed_query, closed_params).fetchall()
        for closed_head in closed:
            if not dry_run:
                remove_projected_status_labels(client, closed_head["pr_number"])
            closed_projection = connection.execute(
                """
                SELECT * FROM projections
                WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=?
                  AND review_epoch=?
                  AND COALESCE(last_projected_state, '') != 'superseded'
                """,
                (
                    closed_head["repository"], closed_head["pr_number"],
                    closed_head["base_sha"], closed_head["head_sha"],
                    closed_head["review_epoch"],
                ),
            ).fetchone()
            if closed_projection is None and dry_run:
                projected.append({
                    "pr_number": closed_head["pr_number"],
                    "base_sha": closed_head["base_sha"],
                    "head_sha": closed_head["head_sha"],
                    "review_epoch": closed_head["review_epoch"],
                    "visible_state": "closed",
                    "checks": {name: "cancelled" for name in CHECK_NAMES},
                    "ready_label": False,
                    "merge_authorized": False,
                    "check_ids": {name: None for name in CHECK_NAMES},
                })
                continue
            if closed_projection is None:
                closed_projection = ensure_projection_row(connection, closed_head)
            projected.append(
                reconcile_superseded_projection(
                    connection, closed_projection, client, dry_run=dry_run,
                    conclusion="cancelled", ready_label_already_removed=True,
                )
            )
        query = "SELECT * FROM heads WHERE repository = ? AND is_current = 1"
        params: list[Any] = [core_config["repository"]]
        if pr_number is not None:
            query += " AND pr_number = ?"
            params.append(pr_number)
        query += " AND state NOT IN ('closed','closed_merged') ORDER BY pr_number"
        rows = connection.execute(query, params).fetchall()
        for row in rows:
            projection = ensure_projection_row(connection, row)
            authority_kwargs = guarded_client_kwargs(client, row)
            state = core.state_projection(dict(row))
            clawsweeper_publication_owner = False
            if row["state"] == "clawsweeper_queued":
                action = core.tuple_action(
                    connection,
                    {
                        "repository": row["repository"],
                        "pr_number": row["pr_number"],
                        "base_sha": row["base_sha"],
                        "head_sha": row["head_sha"],
                    },
                    "clawsweeper.dispatch",
                    int(row["review_epoch"]),
                )
                clawsweeper_publication_owner = action is not None and action["status"] in {
                    "dispatching",
                    "dispatched",
                    "reconcile_required",
                }
            elif row["state"] == "clawsweeper_running":
                clawsweeper_publication_owner = True
            item = {
                "pr_number": row["pr_number"],
                "base_sha": row["base_sha"],
                "head_sha": row["head_sha"],
                "review_epoch": row["review_epoch"],
                "visible_state": row["state"],
                "checks": state["checks"],
                "ready_label": state["ready_for_human_label"],
                "ready_label_action": (
                    "hold_for_clawsweeper_publication"
                    if clawsweeper_publication_owner
                    else (
                        "ensure_present"
                        if state["ready_for_human_label"]
                        else (
                            "ensure_absent_before_clawsweeper_publication"
                            if row["state"] == "clawsweeper_queued"
                            else "ensure_absent"
                        )
                    )
                ),
                "merge_authorized": False,
            }
            if dry_run:
                projected.append(item)
                continue
            check_columns = {
                "OpenClaw Review Rail": (
                    "openclaw_check_run_id",
                    "openclaw_check_create_state",
                ),
                "ClawSweeper Review Rail": (
                    "clawsweeper_check_run_id",
                    "clawsweeper_check_create_state",
                ),
            }
            quality = effective_quality(connection, row, accepted_quality_row(connection, row))
            # Validate publication bytes before any check/comment/label side effect.
            native_report = accepted_clawsweeper_presentation(config, connection, row, quality)
            openclaw_terminal = accepted_openclaw_terminal(connection, row)
            if openclaw_terminal and "original_report" in openclaw_terminal:
                openclaw_terminal["original_report"] = original_report_output(config, openclaw_terminal)
            for name, check_state in state["checks"].items():
                check_report = projection_check_report(
                    row,
                    quality,
                    check_name=name,
                    check_state=check_state,
                    openclaw_terminal=openclaw_terminal,
                )
                column, create_state_column = check_columns[name]
                check_id = projection[column]
                external_id = projection_external_id(row, name)
                if (
                    check_id is not None and check_state in {"queued", "in_progress"}
                    and hasattr(client, "needs_skipped_check_replacement")
                    and client.needs_skipped_check_replacement(
                        check_id, name, row["head_sha"], external_id, **authority_kwargs,
                    )
                ):
                    # Migrate only a verified owned terminal skip, not a verdict.
                    # Preserve the old ID in durable audit evidence; the existing
                    # uncertain-create claim fences the single replacement POST.
                    core.insert_event(
                        connection, event_id=f"check-skipped-replacement:{external_id}:{check_id}",
                        kind="projection.skipped_check_replacement", stale=False,
                        repository=row["repository"], pr_number=row["pr_number"],
                        base_sha=row["base_sha"], head_sha=row["head_sha"],
                        payload={"review_epoch": row["review_epoch"], "check_name": name,
                                 "old_check_run_id": check_id, "external_id": external_id,
                                 "old_conclusion": "skipped", "desired_state": check_state},
                    )
                    migrated = connection.execute(
                        f"""UPDATE projections SET {column} = NULL,
                          {create_state_column} = 'pending', updated_at = ?
                        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                          AND review_epoch = ? AND {column} = ?
                          AND {create_state_column} = 'active'""",
                        (core.utc_now(), row["repository"], row["pr_number"], row["base_sha"],
                         row["head_sha"], row["review_epoch"], check_id),
                    )
                    if migrated.rowcount != 1:
                        raise RuntimeError("skipped check migration lost its active ownership claim")
                    check_id = None
                    projection = ensure_projection_row(connection, row)
                if check_id is None:
                    if projection[create_state_column] == "creating":
                        raise RuntimeError(
                            f"{name} creation outcome is uncertain and requires reconciliation"
                        )
                    connection.execute(
                        f"""
                        UPDATE projections SET {create_state_column} = 'creating', updated_at = ?
                        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                          AND review_epoch = ? AND {column} IS NULL
                          AND {create_state_column} = 'pending'
                        """,
                        (
                            core.utc_now(), row["repository"], row["pr_number"],
                            row["base_sha"], row["head_sha"], row["review_epoch"],
                        ),
                    )
                    connection.commit()
                    connection.execute("BEGIN IMMEDIATE")
                    current = core.exact_current_head(
                        connection,
                        row["repository"],
                        row["pr_number"],
                        row["base_sha"],
                        row["head_sha"],
                    )
                    if current is None or int(current["review_epoch"]) != row["review_epoch"]:
                        raise RuntimeError("check creation claim became stale before projection")
                    try:
                        check_id = _invoke_check(
                            client,
                            "create_check",
                            name,
                            row["head_sha"],
                            external_id,
                            check_state,
                            report=check_report,
                            authority_kwargs=authority_kwargs,
                        )
                    except core.AuthorityDenied:
                        connection.execute(
                            f"""
                            UPDATE projections SET {create_state_column} = 'pending',
                              last_error = 'authority revoked before check creation',
                              updated_at = ?
                            WHERE repository = ? AND pr_number = ? AND base_sha = ?
                              AND head_sha = ? AND review_epoch = ? AND {column} IS NULL
                              AND {create_state_column} = 'creating'
                            """,
                            (
                                core.utc_now(), row["repository"], row["pr_number"],
                                row["base_sha"], row["head_sha"], row["review_epoch"],
                            ),
                        )
                        connection.commit()
                        raise
                    connection.execute(
                        f"""
                        UPDATE projections SET {column} = ?, {create_state_column} = 'active',
                          updated_at = ?
                        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                          AND review_epoch = ? AND {column} IS NULL
                          AND {create_state_column} = 'creating'
                        """,
                        (
                            check_id, core.utc_now(), row["repository"], row["pr_number"],
                            row["base_sha"], row["head_sha"], row["review_epoch"],
                        ),
                    )
                    connection.commit()
                    connection.execute("BEGIN IMMEDIATE")
                else:
                    _invoke_check(
                        client,
                        "update_check",
                        check_id,
                        name,
                        row["head_sha"],
                        external_id,
                        check_state,
                        report=check_report,
                        authority_kwargs=authority_kwargs,
                    )
            desired = bool(state["ready_for_human_label"])
            label_action = item["ready_label_action"]
            if label_action == "ensure_present":
                client.add_ready_label(row["pr_number"], **authority_kwargs)
            elif label_action in {
                "ensure_absent",
                "ensure_absent_before_clawsweeper_publication",
            }:
                client.remove_ready_label(row["pr_number"], **authority_kwargs)
            if label_action != "hold_for_clawsweeper_publication":
                connection.execute(
                    """
                    UPDATE projections
                    SET ready_label_applied = 0, updated_at = ?
                    WHERE repository = ? AND pr_number = ?
                    """,
                    (core.utc_now(), row["repository"], row["pr_number"]),
                )
            connection.execute(
                """
                UPDATE projections SET ready_label_applied = CASE
                    WHEN ? = 'hold_for_clawsweeper_publication' THEN ready_label_applied
                    ELSE ?
                  END, last_projected_state = ?,
                  ready_label_reconcile_action = ?, ready_label_reconciled_at = ?,
                  last_error = NULL, updated_at = ?
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                  AND review_epoch = ?
                """,
                (
                    label_action, int(desired), row["state"], label_action, core.utc_now(),
                    core.utc_now(), row["repository"],
                    row["pr_number"], row["base_sha"], row["head_sha"], row["review_epoch"],
                ),
            )
            item["check_ids"] = {
                name: connection.execute(
                    f"SELECT {column} FROM projections WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ? AND review_epoch = ?",
                    (
                        row["repository"], row["pr_number"], row["base_sha"],
                        row["head_sha"], row["review_epoch"],
                    ),
                ).fetchone()[0]
                for name, (column, _create_state_column) in check_columns.items()
            }
            if (
                quality is not None
                and quality["content_verdict"]
                and row["state"]
                in {
                    "ready_for_human_merge",
                    "awaiting_adjudication",
                    "waiting_human",
                    "openclaw_failed",
                    "clawsweeper_failed",
                }
            ):
                item["publication"] = publish_accepted_github_projection(
                    client,
                    row,
                    quality,
                    dry_run=False,
                    authority_kwargs=authority_kwargs,
                    native_report=native_report,
                    before_apply=lambda plan: journal_native_publication(connection, row, plan),
                )
            projected.append(item)
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "schema": "smoky.review-conductor.projection.v1",
        "result": "planned" if dry_run else "reconciled",
        "projected": projected,
        "merge_authorized": False,
    }


def parse_timestamp(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("durable timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("durable timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def lease_deadline(seconds: int) -> str:
    return (
        dt.datetime.now(tz=UTC) + dt.timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def recover_abandoned_actions(config: dict[str, Any]) -> list[dict[str, Any]]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    recovered: list[dict[str, Any]] = []
    now = dt.datetime.now(tz=UTC)
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT * FROM actions WHERE status IN ('preparing', 'dispatching') ORDER BY updated_at, action_id"
        ).fetchall()
        for row in rows:
            current = core.exact_current_head(
                connection,
                row["repository"],
                row["pr_number"],
                row["base_sha"],
                row["head_sha"],
            )
            superseded = (
                current is None
                or int(current["review_epoch"]) != int(row["review_epoch"])
            )
            if superseded:
                connection.execute(
                    """
                    UPDATE actions
                    SET status = 'obsolete', claim_owner = NULL, claimed_at = NULL,
                        lease_expires_at = NULL,
                        last_error = 'superseded action claim revoked by review_epoch',
                        updated_at = ?
                    WHERE action_id = ? AND status IN ('preparing', 'dispatching')
                    """,
                    (core.utc_now(), row["action_id"]),
                )
                recovered.append(
                    {"action_id": row["action_id"], "kind": row["kind"], "status": "obsolete"}
                )
                continue
            expires = row["lease_expires_at"]
            if expires:
                abandoned = parse_timestamp(expires) <= now
            else:
                updated = parse_timestamp(row["updated_at"])
                abandoned = updated + dt.timedelta(
                    seconds=config["worker"]["claim_lease_seconds"]
                ) <= now
            if not abandoned:
                continue
            if row["status"] == "preparing":
                next_status = "pending"
                reason = "abandoned pre-dispatch cleanup claim returned to pending"
            elif row["kind"] == "openclaw.enqueue":
                next_status = "failed"
                reason = "abandoned idempotent claim recovered for explicit retry"
            elif row["kind"] == "clawsweeper.dispatch":
                next_status = "reconcile_required"
                reason = "abandoned non-idempotent dispatch requires workflow-run reconciliation"
            elif row["kind"] == "rereview.acknowledge":
                next_status = "reconcile_required"
                reason = "abandoned non-idempotent acknowledgement requires comment reconciliation"
            else:
                next_status = "pending"
                reason = "abandoned local handoff claim returned to pending"
            connection.execute(
                """
                UPDATE actions SET status = ?, claim_owner = NULL, claimed_at = NULL,
                  lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE action_id = ? AND status = ?
                """,
                (next_status, reason, core.utc_now(), row["action_id"], row["status"]),
            )
            recovered.append(
                {"action_id": row["action_id"], "kind": row["kind"], "status": next_status}
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return recovered


def claim_clawsweeper_preparation(
    connection: sqlite3.Connection, action: sqlite3.Row, config: dict[str, Any], owner: str
) -> bool:
    connection.execute("BEGIN IMMEDIATE")
    current = core.exact_current_head(
        connection,
        action["repository"], action["pr_number"], action["base_sha"], action["head_sha"],
    )
    if (
        core.dispatch_block_reason(
            current,
            action_kind=action["kind"],
            review_epoch=int(action["review_epoch"]),
        )
        is not None
    ):
        connection.execute(
            "UPDATE actions SET status = 'obsolete', last_error = 'exact tuple is not dispatchable', updated_at = ? WHERE action_id = ? AND status = 'pending'",
            (core.utc_now(), action["action_id"]),
        )
        connection.commit()
        return False
    claimed = connection.execute(
        """
        UPDATE actions SET status = 'preparing', claim_owner = ?,
          claimed_at = ?, lease_expires_at = ?, last_error = NULL, updated_at = ?
        WHERE action_id = ? AND status = 'pending' AND attempts = ?
        """,
        (
            owner, core.utc_now(), lease_deadline(config["worker"]["claim_lease_seconds"]),
            core.utc_now(), action["action_id"], action["attempts"],
        ),
    )
    if claimed.rowcount != 1:
        connection.rollback()
        return False
    connection.commit()
    return True


def claim_clawsweeper_action(
    connection: sqlite3.Connection, action: sqlite3.Row, config: dict[str, Any], owner: str
) -> int:
    connection.execute("BEGIN IMMEDIATE")
    current = core.exact_current_head(
        connection,
        action["repository"], action["pr_number"], action["base_sha"], action["head_sha"],
    )
    if (
        core.dispatch_block_reason(
            current,
            action_kind=action["kind"],
            review_epoch=int(action["review_epoch"]),
        )
        is not None
    ):
        connection.execute(
            "UPDATE actions SET status = 'obsolete', claim_owner = NULL, claimed_at = NULL, lease_expires_at = NULL, last_error = 'exact tuple is not dispatchable', updated_at = ? WHERE action_id = ? AND status = 'preparing' AND claim_owner = ?",
            (core.utc_now(), action["action_id"], owner),
        )
        connection.commit()
        return 0
    attempts = int(action["attempts"]) + 1
    claimed = connection.execute(
        """
        UPDATE actions SET status = 'dispatching', attempts = ?,
          lease_expires_at = ?, last_error = NULL, updated_at = ?
        WHERE action_id = ? AND status = 'preparing' AND claim_owner = ?
          AND attempts = ?
        """,
        (
            attempts, lease_deadline(config["worker"]["claim_lease_seconds"]),
            core.utc_now(), action["action_id"], owner, action["attempts"],
        ),
    )
    if claimed.rowcount != 1:
        connection.rollback()
        return 0
    connection.commit()
    return attempts


def dispatch_clawsweeper_action(
    config: dict[str, Any], action_id: str, client: GitHubAppClient | Any
) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if action is None or action["kind"] != "clawsweeper.dispatch":
            raise RuntimeError("ClawSweeper action was not found")
        if action["status"] == "dispatched":
            return {"action_id": action_id, "result": "already_dispatched", "merge_dispatched": False}
        if action["status"] != "pending":
            raise RuntimeError("ClawSweeper action is not safely claimable")
        if not claim_clawsweeper_preparation(connection, action, config, "cp1-worker"):
            return {"action_id": action_id, "result": "not_claimed", "merge_dispatched": False}
        authority_kwargs = guarded_client_kwargs(client, action)
        try:
            client.remove_ready_label(action["pr_number"], **authority_kwargs)
        except Exception:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE actions SET status = 'pending', claim_owner = NULL,
                  claimed_at = NULL, lease_expires_at = NULL,
                  last_error = 'pre-dispatch label cleanup failed; safe to retry',
                  updated_at = ?
                WHERE action_id = ? AND status = 'preparing' AND claim_owner = 'cp1-worker'
                """,
                (core.utc_now(), action_id),
            )
            connection.commit()
            raise
        connection.execute("BEGIN IMMEDIATE")
        current = core.exact_current_head(
            connection,
            action["repository"],
            action["pr_number"],
            action["base_sha"],
            action["head_sha"],
        )
        current_action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        block_reason = core.dispatch_block_reason(
            current,
            action_kind=action["kind"],
            review_epoch=int(action["review_epoch"]),
        )
        if (
            current_action is None
            or current_action["status"] != "preparing"
            or current_action["claim_owner"] != "cp1-worker"
        ):
            connection.rollback()
            return {"action_id": action_id, "result": "not_claimed", "merge_dispatched": False}
        if block_reason is not None:
            connection.execute(
                """
                UPDATE actions SET status = 'obsolete', claim_owner = NULL,
                  claimed_at = NULL, lease_expires_at = NULL,
                  last_error = 'exact tuple changed during pre-dispatch label cleanup',
                  updated_at = ?
                WHERE action_id = ? AND status = 'preparing' AND claim_owner = 'cp1-worker'
                """,
                (core.utc_now(), action_id),
            )
            connection.commit()
            return {"action_id": action_id, "result": "not_claimed", "merge_dispatched": False}
        ensure_projection_row(connection, current)
        now = core.utc_now()
        connection.execute(
            """
            UPDATE projections
            SET ready_label_applied = 0, updated_at = ?
            WHERE repository = ? AND pr_number = ?
            """,
            (now, action["repository"], action["pr_number"]),
        )
        connection.execute(
            """
            UPDATE projections
            SET ready_label_applied = 0,
                ready_label_reconcile_action = 'ensure_absent_before_clawsweeper_publication',
                ready_label_reconciled_at = ?, last_projected_state = 'clawsweeper_queued',
                last_error = NULL, updated_at = ?
            WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
              AND review_epoch = ?
            """,
            (
                now,
                now,
                action["repository"],
                action["pr_number"],
                action["base_sha"],
                action["head_sha"],
                action["review_epoch"],
            ),
        )
        connection.commit()
        attempts = claim_clawsweeper_action(connection, action, config, "cp1-worker")
        if attempts == 0:
            return {"action_id": action_id, "result": "not_claimed", "merge_dispatched": False}
        payload = json.loads(action["payload_json"])
        try:
            dispatched_run_id = client.dispatch_clawsweeper(
                pr_number=payload["pr_number"],
                base_sha=payload["base_sha"],
                head_sha=payload["head_sha"],
                publish=payload["publish"],
                **({"review_epoch": payload["review_epoch"]} if config.get("review_policy") else {}),
                **authority_kwargs,
            )
        except core.AuthorityDenied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE actions SET status = 'pending', claim_owner = NULL,
                  claimed_at = NULL, lease_expires_at = NULL,
                  last_error = 'authority revoked before workflow dispatch', updated_at = ?
                WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                """,
                (core.utc_now(), action_id, attempts),
            )
            connection.commit()
            raise
        except Exception:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE actions SET status = 'reconcile_required',
                  last_error = 'workflow dispatch outcome is uncertain; do not resend',
                  lease_expires_at = NULL, updated_at = ?
                WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                """,
                (core.utc_now(), action_id, attempts),
            )
            connection.commit()
            raise
        receipt = {
            "schema": "smoky.review-conductor.dispatch.v1",
            "result": "dispatched",
            "action_id": action_id,
            "kind": "clawsweeper.dispatch",
            "exact_head": action["head_sha"],
            "transport_receipt_only": True,
            "terminal_verdict_received": False,
            "merge_dispatched": False,
        }
        if dispatched_run_id is not None:
            receipt["workflow_run_id"] = dispatched_run_id
        connection.execute("BEGIN IMMEDIATE")
        updated = connection.execute(
            """
            UPDATE actions SET status = 'dispatched', receipt_json = ?,
              lease_expires_at = NULL, updated_at = ?
            WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
            """,
            (core.canonical_json(receipt), core.utc_now(), action_id, attempts),
        )
        if updated.rowcount != 1:
            connection.rollback()
            raise RuntimeError("ClawSweeper action lost its atomic claim after dispatch")
        connection.commit()
        return receipt
    finally:
        connection.close()


def service_transport_environment(config: dict[str, Any]) -> dict[str, str]:
    """Return the isolated environment for one adapter subprocess.

    The threaded service must never rewrite ``os.environ`` process-wide: other
    webhook threads and libraries may read it while an adapter is running.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "PYTHONUNBUFFERED": "1",
        "SMOKY_REVIEW_CONDUCTOR_SMOKY": config["spark"]["smoky_path"],
        "SMOKY_SPARK_SSH_BIN": config["spark"]["ssh_path"],
        "SMOKY_SPARK_SCP_BIN": config["spark"]["scp_path"],
        "SPARK_OPENCLAW_MATERIALIZE_TARGET": config["spark"]["target"],
        "SPARK_OPENCLAW_AUTOREVIEW_TARGET": config["spark"]["target"],
    }


def dispatch_rereview_acknowledge(
    config: dict[str, Any], action_id: str, client: GitHubAppClient | Any
) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if action is None or action["kind"] != "rereview.acknowledge":
            raise RuntimeError("rereview acknowledgement action was not found")
        if action["status"] == "dispatched":
            return {"action_id": action_id, "result": "already_dispatched", "merge_dispatched": False}
        if action["status"] != "pending":
            raise RuntimeError("rereview acknowledgement is not safely claimable")
        current = core.exact_current_head(
            connection,
            action["repository"],
            action["pr_number"],
            action["base_sha"],
            action["head_sha"],
        )
        if current is None or int(current["review_epoch"]) != int(action["review_epoch"]):
            connection.execute(
                """
                UPDATE actions SET status = 'obsolete', last_error = ?, updated_at = ?
                WHERE action_id = ? AND status = 'pending'
                """,
                ("exact tuple is no longer current", core.utc_now(), action_id),
            )
            connection.commit()
            return {"action_id": action_id, "result": "obsolete", "merge_dispatched": False}
        claimed = connection.execute(
            """
            UPDATE actions SET status = 'dispatching', attempts = attempts + 1,
              claim_owner = ?, claimed_at = ?, updated_at = ?
            WHERE action_id = ? AND status = 'pending'
            """,
            ("cp1-worker", core.utc_now(), core.utc_now(), action_id),
        )
        if claimed.rowcount != 1:
            connection.commit()
            return {"action_id": action_id, "result": "not_claimed", "merge_dispatched": False}
        connection.commit()
        payload = json.loads(action["payload_json"])
        authority_kwargs = guarded_client_kwargs(client, action)
        try:
            comment_id = client.create_issue_comment(
                payload["pr_number"], payload["body"], **authority_kwargs
            )
        except Exception:
            connection.execute(
                """
                UPDATE actions SET status = 'reconcile_required', last_error = ?,
                    lease_expires_at = NULL, updated_at = ?
                WHERE action_id = ? AND status = 'dispatching'
                """,
                (
                    "rereview acknowledgement outcome is uncertain; do not resend",
                    core.utc_now(),
                    action_id,
                ),
            )
            connection.commit()
            raise
        connection.execute(
            """
            UPDATE actions SET status = 'dispatched', receipt_json = ?, updated_at = ?
            WHERE action_id = ? AND status = 'dispatching'
            """,
            (
                core.canonical_json({"comment_id": comment_id, "result": "dispatched"}),
                core.utc_now(),
                action_id,
            ),
        )
        connection.commit()
        return {
            "action_id": action_id,
            "result": "dispatched",
            "comment_id": comment_id,
            "merge_dispatched": False,
        }
    finally:
        connection.close()


def drain_actions(
    config: dict[str, Any], client: GitHubAppClient | Any | None, *, dry_run: bool = False
) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    recovered = recover_abandoned_actions(config)
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        actions = connection.execute(
            """
            SELECT actions.* FROM actions
            JOIN heads
              ON heads.repository = actions.repository
             AND heads.pr_number = actions.pr_number
             AND heads.base_sha = actions.base_sha
             AND heads.head_sha = actions.head_sha
             AND heads.review_epoch = actions.review_epoch
             AND heads.is_current = 1
            WHERE actions.kind IN ('openclaw.enqueue', 'clawsweeper.dispatch', 'rereview.acknowledge')
              AND actions.status = 'pending'
              AND (
                (actions.kind = 'openclaw.enqueue' AND heads.state = 'openclaw_queued')
                OR
                (actions.kind = 'clawsweeper.dispatch' AND heads.state = 'clawsweeper_queued')
                OR
                actions.kind = 'rereview.acknowledge'
              )
            ORDER BY actions.created_at, actions.action_id LIMIT ?
            """,
            (config["worker"]["max_actions_per_wake"],),
        ).fetchall()
    finally:
        connection.close()
    outcomes: list[dict[str, Any]] = []
    for action in actions:
        if dry_run:
            outcomes.append(
                {"action_id": action["action_id"], "kind": action["kind"], "result": "planned"}
            )
            continue
        if action["kind"] == "rereview.acknowledge":
            if client is None:
                raise RuntimeError("GitHub installation client is required for rereview acknowledgement")
            outcomes.append(dispatch_rereview_acknowledge(config, action["action_id"], client))
        elif action["kind"] == "openclaw.enqueue":
            authority = tuple_authority(action)

            def before_external_command(
                index: int,
                _command: list[str],
                *,
                action_id: str = action["action_id"],
                exact_authority: dict[str, Any] = authority,
            ) -> None:
                assert_authority(
                    client,
                    f"openclaw.enqueue:{action_id}:step:{index + 1}",
                    exact_authority,
                )

            args = argparse.Namespace(
                config=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                action_id=action["action_id"],
                source_checkout=Path(config["paths"]["blocks_checkout"]),
                apply=True,
                retry=action["status"] == "failed",
                claim_owner="cp1-worker",
                claim_lease_seconds=config["worker"]["claim_lease_seconds"],
                before_external_command=before_external_command,
                command_environment=service_transport_environment(config),
            )
            # Each OpenClaw dispatch is an external side effect; fence it like a
            # GitHub write so revoked authority stops the whole drain here instead
            # of being recorded as an adapter rejection of this one action.
            assert_authority(
                client,
                f"openclaw.enqueue:{action['action_id']}",
                authority,
            )
            try:
                outcomes.append(core.dispatch_action(args))
            except core.AuthorityDenied:
                raise
            except core.ContractError:
                connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """
                        UPDATE actions SET status = 'failed', attempts = attempts + 1,
                          last_error = 'OpenClaw adapter rejected before dispatch',
                          updated_at = ?
                        WHERE action_id = ? AND status = 'pending'
                        """,
                        (core.utc_now(), action["action_id"]),
                    )
                    connection.commit()
                finally:
                    connection.close()
                outcomes.append(
                    {
                        "action_id": action["action_id"],
                        "kind": action["kind"],
                        "result": "failed",
                    }
                )
        else:
            if client is None:
                raise RuntimeError("GitHub installation client is required for ClawSweeper dispatch")
            outcomes.append(dispatch_clawsweeper_action(config, action["action_id"], client))
    failed = terminalize_failed_openclaw_actions(config, dry_run=dry_run)
    return {
        "schema": "smoky.review-conductor.worker-wake.v1",
        "result": "planned" if dry_run else "drained",
        "recovered": recovered,
        "actions": outcomes,
        "failed_actions": failed,
        "github_ci_polled": False,
        "merge_dispatched": False,
    }


def terminalize_failed_openclaw_actions(
    config: dict[str, Any], *, dry_run: bool = False
) -> list[dict[str, Any]]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        rows = connection.execute(
            """
            SELECT actions.action_id, actions.attempts, heads.repository,
              heads.pr_number, heads.base_sha, heads.head_sha, heads.review_epoch
            FROM actions
            JOIN heads
              ON heads.repository = actions.repository
             AND heads.pr_number = actions.pr_number
             AND heads.base_sha = actions.base_sha
             AND heads.head_sha = actions.head_sha
             AND heads.review_epoch = actions.review_epoch
             AND heads.is_current = 1
            WHERE actions.kind = 'openclaw.enqueue'
              AND actions.status = 'failed'
              AND heads.state = 'openclaw_queued'
            ORDER BY actions.created_at, actions.action_id
            """
        ).fetchall()
        outcomes = [
            {
                "action_id": row["action_id"],
                "pr_number": row["pr_number"],
                "attempts": row["attempts"],
                "result": "planned" if dry_run else "openclaw_failed",
            }
            for row in rows
        ]
        if dry_run or not rows:
            return outcomes
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            current = core.exact_current_head(
                connection,
                row["repository"],
                row["pr_number"],
                row["base_sha"],
                row["head_sha"],
            )
            if (
                current is None
                or current["state"] != "openclaw_queued"
                or int(current["review_epoch"]) != int(row["review_epoch"])
            ):
                continue
            core.update_exact_head(
                connection,
                {
                    "repository": row["repository"],
                    "pr_number": row["pr_number"],
                    "base_sha": row["base_sha"],
                    "head_sha": row["head_sha"],
                },
                state="openclaw_failed",
                rail="openclaw",
                blocker="OpenClaw enqueue adapter failed; operator recovery required",
            )
        connection.commit()
        return outcomes
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def bridge_receipt_path(artifact: Path) -> Path:
    suffix = ".terminal.json"
    if not artifact.name.endswith(suffix):
        raise RuntimeError("bridge inbox artifact must end in .terminal.json")
    return artifact.with_name(artifact.name[: -len(suffix)] + ".receipt.json")


def write_bridge_receipt(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    old_umask = os.umask(0o077)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        os.umask(old_umask)
        with contextlib.suppress(OSError):
            temporary.unlink()


def drain_bridge_inboxes(config: dict[str, Any]) -> dict[str, Any]:
    inboxes = {
        "openclaw": Path(config["spark"]["terminal_inbox"]),
        "clawsweeper": Path(config["clawsweeper_bridge"]["terminal_inbox"]),
    }
    outcomes: list[dict[str, Any]] = []
    for rail, inbox in inboxes.items():
        if not inbox.is_dir():
            outcomes.append({"rail": rail, "result": "inbox_unavailable"})
            continue
        for artifact in sorted(inbox.glob("*.terminal.json")):
            receipt_path = bridge_receipt_path(artifact)
            try:
                metadata = artifact.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_size > 1024 * 1024
                ):
                    outcomes.append({"rail": rail, "result": "artifact_shape_rejected"})
                    continue
                raw_artifact = artifact.read_bytes()
            except OSError:
                outcomes.append({"rail": rail, "result": "artifact_unavailable"})
                continue
            digest = hashlib.sha256(raw_artifact).hexdigest()
            if receipt_path.is_file():
                try:
                    receipt_metadata = receipt_path.lstat()
                    if (
                        not stat.S_ISREG(receipt_metadata.st_mode)
                        or stat.S_ISLNK(receipt_metadata.st_mode)
                        or receipt_metadata.st_size > 64 * 1024
                    ):
                        raise RuntimeError("bridge receipt shape is invalid")
                    prior = core.read_json(receipt_path, "bridge receipt")
                except (core.ContractError, OSError):
                    outcomes.append({"rail": rail, "result": "receipt_invalid"})
                    continue
                if prior.get("artifact_sha256") != digest:
                    outcomes.append({"rail": rail, "result": "receipt_artifact_mismatch"})
                    continue
                outcomes.append({"rail": rail, "result": "already_processed", "artifact_sha256": digest})
                continue
            try:
                if rail == "openclaw":
                    result = bridge_openclaw(config, artifact)
                else:
                    result = bridge_clawsweeper(config, artifact)
            except core.ContractError:
                outcomes.append({"rail": rail, "result": "blocked_pending_recovery", "artifact_sha256": digest})
                continue
            receipt = {
                "schema": "smoky.review-conductor.bridge-receipt.v1",
                "rail": rail,
                "result": result["result"],
                "artifact_sha256": digest,
                "processed_at": core.utc_now(),
                "merge_dispatched": False,
            }
            write_bridge_receipt(receipt_path, receipt)
            outcomes.append(receipt)
    return {
        "schema": "smoky.review-conductor.bridge-drain.v1",
        "result": "drained",
        "artifacts": outcomes,
        "agent_polling": False,
        "merge_dispatched": False,
    }


def proof_file(config: dict[str, Any], raw: Any, digest: Any) -> Path:
    path = require_absolute_path(raw, "terminal proof_ref")
    root = Path(config["paths"]["proof_root"]).resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("terminal proof_ref is unavailable") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise RuntimeError("terminal proof_ref must be a file under the external proof root")
    expected = core.require_text(digest, "terminal proof_sha256", 64)
    if not SHA256_RE.fullmatch(expected):
        raise RuntimeError("terminal proof_sha256 must be lowercase SHA-256")
    observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if observed != expected:
        raise RuntimeError("terminal proof digest does not match")
    return resolved


def original_report_output(config: dict[str, Any], terminal: dict[str, Any]) -> dict[str, Any]:
    """Reverify original output at bridge and publication time; no historical backfill."""
    report = core.validate_original_report(terminal["original_report"])
    if report["status"] != "available":
        return report
    request_id = core.require_text(terminal.get("request_id"), "original report request", 200)
    if core.SAFE_ID_RE.fullmatch(request_id) is None:
        raise RuntimeError("original report request identity is invalid")
    root = Path(config["paths"]["proof_root"]).resolve(strict=True)
    expected = root / "openclaw" / request_id / "review_output.txt"
    path = require_absolute_path(report["ref"], "original report ref")
    if path != expected:
        raise RuntimeError("original report path or size does not match the exact request")
    try:
        with contextlib.ExitStack() as stack:
            # Resolve only the configured root; bind descendants to opened directories.
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, directory_flags)
            stack.callback(os.close, directory)
            for component in ("openclaw", request_id):
                directory = os.open(component, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open("review_output.txt", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            stack.callback(os.close, fd)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > core.OPENCLAW_REPORT_MAX_BYTES:
                raise RuntimeError("original report path or size does not match the exact request")
            # One inode supplies shape, bound and bytes; growth is bounded independently.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(core.OPENCLAW_REPORT_MAX_BYTES + 1)
    except OSError as exc:
        raise RuntimeError("original report is unavailable") from exc
    if len(raw) > core.OPENCLAW_REPORT_MAX_BYTES or hashlib.sha256(raw).hexdigest() != report["sha256"]:
        raise RuntimeError("original report digest or size does not match")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError("original report is not UTF-8") from exc
    if not text.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise RuntimeError("original report is not publishable text")
    return {"status": "available", "sha256": report["sha256"], "text": text}


def read_terminal_artifact(path: Path, schema: str) -> tuple[dict[str, Any], str]:
    artifact = core.read_json(path, "terminal bridge artifact")
    if artifact.get("schema") != schema:
        raise RuntimeError("terminal bridge artifact schema is unsupported")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return artifact, digest


def bridge_openclaw(config: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    artifact, artifact_digest = read_terminal_artifact(artifact_path, OPENCLAW_ARTIFACT_SCHEMA)
    required = {
        "schema", "request_id", "operator_id", "status", "repository", "pr_number",
        "base_sha", "head_sha", "review_epoch", "review_clean", "review_finding_count",
        "reviewer_actor", "proof_ref", "proof_sha256",
    }
    policy = config.get("review_policy", {})
    if policy:
        required.update(
            {
                "review_scope",
                "native_max_priority",
                "applied_max_priority",
                "exact_tuple_qualified",
            }
        )
    core.require_exact_keys(artifact, required, {"original_report"}, "OpenClaw terminal artifact")
    if artifact["repository"] != config["github_app"]["repository"]:
        raise RuntimeError("terminal artifact belongs to another repository")
    if policy and (
        artifact.get("review_scope") != "comprehensive"
        or artifact["reviewer_actor"] != policy["reviewers"]["openclaw"]
        or not core.has_openclaw_applied_p3_qualification(artifact)
    ):
        raise RuntimeError(
            "terminal artifact has untrusted reviewer, non-comprehensive scope, or missing exact-tuple qualification"
        )
    if artifact["status"] not in {"completed", "needs-human", "failed"}:
        raise RuntimeError("OpenClaw queue submission or running state is not terminal")
    review_epoch = require_review_epoch(
        artifact["review_epoch"], "OpenClaw terminal review_epoch"
    )
    proof = proof_file(config, artifact["proof_ref"], artifact["proof_sha256"])
    if "original_report" in artifact:
        original_report_output(config, artifact)
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = core.exact_current_head(
            connection, artifact["repository"], artifact["pr_number"], artifact["base_sha"], artifact["head_sha"]
        )
        if current is None or int(current["review_epoch"]) != review_epoch:
            raise RuntimeError("OpenClaw terminal artifact is stale for the current exact tuple")
        action = core.tuple_action(connection, {
            "repository": artifact["repository"], "pr_number": artifact["pr_number"],
            "base_sha": artifact["base_sha"], "head_sha": artifact["head_sha"],
        }, "openclaw.enqueue", review_epoch)
        if action is None or action["status"] != "dispatched":
            raise RuntimeError("OpenClaw terminal artifact has no dispatched exact action")
        payload = json.loads(action["payload_json"])
        if payload["queue_request_id"] != artifact["request_id"]:
            raise RuntimeError("OpenClaw terminal request identity does not match")
        if artifact["operator_id"] != payload["operator_id"]:
            raise RuntimeError("OpenClaw terminal operator identity does not match")
        finding_count = artifact["review_finding_count"]
        if isinstance(finding_count, bool) or not isinstance(finding_count, int) or finding_count < 0:
            raise RuntimeError("OpenClaw terminal finding count is invalid")
        if artifact["status"] == "failed":
            result = "failed"
        elif artifact["status"] == "needs-human" and finding_count == 0:
            result = "human_gate"
        elif artifact["review_clean"] is True and finding_count == 0 and artifact["status"] == "completed":
            result = "clean"
        elif artifact["review_clean"] is False and finding_count > 0:
            result = "findings"
        else:
            raise RuntimeError("OpenClaw terminal verdict fields are ambiguous")
        event = {
            "schema": core.INTERNAL_EVENT_SCHEMA,
            "event_id": f"spark:{artifact['request_id']}:{artifact_digest[:16]}",
            "type": "openclaw.terminal",
            "repository": artifact["repository"],
            "pr_number": artifact["pr_number"],
            "base_sha": artifact["base_sha"],
            "head_sha": artifact["head_sha"],
            "request_id": artifact["request_id"],
            "result": result,
            "finding_count": finding_count,
            "reviewer_actor": artifact["reviewer_actor"],
            "proof_ref": str(proof),
            "proof_sha256": artifact["proof_sha256"],
            "artifact_digest": artifact_digest,
            "review_epoch": artifact["review_epoch"],
        }
        if "original_report" in artifact:
            event["original_report"] = core.validate_original_report(artifact["original_report"])
        if config.get("review_policy"):
            event["review_scope"] = artifact["review_scope"]
            event["review_epoch"] = artifact["review_epoch"]
            event["native_max_priority"] = artifact["native_max_priority"]
            event["applied_max_priority"] = artifact["applied_max_priority"]
            event["exact_tuple_qualified"] = artifact["exact_tuple_qualified"]
        event = core.validate_internal_event(core.load_config(Path(config["core_config"])), event)
        prior = connection.execute("SELECT payload_json FROM events WHERE event_id = ?", (event["event_id"],)).fetchone()
        if prior is not None:
            if prior["payload_json"] != core.canonical_json(event):
                raise RuntimeError("OpenClaw terminal artifact identity was reused")
            connection.rollback()
            return {"result": "duplicate_event", "event_id": event["event_id"], "merge_dispatched": False}
        outcome = core.process_internal_event(
            connection, core.load_config(Path(config["core_config"])), event
        )
        connection.commit()
        return {**outcome, "artifact_sha256": artifact_digest, "transport_receipt_only": False}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def recover_draft_openclaw_handoff(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    current: sqlite3.Row,
    terminal_event: dict[str, Any],
) -> str | None:
    """Repair only a proven retained draft OpenClaw ID, inside terminal acceptance.

    Called after the native artifact, dispatched action and workflow are qualified.
    A running/native request ID is never replaced. Failure later in the bridge
    transaction rolls this normalization back with the terminal acceptance.
    """
    retained = current["review_request_id"]
    if (not config.get("review_policy") or not retained
            or current["state"] != "clawsweeper_queued"
            or current["rail"] != "clawsweeper" or current["is_draft"]):
        return None
    identity = {key: current[key] for key in ("repository", "pr_number", "base_sha", "head_sha")}
    enqueue = core.tuple_action(connection, identity, "openclaw.enqueue", current["review_epoch"])
    if (enqueue is None or enqueue["status"] != "dispatched"
            or json.loads(enqueue["payload_json"]).get("queue_request_id") != retained):
        return None
    candidates = connection.execute(
        "SELECT sequence, payload_json FROM events WHERE kind='openclaw.terminal' "
        "AND repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND stale=0 "
        "ORDER BY sequence DESC", tuple(identity.values()),
    )
    for candidate in candidates:
        payload = json.loads(candidate["payload_json"])
        if type(payload.get("review_epoch")) is not int or payload["review_epoch"] != current["review_epoch"]:
            continue
        # Revalidate the persisted authoritative event rather than infer from ID shape.
        core.validate_internal_event(config, payload)
        if (any(payload[key] != value for key, value in identity.items())
                or payload["request_id"] != retained or payload["result"] != "clean"):
            return None
        ready = connection.execute(
            "SELECT 1 FROM events WHERE kind='pull_request.ready_for_review' "
            "AND repository=? AND pr_number=? AND base_sha=? AND head_sha=? "
            "AND stale=0 AND sequence>? LIMIT 1",
            (*identity.values(), candidate["sequence"]),
        ).fetchone()
        if ready is None:
            return None
        core.update_exact_head(connection, identity, review_request_id=None)
        core.insert_event(
            connection, event_id=terminal_event["event_id"] + ":draft-handoff",
            kind="clawsweeper.draft_handoff_recovered", stale=False, **identity,
            payload={"review_epoch": current["review_epoch"], "prior_request_id": retained,
                     "workflow_run_id": terminal_event["workflow_run_id"],
                     "terminal_event_id": terminal_event["event_id"]},
        )
        return retained
    return None


def bridge_clawsweeper(config: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    core.require_enabled({"review_policy": config.get("review_policy", {})})
    artifact, artifact_digest = read_terminal_artifact(artifact_path, CLAWSWEEPER_ARTIFACT_SCHEMA)
    required = {
        "schema", "workflow_run_id", "repository", "pr_number", "base_sha", "head_sha",
        "review_epoch", "verdict", "finding_count", "reviewer_actor", "proof_ref", "proof_sha256",
    }
    policy = config.get("review_policy", {})
    if policy:
        required.add("review_scope")
    core.require_exact_keys(artifact, required, set(), "ClawSweeper terminal artifact")
    if artifact["repository"] != config["github_app"]["repository"]:
        raise RuntimeError("terminal artifact belongs to another repository")
    if policy and (artifact.get("review_scope") != "comprehensive" or artifact["reviewer_actor"] != policy["reviewers"]["clawsweeper"]):
        raise RuntimeError("terminal artifact has untrusted reviewer or non-comprehensive scope")
    if artifact["verdict"] not in core.TERMINAL_RESULTS:
        raise RuntimeError("ClawSweeper terminal verdict is unsupported")
    review_epoch = require_review_epoch(
        artifact["review_epoch"], "ClawSweeper terminal review_epoch"
    )
    proof = proof_file(config, artifact["proof_ref"], artifact["proof_sha256"])
    run_id = str(artifact["workflow_run_id"])
    finding_count = artifact["finding_count"]
    if isinstance(finding_count, bool) or not isinstance(finding_count, int) or finding_count < 0:
        raise RuntimeError("ClawSweeper terminal finding_count is invalid")
    if artifact["verdict"] == "clean" and finding_count != 0:
        raise RuntimeError("ClawSweeper clean verdict must have zero findings")
    if artifact["verdict"] == "findings" and finding_count == 0:
        raise RuntimeError("ClawSweeper findings verdict must have findings")
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        identity = {
            "repository": artifact["repository"], "pr_number": artifact["pr_number"],
            "base_sha": artifact["base_sha"], "head_sha": artifact["head_sha"],
        }
        current = core.exact_current_head(connection, **identity)
        if current is None or int(current["review_epoch"]) != review_epoch:
            raise RuntimeError("ClawSweeper verdict is stale for the current exact tuple")
        action = core.tuple_action(connection, identity, "clawsweeper.dispatch", review_epoch)
        if action is None or action["status"] != "dispatched":
            raise RuntimeError("ClawSweeper verdict has no dispatched exact-tuple action")
        rail_run = connection.execute(
            "SELECT * FROM rail_workflow_runs WHERE rail = 'clawsweeper' AND workflow_run_id = ?",
            (run_id,),
        ).fetchone()
        if rail_run is None:
            raise RuntimeError("ClawSweeper verdict does not match one unbound terminal workflow_run")
        if rail_run["status"] == "verdict_ingested":
            matches = (
                rail_run["bound_repository"] == identity["repository"]
                and rail_run["bound_pr_number"] == identity["pr_number"]
                and rail_run["bound_base_sha"] == identity["base_sha"]
                and rail_run["bound_head_sha"] == identity["head_sha"]
                and rail_run["bound_review_epoch"] == review_epoch
                and rail_run["verdict"] == artifact["verdict"]
                and rail_run["proof_ref"] == str(proof)
            )
            if matches:
                connection.rollback()
                return {
                    "schema": "smoky.review-conductor.receipt.v1",
                    "result": "duplicate_event",
                    "workflow_run_id": run_id,
                    "artifact_sha256": artifact_digest,
                    "verdict_inferred": False,
                    "merge_dispatched": False,
                }
            raise RuntimeError("ClawSweeper workflow_run was already bound to different terminal proof")
        if rail_run["status"] != "terminal_pending_verdict_bridge":
            raise RuntimeError("ClawSweeper workflow_run is not bridgeable")
        if artifact["verdict"] in {"clean", "findings", "human_gate"} and rail_run["conclusion"] != "success":
            raise RuntimeError("non-success ClawSweeper workflow cannot carry a clean/findings verdict")
        event = {
            "schema": core.INTERNAL_EVENT_SCHEMA,
            "event_id": f"clawsweeper:{run_id}:{artifact_digest[:16]}",
            "type": "clawsweeper.terminal",
            **identity,
            "workflow_run_id": run_id,
            "result": artifact["verdict"],
            "finding_count": finding_count,
            "reviewer_actor": artifact["reviewer_actor"],
            "proof_ref": str(proof),
            "proof_sha256": artifact["proof_sha256"],
        }
        if config.get("review_policy"):
            event["review_scope"] = artifact["review_scope"]
            event["review_epoch"] = artifact["review_epoch"]
        core_config = core.load_config(Path(config["core_config"]))
        event = core.validate_internal_event(core_config, event)
        recovered = recover_draft_openclaw_handoff(connection, core_config, current, event)
        outcome = core.process_internal_event(
            connection, core_config, event
        )
        connection.execute(
            """
            UPDATE rail_workflow_runs SET status = 'verdict_ingested',
              bound_repository = ?, bound_pr_number = ?, bound_base_sha = ?, bound_head_sha = ?,
              bound_review_epoch = ?, verdict = ?, proof_ref = ?
            WHERE rail = 'clawsweeper' AND workflow_run_id = ?
              AND status = 'terminal_pending_verdict_bridge'
            """,
            (
                identity["repository"], identity["pr_number"], identity["base_sha"],
                identity["head_sha"], review_epoch, artifact["verdict"],
                str(proof), run_id,
            ),
        )
        connection.commit()
        return {**outcome, "artifact_sha256": artifact_digest, "verdict_inferred": False,
                "draft_handoff_recovered": recovered is not None}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def write_wake(path: Path, reason: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = core.canonical_json(
        {"schema": "smoky.review-conductor.wake.v1", "reason": reason, "at": core.utc_now()}
    ) + "\n"
    old_umask = os.umask(0o077)
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        os.umask(old_umask)
        with contextlib.suppress(OSError):
            temporary.unlink()


def handle_webhook_request(
    config: dict[str, Any], *, method: str, path: str, headers: dict[str, str], body: bytes,
    secret: str, ingestor: Callable[..., dict[str, Any]] | None = None,
    reconciler: Callable[..., dict[str, Any]] | None = None,
    reconciliation_path: str | None = None,
) -> tuple[int, dict[str, Any]]:
    ingress = config["ingress"]
    if method != "POST":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "reason": "method_not_allowed"}
    is_reconciliation = reconciliation_path is not None and path == reconciliation_path
    if not is_reconciliation and path != ingress["path"]:
        return HTTPStatus.NOT_FOUND, {"ok": False, "reason": "not_found"}
    if is_reconciliation and reconciler is None:
        return HTTPStatus.NOT_FOUND, {"ok": False, "reason": "not_found"}
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "reason": "unsupported_content_type"}
    if len(body) > ingress["max_body_bytes"]:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "reason": "body_too_large"}
    try:
        if is_reconciliation:
            receipt = reconciler(
                config_path=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                headers=headers,
                body=body,
                secret=secret,
            )
            write_wake(Path(config["paths"]["action_wake"]), "github-workflow-readback")
            write_wake(Path(config["paths"]["projection_wake"]), "github-workflow-readback")
            return HTTPStatus.ACCEPTED, {
                "ok": True,
                "schema": "smoky.review-conductor.workflow-readback-receipt.v1",
                "result": receipt["result"],
                "workflow_run_id": receipt["run_id"],
                "source": receipt["source"],
                "merge_dispatched": False,
            }
        event_type = headers.get("x-github-event", "")
        delivery_id = headers.get("x-github-delivery", "")
        signature = headers.get("x-hub-signature-256", "")
        receipt = (ingestor or core.ingest_github_delivery)(
            config_path=Path(config["core_config"]),
            state_root=Path(config["paths"]["state_root"]),
            event_type=event_type,
            delivery_id=delivery_id,
            signature=signature,
            body=body,
            secret=secret,
        )
        write_wake(Path(config["paths"]["action_wake"]), "github-delivery")
        write_wake(Path(config["paths"]["projection_wake"]), "github-delivery")
        return HTTPStatus.ACCEPTED, {
            "ok": True,
            "schema": "smoky.review-conductor.http-receipt.v1",
            "delivery_id": receipt["delivery_id"],
            "event_type": receipt["event_type"],
            "result": receipt["result"],
            "merge_dispatched": False,
        }
    except RetryableIngestError:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "reason": "dependency_unavailable"}
    except core.ContractError:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "reason": "request_rejected"}
    except sqlite3.Error:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "reason": "state_unavailable"}


def build_http_handler(
    config: dict[str, Any], secret: str,
    *, ingestor: Callable[..., dict[str, Any]] | None = None,
    reconciler: Callable[..., dict[str, Any]] | None = None,
    reconciliation_path: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SmokyReviewConductor/1"
        sys_version = ""

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _write(self, status: int, payload: dict[str, Any]) -> None:
            encoded = core.canonical_json(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:  # noqa: N802
            self.connection.settimeout(config["ingress"]["request_timeout_seconds"])
            raw_length = self.headers.get("Content-Length", "")
            try:
                length = int(raw_length)
            except ValueError:
                self._write(HTTPStatus.BAD_REQUEST, {"ok": False, "reason": "invalid_content_length"})
                return
            if length < 0 or length > config["ingress"]["max_body_bytes"]:
                self._write(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "reason": "body_too_large"})
                return
            try:
                body = self.rfile.read(length)
            except (TimeoutError, socket.timeout, OSError):
                self._write(HTTPStatus.REQUEST_TIMEOUT, {"ok": False, "reason": "request_timeout"})
                return
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, payload = handle_webhook_request(
                config, method="POST", path=self.path, headers=headers, body=body,
                secret=secret, ingestor=ingestor, reconciler=reconciler,
                reconciliation_path=reconciliation_path,
            )
            self._write(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._write(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "reason": "method_not_allowed"})

        do_PUT = do_GET
        do_PATCH = do_GET
        do_DELETE = do_GET

    return Handler


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *args: Any,
        max_clients: int = 8,
        request_timeout_seconds: int,
        **kwargs: Any,
    ) -> None:
        self._capacity = threading.BoundedSemaphore(max_clients)
        self._request_timeout_seconds = request_timeout_seconds
        super().__init__(*args, **kwargs)

    def server_bind(self) -> None:
        # Ingress readiness must not wait on reverse DNS (macOS mDNS).
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host or "127.0.0.1"
        self.server_port = port

    def get_request(self) -> tuple[Any, Any]:
        request, client_address = super().get_request()
        request.settimeout(self._request_timeout_seconds)
        return request, client_address

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._capacity.release()
            raise

    def shutdown_request(self, request: Any) -> None:
        try:
            super().shutdown_request(request)
        finally:
            self._capacity.release()


def component(state: str, reason: str) -> dict[str, str]:
    return {"state": state, "reason": reason}
