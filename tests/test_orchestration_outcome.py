#!/usr/bin/env python3
"""Deterministic notification eligibility and enrollment-route contract."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import orchestration_outcome as outcome


def enrolled(**overrides):
    value = {
        "schema": outcome.INPUT_SCHEMA,
        "enrollment": {"review_conductor": "present", "legacy_xapi": "absent"},
        "state": "ci_running",
        "rail": None,
        "repair_cycle": 0,
        "head_changed": False,
        "openclaw_result": "absent",
        "clawsweeper_result": "absent",
        "ready_qualified": None,
        "adjudication_dispositions": None,
        "human_gate": False,
    }
    value.update(overrides)
    return value


def notify(value):
    return outcome.decide_orchestration_outcome(value)


def notification_matches_schema(notification):
    schema = json.loads(
        (ROOT / "contracts/orchestration-outcome.schema.json").read_text(encoding="utf-8")
    )
    if not isinstance(notification, dict) or set(notification) != {"eligibility", "kind", "channels"}:
        return False
    channels = notification["channels"]
    if not isinstance(channels, list):
        return False
    for branch in schema["properties"]["notification"]["oneOf"]:
        eligibility = branch["properties"]["eligibility"]
        kind = branch["properties"]["kind"]
        allowed = {eligibility["const"]} if "const" in eligibility else set(eligibility["enum"])
        if notification["eligibility"] not in allowed:
            continue
        if "const" in kind:
            if notification["kind"] != kind["const"]:
                continue
        elif kind.get("type") == "null" and notification["kind"] is not None:
            continue
        channel_schema = branch["properties"]["channels"]
        if channel_schema.get("maxItems") == 0:
            if channels:
                continue
            return True
        expected = [item["const"] for item in channel_schema.get("prefixItems", ())]
        if channels == expected:
            return True
    return False


class EnrollmentRouteTests(unittest.TestCase):
    def test_review_conductor_only_wins_and_dispatches(self):
        decision = notify(enrolled())
        self.assertEqual(decision["schema"], outcome.OUTCOME_SCHEMA)
        self.assertEqual(decision["route"], "review_conductor")
        self.assertTrue(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["notification"]["eligibility"], "silent")

    def test_legacy_only_is_untouched_and_does_not_notify(self):
        decision = notify(
            enrolled(enrollment={"review_conductor": "absent", "legacy_xapi": "present"})
        )
        self.assertEqual(decision["route"], "legacy_xapi")
        self.assertFalse(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["notification"]["eligibility"], "none")
        self.assertEqual(decision["reason"], "legacy_xapi_handoff_required")

    def test_dual_enrollment_review_conductor_wins_without_legacy_dispatch(self):
        decision = notify(
            enrolled(enrollment={"review_conductor": "present", "legacy_xapi": "present"})
        )
        self.assertEqual(decision["route"], "review_conductor")
        self.assertTrue(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["reason"], "review_conductor_wins_duplicate_legacy_forbidden")

    def test_neither_enrolled_ends_without_review_or_notification(self):
        decision = notify(
            enrolled(enrollment={"review_conductor": "absent", "legacy_xapi": "absent"})
        )
        self.assertEqual(decision["route"], "none")
        self.assertFalse(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["notification"]["eligibility"], "none")
        self.assertEqual(decision["reason"], "unenrolled_no_review_no_notification")

    def test_broken_enrollment_is_not_treated_as_unenrolled(self):
        for enrollment in (
            {"review_conductor": "broken", "legacy_xapi": "absent"},
            {"review_conductor": "absent", "legacy_xapi": "broken"},
            {"review_conductor": "broken", "legacy_xapi": "present"},
            {"review_conductor": "present", "legacy_xapi": "broken"},
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        ):
            with self.subTest(enrollment=enrollment):
                decision = notify(enrolled(enrollment=enrollment))
                self.assertEqual(decision["route"], "fail_closed")
                self.assertFalse(decision["review_dispatch"])
                self.assertFalse(decision["legacy_dispatch"])
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(decision["notification"]["kind"], "human_action_required")
                self.assertEqual(decision["notification"]["channels"], list(outcome.NOTIFY_CHANNELS))
                self.assertEqual(decision["reason"], "ambiguous_or_broken_enrollment")
                self.assertNotEqual(decision["reason"], "unenrolled_no_review_no_notification")


class NotificationEligibilityTests(unittest.TestCase):
    def test_first_and_second_repair_rounds_are_silent(self):
        for cycle, state, rail_result in (
            (0, "awaiting_adjudication", "findings"),
            (0, "repair_required", "findings"),
            (1, "awaiting_adjudication", "findings"),
            (1, "repair_required", "findings"),
        ):
            with self.subTest(cycle=cycle, state=state):
                decision = notify(
                    enrolled(
                        state=state,
                        rail="openclaw",
                        repair_cycle=cycle,
                        openclaw_result=rail_result,
                        clawsweeper_result="absent",
                    )
                )
                self.assertEqual(decision["notification"]["eligibility"], "silent")
                self.assertEqual(decision["notification"]["channels"], [])
                self.assertFalse(decision["clawsweeper_eligible"])
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["repair"]["cycle"], cycle)
                self.assertEqual(decision["repair"]["finding_set"], cycle + 1)
                self.assertTrue(decision["repair"]["ledger_preserved"])

    def test_third_set_dispositions_stay_silent_until_human_gate_state(self):
        awaiting = notify(
            enrolled(
                state="awaiting_adjudication",
                rail="openclaw",
                repair_cycle=2,
                openclaw_result="findings",
                clawsweeper_result="absent",
                adjudication_dispositions=["required_fix", "defer", "reject_false_positive"],
            )
        )
        self.assertEqual(awaiting["notification"]["eligibility"], "silent")
        self.assertEqual(awaiting["reason"], "third_set_adjudication_silent")
        self.assertEqual(awaiting["repair"]["finding_set"], 3)
        self.assertEqual(awaiting["repair"]["automatic_rounds_remaining"], 0)

        blocked = notify(
            enrolled(
                state="waiting_human",
                rail="openclaw",
                repair_cycle=2,
                openclaw_result="human_gate",
                clawsweeper_result="absent",
                adjudication_dispositions=["required_fix", "human_gate"],
                human_gate=True,
            )
        )
        self.assertEqual(blocked["notification"]["eligibility"], "blocked")
        self.assertEqual(blocked["notification"]["kind"], "human_action_required")
        self.assertEqual(blocked["notification"]["channels"], list(outcome.NOTIFY_CHANNELS))
        self.assertEqual(blocked["reason"], "human_action_required")

    def test_changed_head_preserves_the_repair_ledger(self):
        decision = notify(
            enrolled(
                state="repair_required",
                rail="openclaw",
                repair_cycle=1,
                head_changed=True,
                openclaw_result="findings",
                clawsweeper_result="absent",
            )
        )
        self.assertEqual(decision["repair"]["cycle"], 1)
        self.assertEqual(decision["repair"]["finding_set"], 2)
        self.assertTrue(decision["repair"]["ledger_preserved"])
        self.assertEqual(decision["notification"]["eligibility"], "silent")

    def test_openclaw_findings_suppress_clawsweeper(self):
        decision = notify(
            enrolled(
                state="awaiting_adjudication",
                rail="openclaw",
                openclaw_result="findings",
                clawsweeper_result="absent",
            )
        )
        self.assertFalse(decision["clawsweeper_eligible"])
        self.assertFalse(decision["merge_ready_eligible"])
        self.assertEqual(decision["reason"], "openclaw_findings_suppress_clawsweeper")
        self.assertEqual(decision["notification"]["eligibility"], "silent")

    def test_clawsweeper_findings_suppress_merge_ready(self):
        awaiting = notify(
            enrolled(
                state="awaiting_adjudication",
                rail="clawsweeper",
                openclaw_result="clean",
                clawsweeper_result="findings",
            )
        )
        self.assertTrue(awaiting["clawsweeper_eligible"])
        self.assertFalse(awaiting["merge_ready_eligible"])
        self.assertEqual(awaiting["reason"], "clawsweeper_findings_suppress_merge_ready")
        self.assertEqual(awaiting["notification"]["eligibility"], "silent")
        ready_blocked = notify(
            enrolled(
                state="ready_for_human_merge",
                rail="clawsweeper",
                openclaw_result="clean",
                clawsweeper_result="findings",
                ready_qualified=True,
            )
        )
        self.assertTrue(ready_blocked["clawsweeper_eligible"])
        self.assertFalse(ready_blocked["merge_ready_eligible"])
        self.assertEqual(ready_blocked["notification"]["eligibility"], "silent")
        self.assertEqual(ready_blocked["reason"], "merge_ready_suppressed")

    def test_merge_ready_requires_both_effectively_clean_rails(self):
        decision = notify(
            enrolled(
                state="ready_for_human_merge",
                rail="clawsweeper",
                openclaw_result="clean",
                clawsweeper_result="effectively_clean",
                ready_qualified=True,
            )
        )
        self.assertTrue(decision["clawsweeper_eligible"])
        self.assertTrue(decision["merge_ready_eligible"])
        self.assertEqual(decision["notification"]["eligibility"], "merge_ready")
        self.assertEqual(decision["notification"]["kind"], "merge_ready")
        self.assertEqual(decision["notification"]["channels"], list(outcome.NOTIFY_CHANNELS))
        self.assertEqual(decision["reason"], "both_rails_effectively_clean")

    def test_authorized_deferrals_on_unchanged_head_can_be_merge_ready(self):
        decision = notify(
            enrolled(
                state="ready_for_human_merge",
                rail="clawsweeper",
                repair_cycle=2,
                openclaw_result="effectively_clean",
                clawsweeper_result="effectively_clean",
                ready_qualified=True,
                adjudication_dispositions=["defer", "reject_false_positive"],
            )
        )
        self.assertEqual(decision["notification"]["eligibility"], "merge_ready")
        self.assertTrue(decision["repair"]["ledger_preserved"])
        self.assertEqual(decision["repair"]["cycle"], 2)

    def test_ready_state_without_ready_policy_stays_silent(self):
        for ready in (None, False):
            with self.subTest(ready_qualified=ready):
                decision = notify(
                    enrolled(
                        state="ready_for_human_merge",
                        rail="clawsweeper",
                        openclaw_result="clean",
                        clawsweeper_result="effectively_clean",
                        ready_qualified=ready,
                    )
                )
                self.assertEqual(decision["notification"]["eligibility"], "silent")
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["reason"], "merge_ready_suppressed")

    def test_blocked_human_action_states_notify(self):
        for state, openclaw_result, clawsweeper_result, rail in (
            ("ci_failed", "absent", "absent", None),
            ("openclaw_failed", "failed", "absent", "openclaw"),
            ("clawsweeper_failed", "clean", "failed", "clawsweeper"),
            ("waiting_human", "human_gate", "absent", "openclaw"),
        ):
            with self.subTest(state=state):
                decision = notify(
                    enrolled(
                        state=state,
                        rail=rail,
                        openclaw_result=openclaw_result,
                        clawsweeper_result=clawsweeper_result,
                        human_gate=state == "waiting_human",
                    )
                )
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(decision["notification"]["kind"], "human_action_required")
                self.assertEqual(
                    decision["notification"]["channels"], list(outcome.NOTIFY_CHANNELS)
                )

    def test_human_gate_precedes_terminal_ready_and_nonterminal_dispatch(self):
        ready = notify(
            enrolled(
                state="ready_for_human_merge",
                rail="clawsweeper",
                openclaw_result="clean",
                clawsweeper_result="effectively_clean",
                ready_qualified=True,
                human_gate=True,
            )
        )
        self.assertEqual(ready["notification"]["eligibility"], "blocked")
        self.assertEqual(ready["notification"]["kind"], "human_action_required")
        self.assertEqual(ready["notification"]["channels"], list(outcome.NOTIFY_CHANNELS))
        self.assertFalse(ready["merge_ready_eligible"])
        self.assertEqual(ready["reason"], "human_action_required")
        self.assertNotEqual(ready["notification"]["eligibility"], "merge_ready")

        running = notify(enrolled(state="ci_running", human_gate=True))
        self.assertEqual(running["notification"]["eligibility"], "blocked")
        self.assertEqual(running["notification"]["kind"], "human_action_required")
        self.assertEqual(running["reason"], "human_action_required")
        self.assertNotEqual(running["notification"]["eligibility"], "silent")
        self.assertTrue(running["review_dispatch"])

        closed = notify(enrolled(state="closed", human_gate=True))
        self.assertEqual(closed["notification"]["eligibility"], "silent")
        self.assertFalse(closed["review_dispatch"])
        self.assertEqual(closed["reason"], "terminal_closed_non_dispatchable")


class FailClosedTests(unittest.TestCase):
    def test_malformed_and_unknown_inputs_fail_closed(self):
        cases = [
            {**enrolled(), "schema": "review-conductor.orchestration-input.v0"},
            {k: v for k, v in enrolled().items() if k != "state"},
            {**enrolled(), "extra": True},
            {**enrolled(), "repair_cycle": True},
            {**enrolled(), "repair_cycle": 3},
            {**enrolled(), "head_changed": 1},
            {**enrolled(), "ready_qualified": "yes"},
            {**enrolled(), "human_gate": "true"},
            {**enrolled(), "rail": "spark"},
            {**enrolled(), "openclaw_result": "chunked findings: 2"},
            {**enrolled(), "enrollment": {"review_conductor": "maybe", "legacy_xapi": "absent"}},
            {**enrolled(), "adjudication_dispositions": ["required_fix", "invented"]},
            {**enrolled(), "adjudication_dispositions": ["required_fix", "required_fix"]},
            {**enrolled(), "adjudication_dispositions": []},
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(outcome.OrchestrationError):
                    notify(raw)

    def test_unhashable_enum_inputs_raise_orchestration_error(self):
        class Token(str):
            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash("present")

        cases = [
            {**enrolled(), "enrollment": {"review_conductor": [], "legacy_xapi": "absent"}},
            {**enrolled(), "enrollment": {"review_conductor": {}, "legacy_xapi": "absent"}},
            {**enrolled(), "enrollment": {"review_conductor": Token("x"), "legacy_xapi": "absent"}},
            {**enrolled(), "state": []},
            {**enrolled(), "state": {}},
            {**enrolled(), "state": Token("ci_running")},
            {**enrolled(), "rail": []},
            {**enrolled(), "rail": {"name": "openclaw"}},
            {**enrolled(), "openclaw_result": []},
            {**enrolled(), "openclaw_result": {"result": "clean"}},
            {**enrolled(), "clawsweeper_result": []},
            {**enrolled(), "adjudication_dispositions": [["required_fix"]]},
            {**enrolled(), "adjudication_dispositions": [{"name": "required_fix"}]},
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                try:
                    notify(raw)
                except outcome.OrchestrationError:
                    continue
                except TypeError:
                    self.fail("unhashable enum input leaked TypeError")
                else:
                    self.fail("unhashable enum input was accepted")
        valid = notify(enrolled())
        self.assertEqual(valid["route"], "review_conductor")
        self.assertEqual(valid["notification"]["eligibility"], "silent")

    def test_persisted_quality_flags_accept_only_integer_zero_one(self):
        enrolled_pair = {"review_conductor": "present", "legacy_xapi": "absent"}
        ready_row = {
            "state": "ready_for_human_merge",
            "rail": "clawsweeper",
            "repair_cycle": 0,
        }
        accepted = outcome.outcome_from_review_row(
            ready_row, {"ready_qualified": 1}, enrollment=enrolled_pair
        )
        self.assertEqual(accepted["ready_qualified"], True)
        self.assertEqual(accepted["openclaw_result"], "clean")
        self.assertEqual(accepted["clawsweeper_result"], "effectively_clean")
        ready = outcome.decide_orchestration_outcome(accepted)
        self.assertTrue(ready["merge_ready_eligible"])
        self.assertEqual(ready["notification"]["eligibility"], "merge_ready")
        unqualified = outcome.outcome_from_review_row(
            ready_row, {"ready_qualified": 0}, enrollment=enrolled_pair
        )
        self.assertEqual(unqualified["ready_qualified"], False)
        self.assertEqual(unqualified["openclaw_result"], "clean")
        silent = outcome.decide_orchestration_outcome(unqualified)
        self.assertFalse(silent["merge_ready_eligible"])
        self.assertEqual(silent["notification"]["eligibility"], "silent")
        for raw in ("false", "true", "0", "1", 2, -1, True, False, 1.0, [1], {"v": 1}):
            with self.subTest(raw=raw):
                mapped = outcome.outcome_from_review_row(
                    ready_row, {"ready_qualified": raw}, enrollment=enrolled_pair
                )
                self.assertIsNone(mapped["ready_qualified"])
                self.assertEqual(mapped["openclaw_result"], "unknown")
                self.assertEqual(mapped["clawsweeper_result"], "unknown")
                decision = outcome.decide_orchestration_outcome(mapped)
                self.assertEqual(decision["route"], "fail_closed")
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(decision["reason"], "unknown_rail_result_fail_closed")
                self.assertNotEqual(decision["notification"]["eligibility"], "merge_ready")
        with self.assertRaises(outcome.OrchestrationError):
            notify(enrolled(ready_qualified="false"))

    def test_unknown_state_and_rail_results_are_fail_closed_not_silent(self):
        unknown_state = notify(enrolled(state="mystery_state"))
        self.assertEqual(unknown_state["route"], "fail_closed")
        self.assertFalse(unknown_state["review_dispatch"])
        self.assertFalse(unknown_state["legacy_dispatch"])
        self.assertEqual(unknown_state["notification"]["eligibility"], "blocked")
        self.assertEqual(unknown_state["notification"]["kind"], "human_action_required")
        self.assertEqual(unknown_state["reason"], "unknown_state_fail_closed")
        unknown_rail = notify(
            enrolled(
                state="ready_for_human_merge",
                openclaw_result="unknown",
                clawsweeper_result="clean",
                ready_qualified=True,
            )
        )
        self.assertEqual(unknown_rail["route"], "fail_closed")
        self.assertFalse(unknown_rail["review_dispatch"])
        self.assertFalse(unknown_rail["legacy_dispatch"])
        self.assertEqual(unknown_rail["notification"]["eligibility"], "blocked")
        self.assertEqual(unknown_rail["reason"], "unknown_rail_result_fail_closed")

    def test_inconsistent_state_rail_results_fail_closed_before_dispatch(self):
        running_findings = notify(
            enrolled(
                state="ci_running",
                rail=None,
                openclaw_result="findings",
                clawsweeper_result="absent",
            )
        )
        self.assertEqual(running_findings["route"], "fail_closed")
        self.assertFalse(running_findings["review_dispatch"])
        self.assertFalse(running_findings["legacy_dispatch"])
        self.assertFalse(running_findings["merge_ready_eligible"])
        self.assertEqual(running_findings["notification"]["eligibility"], "blocked")
        self.assertEqual(running_findings["notification"]["kind"], "human_action_required")
        self.assertEqual(
            running_findings["reason"], "inconsistent_state_rail_result_fail_closed"
        )
        for rail in (None, "openclaw"):
            with self.subTest(rail=rail):
                ready = notify(
                    enrolled(
                        state="ready_for_human_merge",
                        rail=rail,
                        openclaw_result="clean",
                        clawsweeper_result="effectively_clean",
                        ready_qualified=True,
                    )
                )
                self.assertEqual(ready["route"], "fail_closed")
                self.assertFalse(ready["review_dispatch"])
                self.assertFalse(ready["merge_ready_eligible"])
                self.assertEqual(ready["notification"]["eligibility"], "blocked")
                self.assertNotEqual(ready["notification"]["eligibility"], "merge_ready")
                self.assertEqual(
                    ready["reason"], "inconsistent_state_rail_result_fail_closed"
                )
        gated_missing_rail = notify(
            enrolled(
                state="waiting_human",
                rail=None,
                openclaw_result="human_gate",
                clawsweeper_result="absent",
                human_gate=True,
            )
        )
        self.assertEqual(gated_missing_rail["route"], "fail_closed")
        self.assertFalse(gated_missing_rail["review_dispatch"])
        self.assertEqual(
            gated_missing_rail["reason"], "inconsistent_state_rail_result_fail_closed"
        )

    def test_persisted_row_adapter_maps_inconsistent_rows_to_unknown(self):
        enrolled_pair = {"review_conductor": "present", "legacy_xapi": "absent"}
        mapped = outcome.outcome_from_review_row(
            {"state": "waiting_human", "rail": None, "repair_cycle": 0},
            enrollment=enrolled_pair,
        )
        self.assertEqual(mapped["openclaw_result"], "unknown")
        self.assertEqual(mapped["clawsweeper_result"], "unknown")
        decision = outcome.decide_orchestration_outcome(mapped)
        self.assertEqual(decision["route"], "fail_closed")
        self.assertFalse(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["notification"]["eligibility"], "blocked")
        self.assertEqual(decision["reason"], "unknown_rail_result_fail_closed")
        with self.assertRaises(outcome.OrchestrationError):
            outcome.derive_rail_results("waiting_human", None)

    def test_unenrolled_and_legacy_inconsistent_tuples_fail_closed_before_short_circuit(self):
        enrollments = (
            {"review_conductor": "absent", "legacy_xapi": "absent"},
            {"review_conductor": "absent", "legacy_xapi": "present"},
        )
        for enrollment in enrollments:
            with self.subTest(enrollment=enrollment):
                decision = notify(
                    enrolled(
                        enrollment=enrollment,
                        state="ci_running",
                        rail=None,
                        openclaw_result="findings",
                        clawsweeper_result="absent",
                    )
                )
                self.assertEqual(decision["route"], "fail_closed")
                self.assertFalse(decision["review_dispatch"])
                self.assertFalse(decision["legacy_dispatch"])
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(decision["notification"]["kind"], "human_action_required")
                self.assertEqual(
                    decision["reason"], "inconsistent_state_rail_result_fail_closed"
                )
                self.assertNotEqual(decision["notification"]["eligibility"], "none")

    def test_mismatched_rails_fail_closed_without_review_dispatch(self):
        cases = (
            (
                "ci_failed",
                "clawsweeper",
                "absent",
                "absent",
                False,
            ),
            (
                "openclaw_failed",
                None,
                "failed",
                "absent",
                False,
            ),
            (
                "ci_running",
                "openclaw",
                "absent",
                "absent",
                False,
            ),
            (
                "openclaw_running",
                None,
                "absent",
                "absent",
                False,
            ),
        )
        for state, rail, openclaw_result, clawsweeper_result, human_gate in cases:
            with self.subTest(state=state, rail=rail):
                decision = notify(
                    enrolled(
                        state=state,
                        rail=rail,
                        openclaw_result=openclaw_result,
                        clawsweeper_result=clawsweeper_result,
                        human_gate=human_gate,
                    )
                )
                self.assertEqual(decision["route"], "fail_closed")
                self.assertFalse(decision["review_dispatch"])
                self.assertFalse(decision["legacy_dispatch"])
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(
                    decision["reason"], "inconsistent_state_rail_result_fail_closed"
                )
                self.assertNotEqual(decision["review_dispatch"], True)

    def test_persisted_row_adapter_maps_mismatched_rails_to_unknown(self):
        enrolled_pair = {"review_conductor": "present", "legacy_xapi": "absent"}
        cases = (
            {"state": "ci_failed", "rail": "clawsweeper", "repair_cycle": 0},
            {"state": "openclaw_failed", "rail": None, "repair_cycle": 0},
            {"state": "ci_running", "rail": "openclaw", "repair_cycle": 0},
        )
        for row in cases:
            with self.subTest(row=row):
                mapped = outcome.outcome_from_review_row(row, enrollment=enrolled_pair)
                self.assertEqual(mapped["openclaw_result"], "unknown")
                self.assertEqual(mapped["clawsweeper_result"], "unknown")
                decision = outcome.decide_orchestration_outcome(mapped)
                self.assertEqual(decision["route"], "fail_closed")
                self.assertFalse(decision["review_dispatch"])
                self.assertFalse(decision["legacy_dispatch"])
                self.assertEqual(decision["notification"]["eligibility"], "blocked")
                self.assertEqual(decision["reason"], "unknown_rail_result_fail_closed")
                with self.assertRaises(outcome.OrchestrationError):
                    outcome.derive_rail_results(row["state"], row["rail"])

    def test_row_mapper_preserves_silent_repair_and_blocked_ci(self):
        enrolled_pair = {"review_conductor": "present", "legacy_xapi": "absent"}
        silent = outcome.decide_orchestration_outcome(
            outcome.outcome_from_review_row(
                {
                    "state": "awaiting_adjudication",
                    "rail": "openclaw",
                    "repair_cycle": 0,
                },
                enrollment=enrolled_pair,
            )
        )
        self.assertEqual(silent["notification"]["eligibility"], "silent")
        blocked = outcome.decide_orchestration_outcome(
            outcome.outcome_from_review_row(
                {"state": "ci_failed", "rail": None, "repair_cycle": 0},
                enrollment=enrolled_pair,
            )
        )
        self.assertEqual(blocked["notification"]["eligibility"], "blocked")

    def test_closed_states_are_terminal_and_non_dispatchable(self):
        for state in ("closed", "closed_merged"):
            with self.subTest(state=state):
                decision = notify(enrolled(state=state, rail=None))
                self.assertEqual(decision["route"], "review_conductor")
                self.assertFalse(decision["review_dispatch"])
                self.assertFalse(decision["legacy_dispatch"])
                self.assertFalse(decision["clawsweeper_eligible"])
                self.assertFalse(decision["merge_ready_eligible"])
                self.assertEqual(decision["notification"]["eligibility"], "silent")
                self.assertEqual(decision["notification"]["channels"], [])
                self.assertEqual(decision["reason"], "terminal_closed_non_dispatchable")
                mapped = outcome.decide_orchestration_outcome(
                    outcome.outcome_from_review_row(
                        {"state": state, "rail": None, "repair_cycle": 0},
                        enrollment={"review_conductor": "present", "legacy_xapi": "absent"},
                    )
                )
                self.assertFalse(mapped["review_dispatch"])
                self.assertEqual(mapped["reason"], "terminal_closed_non_dispatchable")

    def test_closed_states_remain_silent_when_enrollment_is_broken(self):
        enrollments = (
            {"review_conductor": "broken", "legacy_xapi": "absent"},
            {"review_conductor": "broken", "legacy_xapi": "broken"},
            {"review_conductor": "present", "legacy_xapi": "broken"},
            {"review_conductor": "absent", "legacy_xapi": "absent"},
            {"review_conductor": "absent", "legacy_xapi": "present"},
        )
        for state in ("closed", "closed_merged"):
            for enrollment in enrollments:
                with self.subTest(state=state, enrollment=enrollment):
                    decision = notify(
                        enrolled(
                            state=state,
                            enrollment=enrollment,
                            openclaw_result="unknown",
                            clawsweeper_result="unknown",
                        )
                    )
                    self.assertFalse(decision["review_dispatch"])
                    self.assertFalse(decision["legacy_dispatch"])
                    self.assertFalse(decision["clawsweeper_eligible"])
                    self.assertFalse(decision["merge_ready_eligible"])
                    self.assertEqual(decision["notification"]["eligibility"], "silent")
                    self.assertEqual(decision["notification"]["channels"], [])
                    self.assertEqual(decision["reason"], "terminal_closed_non_dispatchable")
                    self.assertNotEqual(decision["notification"]["eligibility"], "blocked")

    def test_fail_closed_is_not_a_notification_eligibility(self):
        self.assertNotIn("fail_closed", outcome.NOTIFICATION_ELIGIBILITIES)
        self.assertIn("fail_closed", outcome.ROUTES)
        with self.assertRaises(outcome.OrchestrationError):
            outcome._notification("fail_closed", None)

    def test_missing_row_enrollment_fails_closed_instead_of_granting_conductor(self):
        decision = outcome.decide_orchestration_outcome(
            outcome.outcome_from_review_row(
                {"state": "ci_failed", "rail": None, "repair_cycle": 0}
            )
        )
        self.assertEqual(decision["route"], "fail_closed")
        self.assertFalse(decision["review_dispatch"])
        self.assertEqual(decision["reason"], "ambiguous_or_broken_enrollment")
        self.assertEqual(decision["notification"]["eligibility"], "blocked")

    def test_trusted_enrollment_resolution_covers_runtime_routes(self):
        self.assertEqual(
            outcome.resolve_trusted_enrollment({}),
            {"review_conductor": "present", "legacy_xapi": "absent"},
        )
        self.assertEqual(
            outcome.resolve_trusted_enrollment(
                {"enrollment": {"enabled": True, "blockers": []}}
            ),
            {"review_conductor": "present", "legacy_xapi": "absent"},
        )
        cases = (
            ({"review_conductor": "present", "legacy_xapi": "absent"}, "present"),
            ({"review_conductor": "absent", "legacy_xapi": "present"}, "absent"),
            ({"review_conductor": "present", "legacy_xapi": "present"}, "present"),
            ({"review_conductor": "absent", "legacy_xapi": "absent"}, "absent"),
            ({"review_conductor": "broken", "legacy_xapi": "absent"}, "broken"),
        )
        for enrollment, review_status in cases:
            with self.subTest(enrollment=enrollment):
                self.assertEqual(
                    outcome.resolve_trusted_enrollment({"enrollment": enrollment}),
                    enrollment,
                )
                self.assertEqual(enrollment["review_conductor"], review_status)
        self.assertEqual(
            outcome.resolve_trusted_enrollment(
                {"enrollment": {"review_conductor": "present"}}
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        self.assertEqual(
            outcome.resolve_trusted_enrollment({"enrollment": {"enabled": False, "blockers": ["x"]}}),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )
        trusted = {"review_conductor": "absent", "legacy_xapi": "absent"}
        self.assertEqual(
            outcome.effective_trusted_enrollment({"enrollment": trusted}, trusted),
            trusted,
        )
        self.assertEqual(
            outcome.effective_trusted_enrollment(
                {"enrollment": trusted},
                {"review_conductor": "present", "legacy_xapi": "absent"},
            ),
            {"review_conductor": "broken", "legacy_xapi": "broken"},
        )

    def test_repair_cycle_saturates_and_required_fix_stays_scoped(self):
        self.assertEqual(outcome.next_repair_cycle(0), 1)
        self.assertEqual(outcome.next_repair_cycle(1), 2)
        self.assertEqual(outcome.next_repair_cycle(2), 2)
        decision = notify(
            enrolled(
                state="repair_required",
                rail="openclaw",
                repair_cycle=2,
                head_changed=True,
                openclaw_result="findings",
                clawsweeper_result="absent",
                adjudication_dispositions=["required_fix"],
            )
        )
        self.assertEqual(decision["repair"]["cycle"], 2)
        self.assertEqual(decision["repair"]["automatic_rounds_remaining"], 0)
        self.assertTrue(decision["repair"]["ledger_preserved"])
        self.assertTrue(decision["review_dispatch"])
        self.assertFalse(decision["legacy_dispatch"])
        self.assertEqual(decision["notification"]["eligibility"], "silent")
        self.assertEqual(decision["reason"], "third_set_adjudication_silent")

    def test_schema_file_matches_the_python_contract(self):
        schema = json.loads(
            (ROOT / "contracts/orchestration-outcome.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema"]["const"], outcome.OUTCOME_SCHEMA)
        self.assertEqual(set(schema["required"]), set(outcome.OUTCOME_KEYS))
        self.assertEqual(set(schema["properties"]["route"]["enum"]), set(outcome.ROUTES))
        branches = schema["properties"]["notification"]["oneOf"]
        self.assertEqual(len(branches), 3)
        eligibilities = set()
        for branch in branches:
            eligibility = branch["properties"]["eligibility"]
            if "const" in eligibility:
                eligibilities.add(eligibility["const"])
            else:
                eligibilities.update(eligibility["enum"])
        self.assertEqual(eligibilities, set(outcome.NOTIFICATION_ELIGIBILITIES))
        self.assertNotIn("fail_closed", eligibilities)
        self.assertEqual(schema["properties"]["legacy_dispatch"].get("const"), False)
        self.assertFalse(schema["additionalProperties"])

    def test_notification_schema_accepts_producer_samples_and_rejects_impossibles(self):
        samples = [
            notify(enrolled()),
            notify(
                enrolled(
                    state="ready_for_human_merge",
                    rail="clawsweeper",
                    openclaw_result="clean",
                    clawsweeper_result="effectively_clean",
                    ready_qualified=True,
                )
            ),
            notify(enrolled(state="ci_failed")),
            notify(
                enrolled(enrollment={"review_conductor": "absent", "legacy_xapi": "absent"})
            ),
            notify(
                enrolled(enrollment={"review_conductor": "broken", "legacy_xapi": "absent"})
            ),
        ]
        for decision in samples:
            with self.subTest(reason=decision["reason"]):
                self.assertTrue(notification_matches_schema(decision["notification"]))
        impossibles = (
            {"eligibility": "merge_ready", "kind": None, "channels": []},
            {
                "eligibility": "silent",
                "kind": "human_action_required",
                "channels": list(outcome.NOTIFY_CHANNELS),
            },
            {"eligibility": "blocked", "kind": "merge_ready", "channels": list(outcome.NOTIFY_CHANNELS)},
            {"eligibility": "none", "kind": "merge_ready", "channels": list(outcome.NOTIFY_CHANNELS)},
            {"eligibility": "blocked", "kind": "human_action_required", "channels": []},
            {"eligibility": "merge_ready", "kind": "merge_ready", "channels": ["discord"]},
            {
                "eligibility": "merge_ready",
                "kind": "human_action_required",
                "channels": list(outcome.NOTIFY_CHANNELS),
            },
        )
        for raw in impossibles:
            with self.subTest(raw=raw):
                self.assertFalse(notification_matches_schema(raw))


class PreciseMutantTests(unittest.TestCase):
    MUTANTS = (
        (
            "notify awaiting_adjudication",
            '    if state in SILENT_INTERNAL_STATES and not value["human_gate"]:\n',
            "    if False and state in SILENT_INTERNAL_STATES and not value[\"human_gate\"]:\n",
            "test_first_and_second_repair_rounds_are_silent",
        ),
        (
            "notify repair_required by dropping the silent set",
            'SILENT_INTERNAL_STATES = frozenset({"awaiting_adjudication", "repair_required"})\n',
            'SILENT_INTERNAL_STATES = frozenset({"awaiting_adjudication"})\n',
            "test_first_and_second_repair_rounds_are_silent",
        ),
        (
            "treat unenrolled as review_conductor",
            '    return "none", "unenrolled_no_review_no_notification"\n',
            '    return "review_conductor", "unenrolled_no_review_no_notification"\n',
            "test_neither_enrolled_ends_without_review_or_notification",
        ),
        (
            "treat broken enrollment as unenrolled",
            '    if review_conductor == "broken" or legacy_xapi == "broken":\n',
            "    if False and (review_conductor == \"broken\" or legacy_xapi == \"broken\"):\n",
            "test_broken_enrollment_is_not_treated_as_unenrolled",
        ),
        (
            "allow clawsweeper after openclaw findings",
            "    if openclaw_result in BLOCKING_OPENCLAW:\n        clawsweeper_eligible = False\n",
            "    if False and openclaw_result in BLOCKING_OPENCLAW:\n        clawsweeper_eligible = False\n",
            "test_openclaw_findings_suppress_clawsweeper",
        ),
        (
            "allow merge-ready after clawsweeper findings",
            "    if clawsweeper_result in BLOCKING_CLAWSWEEPER:\n        merge_ready_eligible = False\n",
            "    if False and clawsweeper_result in BLOCKING_CLAWSWEEPER:\n        merge_ready_eligible = False\n",
            "test_clawsweeper_findings_suppress_merge_ready",
        ),
        (
            "reset ledger on changed head",
            "    repair_cycle = value[\"repair_cycle\"]\n",
            '    repair_cycle = 0 if value["head_changed"] else value["repair_cycle"]\n',
            "test_changed_head_preserves_the_repair_ledger",
        ),
        (
            "treat unknown state as silent",
            '        return _fail_closed_blocked(repair, "unknown_state_fail_closed")\n',
            '        return _fail_closed_blocked(repair, "silent_internal_progression")\n',
            "test_unknown_state_and_rail_results_are_fail_closed_not_silent",
        ),
        (
            "keep fail-closed routing off the blocked notify path",
            'def _fail_closed_blocked(repair: dict[str, Any], reason: str) -> dict[str, Any]:\n    """Suppress routing/dispatch while remaining eligible for blocked notify."""\n    return _outcome(\n        route="fail_closed",\n        review_dispatch=False,\n        legacy_dispatch=False,\n        clawsweeper_eligible=False,\n        merge_ready_eligible=False,\n        notification=_notification("blocked", "human_action_required"),\n',
            'def _fail_closed_blocked(repair: dict[str, Any], reason: str) -> dict[str, Any]:\n    """Suppress routing/dispatch while remaining eligible for blocked notify."""\n    return _outcome(\n        route="fail_closed",\n        review_dispatch=False,\n        legacy_dispatch=False,\n        clawsweeper_eligible=False,\n        merge_ready_eligible=False,\n        notification=_notification("fail_closed", None),\n',
            "test_unknown_state_and_rail_results_are_fail_closed_not_silent",
        ),
        (
            "encode legacy x-api dispatch",
            '    if route == "legacy_xapi":\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n            legacy_dispatch=False,\n',
            '    if route == "legacy_xapi":\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n            legacy_dispatch=True,\n',
            "test_legacy_only_is_untouched_and_does_not_notify",
        ),
        (
            "increment automatic ledger past two",
            "    if current >= MAX_REPAIR_CYCLES:\n        return MAX_REPAIR_CYCLES\n",
            "    if False and current >= MAX_REPAIR_CYCLES:\n        return MAX_REPAIR_CYCLES\n",
            "test_repair_cycle_saturates_and_required_fix_stays_scoped",
        ),
        (
            "merge-ready without ready_qualified",
            "        and ready_qualified is True\n",
            "        and ready_qualified is not False\n",
            "test_ready_state_without_ready_policy_stays_silent",
        ),
        (
            "duplicate legacy dispatch when both enrolled",
            '            return "review_conductor", "review_conductor_wins_duplicate_legacy_forbidden"\n',
            '            return "legacy_xapi", "review_conductor_wins_duplicate_legacy_forbidden"\n',
            "test_dual_enrollment_review_conductor_wins_without_legacy_dispatch",
        ),
        (
            "dispatch reviews on closed heads",
            '    if state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n',
            '    if state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n            route=route,\n            review_dispatch=True,\n',
            "test_closed_states_are_terminal_and_non_dispatchable",
        ),
        (
            "skip the closed-state dispatch fence",
            "    if state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n",
            "    if False and state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n",
            "test_closed_states_are_terminal_and_non_dispatchable",
        ),
        (
            "handle closed after broken enrollment short-circuit",
            '    if state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n            legacy_dispatch=False,\n            clawsweeper_eligible=False,\n            merge_ready_eligible=False,\n            notification=_notification("silent", None),\n            repair=repair,\n            reason="terminal_closed_non_dispatchable",\n        )\n    if state not in KNOWN_STATES:\n',
            '    if route == "fail_closed":\n        return _fail_closed_blocked(repair, route_reason)\n    if state in TERMINAL_CLOSED_STATES:\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n            legacy_dispatch=False,\n            clawsweeper_eligible=False,\n            merge_ready_eligible=False,\n            notification=_notification("silent", None),\n            repair=repair,\n            reason="terminal_closed_non_dispatchable",\n        )\n    if state not in KNOWN_STATES:\n',
            "test_closed_states_remain_silent_when_enrollment_is_broken",
        ),
        (
            "advertise fail_closed notification eligibility",
            'NOTIFICATION_ELIGIBILITIES = frozenset(\n    {"silent", "merge_ready", "blocked", "none"}\n)\n',
            'NOTIFICATION_ELIGIBILITIES = frozenset(\n    {"silent", "merge_ready", "blocked", "none", "fail_closed"}\n)\n',
            "test_fail_closed_is_not_a_notification_eligibility",
        ),
        (
            "default missing enrollment to Review Conductor present",
            '        enrollment = {"review_conductor": "broken", "legacy_xapi": "broken"}\n',
            '        enrollment = {"review_conductor": "present", "legacy_xapi": "absent"}\n',
            "test_missing_row_enrollment_fails_closed_instead_of_granting_conductor",
        ),
        (
            "treat a contradicting caller enrollment as trusted",
            '    if not isinstance(claimed, Mapping) or dict(claimed) != trusted:\n        return {"review_conductor": "broken", "legacy_xapi": "broken"}\n',
            '    if False and (not isinstance(claimed, Mapping) or dict(claimed) != trusted):\n        return {"review_conductor": "broken", "legacy_xapi": "broken"}\n',
            "test_trusted_enrollment_resolution_covers_runtime_routes",
        ),
        (
            "let human_gate produce merge_ready",
            '    if value["human_gate"]:\n        return _outcome(\n            route=route,\n            review_dispatch=True,\n            legacy_dispatch=False,\n            clawsweeper_eligible=False,\n            merge_ready_eligible=False,\n            notification=_notification("blocked", "human_action_required"),\n            repair=repair,\n            reason="human_action_required",\n        )\n',
            "    if False and value[\"human_gate\"]:\n        return _outcome(\n            route=route,\n            review_dispatch=True,\n            legacy_dispatch=False,\n            clawsweeper_eligible=False,\n            merge_ready_eligible=False,\n            notification=_notification(\"blocked\", \"human_action_required\"),\n            repair=repair,\n            reason=\"human_action_required\",\n        )\n",
            "test_human_gate_precedes_terminal_ready_and_nonterminal_dispatch",
        ),
        (
            "emit merge_ready with a null kind",
            '        if kind not in {"merge_ready", "human_action_required"}:\n            _fail("notification kind is required for a terminal send")\n        channels = list(NOTIFY_CHANNELS)',
            '        kind = None\n        channels = list(NOTIFY_CHANNELS)',
            "test_notification_schema_accepts_producer_samples_and_rejects_impossibles",
        ),
        (
            "skip state/rail/result tuple consistency",
            '    if not _consistent_state_rail_results(state, rail, openclaw_result, clawsweeper_result):\n        return _fail_closed_blocked(repair, "inconsistent_state_rail_result_fail_closed")\n',
            "    if False and not _consistent_state_rail_results(state, rail, openclaw_result, clawsweeper_result):\n        return _fail_closed_blocked(repair, \"inconsistent_state_rail_result_fail_closed\")\n",
            "test_inconsistent_state_rail_results_fail_closed_before_dispatch",
        ),
        (
            "let the row mapper raise instead of mapping unknown results",
            '    try:\n        openclaw_result, clawsweeper_result = derive_rail_results(state, rail)\n    except OrchestrationError:\n        openclaw_result, clawsweeper_result = "unknown", "unknown"\n',
            "    openclaw_result, clawsweeper_result = derive_rail_results(state, rail)\n",
            "test_persisted_row_adapter_maps_inconsistent_rows_to_unknown",
        ),
        (
            "short-circuit unenrolled before tuple validation",
            '    if not _consistent_state_rail_results(state, rail, openclaw_result, clawsweeper_result):\n        return _fail_closed_blocked(repair, "inconsistent_state_rail_result_fail_closed")\n    if route == "fail_closed":\n        return _fail_closed_blocked(repair, route_reason)\n    if route == "none":\n',
            '    if route == "none":\n        return _outcome(\n            route=route,\n            review_dispatch=False,\n            legacy_dispatch=False,\n            clawsweeper_eligible=False,\n            merge_ready_eligible=False,\n            notification=_notification("none", None),\n            repair=repair,\n            reason=route_reason,\n        )\n    if not _consistent_state_rail_results(state, rail, openclaw_result, clawsweeper_result):\n        return _fail_closed_blocked(repair, "inconsistent_state_rail_result_fail_closed")\n    if route == "fail_closed":\n        return _fail_closed_blocked(repair, route_reason)\n    if route == "none":\n',
            "test_unenrolled_and_legacy_inconsistent_tuples_fail_closed_before_short_circuit",
        ),
        (
            "accept mismatched rails in the complete-tuple predicate",
            "    if not _rail_matches_state(state, rail):\n        return False\n",
            "    if False and not _rail_matches_state(state, rail):\n        return False\n",
            "test_mismatched_rails_fail_closed_without_review_dispatch",
        ),
        (
            "skip rail-aware validation in persisted-row reconstruction",
            '    if not _rail_matches_state(state, rail):\n        if state in SILENT_INTERNAL_STATES | {"waiting_human"}:\n            _fail("finding, repair, and human-gate states require an exact rail")\n        _fail("state and rail are inconsistent")\n',
            "    if False and not _rail_matches_state(state, rail):\n        _fail(\"state and rail are inconsistent\")\n",
            "test_persisted_row_adapter_maps_mismatched_rails_to_unknown",
        ),
        (
            "membership-check unhashable enrollment statuses",
            "    if type(value) is not str or value not in ENROLLMENT_STATUSES:\n",
            "    if value not in ENROLLMENT_STATUSES:\n",
            "test_unhashable_enum_inputs_raise_orchestration_error",
        ),
        (
            "membership-check unhashable rail results",
            "    if type(value) is not str or value not in RAIL_RESULTS:\n",
            "    if value not in RAIL_RESULTS:\n",
            "test_unhashable_enum_inputs_raise_orchestration_error",
        ),
        (
            "membership-check unhashable dispositions",
            "        if type(item) is not str or item not in DISPOSITIONS:\n",
            "        if item not in DISPOSITIONS:\n",
            "test_unhashable_enum_inputs_raise_orchestration_error",
        ),
        (
            "membership-check unhashable state tokens",
            "    state = value[\"state\"]\n    rail = value[\"rail\"]\n    if type(state) is not str:\n        _fail(\"state is unknown\")\n",
            "    state = value[\"state\"]\n    rail = value[\"rail\"]\n    if False and type(state) is not str:\n        _fail(\"state is unknown\")\n",
            "test_unhashable_enum_inputs_raise_orchestration_error",
        ),
        (
            "coerce persisted quality flags with bool()",
            "    if type(raw) is int and raw in (0, 1):\n        return raw == 1, True\n    return None, False\n",
            "    return bool(raw), True\n",
            "test_persisted_quality_flags_accept_only_integer_zero_one",
        ),
    )

    def test_precise_orchestration_outcome_mutants(self):
        source = (ROOT / "tools" / "orchestration_outcome.py").read_text(encoding="utf-8")
        for label, old, new, test_name in self.MUTANTS:
            with self.subTest(mutant=label):
                self.assertEqual(source.count(old), 1, f"mutant anchor drifted: {label}")
                with tempfile.TemporaryDirectory(prefix="review-conductor-mutant-") as temp_name:
                    copy_root = Path(temp_name) / "copy"
                    for name in ("tools", "tests", "contracts"):
                        shutil.copytree(ROOT / name, copy_root / name)
                    target = copy_root / "tools" / "orchestration_outcome.py"
                    target.write_text(source.replace(old, new, 1), encoding="utf-8")
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(copy_root / "tests" / "test_orchestration_outcome.py"),
                            test_name,
                        ],
                        cwd=copy_root,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env={
                            "PATH": "/usr/bin:/bin",
                            "HOME": temp_name,
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                    )
                self.assertNotEqual(
                    completed.returncode, 0, f"mutant survived: {label}\n{completed.stderr}"
                )
                self.assertTrue(
                    "AssertionError" in completed.stderr
                    or "assert " in completed.stderr
                    or "OrchestrationError" in completed.stderr,
                    f"mutant did not fail its intended assertion: {label}\n{completed.stderr}",
                )


def main() -> int:
    loader = unittest.TestLoader()
    selected = [argument for argument in sys.argv[1:] if not argument.startswith("-")]
    if selected:
        suite = unittest.TestSuite()
        full = loader.loadTestsFromModule(sys.modules[__name__])
        for test in full:
            for case in test:
                if case.id().rsplit(".", 1)[-1] in selected:
                    suite.addTest(case)
        if suite.countTestCases() == 0:
            raise SystemExit(f"unknown orchestration outcome tests: {selected}")
    else:
        suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
