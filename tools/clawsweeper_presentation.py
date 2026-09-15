"""Bounded presentation of an accepted native report; never review policy.

Vocabulary/presentation provenance: saari-co/clawsweeper at
f1d79d1234897abca168807faf8a2088525a0e98, clawsweeper-policy.ts,
clawsweeper-label-selection.ts and clawsweeper-report-comment-presentation.ts.
No fallback grading, readiness, workflow commands or producer apply logic.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any


REPORT_MAX_BYTES = 512 * 1024  # Native exact-tuple export bound.
COMMENT_MAX_BYTES = 48 * 1024  # Conservative local publication budget.
RICH_MARKER = "<!-- review-conductor:native-presentation-v1 -->"
RATINGS = {
    "S": ("🦀 challenger crab", "6/6"),
    "A": ("🦞 diamond lobster", "5/6"),
    "B": ("🐚 platinum hermit", "4/6"),
    "C": ("🦐 gold shrimp", "3/6"),
    "D": ("🦪 silver shellfish", "2/6"),
    "F": ("🧂 unranked krab", "1/6"),
    "NA": ("🌊 off-meta tidepool", "N/A"),
}
FAMILIES = {
    "rating": frozenset(f"rating: {name}" for name, _score in RATINGS.values()),
    "priority": frozenset({"P0", "P1", "P2", "P3"}),
    "proof": frozenset({"proof: sufficient"}),
    "media": frozenset({"proof: 📸 screenshot", "proof: 🎥 video"}),
    "risk": frozenset(f"merge-risk: 🚨 {name}" for name in (
        "compatibility", "message-delivery", "session-state", "auth-provider",
        "security-boundary", "availability", "automation", "other",
    )),
}
NATIVE_LABELS = frozenset().union(*FAMILIES.values())
PUBLIC_SECTIONS = (
    "Summary", "What This Changes", "System Context", "Architecture Diagram",
    "Review Findings", "Security Review", "Real Behavior Proof", "PR Rating",
    "Live Proof", "Evidence", "Best Possible Solution", "Maintainer Decision",
    "Risks / Open Questions",
)


class PresentationError(ValueError):
    """Native presentation is unavailable or ambiguous; do not publish it."""


def parse_report(text: str, identity: dict[str, Any], *, actor: str | None) -> dict[str, Any]:
    """Check accepted bytes/identity again, without regrading or reclassifying."""
    raw = text.encode("utf-8")
    if len(raw) > REPORT_MAX_BYTES or hashlib.sha256(raw).hexdigest() != identity["artifact_digest"]:
        raise PresentationError("native report digest or size does not match")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise PresentationError("native report contains control characters")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match or len(match[1].encode()) > 64 * 1024:
        raise PresentationError("native report frontmatter is missing or oversized")
    fields: dict[str, str] = {}
    for line in match[1].splitlines():
        item = re.fullmatch(r"([a-z][a-z0-9_]*):\s*(.*)", line)
        if not item:
            if line.strip():
                raise PresentationError("native report frontmatter is malformed")
            continue
        if item[1] in fields:
            raise PresentationError("native report metadata is ambiguous")
        fields[item[1]] = item[2].strip().strip('"')
    expected = {
        "repository": identity["repository"], "number": str(identity["pr_number"]),
        "main_sha": identity["base_sha"], "pull_head_sha": identity["head_sha"],
        "review_status": "complete",
    }
    if actor is not None:
        expected.update(review_epoch=str(identity["review_epoch"]),
                        review_scope="comprehensive", reviewer_actor=actor)
    if any(fields.get(key) != value for key, value in expected.items()):
        raise PresentationError("native report is not the accepted exact identity")
    # Exact H2 sections only. Never publish raw frontmatter, snapshots, telemetry,
    # work prompts, close comments or arbitrary report sections.
    sections: dict[str, str] = {}
    chunks = re.split(r"(?m)^## ([^\r\n]+)\r?$", text[match.end():])
    for title, body in zip(chunks[1::2], chunks[2::2]):
        if title in PUBLIC_SECTIONS:
            if title in sections:
                raise PresentationError("native report public section is ambiguous")
            sections[title] = body.strip()
    families: set[str] = set()
    labels: set[str] = set()
    for key in ("pr_rating_overall", "pr_rating_proof", "pr_rating_patch"):
        if key in fields and fields[key] not in RATINGS:
            raise PresentationError("native report rating is invalid")
    failed = fields.get("review_terminal_failure", "false")
    if failed not in {"false", "true"}:
        raise PresentationError("native terminal failure flag is invalid")
    if "pr_rating_overall" in fields:
        families.add("rating")
        if failed != "true":
            labels.add("rating: " + RATINGS[fields["pr_rating_overall"]][0])
    if "triage_priority" in fields:
        priority = fields["triage_priority"]
        if priority not in FAMILIES["priority"] | {"none"}:
            raise PresentationError("native triage priority is invalid")
        families.add("priority")
        if priority != "none":
            labels.add(priority)
    if "real_behavior_proof_status" in fields:
        if fields["real_behavior_proof_status"] not in {
            "sufficient", "not_applicable", "not_needed", "insufficient", "missing", "failed", "required",
        }:
            raise PresentationError("native proof status is invalid")
        families.add("proof")
        if fields["real_behavior_proof_status"] == "sufficient":
            labels.add("proof: sufficient")
    if "real_behavior_proof_evidence_kind" in fields:
        kind = fields["real_behavior_proof_evidence_kind"]
        if kind not in {"none", "not_applicable", "screenshot", "recording", "terminal", "logs", "live_output", "linked_artifact"}:
            raise PresentationError("native proof evidence kind is invalid")
        families.add("media")
        if kind in {"screenshot", "recording"}:
            labels.add("proof: 📸 screenshot" if kind == "screenshot" else "proof: 🎥 video")
    if "merge_risk_labels" in fields:
        try:
            risks = json.loads(fields["merge_risk_labels"])
        except ValueError as exc:
            raise PresentationError("native merge-risk labels are invalid") from exc
        if not isinstance(risks, list) or len(risks) > 32 or any(
            not isinstance(label, str) or label not in FAMILIES["risk"] for label in risks
        ):
            raise PresentationError("native merge-risk labels are outside the native allowlist")
        families.add("risk")
        labels.update(risks)
    return {"identity": dict(identity), "fields": fields, "sections": sections,
            "labels": sorted(labels), "families": sorted(families)}


def managed_labels(report: dict[str, Any]) -> set[str]:
    return set().union(*(FAMILIES[name] for name in report["families"]))


def quoted(text: str, limit: int = 4500) -> str:
    """Bound and quote native prose; no HTML, mentions, images or control fences."""
    omitted = len(text) > limit
    text = text[:limit]
    text = html.escape(text, quote=False).replace("@", "@\u200b").replace("\\", "\\\\")
    text = text.replace("`", "\\`").replace("![", "\\![")
    text = re.sub(r"(?m)^(\s*)(#{1,6}|~~~)", r"\1\\\2", text)
    lines = [("> " + line).rstrip() for line in text.splitlines()]
    if omitted:
        lines.append("> _Excerpt truncated; consult the original accepted artifact below._")
    return "\n".join(lines) or "> Not supplied by the native report."


def safe_diagram(text: str) -> str | None:
    text = text.strip()
    if not text or text.lower() in {"none", "none.", "not applicable", "n/a", "_none_"}:
        return None
    if len(text.encode()) > 4096 or len(text.splitlines()) > 80:
        return None
    if not re.match(r"\Aflowchart[ \t]+(?:TD|TB|BT|RL|LR)[ \t]*;?[ \t]*(?:\n|$)", text, re.I):
        return None
    if re.search(r"`|~~~|@\{|<[/!a-zA-Z]|//|%%\{|^\s*#|\b(?:data|javascript|vbscript|https?|ftp|file|blob|mailto):\S", text, re.M | re.I):
        return None
    if any(re.match(r"\s*(?:click|style|classDef|class|linkStyle)\b", line, re.I)
           for line in re.split(r"[;\r\n]+", text)):
        return None
    if re.search(r"\bclick\s+[\w-]+\s+(?:href|call)\b|\b(?:style|linkStyle|classDef)\s+[\w-]+\s+[\w-]+\s*:", text, re.I):
        return None
    return text


def render(report: dict[str, Any]) -> str:
    fields, sections = report["fields"], report["sections"]
    lines = [RICH_MARKER, "# ClawSweeper review", "",
             "Native evaluation from the accepted report. Conductor status and human-only merge authority remain separate.", ""]

    def section(title: str, content: str | None, limit: int = 4500) -> None:
        lines.extend(["## " + title, "", quoted(content or "", limit), ""])

    section("What This Changes", sections.get("What This Changes") or sections.get("Summary"))
    lines.extend(["## Review scores", "", "| Dimension | Native tier | Score |", "| --- | --- | --- |"])
    for title, key in (("Overall", "pr_rating_overall"), ("Proof", "pr_rating_proof"), ("Patch", "pr_rating_patch")):
        tier = fields.get(key)
        name, score = RATINGS[tier] if tier else ("Not supplied", "—")
        lines.append(f"| {title} | {tier + ' · ' if tier else ''}{name} | {score} |")
    lines.extend(["", "Scores are the producer's categorical tier scale, not a new assessment.", ""])
    section("Rating explanation and next rank-up steps", sections.get("PR Rating"), 3000)
    lines.extend(["## Verification", "",
                  "- Native proof status: " + fields.get("real_behavior_proof_status", "not supplied"),
                  "- Native evidence kind: " + fields.get("real_behavior_proof_evidence_kind", "not supplied"), ""])
    lines.extend([quoted(sections.get("Real Behavior Proof", "")), ""])
    if sections.get("Live Proof"):
        section("Live proof", sections["Live Proof"], 2000)
    section("Evidence", sections.get("Evidence"), 4000)
    if sections.get("System Context"):
        section("How this fits together", sections["System Context"], 3000)
    diagram = safe_diagram(sections.get("Architecture Diagram", ""))
    if diagram:
        lines.extend(["```mermaid", diagram, "```", ""])
    elif sections.get("Architecture Diagram"):
        lines.extend(["_Native diagram absent or outside the safe presentation subset; see the original artifact._", ""])
    section("Findings", sections.get("Review Findings"))
    if sections.get("Security Review"):
        section("Security", sections["Security Review"], 2000)
    section("Next steps", sections.get("Best Possible Solution"), 3000)
    if sections.get("Maintainer Decision"):
        section("Native maintainer question", sections["Maintainer Decision"], 2000)
    if sections.get("Risks / Open Questions"):
        section("Risks / Open Questions", sections["Risks / Open Questions"], 2000)
    lines.extend(["_Selected native public sections only; omitted internal metadata is retained in the original accepted artifact._", ""])
    return "\n".join(lines)
