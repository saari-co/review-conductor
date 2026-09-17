#!/usr/bin/env python3
"""Canonical Review Conductor orchestration-outcome decision.

Source-only library. One versioned function interprets enrollment, rail
results, the saturating two-cycle automatic-repair ledger, and terminal
engine state into a structured dispatch/notification decision. The
existing notification queue consumes this outcome; this is not a second
notification system and it does not import, invoke, or dispatch x-api.
Representable fail-closed states suppress routing and keep
legacy_dispatch false, but they remain eligible for the blocked
notification path.
"""

from __future__ import annotations

from typing import Any, Mapping


INPUT_SCHEMA = "review-conductor.orchestration-input.v1"
OUTCOME_SCHEMA = "review-conductor.orchestration-outcome.v1"
MAX_REPAIR_CYCLES = 2

ENROLLMENT_STATUSES = frozenset({"present", "absent", "broken"})
ROUTES = frozenset({"review_conductor", "legacy_xapi", "none", "fail_closed"})
NOTIFICATION_ELIGIBILITIES = frozenset(
    {"silent", "merge_ready", "blocked", "none", "fail_closed"}
)
RAIL_RESULTS = frozenset(
    {"absent", "clean", "effectively_clean", "findings", "human_gate", "failed", "unknown"}
)
RAILS = frozenset({"openclaw", "clawsweeper"})
DISPOSITIONS = frozenset(
    {"required_fix", "defer", "reject_false_positive", "human_gate"}
)
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
NONTERMINAL_STATES = frozenset(
    {
        "ci_running",
        "openclaw_queued",
        "openclaw_running",
        "openclaw_clean_draft",
        "clawsweeper_queued",
        "clawsweeper_running",
        "clawsweeper_clean_draft",
        "closed",
        "closed_merged",
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
    if value not in ENROLLMENT_STATUSES:
        _fail(f"{label} enrollment status is unknown")
    return value


def _rail_result(value: Any, label: str) -> str:
    if value not in RAIL_RESULTS:
        _fail(f"{label} rail result is unknown")
    return value


def _dispositions(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        _fail("adjudication dispositions must be a non-empty list or null")
    seen: list[str] = []
    for item in value:
        if item not in DISPOSITIONS:
            _fail("adjudication disposition is unknown")
        if item in seen:
            _fail("adjudication dispositions must be unique")
        seen.append(item)
    return tuple(seen)


def _enrollment_route(enrollment: Mapping[str, str]) -> tuple[str, str]:
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
    if rail is not None and rail not in RAILS:
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

    route, route_reason = _enrollment_route(
        {"review_conductor": review_conductor, "legacy_xapi": legacy_xapi}
    )
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

    if state not in KNOWN_STATES:
        return _fail_closed_blocked(repair, "unknown_state_fail_closed")
    if openclaw_result == "unknown" or clawsweeper_result == "unknown":
        return _fail_closed_blocked(repair, "unknown_rail_result_fail_closed")

    clawsweeper_eligible = openclaw_result not in {"absent"}
    if openclaw_result in BLOCKING_OPENCLAW:
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


def derive_rail_results(state: str, rail: str | None) -> tuple[str, str]:
    """Map a current engine head onto typed rail results without prose parsing."""
    if state not in KNOWN_STATES:
        return "unknown", "unknown"
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
        if rail not in RAILS:
            _fail("finding, repair, and human-gate states require an exact rail")
        if rail == "clawsweeper":
            return "clean", "human_gate" if state == "waiting_human" else "findings"
        return "human_gate" if state == "waiting_human" else "findings", "absent"
    _fail("state has no rail-result mapping")
    raise AssertionError("unreachable")


def outcome_from_review_row(
    row: Mapping[str, Any],
    quality: Mapping[str, Any] | None = None,
    *,
    enrollment: Mapping[str, str] | None = None,
    head_changed: bool = False,
) -> dict[str, Any]:
    """Build the exact decision input from a current Review Conductor head."""
    state = row["state"]
    rail = row["rail"]
    openclaw_result, clawsweeper_result = derive_rail_results(state, rail)
    ready_qualified = None
    if quality is not None:
        ready_qualified = bool(quality["ready_qualified"])
    return {
        "schema": INPUT_SCHEMA,
        "enrollment": dict(enrollment or {"review_conductor": "present", "legacy_xapi": "absent"}),
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
