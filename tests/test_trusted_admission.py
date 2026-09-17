"""Fail-closed enrollment, approved-policy loading and tuple/epoch binding."""
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import trusted_admission as ta

BLOCKS = "dinkuskit/blocks"
SMCBD = "saari-co/openclaw-smcbd-suite"
# Synthetic commits/App installations only; never live credentials.
BLOCKS_COMMIT = "a" * 40
SMCBD_COMMIT = "b" * 40
BASE = "c" * 40
HEAD = "d" * 40
BLOCKS_INSTALL = 1001
SMCBD_INSTALL = 2002
BLOCKS_APP = 3003
SMCBD_APP = 4004


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Fixture:
    """In-memory policy source keyed by (repository, commit); records reads."""
    def __init__(self):
        self.blocks = (ROOT / "examples/blocks.review-conductor.json").read_bytes()
        self.smcbd = (ROOT / "examples/smcbd.review-conductor.json").read_bytes()
        self.store = {(BLOCKS, BLOCKS_COMMIT): self.blocks, (SMCBD, SMCBD_COMMIT): self.smcbd}
        self.reads = []

    def read(self, repository, commit):
        self.reads.append((repository, commit))
        return self.store.get((repository, commit), b"")

    def registry_doc(self):
        return {"schema": ta.REGISTRY_SCHEMA, "enrollments": [
            {"repository": BLOCKS, "repository_id": 1306882611,
             "github_app": {"id": BLOCKS_APP, "installation_id": BLOCKS_INSTALL,
                            "installation_account": "dinkuskit"},
             "approved_policy": {"commit": BLOCKS_COMMIT, "sha256": sha(self.blocks)},
             "reviewers": {"openclaw": "blocks-openclaw", "clawsweeper": "blocks-clawsweeper"}},
            {"repository": SMCBD, "repository_id": 1366416798,
             "github_app": {"id": SMCBD_APP, "installation_id": SMCBD_INSTALL,
                            "installation_account": "saari-co"},
             "approved_policy": {"commit": SMCBD_COMMIT, "sha256": sha(self.smcbd)},
             "reviewers": {"openclaw": "smcbd-openclaw", "clawsweeper": "smcbd-clawsweeper"}}]}

    def registry(self, doc=None):
        return ta.load_registry(json.dumps(doc or self.registry_doc()).encode())


def request(**overrides):
    value = {"repository": BLOCKS, "repository_id": 1306882611, "app_id": BLOCKS_APP,
             "installation_id": BLOCKS_INSTALL,
             "pr_number": 7, "base_sha": BASE, "head_sha": HEAD, "review_epoch": 0,
             "policy_commit": BLOCKS_COMMIT}
    value.update(overrides)
    return value


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def test_registry_scope_is_exactly_two_repositories(self):
        registry = self.fx.registry()
        self.assertEqual({e.repository for e in registry.enrollments}, {BLOCKS, SMCBD})
        self.assertEqual(set(ta.INITIAL_ENROLLMENT_SCOPE), {BLOCKS, SMCBD})
        doc = self.fx.registry_doc()
        for name in ["saari-co/review-conductor", "dinkuskit/blocks-fork", "Dinkuskit/blocks",
                     "saari-co/x-api", "attacker/openclaw-smcbd-suite"]:
            item = copy.deepcopy(doc["enrollments"][0]); item["repository"] = name
            with self.subTest(name=name), self.assertRaises(ta.AdmissionError):
                self.fx.registry({**doc, "enrollments": [item]})

    def test_numeric_identity_and_installation_ownership_fail_closed(self):
        doc = self.fx.registry_doc()
        bad = []
        item = copy.deepcopy(doc["enrollments"][0]); item["repository_id"] = 1366416798; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["repository_id"] = "1306882611"; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["repository_id"] = True; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["installation_account"] = "saari-co"; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["id"] = 0; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["id"] = True; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["installation_id"] = 0; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["installation_id"] = None; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["approved_policy"]["commit"] = "A" * 40; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["approved_policy"]["commit"] = "main"; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["approved_policy"]["sha256"] = "f" * 63; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["credentials"] = "vault://x"; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); item["github_app"]["token"] = "x"; bad.append(item)
        item = copy.deepcopy(doc["enrollments"][0]); del item["approved_policy"]; bad.append(item)
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ta.AdmissionError):
                self.fx.registry({**doc, "enrollments": [item]})

    def test_duplicate_identities_and_malformed_documents_fail_closed(self):
        doc = self.fx.registry_doc()
        dup_repo = {**doc, "enrollments": [doc["enrollments"][0], doc["enrollments"][0]]}
        shared_install = copy.deepcopy(doc); shared_install["enrollments"][1]["github_app"]["installation_id"] = BLOCKS_INSTALL
        empty_reviewer = copy.deepcopy(doc); empty_reviewer["enrollments"][0]["reviewers"]["openclaw"] = ""
        duplicate_reviewer = copy.deepcopy(doc); duplicate_reviewer["enrollments"][0]["reviewers"]["clawsweeper"] = duplicate_reviewer["enrollments"][0]["reviewers"]["openclaw"]
        legacy_v1 = {**doc, "schema": "review-conductor.enrollment.v1"}
        for candidate in [dup_repo, shared_install, empty_reviewer, duplicate_reviewer, legacy_v1,
                          {**doc, "schema": "review-conductor.enrollment.v3"},
                          {**doc, "enrollments": {}}, {**doc, "extra": 1}, {"schema": ta.REGISTRY_SCHEMA}]:
            with self.subTest(candidate=candidate), self.assertRaises(ta.AdmissionError):
                self.fx.registry(candidate)
        for raw in [b'{"schema":1,"schema":2}', b"\xff", b"[" * 3000, b" " * 65537,
                    json.dumps(doc).encode("utf-16"), "not bytes"]:
            with self.subTest(raw=raw[:20]), self.assertRaises(ta.AdmissionError):
                ta.load_registry(raw)

    def test_omitted_legacy_xapi_is_absent_for_every_scoped_profile(self):
        registry = self.fx.registry()
        self.assertIsNone(registry.legacy_xapi)
        self.assertEqual(registry.legacy_status_for(BLOCKS), "absent")
        self.assertEqual(registry.legacy_status_for(SMCBD), "absent")
        empty = self.fx.registry({"schema": ta.REGISTRY_SCHEMA, "enrollments": []})
        self.assertIsNone(empty.legacy_xapi)
        self.assertEqual(empty.legacy_status_for(BLOCKS), "absent")

    def test_legacy_xapi_marker_is_exact_profile_status(self):
        doc = self.fx.registry_doc()
        doc["legacy_xapi"] = {"repository": SMCBD, "status": "present"}
        registry = self.fx.registry(doc)
        self.assertEqual(registry.legacy_status_for(SMCBD), "present")
        self.assertEqual(registry.legacy_status_for(BLOCKS), "absent")
        doc["legacy_xapi"] = {"repository": BLOCKS, "status": "absent"}
        explicit_absent = self.fx.registry(doc)
        self.assertEqual(explicit_absent.legacy_status_for(BLOCKS), "absent")
        self.assertEqual(explicit_absent.legacy_status_for(SMCBD), "absent")

    def test_legacy_xapi_malformed_forms_fail_closed(self):
        doc = self.fx.registry_doc()
        bad = [
            {**doc, "legacy_xapi": "present"},
            {**doc, "legacy_xapi": True},
            {**doc, "legacy_xapi": 1},
            {**doc, "legacy_xapi": None},
            {**doc, "legacy_xapi": []},
            {**doc, "legacy_xapi": {}},
            {**doc, "legacy_xapi": {"status": "present"}},
            {**doc, "legacy_xapi": {"repository": SMCBD}},
            {**doc, "legacy_xapi": {"repository": SMCBD, "status": "present", "extra": 1}},
            {**doc, "legacy_xapi": {"repository": "saari-co/x-api", "status": "present"}},
            {**doc, "legacy_xapi": {"repository": SMCBD, "status": "broken"}},
            {**doc, "legacy_xapi": {"repository": SMCBD, "status": "Present"}},
            {**doc, "legacy_xapi": {"repository": SMCBD, "status": True}},
            {**doc, "legacy_xapi": {"repository": SMCBD, "status": None}},
        ]
        enrollment_marker = copy.deepcopy(doc)
        enrollment_marker["enrollments"][0]["legacy_xapi"] = {"repository": BLOCKS, "status": "present"}
        bad.append(enrollment_marker)
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(ta.AdmissionError):
                self.fx.registry(candidate)
        with self.assertRaises(ta.AdmissionError):
            ta.Registry((), legacy_xapi="present")
        with self.assertRaises(ta.AdmissionError):
            ta.LegacyXapiMarker("saari-co/x-api", "present")
        with self.assertRaises(ta.AdmissionError):
            ta.LegacyXapiMarker(SMCBD, "broken")

    def test_lookup_requires_every_identity_component(self):
        registry = self.fx.registry()
        self.assertEqual(registry.lookup(BLOCKS, 1306882611, BLOCKS_APP, BLOCKS_INSTALL).repository, BLOCKS)
        for args in [(SMCBD, 1306882611, BLOCKS_APP, BLOCKS_INSTALL), (BLOCKS, 1366416798, BLOCKS_APP, BLOCKS_INSTALL),
                     (BLOCKS, 1306882611, SMCBD_APP, BLOCKS_INSTALL), (BLOCKS, 1306882611, BLOCKS_APP, SMCBD_INSTALL),
                     (BLOCKS, "1306882611", BLOCKS_APP, BLOCKS_INSTALL),
                     (BLOCKS, 1306882611, str(BLOCKS_APP), BLOCKS_INSTALL),
                     (BLOCKS, 1306882611, BLOCKS_APP, str(BLOCKS_INSTALL)),
                     ("dinkuskit/Blocks", 1306882611, BLOCKS_APP, BLOCKS_INSTALL),
                     (None, 1306882611, BLOCKS_APP, BLOCKS_INSTALL), ("saari-co/x-api", 1, 1, 1)]:
            with self.subTest(args=args), self.assertRaises(ta.AdmissionError):
                registry.lookup(*args)


class PolicyLoadingTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.registry = self.fx.registry()
        self.blocks = self.registry.lookup(BLOCKS, 1306882611, BLOCKS_APP, BLOCKS_INSTALL)

    def test_loads_only_the_approved_commit_and_hash(self):
        policy = ta.load_approved_policy(self.blocks, BLOCKS_COMMIT, self.fx.read)
        self.assertEqual((policy.repository, policy.repository_id, policy.commit, policy.sha256),
                         (BLOCKS, 1306882611, BLOCKS_COMMIT, sha(self.fx.blocks)))
        self.assertIs(policy.clawsweeper_requires_ready, True)
        self.assertEqual(self.fx.reads, [(BLOCKS, BLOCKS_COMMIT)])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.clawsweeper_requires_ready = False

    def test_unapproved_commit_is_refused_before_transport(self):
        for commit in [HEAD, BASE, SMCBD_COMMIT, "e" * 40, BLOCKS_COMMIT.upper(), None, ""]:
            with self.subTest(commit=commit), self.assertRaises(ta.AdmissionError):
                ta.load_approved_policy(self.blocks, commit, self.fx.read)
        self.assertEqual(self.fx.reads, [])

    def test_mutated_or_swapped_content_at_approved_commit_is_refused(self):
        candidates = {
            "whitespace": self.fx.blocks + b"\n",
            "semantically-equal-reserialized": json.dumps(json.loads(self.fx.blocks)).encode(),
            "other-repository-policy": self.fx.smcbd,
            "empty": b"",
            "oversized": self.fx.blocks + b" " * 20000,
            "not-bytes": self.fx.blocks.decode(),
        }
        for label, content in candidates.items():
            self.fx.store[(BLOCKS, BLOCKS_COMMIT)] = content
            with self.subTest(label=label), self.assertRaises(ta.AdmissionError):
                ta.load_approved_policy(self.blocks, BLOCKS_COMMIT, self.fx.read)

    def test_policy_reader_failures_use_the_public_admission_error_contract(self):
        failures = [TimeoutError("transport unavailable"), OSError("connection reset"),
                    RuntimeError("reader failed")]
        for failure in failures:
            def broken_reader(*_args, failure=failure):
                raise failure

            with self.subTest(failure=type(failure).__name__), \
                    self.assertRaises(ta.AdmissionError) as ctx:
                ta.load_approved_policy(self.blocks, BLOCKS_COMMIT, broken_reader)
            self.assertIs(ctx.exception.__cause__, failure)
            self.assertEqual(str(ctx.exception), "approved policy content is unavailable")

    def test_hash_matching_but_invalid_or_misnamed_manifest_is_refused(self):
        renamed = json.loads(self.fx.smcbd); renamed["repository"] = BLOCKS
        raw = json.dumps(renamed).encode()
        enrollment = dataclasses.replace(self.blocks, approved_policy_sha256=sha(raw))
        self.fx.store[(BLOCKS, BLOCKS_COMMIT)] = raw
        self.assertEqual(ta.load_approved_policy(enrollment, BLOCKS_COMMIT, self.fx.read).repository, BLOCKS)
        for content in [self.fx.smcbd, b'{"schema":"review-conductor.target.v1"}']:
            enrollment = dataclasses.replace(self.blocks, approved_policy_sha256=sha(content))
            self.fx.store[(BLOCKS, BLOCKS_COMMIT)] = content
            with self.subTest(content=content[:40]), self.assertRaises(ta.AdmissionError):
                ta.load_approved_policy(enrollment, BLOCKS_COMMIT, self.fx.read)

    def test_policy_identity_is_deterministic_and_content_bound(self):
        one = ta.load_approved_policy(self.blocks, BLOCKS_COMMIT, self.fx.read)
        two = ta.load_approved_policy(self.blocks, BLOCKS_COMMIT, Fixture().read)
        self.assertEqual(one.policy_id, two.policy_id)
        self.assertEqual(one.policy_id, ta.policy_identity(BLOCKS, 1306882611, BLOCKS_COMMIT, sha(self.fx.blocks)))
        self.assertRegex(one.policy_id, r"^[0-9a-f]{64}$")
        variants = {ta.policy_identity(BLOCKS, 1306882611, "e" * 40, sha(self.fx.blocks)),
                    ta.policy_identity(BLOCKS, 1306882611, BLOCKS_COMMIT, sha(self.fx.blocks + b"\n")),
                    ta.policy_identity(SMCBD, 1306882611, BLOCKS_COMMIT, sha(self.fx.blocks)),
                    ta.policy_identity(BLOCKS, 1366416798, BLOCKS_COMMIT, sha(self.fx.blocks)),
                    one.policy_id}
        self.assertEqual(len(variants), 5)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.registry = self.fx.registry()

    def test_admission_binds_policy_to_exact_tuple_and_epoch(self):
        self.assertEqual(ta.BINDING_SCHEMA, "review-conductor.admission-binding.v2")
        admission = ta.admit(self.registry, request(), self.fx.read)
        self.assertEqual(dataclasses.astuple(admission.review), (BLOCKS, 1306882611, 7, BASE, HEAD, 0))
        self.assertEqual(admission.installation_id, BLOCKS_INSTALL)
        self.assertEqual(admission.app_id, BLOCKS_APP)
        self.assertEqual(admission.policy.commit, BLOCKS_COMMIT)
        again = ta.admit(self.registry, request(), Fixture().read)
        self.assertEqual(admission.binding_id, again.binding_id)
        self.assertRegex(admission.binding_id, r"^[0-9a-f]{64}$")
        changed = [request(head_sha="e" * 40), request(base_sha="e" * 40), request(review_epoch=1),
                   request(pr_number=8)]
        ids = {ta.admit(self.registry, item, self.fx.read).binding_id for item in changed}
        self.assertEqual(len(ids | {admission.binding_id}), 5)
        # Identical review tuple and policy under a different valid App must not
        # collide: the App id is part of the canonical binding identity.
        other_app = dataclasses.replace(admission, app_id=BLOCKS_APP + 1)
        self.assertEqual((other_app.review, other_app.policy.policy_id), (admission.review, admission.policy.policy_id))
        self.assertNotEqual(other_app.binding_id, admission.binding_id)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            admission.review.head_sha = BASE
        self.assertTrue(ta.policy_is_current(admission, self.registry))

    def test_cross_org_and_cross_repo_isolation(self):
        smcbd = ta.admit(self.registry, request(repository=SMCBD, repository_id=1366416798,
                                                app_id=SMCBD_APP, installation_id=SMCBD_INSTALL,
                                                policy_commit=SMCBD_COMMIT), self.fx.read)
        self.assertEqual(smcbd.policy.repository, SMCBD)
        for item in [request(app_id=SMCBD_APP), request(installation_id=SMCBD_INSTALL),
                     request(repository=SMCBD, installation_id=SMCBD_INSTALL),
                     request(repository=SMCBD, repository_id=1366416798),
                     request(repository_id=1366416798),
                     request(policy_commit=SMCBD_COMMIT),
                     request(repository=SMCBD, repository_id=1366416798, app_id=SMCBD_APP,
                             installation_id=SMCBD_INSTALL),
                     request(repository="dinkuskit/blocks-fork"),
                     request(repository="saari-co/review-conductor", repository_id=1, app_id=1, installation_id=1)]:
            with self.subTest(item=item), self.assertRaises(ta.AdmissionError):
                ta.admit(self.registry, item, self.fx.read)
        self.assertFalse(ta.policy_is_current(smcbd, self.fx.registry({"schema": ta.REGISTRY_SCHEMA, "enrollments": [
            self.fx.registry_doc()["enrollments"][0]]})))

    def test_unknown_installation_and_mismatched_ids_fail_closed(self):
        for item in [request(app_id=SMCBD_APP), request(app_id=0), request(app_id=True),
                     request(installation_id=3003), request(installation_id=-BLOCKS_INSTALL),
                     request(installation_id=str(BLOCKS_INSTALL)), request(installation_id=True),
                     request(repository_id=str(1306882611)), request(repository_id=1306882612),
                     request(repository_id=1306882611.0), request(pr_number=0), request(pr_number="7"),
                     request(review_epoch=-1), request(review_epoch="0"), request(review_epoch=False),
                     request(base_sha=HEAD), request(head_sha=HEAD.upper()), request(head_sha=HEAD[:39])]:
            with self.subTest(item=item), self.assertRaises(ta.AdmissionError):
                ta.admit(self.registry, item, self.fx.read)
        for item in [{**request(), "reviewers": ["x"]}, {**request(), "policy": {"quiet_seconds": 0}},
                     {k: v for k, v in request().items() if k != "app_id"},
                     {k: v for k, v in request().items() if k != "installation_id"}, [], None]:
            with self.subTest(item=item), self.assertRaises(ta.AdmissionError):
                ta.admit(self.registry, item, self.fx.read)
        self.assertEqual(self.fx.reads, [])

    def test_pr_head_manifest_mutation_cannot_self_promote(self):
        mutated = json.loads(self.fx.blocks)
        mutated["ci"]["workflow_name"] = "attacker-controlled"
        self.fx.store[(BLOCKS, HEAD)] = json.dumps(mutated).encode()
        for commit in [HEAD, BASE]:
            with self.subTest(commit=commit), self.assertRaises(ta.AdmissionError) as ctx:
                ta.admit(self.registry, request(policy_commit=commit), self.fx.read)
            self.assertNotIn("attacker", str(ctx.exception))
        self.assertEqual(self.fx.reads, [])
        admission = ta.admit(self.registry, request(), self.fx.read)
        self.assertEqual(admission.policy.sha256, sha(self.fx.blocks))
        self.assertEqual(self.fx.reads, [(BLOCKS, BLOCKS_COMMIT)])

    def test_promoted_policy_makes_in_flight_binding_stale(self):
        admission = ta.admit(self.registry, request(), self.fx.read)
        doc = self.fx.registry_doc()
        promoted = self.fx.blocks + b"\n"
        doc["enrollments"][0]["approved_policy"] = {"commit": "e" * 40, "sha256": sha(promoted)}
        self.fx.store[(BLOCKS, "e" * 40)] = promoted
        new_registry = self.fx.registry(doc)
        self.assertFalse(ta.policy_is_current(admission, new_registry))
        with self.assertRaises(ta.AdmissionError):
            ta.admit(new_registry, request(), self.fx.read)
        fresh = ta.admit(new_registry, request(policy_commit="e" * 40), self.fx.read)
        self.assertNotEqual(fresh.binding_id, admission.binding_id)
        self.assertNotEqual(fresh.policy.policy_id, admission.policy.policy_id)
        self.assertTrue(ta.policy_is_current(fresh, new_registry))
        self.assertFalse(ta.policy_is_current(fresh, self.registry))
        self.assertFalse(ta.policy_is_current(None, new_registry))
        same_commit_new_hash = self.fx.registry_doc()
        same_commit_new_hash["enrollments"][0]["approved_policy"]["sha256"] = sha(promoted)
        self.assertFalse(ta.policy_is_current(admission, self.fx.registry(same_commit_new_hash)))

    def test_equivalent_direct_construction_preserves_binding_identity(self):
        admitted = ta.admit(self.registry, request(), self.fx.read)
        reconstructed = ta.Admission(dataclasses.replace(admitted.review), admitted.app_id,
                                     admitted.installation_id, dataclasses.replace(admitted.policy))
        self.assertIsNot(admitted, reconstructed)
        self.assertEqual(admitted.binding_id, reconstructed.binding_id)

    def test_directly_constructed_incoherent_objects_fail_closed(self):
        good = ta.admit(self.registry, request(), self.fx.read)
        smcbd_policy = ta.admit(self.registry, request(repository=SMCBD, repository_id=1366416798,
                                                       app_id=SMCBD_APP, installation_id=SMCBD_INSTALL,
                                                       policy_commit=SMCBD_COMMIT),
                                self.fx.read).policy
        # Constructor invariants: mismatched repository/ID between tuple and policy, bad installation.
        for args in [(good.review, BLOCKS_APP, BLOCKS_INSTALL, smcbd_policy),
                     (good.review, 0, BLOCKS_INSTALL, good.policy),
                     (good.review, BLOCKS_APP, 0, good.policy),
                     (good.review, "3003", BLOCKS_INSTALL, good.policy),
                     (good.review, BLOCKS_APP, "1001", good.policy),
                     (good.review, True, BLOCKS_INSTALL, good.policy),
                     ("dinkuskit/blocks", BLOCKS_APP, BLOCKS_INSTALL, good.policy),
                     (good.review, BLOCKS_APP, BLOCKS_INSTALL, {"commit": BLOCKS_COMMIT})]:
            with self.subTest(args=args), self.assertRaises(ta.AdmissionError):
                ta.Admission(*args)
        for kwargs in [{"repository": SMCBD}, {"repository": []},
                       {"repository_id": 1366416798}, {"repository": "attacker/blocks"},
                       {"repository_id": "1306882611"}, {"commit": HEAD.upper()}, {"sha256": "z" * 64},
                       {"clawsweeper_requires_ready": False}, {"clawsweeper_requires_ready": 1},
                       {"default_branch": ""}, {"default_branch": "forged"},
                       {"manifest_bytes": good.policy.manifest_bytes + b"\n"},
                       {"manifest_bytes": good.policy.manifest_bytes.decode()}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ta.AdmissionError):
                dataclasses.replace(good.policy, **kwargs)
        for kwargs in [{"repository": SMCBD}, {"repository": []},
                       {"repository_id": 1366416798}, {"app_id": 0}, {"installation_id": 0},
                       {"installation_account": "saari-co"}, {"approved_policy_commit": "main"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ta.AdmissionError):
                dataclasses.replace(self.registry.enrollments[0], **kwargs)
        with self.assertRaises(ta.AdmissionError):
            ta.Registry((self.registry.enrollments[0], self.registry.enrollments[0]))
        with self.assertRaises(ta.AdmissionError):
            ta.Registry(("dinkuskit/blocks",))
        # Crafted objects that bypass __post_init__ must yield neither currency nor a binding.
        crafted = []
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "policy", smcbd_policy); crafted.append(item)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "app_id", "3003"); crafted.append(item)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "installation_id", "1001"); crafted.append(item)
        review = dataclasses.replace(good.review)
        object.__setattr__(review, "repository", SMCBD)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "review", review); crafted.append(item)
        review = dataclasses.replace(good.review)
        object.__setattr__(review, "head_sha", review.base_sha)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "review", review); crafted.append(item)
        policy = dataclasses.replace(good.policy)
        object.__setattr__(policy, "repository_id", 1366416798)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "policy", policy); crafted.append(item)
        policy = dataclasses.replace(good.policy)
        object.__setattr__(policy, "clawsweeper_requires_ready", False)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "policy", policy); crafted.append(item)
        policy = dataclasses.replace(good.policy)
        object.__setattr__(policy, "default_branch", "forged")
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "policy", policy); crafted.append(item)
        item = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(item, "review", None); crafted.append(item)
        for item in crafted:
            with self.subTest(item=item):
                self.assertFalse(ta.policy_is_current(item, self.registry))
                with self.assertRaises(ta.AdmissionError):
                    item.binding_id
                with self.assertRaises(ta.AdmissionError):
                    item.revalidate()
        # A well-formed object bound to the other account's installation is only detectable
        # against the registry: never current, and it cannot be re-admitted.
        borrowed = ta.Admission(good.review, BLOCKS_APP, BLOCKS_INSTALL, good.policy)
        object.__setattr__(borrowed, "installation_id", SMCBD_INSTALL)
        self.assertFalse(ta.policy_is_current(borrowed, self.registry))
        with self.assertRaises(ta.AdmissionError):
            ta.admit(self.registry, request(installation_id=borrowed.installation_id), self.fx.read)
        self.assertNotEqual(borrowed.binding_id, good.binding_id)
        self.assertTrue(ta.policy_is_current(good, self.registry))
        self.assertEqual(good.revalidate(), good)
        # A policy that is genuinely approved for SMCBD is still not current for a Blocks tuple.
        self.assertFalse(ta.policy_is_current(crafted[0], self.registry))

    def test_library_is_inert_in_packaged_scaffold_and_cli(self):
        for name in ["tools/conductor_cli.py", "scripts/build.py", "bin/review-conductor"]:
            self.assertNotIn("trusted_admission", (ROOT / name).read_text(), name)
        source = (ROOT / "tools/trusted_admission.py").read_text()
        for token in ["import os", "import sqlite3", "import subprocess", "import urllib", "import socket",
                      "open(", "environ"]:
            self.assertNotIn(token, source, token)


if __name__ == "__main__":
    unittest.main()
