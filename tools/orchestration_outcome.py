#!/usr/bin/env python3
"""Canonical Review Conductor orchestration-outcome decision.

Source-only library. One versioned function interprets enrollment, rail
results, the saturating two-cycle automatic-repair ledger, and terminal
engine state into a structured dispatch/notification decision. The
existing notification queue consumes this outcome; this is not a second
notification system and it does not import, invoke, or dispatch x-api.
Representable fail-closed states suppress routing and keep
legacy_dispatch false, but they remain eligible for the blocked
notification path. Impossible state/rail/result tuples, including mismatched rails,
fail closed before any enrollment-route short-circuit, dispatch, or
notification eligibility is calculated. Closed-state handling stays
first. The persisted-row adapter applies the same rail-aware
validation and maps inconsistent stored state/rail data to typed
unknown results; direct public contract inputs remain strict.
"""

from __future__ import annotations

from typing import Any, Mapping


INPUT_SCHEMA = "review-conductor.orchestration-input.v1"
OUTCOME_SCHEMA = "review-conductor.orchestration-outcome.v1"
MAX_REPAIR_CYCLES = 2

ENROLLMENT_STATUSES = frozenset({"present", "absent", "broken"})
ROUTES = frozenset({"review_conductor", "legacy_xapi", "none", "fail_closed"})
NOTIFICATION_ELIGIBILITIES = frozenset(
    {"silent", "merge_ready", "blocked", "none"}
)
RAIL_RESULTS = frozenset(
    {"absent", "clean", "effectively_clean", "findings", "human_gate", "failed", "unknown"}
)
RAILS = frozenset({"openclaw", "clawsweeper"})
DISPOSITIONS = frozenset(
    {"required_fix", "defer", "reject_false_positive", "human_gate"}
)
READY_COMPATIBLE_DISPOSITIONS = frozenset({"defer", "reject_false_positive"})
KNOWN_STATES = frozenset(
    {
        "ci_running",
        "ci_failed",
        "openclaw_queued",
        "openclaw_running",
        "openclaw_failed",
        "openclaw_clean_draft",
        "clawsweeper_queued",
        "clawsweeper_running",
        "clawsweeper_failed",
        "clawsweeper_clean_draft",
        "awaiting_adjudication",
        "repair_required",
        "waiting_human",
        "ready_for_human_merge",
        "closed",
        "closed_merged",
    }
)
SILENT_INTERNAL_STATES = frozenset({"awaiting_adjudication", "repair_required"})
BLOCKED_STATES = frozenset(
    {"ci_failed", "openclaw_failed", "clawsweeper_failed", "waiting_human"}
)
TERMINAL_CLOSED_STATES = frozenset({"closed", "closed_merged"})
NONTERMINAL_STATES = frozenset(
    {
        "ci_running",
        "openclaw_queued",
        "openclaw_running",
        "openclaw_clean_draft",
        "clawsweeper_queued",
        "clawsweeper_running",
        "clawsweeper_clean_draft",
    }
)
NOTIFY_CHANNELS = ("openclaw_context", "discord", "signal")
INPUT_KEYS = (
    "schema",
    "enrollment",
    "state",
    "rail",
    "repair_cycle",
    "head_changed",
    "openclaw_result",
    "clawsweeper_result",
    "ready_qualified",
    "adjudication_dispositions",
    "human_gate",
)
ENROLLMENT_KEYS = ("review_conductor", "legacy_xapi")
OUTCOME_KEYS = (
    "schema",
    "route",
    "review_dispatch",
    "legacy_dispatch",
    "clawsweeper_eligible",
    "merge_ready_eligible",
    "notification",
    "repair",
    "reason",
)
NOTIFICATION_KEYS = ("eligibility", "kind", "channels")
REPAIR_KEYS = (
    "cycle",
    "finding_set",
    "automatic_rounds_remaining",
    "ledger_preserved",
)
BLOCKING_OPENCLAW = frozenset({"findings", "human_gate", "failed", "unknown"})
BLOCKING_CLAWSWEEPER = frozenset({"findings", "human_gate", "failed", "unknown"})


class OrchestrationError(ValueError):
    """Fail-closed orchestration input or outcome; the message names the check."""


def _fail(reason: str) -> None:
    raise OrchestrationError(reason)


def _exact(value: Any, keys: tuple[str, ...] | list[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(f"invalid {label} keys")
    return value


def _status(value: Any, label: str) -> str:
    if type(value) is not str or value not in ENROLLMENT_STATUSES:
        _fail(f"{label} enrollment status is unknown")
    return value


def _rail_result(value: Any, label: str) -> str:
    if type(value) is not str or value not in RAIL_RESULTS:
        _fail(f"{label} rail result is unknown")
    return value


def _dispositions(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        _fail("adjudication dispositions must be a non-empty list or null")
    seen: list[str] = []
    for item in value:
        if type(item) is not str or item not in DISPOSITIONS:
            _fail("adjudication disposition is unknown")
        if item in seen:
            _fail("adjudication dispositions must be unique")
        seen.append(item)
    return tuple(seen)


def _ready_compatible_dispositions(dispositions: tuple[str, ...] | None) -> bool:
    """Ready state may carry no dispositions or only defer/reject_false_positive."""
    if dispositions is None:
        return True
    return set(dispositions) <= READY_COMPATIBLE_DISPOSITIONS


def enrollment_route(enrollment: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(route, reason)`` from an already-resolved trusted pair."""
    review_conductor = enrollment["review_conductor"]
    legacy_xapi = enrollment["legacy_xapi"]
    if review_conductor == "broken" or legacy_xapi == "broken":
        return "fail_closed", "ambiguous_or_broken_enrollment"
    if review_conductor == "present":
        if legacy_xapi == "present":
            return "review_conductor", "review_conductor_wins_duplicate_legacy_forbidden"
        return "review_conductor", "review_conductor_enrolled"
    if legacy_xapi == "present":
        return "legacy_xapi", "legacy_xapi_handoff_required"
    return "none", "unenrolled_no_review_no_notification"


def _enrollment_route(enrollment: Mapping[str, str]) -> tuple[str, str]:
    return enrollment_route(enrollment)


def resolve_trusted_enrollment(config: Mapping[str, Any] | None) -> dict[str, str]:
    """Resolve Conductor/legacy enrollment from service-owned config only.

    Caller payloads cannot grant enrollment. A legacy userland profile with no
    enrollment object is the historical Conductor-only Blocks route. Explicit
    ``review_conductor`` / ``legacy_xapi`` statuses, when both present, are the
    trusted pair. Missing one status, an inactive profile without statuses, or
    any malformed value fails closed as broken and is never treated as
    unenrolled.
    """
    broken = {"review_conductor": "broken", "legacy_xapi": "broken"}
    if not isinstance(config, Mapping):
        return dict(broken)
    raw = config.get("enrollment")
    if raw is None:
        return {"review_conductor": "present", "legacy_xapi": "absent"}
    if not isinstance(raw, Mapping):
        return dict(broken)
    has_review = "review_conductor" in raw
    has_legacy = "legacy_xapi" in raw
    if has_review or has_legacy:
        if not has_review or not has_legacy:
            return dict(broken)
        review_conductor = raw["review_conductor"]
        legacy_xapi = raw["legacy_xapi"]
        if (
            type(review_conductor) is not str
            or type(legacy_xapi) is not str
            or review_conductor not in ENROLLMENT_STATUSES
            or legacy_xapi not in ENROLLMENT_STATUSES
        ):
            return dict(broken)
        return {"review_conductor": review_conductor, "legacy_xapi": legacy_xapi}
    if raw.get("enabled") is True and raw.get("blockers") in (None, []):
        return {"review_conductor": "present", "legacy_xapi": "absent"}
    return dict(broken)


def require_trusted_pair(value: Mapping[str, str] | None) -> dict[str, str]:
    """Accept an already-resolved trusted pair; anything else fails closed."""
    broken = {"review_conductor": "broken", "legacy_xapi": "broken"}
    if not isinstance(value, Mapping):
        return dict(broken)
    if set(value) != set(ENROLLMENT_KEYS):
        return dict(broken)
    review_conductor = value["review_conductor"]
    legacy_xapi = value["legacy_xapi"]
    if (
        type(review_conductor) is not str
        or type(legacy_xapi) is not str
        or review_conductor not in ENROLLMENT_STATUSES
        or legacy_xapi not in ENROLLMENT_STATUSES
    ):
        return dict(broken)
    return {"review_conductor": review_conductor, "legacy_xapi": legacy_xapi}


def effective_trusted_enrollment(
    config: Mapping[str, Any] | None,
    claimed: Mapping[str, str] | None = None,
    *,
    authoritative: bool = False,
) -> dict[str, str]:
    """Use service-owned enrollment; a contradicting caller claim fails closed.

    ``authoritative=True`` accepts an already-resolved registry/admission pair
    without consulting userland activation flags.
    """
    if authoritative:
        if claimed is None:
            return resolve_trusted_enrollment(config)
        return require_trusted_pair(claimed)
    trusted = resolve_trusted_enrollment(config)
    if claimed is None:
        return trusted
    if not isinstance(claimed, Mapping) or dict(claimed) != trusted:
        return {"review_conductor": "broken", "legacy_xapi": "broken"}
    return trusted


def next_repair_cycle(current: int) -> int:
    """Advance the saturating 2/2 automatic-repair ledger.

    Cycle 2 is not a ceiling on later scoped ``required_fix`` routes. It only
    records that the first two broad automatic rounds are exhausted.
    """
    if type(current) is not int or current < 0:
        _fail("repair cycle must be a non-negative integer")
    if current >= MAX_REPAIR_CYCLES:
        return MAX_REPAIR_CYCLES
    return current + 1


def _notification(eligibility: str, kind: str | None) -> dict[str, Any]:
    if eligibility not in NOTIFICATION_ELIGIBILITIES:
        _fail("notification eligibility is unknown")
    if eligibility in {"merge_ready", "blocked"}:
        if kind not in {"merge_ready", "human_action_required"}:
            _fail("notification kind is required for a terminal send")
        channels = list(NOTIFY_CHANNELS)
    else:
        if kind is not None:
            _fail("notification kind must be null when no send is eligible")
        channels = []
    return {"eligibility": eligibility, "kind": kind, "channels": channels}


def _fail_closed_blocked(repair: dict[str, Any], reason: str) -> dict[str, Any]:
    """Suppress routing/dispatch while remaining eligible for blocked notify."""
    return _outcome(
        route="fail_closed",
        review_dispatch=False,
        legacy_dispatch=False,
        clawsweeper_eligible=False,
        merge_ready_eligible=False,
        notification=_notification("blocked", "human_action_required"),
        repair=repair,
        reason=reason,
    )


def _outcome(
    *,
    route: str,
    review_dispatch: bool,
    legacy_dispatch: bool,
    clawsweeper_eligible: bool,
    merge_ready_eligible: bool,
    notification: dict[str, Any],
    repair: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    value = {
        "schema": OUTCOME_SCHEMA,
        "route": route,
        "review_dispatch": review_dispatch,
        "legacy_dispatch": legacy_dispatch,
        "clawsweeper_eligible": clawsweeper_eligible,
        "merge_ready_eligible": merge_ready_eligible,
        "notification": notification,
        "repair": repair,
        "reason": reason,
    }
    _exact(value, OUTCOME_KEYS, "orchestration outcome")
    _exact(value["notification"], NOTIFICATION_KEYS, "notification")
    _exact(value["repair"], REPAIR_KEYS, "repair ledger")
    if route not in ROUTES:
        _fail("orchestration route is unknown")
    if type(review_dispatch) is not bool or type(legacy_dispatch) is not bool:
        _fail("dispatch flags must be booleans")
    if type(clawsweeper_eligible) is not bool or type(merge_ready_eligible) is not bool:
        _fail("rail eligibility flags must be booleans")
    if review_dispatch and route != "review_conductor":
        _fail("review dispatch requires the Review Conductor route")
    if legacy_dispatch and route != "legacy_xapi":
        _fail("legacy dispatch requires the exclusive legacy route")
    if review_dispatch and legacy_dispatch:
        _fail("duplicate legacy dispatch is forbidden")
    return value


def decide_orchestration_outcome(raw: Any) -> dict[str, Any]:
    """Return the versioned enrollment, suppression, and notification decision."""
    value = _exact(raw, INPUT_KEYS, "orchestration input")
    if value["schema"] != INPUT_SCHEMA:
        _fail("orchestration input schema is unknown")
    enrollment = _exact(value["enrollment"], ENROLLMENT_KEYS, "enrollment")
    review_conductor = _status(enrollment["review_conductor"], "review_conductor")
    legacy_xapi = _status(enrollment["legacy_xapi"], "legacy_xapi")
    state = value["state"]
    rail = value["rail"]
    if type(state) is not str:
        _fail("state is unknown")
    if rail is not None and (type(rail) is not str or rail not in RAILS):
        _fail("rail is unknown")
    if type(value["repair_cycle"]) is not int or not 0 <= value["repair_cycle"] <= MAX_REPAIR_CYCLES:
        _fail("repair cycle must be an integer in 0..2")
    if type(value["head_changed"]) is not bool:
        _fail("head_changed must be a boolean")
    openclaw_result = _rail_result(value["openclaw_result"], "openclaw")
    clawsweeper_result = _rail_result(value["clawsweeper_result"], "clawsweeper")
    ready_qualified = value["ready_qualified"]
    if ready_qualified is not None and type(ready_qualified) is not bool:
        _fail("ready_qualified must be a boolean or null")
    dispositions = _dispositions(value["adjudication_dispositions"])
    if type(value["human_gate"]) is not bool:
        _fail("human_gate must be a boolean")

    # A changed head never resets the 2/2 ledger. The supplied cycle is truth.
    repair_cycle = value["repair_cycle"]
    finding_set = repair_cycle + 1 if state in SILENT_INTERNAL_STATES | {"waiting_human"} else 0
    repair = {
        "cycle": repair_cycle,
        "finding_set": finding_set,
        "automatic_rounds_remaining": max(0, MAX_REPAIR_CYCLES - repair_cycle),
        "ledger_preserved": True,
    }

    route, route_reason = enrollment_route(
        {"review_conductor": review_conductor, "legacy_xapi": legacy_xapi}
    )
    if state in TERMINAL_CLOSED_STATES:
        return _outcome(
            route=route,
            review_dispatch=False,
            legacy_dispatch=False,
            clawsweeper_eligible=False,
            merge_ready_eligible=False,
            notification=_notification("silent", None),
            repair=repair,
            reason="terminal_closed_non_dispatchable",
        )
    if state not in KNOWN_STATES:
        return _fail_closed_blocked(repair, "unknown_state_fail_closed")
    if openclaw_result == "unknown" or clawsweeper_result == "unknown":
        return _fail_closed_blocked(repair, "unknown_rail_result_fail_closed")
    if not _consistent_state_rail_results(state, rail, openclaw_result, clawsweeper_result):
        return _fail_closed_blocked(repair, "inconsistent_state_rail_result_fail_closed")
    if route == "fail_closed":
        return _fail_closed_blocked(repair, route_reason)
    if route == "none":
        return _outcome(
            route=route,
            review_dispatch=False,
            legacy_dispatch=False,
            clawsweeper_eligible=False,
            merge_ready_eligible=False,
            notification=_notification("none", None),
            repair=repair,
            reason=route_reason,
        )
    if route == "legacy_xapi":
        return _outcome(
            route=route,
            review_dispatch=False,
            legacy_dispatch=False,
            clawsweeper_eligible=False,
            merge_ready_eligible=False,
            notification=_notification("none", None),
            repair=repair,
            reason=route_reason,
        )
    if value["human_gate"]:
        return _outcome(
            route=route,
            review_dispatch=True,
            legacy_dispatch=False,
            clawsweeper_eligible=False,
            merge_ready_eligible=False,
            notification=_notification("blocked", "human_action_required"),
            repair=repair,
            reason="human_action_required",
        )
    if state == "ready_for_human_merge" and not _ready_compatible_dispositions(
        dispositions
    ):
        return _fail_closed_blocked(repair, "inconsistent_ready_disposition_fail_closed")

    clawsweeper_eligible = openclaw_result not in {"absent"}
    if openclaw_result in BLOCKING_OPENCLAW:
        clawsweeper_eligible = False
    if state == "openclaw_clean_draft":
        clawsweeper_eligible = False
    merge_ready_eligible = (
        clawsweeper_eligible
        and clawsweeper_result not in {"absent"}
        and state == "ready_for_human_merge"
        and ready_qualified is True
    )
    if clawsweeper_result in BLOCKING_CLAWSWEEPER:
        merge_ready_eligible = False

    if state in NONTERMINAL_STATES:
        return _outcome(
            route=route,
            review_dispatch=True,
            legacy_dispatch=False,
            clawsweeper_eligible=clawsweeper_eligible,
            merge_ready_eligible=False,
            notification=_notification("silent", None),
            repair=repair,
            reason=(
                route_reason
                if route_reason != "review_conductor_enrolled"
                else "silent_internal_progression"
            ),
        )
    if state in SILENT_INTERNAL_STATES and not value["human_gate"]:
        if repair_cycle >= MAX_REPAIR_CYCLES and dispositions is not None:
            reason = "third_set_adjudication_silent"
        elif openclaw_result == "findings":
            reason = "openclaw_findings_suppress_clawsweeper"
        elif clawsweeper_result == "findings":
            reason = "clawsweeper_findings_suppress_merge_ready"
        else:
            reason = "silent_automatic_repair_round"
        return _outcome(
            route=route,
            review_dispatch=True,
            legacy_dispatch=False,
            clawsweeper_eligible=clawsweeper_eligible,
            merge_ready_eligible=False,
            notification=_notification("silent", None),
            repair=repair,
            reason=reason,
        )
    if state == "ready_for_human_merge":
        if merge_ready_eligible:
            return _outcome(
                route=route,
                review_dispatch=True,
                legacy_dispatch=False,
                clawsweeper_eligible=True,
                merge_ready_eligible=True,
                notification=_notification("merge_ready", "merge_ready"),
                repair=repair,
                reason="both_rails_effectively_clean",
            )
        return _outcome(
            route=route,
            review_dispatch=True,
            legacy_dispatch=False,
            clawsweeper_eligible=clawsweeper_eligible,
            merge_ready_eligible=False,
            notification=_notification("silent", None),
            repair=repair,
            reason="merge_ready_suppressed",
        )
    if state in BLOCKED_STATES or value["human_gate"]:
        return _outcome(
            route=route,
            review_dispatch=True,
            legacy_dispatch=False,
            clawsweeper_eligible=clawsweeper_eligible,
            merge_ready_eligible=False,
            notification=_notification("blocked", "human_action_required"),
            repair=repair,
            reason="human_action_required",
        )
    return _fail_closed_blocked(repair, "unknown_outcome_fail_closed")


def _allowed_rails(state: str) -> frozenset[str | None] | None:
    """Return the exact rails a known state may carry, or None if unknown."""
    if state in {"ci_running", "ci_failed", "openclaw_queued"}:
        return frozenset({None})
    if state in TERMINAL_CLOSED_STATES:
        return frozenset({None}) | RAILS
    if state in {"openclaw_running", "openclaw_failed", "openclaw_clean_draft"}:
        return frozenset({"openclaw"})
    if state in {
        "clawsweeper_queued",
        "clawsweeper_running",
        "clawsweeper_failed",
        "clawsweeper_clean_draft",
        "ready_for_human_merge",
    }:
        return frozenset({"clawsweeper"})
    if state in SILENT_INTERNAL_STATES | {"waiting_human"}:
        return RAILS
    return None


def _rail_matches_state(state: str, rail: str | None) -> bool:
    allowed = _allowed_rails(state)
    if allowed is None:
        return False
    return rail in allowed


def _consistent_state_rail_results(
    state: str,
    rail: str | None,
    openclaw_result: str,
    clawsweeper_result: str,
) -> bool:
    """Return whether the complete state/rail/result tuple is representable."""
    if not _rail_matches_state(state, rail):
        return False
    if state in {"ci_running", "ci_failed", "openclaw_queued", "openclaw_running"}:
        return openclaw_result == "absent" and clawsweeper_result == "absent"
    if state in TERMINAL_CLOSED_STATES:
        return openclaw_result == "absent" and clawsweeper_result == "absent"
    if state == "openclaw_failed":
        return openclaw_result == "failed" and clawsweeper_result == "absent"
    if state == "openclaw_clean_draft":
        return openclaw_result == "clean" and clawsweeper_result == "absent"
    if state in {"clawsweeper_queued", "clawsweeper_running"}:
        return openclaw_result == "clean" and clawsweeper_result == "absent"
    if state == "clawsweeper_failed":
        return openclaw_result == "clean" and clawsweeper_result == "failed"
    if state == "clawsweeper_clean_draft":
        return openclaw_result == "clean" and clawsweeper_result == "clean"
    if state == "ready_for_human_merge":
        if openclaw_result not in {"clean", "effectively_clean"}:
            return False
        return clawsweeper_result in {
            "clean",
            "effectively_clean",
            "findings",
            "human_gate",
            "failed",
        }
    if state in SILENT_INTERNAL_STATES | {"waiting_human"}:
        expected = "human_gate" if state == "waiting_human" else "findings"
        if rail == "clawsweeper":
            return openclaw_result == "clean" and clawsweeper_result == expected
        return openclaw_result == expected and clawsweeper_result == "absent"
    return False


def derive_rail_results(state: str, rail: str | None) -> tuple[str, str]:
    """Map a current engine head onto typed rail results without prose parsing."""
    if type(state) is not str:
        _fail("state is unknown")
    if rail is not None and type(rail) is not str:
        _fail("state and rail are inconsistent")
    if state not in KNOWN_STATES:
        return "unknown", "unknown"
    if not _rail_matches_state(state, rail):
        if state in SILENT_INTERNAL_STATES | {"waiting_human"}:
            _fail("finding, repair, and human-gate states require an exact rail")
        _fail("state and rail are inconsistent")
    if state in {"ci_running", "ci_failed", "openclaw_queued", "openclaw_running", "closed", "closed_merged"}:
        return "absent", "absent"
    if state == "openclaw_failed":
        return "failed", "absent"
    if state == "openclaw_clean_draft":
        return "clean", "absent"
    if state in {"clawsweeper_queued", "clawsweeper_running"}:
        return "clean", "absent"
    if state == "clawsweeper_failed":
        return "clean", "failed"
    if state == "clawsweeper_clean_draft":
        return "clean", "clean"
    if state == "ready_for_human_merge":
        return "clean", "effectively_clean"
    if state in SILENT_INTERNAL_STATES | {"waiting_human"}:
        if rail == "clawsweeper":
            return "clean", "human_gate" if state == "waiting_human" else "findings"
        return "human_gate" if state == "waiting_human" else "findings", "absent"
    _fail("state has no rail-result mapping")
    raise AssertionError("unreachable")


def _persisted_ready_qualified(
    quality: Mapping[str, Any] | None,
) -> tuple[bool | None, bool]:
    """Decode a persisted ready flag. Only integer 0/1 is accepted."""
    if quality is None:
        return None, True
    raw = quality["ready_qualified"]
    if type(raw) is int and raw in (0, 1):
        return raw == 1, True
    return None, False


def outcome_from_review_row(
    row: Mapping[str, Any],
    quality: Mapping[str, Any] | None = None,
    *,
    enrollment: Mapping[str, str] | None = None,
    head_changed: bool = False,
) -> dict[str, Any]:
    """Build the exact decision input from a current Review Conductor head.

    Persisted-row adapter only: inconsistent or malformed stored state/rail
    tokens map to typed unknown results and exact builtin state/rail values
    so the canonical decision can fail closed and notify once. Direct
    ``decide_orchestration_outcome`` / ``derive_rail_results`` inputs remain
    strict. Readiness/quality flags accept only stored integer ``0`` / ``1``;
    any other persisted value maps to unknown rail results.
    """
    raw_state = row["state"]
    raw_rail = row["rail"]
    state = raw_state if type(raw_state) is str else "unknown_persisted_state"
    rail = (
        raw_rail
        if raw_rail is None or (type(raw_rail) is str and raw_rail in RAILS)
        else None
    )
    try:
        openclaw_result, clawsweeper_result = derive_rail_results(raw_state, raw_rail)
    except OrchestrationError:
        openclaw_result, clawsweeper_result = "unknown", "unknown"
    if (
        type(raw_state) is not str
        or raw_state not in KNOWN_STATES
        or (raw_rail is not None and (type(raw_rail) is not str or raw_rail not in RAILS))
    ):
        openclaw_result, clawsweeper_result = "unknown", "unknown"
    ready_qualified, quality_accepted = _persisted_ready_qualified(quality)
    if not quality_accepted:
        openclaw_result, clawsweeper_result = "unknown", "unknown"
    if enrollment is None:
        enrollment = {"review_conductor": "broken", "legacy_xapi": "broken"}
    return {
        "schema": INPUT_SCHEMA,
        "enrollment": dict(enrollment),
        "state": state,
        "rail": rail,
        "repair_cycle": int(row["repair_cycle"]),
        "head_changed": head_changed,
        "openclaw_result": openclaw_result,
        "clawsweeper_result": clawsweeper_result,
        "ready_qualified": ready_qualified,
        "adjudication_dispositions": None,
        "human_gate": state == "waiting_human",
    }
