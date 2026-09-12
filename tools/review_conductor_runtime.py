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
import json
import os
import re
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
from typing import Any, Callable


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402


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
APP_EVENTS = ["pull_request", "workflow_run"]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
API_VERSION = "2022-11-28"
# GitHub statuses that describe a transient upstream condition rather than a
# rejected operation; callers may retry without changing the request.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
FIXED_GIT = Path("/usr/bin/git")



class RuntimeError(core.ContractError):
    """Activation adapter input, state, or policy is invalid."""


class GitHubApiError(RuntimeError):
    """A fixed GitHub API operation failed without exposing response material."""


class GitHubTransientError(GitHubApiError):
    """The transport or GitHub itself failed transiently; the same call may be retried."""


class RetryableIngestError(Exception):
    """A dependency failed transiently before any state was written.

    Deliberately not a ContractError: the delivery was neither malformed nor
    foreign, so the HTTP edge answers 503 and leaves the delivery redeliverable
    from GitHub's delivery log or API (GitHub does not retry automatically).
    """


def assert_authority(client: Any, operation: str) -> None:
    """Run the client's admission authority guard before a non-GitHub side effect.

    Worker phases that act outside the GitHub client (OpenClaw dispatch,
    notifications) call this so the same per-side-effect fence applies to them.
    Clients without a guard (legacy route, dry runs) are unaffected.
    """
    check = getattr(client, "assert_authority", None)
    if check is not None:
        check(operation)


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


Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]
ArtifactTransport = Callable[[str, dict[str, str], float], tuple[int, bytes]]
ArtifactRequest = Callable[
    [str, dict[str, str], float], tuple[int, dict[str, str], bytes]
]
ARTIFACT_MAX_BYTES = 1024 * 1024
ARTIFACT_BLOB_HOST_RE = re.compile(
    r"^productionresultssa[0-9]+\.blob\.core\.windows\.net$", re.IGNORECASE
)


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""
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
        or ARTIFACT_BLOB_HOST_RE.fullmatch(target.hostname) is None
        or target.username is not None
        or target.password is not None
        or target_port not in {None, 443}
        or not target.path.startswith("/")
        or not target.query
        or target.fragment
    ):
        raise GitHubApiError("GitHub artifact redirect target is outside the fixed allowlist")
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
        self._artifact_transport = (
            urllib_artifact_transport
            if artifact_transport is None and transport is urllib_transport
            else artifact_transport
            or (lambda url, headers, timeout: transport("GET", url, headers, None, timeout))
        )
        self._clock = clock
        self._signer = signer or sign_app_jwt
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = threading.Lock()
        self._authority_guard: Callable[[str, str], None] | None = None

    def set_authority_guard(self, guard: Callable[[str, str], None] | None) -> None:
        """Install a check that runs before every mutating GitHub call.

        The service uses it to re-validate current admission (registry, reviewer
        actors, profile digest) immediately before each side effect of a tick, so
        a revocation or profile edit after the tick's opening gate cannot leave
        the remainder of that tick dispatching or publishing.
        """
        self._authority_guard = guard

    def assert_authority(self, operation: str) -> None:
        """Run the installed guard for a side effect that is not a GitHub call."""
        if self._authority_guard is not None:
            self._authority_guard("SIDE_EFFECT", operation)

    @property
    def repository(self) -> str:
        return self._app["repository"]

    def _allow(self, method: str, path: str) -> str:
        repository = re.escape(self.repository)
        rules = (
            ("POST", rf"/app/installations/{self._app['installation_id']}/access_tokens", "installation-token"),
            ("POST", rf"/repos/{repository}/check-runs", "check-create"),
            ("PATCH", rf"/repos/{repository}/check-runs/[1-9][0-9]*", "check-update"),
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
    ) -> dict[str, Any]:
        _operation, raw = self._call_raw(
            method,
            path,
            payload,
            expected=expected,
            app_jwt=app_jwt,
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
    ) -> tuple[str, bytes]:
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
            self._authority_guard(method, path)
        status, raw = self._transport(
            method, self._app["api_base"] + path, headers, body, 15.0
        )
        if status not in expected:
            if status in TRANSIENT_STATUSES:
                raise GitHubTransientError(
                    f"allowlisted GitHub API operation failed transiently ({operation})"
                )
            raise GitHubApiError(f"allowlisted GitHub API operation failed ({operation})")
        return operation, raw

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

    def create_check(self, name: str, head_sha: str, external_id: str, state: str) -> int:
        if name not in CHECK_NAMES:
            raise GitHubApiError("check name is outside the fixed allowlist")
        payload = check_payload(name, head_sha, external_id, state)
        response = self._call(
            "POST",
            f"/repos/{self.repository}/check-runs",
            payload,
            expected={201},
        )
        return core.require_positive_int(response.get("id"), "GitHub check run id")

    def update_check(self, check_id: int, name: str, head_sha: str, external_id: str, state: str) -> None:
        if name not in CHECK_NAMES:
            raise GitHubApiError("check name is outside the fixed allowlist")
        self._call(
            "PATCH",
            f"/repos/{self.repository}/check-runs/{check_id}",
            check_payload(name, head_sha, external_id, state),
            expected={200},
        )

    def add_ready_label(self, pr_number: int) -> None:
        self._call(
            "POST",
            f"/repos/{self.repository}/issues/{pr_number}/labels",
            {"labels": [READY_LABEL]},
            expected={200},
        )

    def remove_ready_label(self, pr_number: int) -> None:
        encoded = urllib.parse.quote(READY_LABEL, safe="")
        self._call(
            "DELETE",
            f"/repos/{self.repository}/issues/{pr_number}/labels/{encoded}",
            None,
            expected={200, 204, 404},
        )

    def dispatch_clawsweeper(
        self, *, pr_number: int, base_sha: str, head_sha: str, publish: bool, review_epoch: int | None = None
    ) -> None:
        core.require_sha(base_sha, "ClawSweeper dispatch base_sha")
        core.require_sha(head_sha, "ClawSweeper dispatch head_sha")
        if publish is not True:
            raise GitHubApiError("ClawSweeper dispatch publish must remain true")
        extra = {}
        if self._strict_adapter:
            require_review_epoch(review_epoch, "dispatch review epoch")
            extra = {"review_epoch": str(review_epoch), "repository": self.repository, "review_scope": "comprehensive"}
        self._call(
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
            },
            expected={204},
        )

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


def check_payload(name: str, head_sha: str, external_id: str, state: str) -> dict[str, Any]:
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
    if state == "queued":
        return {
            "name": name,
            "head_sha": head_sha,
            "external_id": external_id,
            "status": "queued",
            "output": {
                "title": name,
                "summary": "Waiting for the exact prerequisite or Review Rail dispatch.",
            },
        }
    if state == "in_progress":
        return {
            "name": name,
            "head_sha": head_sha,
            "external_id": external_id,
            "status": "in_progress",
            "output": {"title": name, "summary": "The exact-head Review Rail is running."},
        }
    summaries = {
        "success": "The exact-head Review Rail completed cleanly.",
        "failure": "The exact-head Review Rail reached a terminal failure.",
        "action_required": "The exact-head Review Rail requires adjudication or recovery.",
        "skipped": "This rail was not dispatched because an exact prerequisite did not succeed or the tuple was superseded.",
        "cancelled": "This exact tuple was cancelled because the pull request closed.",
    }
    return {
        "name": name,
        "head_sha": head_sha,
        "external_id": external_id,
        "status": "completed",
        "conclusion": state,
        "output": {"title": name, "summary": summaries[state]},
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


def reconcile_superseded_projection(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    client: GitHubAppClient | Any,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    item = {
        "pr_number": row["pr_number"],
        "base_sha": row["base_sha"],
        "head_sha": row["head_sha"],
        "review_epoch": row["review_epoch"],
        "visible_state": "superseded",
        "checks": {name: "skipped" for name in CHECK_NAMES},
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
                "skipped",
            )
        elif row[create_state_column] == "creating":
            raise RuntimeError(
                f"superseded {name} creation outcome is uncertain and requires reconciliation"
            )
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
        query = "SELECT * FROM heads WHERE repository = ? AND is_current = 1"
        params: list[Any] = [core_config["repository"]]
        if pr_number is not None:
            query += " AND pr_number = ?"
            params.append(pr_number)
        query += " ORDER BY pr_number"
        rows = connection.execute(query, params).fetchall()
        for row in rows:
            projection = ensure_projection_row(connection, row)
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
            for name, check_state in state["checks"].items():
                column, create_state_column = check_columns[name]
                check_id = projection[column]
                external_id = projection_external_id(row, name)
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
                    check_id = client.create_check(name, row["head_sha"], external_id, check_state)
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
                else:
                    client.update_check(check_id, name, row["head_sha"], external_id, check_state)
            desired = bool(state["ready_for_human_label"])
            label_action = item["ready_label_action"]
            if label_action == "ensure_present":
                client.add_ready_label(row["pr_number"])
            elif label_action in {
                "ensure_absent",
                "ensure_absent_before_clawsweeper_publication",
            }:
                client.remove_ready_label(row["pr_number"])
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
        try:
            client.remove_ready_label(action["pr_number"])
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
            client.dispatch_clawsweeper(
                pr_number=payload["pr_number"],
                base_sha=payload["base_sha"],
                head_sha=payload["head_sha"],
                publish=payload["publish"],
                **({"review_epoch": payload["review_epoch"]} if config.get("review_policy") else {}),
            )
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


@contextlib.contextmanager
def service_transport_environment(config: dict[str, Any]):
    allowed = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "PYTHONUNBUFFERED": "1",
        "SMOKY_REVIEW_CONDUCTOR_SMOKY": config["spark"]["smoky_path"],
        "SMOKY_SPARK_SSH_BIN": config["spark"]["ssh_path"],
        "SMOKY_SPARK_SCP_BIN": config["spark"]["scp_path"],
        "SPARK_OPENCLAW_MATERIALIZE_TARGET": config["spark"]["target"],
        "SPARK_OPENCLAW_AUTOREVIEW_TARGET": config["spark"]["target"],
    }
    previous = os.environ.copy()
    os.environ.clear()
    os.environ.update(allowed)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


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
            WHERE actions.kind IN ('openclaw.enqueue', 'clawsweeper.dispatch')
              AND actions.status = 'pending'
              AND (
                (actions.kind = 'openclaw.enqueue' AND heads.state = 'openclaw_queued')
                OR
                (actions.kind = 'clawsweeper.dispatch' AND heads.state = 'clawsweeper_queued')
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
        if action["kind"] == "openclaw.enqueue":
            args = argparse.Namespace(
                config=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                action_id=action["action_id"],
                source_checkout=Path(config["paths"]["blocks_checkout"]),
                apply=True,
                retry=action["status"] == "failed",
                claim_owner="cp1-worker",
                claim_lease_seconds=config["worker"]["claim_lease_seconds"],
                before_external_command=lambda index, _command, action_id=action["action_id"]: assert_authority(
                    client, f"openclaw.enqueue:{action_id}:step:{index + 1}"
                ),
            )
            # Each OpenClaw dispatch is an external side effect; fence it like a
            # GitHub write so revoked authority stops the whole drain here instead
            # of being recorded as an adapter rejection of this one action.
            assert_authority(client, f"openclaw.enqueue:{action['action_id']}")
            try:
                with service_transport_environment(config):
                    outcomes.append(core.dispatch_action(args))
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
        required.add("review_scope")
    core.require_exact_keys(artifact, required, set(), "OpenClaw terminal artifact")
    if artifact["repository"] != config["github_app"]["repository"]:
        raise RuntimeError("terminal artifact belongs to another repository")
    if policy and (artifact.get("review_scope") != "comprehensive" or artifact["reviewer_actor"] != policy["reviewers"]["openclaw"]):
        raise RuntimeError("terminal artifact has untrusted reviewer or non-comprehensive scope")
    if artifact["status"] not in {"completed", "needs-human", "failed"}:
        raise RuntimeError("OpenClaw queue submission or running state is not terminal")
    review_epoch = require_review_epoch(
        artifact["review_epoch"], "OpenClaw terminal review_epoch"
    )
    proof = proof_file(config, artifact["proof_ref"], artifact["proof_sha256"])
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
        }
        if config.get("review_policy"):
            event["review_scope"] = artifact["review_scope"]
            event["review_epoch"] = artifact["review_epoch"]
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
        }
        if config.get("review_policy"):
            event["review_scope"] = artifact["review_scope"]
            event["review_epoch"] = artifact["review_epoch"]
        event = core.validate_internal_event(core.load_config(Path(config["core_config"])), event)
        outcome = core.process_internal_event(
            connection, core.load_config(Path(config["core_config"])), event
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
        return {**outcome, "artifact_sha256": artifact_digest, "verdict_inferred": False}
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
) -> tuple[int, dict[str, Any]]:
    ingress = config["ingress"]
    if method != "POST":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "reason": "method_not_allowed"}
    if path != ingress["path"]:
        return HTTPStatus.NOT_FOUND, {"ok": False, "reason": "not_found"}
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "reason": "unsupported_content_type"}
    if len(body) > ingress["max_body_bytes"]:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "reason": "body_too_large"}
    event_type = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "")
    signature = headers.get("x-hub-signature-256", "")
    try:
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
                secret=secret, ingestor=ingestor,
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
