"""Strict, bounded repository requirements; never authority or executable config."""
import json
import re

SCHEMA = "review-conductor.target.v2"
MAX_BYTES = 16384

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result

def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"invalid {label} keys")

def validate_manifest(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("manifest exceeds 16384 bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid JSON encoding or nesting") from exc
    exact(value, ["schema", "repository", "default_branch", "ci", "review", "merge_policy"], "manifest")
    if value["schema"] != SCHEMA or value["merge_policy"] != "human_only":
        raise ValueError("unsupported schema or merge policy")
    repo = value["repository"]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", repo):
        raise ValueError("invalid repository")
    branch = value["default_branch"]
    if (not isinstance(branch, str) or
            not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,254}", branch) or
            any(x in branch for x in ("..", "//")) or branch.endswith(("/", ".")) or
            any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))):
        raise ValueError("invalid default branch")
    exact(value["ci"], ["workflow_name", "workflow_path"], "CI")
    ci = value["ci"]
    if not isinstance(ci["workflow_name"], str) or not ci["workflow_name"].strip() or len(ci["workflow_name"]) > 200 or any(ord(c) < 32 for c in ci["workflow_name"]):
        raise ValueError("invalid CI workflow name")
    if not isinstance(ci["workflow_path"], str) or not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml", ci["workflow_path"]):
        raise ValueError("invalid CI workflow path")
    exact(value["review"], ["clawsweeper_requires_ready", "scope", "rails"], "review")
    review = value["review"]
    if review["clawsweeper_requires_ready"] is not True:
        raise ValueError("ClawSweeper must require ready-for-review state")
    if review["scope"] != "comprehensive" or review["rails"] != ["openclaw", "clawsweeper"]:
        raise ValueError("ordered comprehensive review rails required")
    return value
