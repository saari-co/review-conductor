"""Service-owned enrollment registry, approved base-policy loading and binding.

Source-only library. Nothing here reads credentials, talks to GitHub, opens a
database, or is reachable from the packaged CLI. The service supplies the
registry bytes and a policy reader; a reviewed repository supplies nothing but
the manifest bytes that the registry has already approved by commit and hash.
Every mismatch raises AdmissionError before any admission value is produced.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from target_manifest import MAX_BYTES, unique_object, validate_manifest

REGISTRY_SCHEMA = "review-conductor.enrollment.v2"
POLICY_IDENTITY_SCHEMA = "review-conductor.policy-identity.v1"
BINDING_SCHEMA = "review-conductor.admission-binding.v2"
MAX_REGISTRY_BYTES = 65536
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Declared registry marker only. Runtime "broken" is derived, never a document value.
LEGACY_XAPI_STATUSES = frozenset({"present", "absent"})
# The initial enrollment decision covers exactly these repositories with the
# numeric identities recorded in the historical profiles. Any other name or
# any other numeric identity for these names fails closed. Expanding this map
# is a separate explicit owner enrollment decision, not a registry edit.
INITIAL_ENROLLMENT_SCOPE = {
    "dinkuskit/blocks": 1306882611,
    "saari-co/openclaw-smcbd-suite": 1366416798,
}


class AdmissionError(ValueError):
    """Fail-closed admission failure; the message names the check, never data."""


def _fail(reason):
    raise AdmissionError(reason)


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(f"invalid {label} keys")


def _positive_int(value, label):
    if type(value) is not int or value <= 0:
        _fail(f"{label} must be a positive integer")
    return value


def _exact_str(value, label):
    if type(value) is not str:
        _fail(f"{label} must be an exact string")
    return value


def _sha1(value, label):
    if type(value) is not str or not SHA1_RE.fullmatch(value):
        _fail(f"{label} must be a 40-character lowercase hex commit")
    return value


def _sha256(value, label):
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        _fail(f"{label} must be a 64-character lowercase hex digest")
    return value


def _actor(value, label):
    if type(value) is not str or not value or len(value) > 200 or value.strip() != value:
        _fail(f"{label} must be a non-empty bounded actor identity")
    return value


@dataclass(frozen=True)
class Enrollment:
    repository: str
    repository_id: int
    app_id: int
    installation_id: int
    installation_account: str
    approved_policy_commit: str
    approved_policy_sha256: str
    reviewer_openclaw: str
    reviewer_clawsweeper: str

    def __post_init__(self):
        repository = _exact_str(self.repository, "repository")
        if repository not in INITIAL_ENROLLMENT_SCOPE:
            _fail("repository is outside the initial enrollment scope")
        if type(self.repository_id) is not int or self.repository_id != INITIAL_ENROLLMENT_SCOPE[repository]:
            _fail("enrollment repository_id contradicts the recorded numeric identity")
        _positive_int(self.app_id, "GitHub App id")
        _positive_int(self.installation_id, "installation id")
        account = _exact_str(self.installation_account, "installation account")
        if account != repository.split("/", 1)[0]:
            _fail("installation account must own the enrolled repository")
        _sha1(self.approved_policy_commit, "approved policy commit")
        _sha256(self.approved_policy_sha256, "approved policy sha256")
        _actor(self.reviewer_openclaw, "OpenClaw reviewer actor")
        _actor(self.reviewer_clawsweeper, "ClawSweeper reviewer actor")
        if self.reviewer_openclaw == self.reviewer_clawsweeper:
            _fail("reviewer actors must be distinct")

    @property
    def reviewers(self):
        """Authoritative reviewer actors in the engine's review_policy shape."""
        return {"openclaw": self.reviewer_openclaw, "clawsweeper": self.reviewer_clawsweeper}


@dataclass(frozen=True)
class LegacyXapiMarker:
    """Exact-profile legacy x-api marker loaded from the v2 registry document."""

    repository: str
    status: str

    def __post_init__(self):
        repository = _exact_str(self.repository, "legacy_xapi repository")
        if repository not in INITIAL_ENROLLMENT_SCOPE:
            _fail("legacy_xapi repository is outside the initial enrollment scope")
        status = _exact_str(self.status, "legacy_xapi status")
        if status not in LEGACY_XAPI_STATUSES:
            _fail("legacy_xapi status is unknown")


@dataclass(frozen=True)
class Registry:
    enrollments: tuple
    legacy_xapi: object = None

    def __post_init__(self):
        if not isinstance(self.enrollments, tuple) or not all(isinstance(e, Enrollment) for e in self.enrollments):
            _fail("registry enrollments must be Enrollment values")
        names = [e.repository for e in self.enrollments]
        ids = [e.repository_id for e in self.enrollments]
        installations = [e.installation_id for e in self.enrollments]
        if len(set(names)) != len(names) or len(set(ids)) != len(ids) or len(set(installations)) != len(installations):
            _fail("enrollment identities must be unique across the registry")
        if self.legacy_xapi is not None and not isinstance(self.legacy_xapi, LegacyXapiMarker):
            _fail("registry legacy_xapi must be a validated marker or omitted")

    def legacy_status_for(self, repository):
        """Return the loaded legacy marker for one exact service profile.

        The marker is absent when the v2 field is omitted or names another
        profile. Reads the dataclass field only; subclass attributes and
        properties cannot grant a status. The profile repository must be an
        exact admitted-scope string before omitted-marker absence is treated
        as legitimate. Malformed stored markers fail closed.
        """
        if type(repository) is not str or repository not in INITIAL_ENROLLMENT_SCOPE:
            _fail("legacy_xapi profile repository is required")
        stored = object.__getattribute__(self, "__dict__")
        marker = stored.get("legacy_xapi") if isinstance(stored, dict) else None
        if marker is None:
            return "absent"
        if not isinstance(marker, LegacyXapiMarker):
            _fail("registry legacy_xapi is malformed")
        if marker.repository != repository:
            return "absent"
        if type(marker.status) is not str or marker.status not in LEGACY_XAPI_STATUSES:
            _fail("registry legacy_xapi is malformed")
        return marker.status

    def lookup(self, repository, repository_id, app_id, installation_id):
        """Resolve one enrollment; every identity component must agree."""
        if type(repository) is not str:
            _fail("repository name is required")
        match = [
            e
            for e in self.enrollments
            if type(e.repository) is str and e.repository == repository
        ]
        if not match:
            _fail("repository is not enrolled")
        enrollment = match[0]
        if (
            type(repository_id) is not int
            or type(enrollment.repository_id) is not int
            or repository_id != enrollment.repository_id
        ):
            _fail("repository numeric identity does not match enrollment")
        if (
            type(app_id) is not int
            or type(enrollment.app_id) is not int
            or app_id != enrollment.app_id
        ):
            _fail("GitHub App does not match enrollment")
        if (
            type(installation_id) is not int
            or type(enrollment.installation_id) is not int
            or installation_id != enrollment.installation_id
        ):
            _fail("installation does not match enrollment")
        return enrollment


def load_registry(raw):
    """Parse a service-owned registry document. Never a reviewed-repo file."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_REGISTRY_BYTES:
        _fail("registry document exceeds 65536 bytes or is not bytes")
    try:
        value = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise AdmissionError("registry document is not strict UTF-8 JSON") from exc
    registry_keys = ["schema", "enrollments"]
    if isinstance(value, dict) and "legacy_xapi" in value:
        registry_keys = ["schema", "enrollments", "legacy_xapi"]
    _exact(value, registry_keys, "registry")
    if value["schema"] != REGISTRY_SCHEMA:
        _fail("unsupported registry schema")
    if not isinstance(value["enrollments"], list):
        _fail("registry enrollments must be a list")
    enrollments = []
    for item in value["enrollments"]:
        _exact(item, ["repository", "repository_id", "github_app", "approved_policy", "reviewers"], "enrollment")
        repository = _exact_str(item["repository"], "repository")
        if repository not in INITIAL_ENROLLMENT_SCOPE:
            _fail("repository is outside the initial enrollment scope")
        repository_id = _positive_int(item["repository_id"], "enrollment repository_id")
        github_app = item["github_app"]
        _exact(github_app, ["id", "installation_id", "installation_account"], "GitHub App")
        policy = item["approved_policy"]
        _exact(policy, ["commit", "sha256"], "approved policy")
        reviewers = item["reviewers"]
        _exact(reviewers, ["openclaw", "clawsweeper"], "reviewers")
        enrollments.append(Enrollment(repository, repository_id, github_app["id"], github_app["installation_id"], github_app["installation_account"],
                                      policy["commit"], policy["sha256"], reviewers["openclaw"], reviewers["clawsweeper"]))
    return Registry(tuple(enrollments), _load_legacy_xapi(value))


def _load_legacy_xapi(value):
    """Parse the optional exact-profile legacy marker. Omitted means absent."""
    if "legacy_xapi" not in value:
        return None
    marker = value["legacy_xapi"]
    _exact(marker, ["repository", "status"], "legacy_xapi")
    repository = _exact_str(marker["repository"], "legacy_xapi repository")
    if repository not in INITIAL_ENROLLMENT_SCOPE:
        _fail("legacy_xapi repository is outside the initial enrollment scope")
    status = _exact_str(marker["status"], "legacy_xapi status")
    if status not in LEGACY_XAPI_STATUSES:
        _fail("legacy_xapi status is unknown")
    return LegacyXapiMarker(repository, status)


@dataclass(frozen=True)
class AdmittedPolicy:
    repository: str
    repository_id: int
    commit: str
    sha256: str
    clawsweeper_requires_ready: bool
    default_branch: str
    manifest_bytes: bytes

    def __post_init__(self):
        if not isinstance(self.repository, str) or self.repository not in INITIAL_ENROLLMENT_SCOPE:
            _fail("policy repository is outside the initial enrollment scope")
        if type(self.repository_id) is not int or self.repository_id != INITIAL_ENROLLMENT_SCOPE[self.repository]:
            _fail("policy repository_id contradicts the recorded numeric identity")
        _sha1(self.commit, "policy commit")
        _sha256(self.sha256, "policy sha256")
        if type(self.manifest_bytes) is not bytes or len(self.manifest_bytes) > MAX_BYTES:
            _fail("policy manifest bytes are unavailable or oversized")
        if hashlib.sha256(self.manifest_bytes).hexdigest() != self.sha256:
            _fail("policy manifest bytes do not match the admitted digest")
        try:
            manifest = validate_manifest(self.manifest_bytes)
        except ValueError as exc:
            raise AdmissionError("policy manifest bytes are not a valid manifest") from exc
        if manifest["repository"] != self.repository:
            _fail("policy manifest bytes name a different repository")
        if self.clawsweeper_requires_ready is not True:
            _fail("policy must require ready state before ClawSweeper")
        if not isinstance(self.default_branch, str) or not self.default_branch:
            _fail("policy default_branch is required")
        if self.clawsweeper_requires_ready != manifest["review"]["clawsweeper_requires_ready"]:
            _fail("policy readiness rule contradicts the approved manifest bytes")
        if self.default_branch != manifest["default_branch"]:
            _fail("policy default_branch contradicts the approved manifest bytes")

    @property
    def policy_id(self):
        return policy_identity(self.repository, self.repository_id, self.commit, self.sha256)


def policy_identity(repository, repository_id, commit, sha256):
    """Deterministic identity of one approved policy version."""
    canonical = json.dumps(
        {"schema": POLICY_IDENTITY_SCHEMA, "repository": repository,
         "repository_id": repository_id, "commit": commit, "sha256": sha256},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_approved_policy(enrollment, commit, read_policy):
    """Load the manifest only from the approved commit and only if its hash matches.

    ``read_policy(repository, commit)`` is service-owned transport returning the
    raw manifest bytes at that commit. A PR head, a newer default-branch commit
    or any other commit is refused before transport is consulted.
    """
    if not isinstance(enrollment, Enrollment):
        _fail("enrollment is required")
    if commit != enrollment.approved_policy_commit:
        _fail("policy commit is not the approved base commit")
    try:
        raw = read_policy(enrollment.repository, commit)
    except Exception as exc:
        raise AdmissionError("approved policy content is unavailable") from exc
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_BYTES:
        _fail("approved policy content is unavailable or oversized")
    raw = bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != enrollment.approved_policy_sha256:
        _fail("approved policy content hash does not match enrollment")
    try:
        manifest = validate_manifest(raw)
    except ValueError as exc:
        raise AdmissionError("approved policy content is not a valid manifest") from exc
    if manifest["repository"] != enrollment.repository:
        _fail("approved policy names a different repository")
    return AdmittedPolicy(enrollment.repository, enrollment.repository_id, commit, digest,
                          manifest["review"]["clawsweeper_requires_ready"],
                          manifest["default_branch"], raw)


@dataclass(frozen=True)
class ReviewTuple:
    repository: str
    repository_id: int
    pr_number: int
    base_sha: str
    head_sha: str
    review_epoch: int

    def __post_init__(self):
        if not isinstance(self.repository, str) or "/" not in self.repository:
            _fail("review tuple repository must be owner/name")
        _positive_int(self.repository_id, "review tuple repository_id")
        _positive_int(self.pr_number, "review tuple pr_number")
        _sha1(self.base_sha, "review tuple base_sha")
        _sha1(self.head_sha, "review tuple head_sha")
        if self.base_sha == self.head_sha:
            _fail("review tuple base and head must differ")
        if type(self.review_epoch) is not int or self.review_epoch < 0:
            _fail("review tuple review_epoch must be a non-negative integer")


@dataclass(frozen=True)
class Admission:
    review: ReviewTuple
    app_id: int
    installation_id: int
    policy: AdmittedPolicy

    def __post_init__(self):
        if not isinstance(self.review, ReviewTuple) or not isinstance(self.policy, AdmittedPolicy):
            _fail("admission requires a validated review tuple and admitted policy")
        _positive_int(self.app_id, "admission app_id")
        _positive_int(self.installation_id, "admission installation_id")
        if (self.review.repository != self.policy.repository
                or self.review.repository_id != self.policy.repository_id):
            _fail("admission review tuple and policy identify different repositories")

    def revalidate(self):
        """Re-run every invariant; a crafted object bypassing __post_init__ fails here."""
        try:
            review = ReviewTuple(*(getattr(self.review, f) for f in ReviewTuple.__dataclass_fields__))
            policy = AdmittedPolicy(*(getattr(self.policy, f) for f in AdmittedPolicy.__dataclass_fields__))
            Admission(review, self.app_id, self.installation_id, policy)
        except (AttributeError, TypeError) as exc:
            raise AdmissionError("admission object is malformed") from exc
        return self

    @property
    def binding_id(self):
        self.revalidate()
        canonical = json.dumps(
            {"schema": BINDING_SCHEMA, "repository": self.review.repository,
             "repository_id": self.review.repository_id, "pr_number": self.review.pr_number,
             "base_sha": self.review.base_sha, "head_sha": self.review.head_sha,
             "review_epoch": self.review.review_epoch, "app_id": self.app_id,
             "installation_id": self.installation_id,
             "policy_id": self.policy.policy_id},
            sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def admit(registry, request, read_policy):
    """Bind the approved policy to one exact review tuple.

    ``request`` is an untrusted mapping with exactly: repository, repository_id,
    app_id, installation_id, pr_number, base_sha, head_sha, review_epoch,
    policy_commit.
    The registry decides which policy applies; the request cannot choose it.
    """
    if not isinstance(registry, Registry):
        _fail("registry is required")
    _exact(request, ["repository", "repository_id", "app_id", "installation_id", "pr_number",
                     "base_sha", "head_sha", "review_epoch", "policy_commit"], "admission request")
    enrollment = registry.lookup(request["repository"], request["repository_id"], request["app_id"], request["installation_id"])
    review = ReviewTuple(enrollment.repository, enrollment.repository_id, request["pr_number"],
                         request["base_sha"], request["head_sha"], request["review_epoch"])
    if request["policy_commit"] == review.head_sha and review.head_sha != enrollment.approved_policy_commit:
        _fail("PR head cannot promote its own policy")
    policy = load_approved_policy(enrollment, request["policy_commit"], read_policy)
    return Admission(review, enrollment.app_id, enrollment.installation_id, policy)


def policy_is_current(admission, registry):
    """True only while the registry still approves exactly the bound policy."""
    if not isinstance(admission, Admission) or not isinstance(registry, Registry):
        return False
    try:
        admission.revalidate()
        enrollment = registry.lookup(admission.review.repository, admission.review.repository_id,
                                     admission.app_id, admission.installation_id)
    except AdmissionError:
        return False
    return (enrollment.repository == admission.policy.repository
            and enrollment.repository_id == admission.policy.repository_id
            and enrollment.app_id == admission.app_id
            and enrollment.installation_id == admission.installation_id
            and enrollment.approved_policy_commit == admission.policy.commit
            and enrollment.approved_policy_sha256 == admission.policy.sha256)
