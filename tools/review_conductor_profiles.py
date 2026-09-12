"""Validated repository isolation for the shared, single-profile-per-process engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import review_conductor as core

SCHEMA = 'smoky.review-conductor.userland.v2'
ROLES = ('webhook-verify', 'github-installation', 'cloudflare-tunnel')


def capabilities(config: dict[str, Any]) -> tuple[str, ...]:
    namespace = config.get('profile_id', 'blocks')
    return tuple(f'review-conductor.{namespace}.{role}' for role in ROLES)


def require_enabled(config: dict[str, Any]) -> None:
    if config.get('enrollment', {}).get('enabled', True) is not True:
        raise core.ContractError('repository profile is inactive: ' + '; '.join(config['enrollment']['blockers']))


def validate_profile(config: dict[str, Any], core_config: dict[str, Any]) -> None:
    for key in ('profile_id', 'enrollment', 'adapter'):
        if key not in config:
            raise core.ContractError(f'generalized profile omitted {key}')
    profile = core.require_text(config['profile_id'], 'profile_id', 80)
    if not core.SAFE_ID_RE.fullmatch(profile) or ':' in profile:
        raise core.ContractError('profile_id is unsafe')
    enrollment = core.require_object(config['enrollment'], 'enrollment')
    core.require_exact_keys(enrollment, {'enabled', 'blockers'}, set(), 'enrollment')
    if type(enrollment['enabled']) is not bool or not isinstance(enrollment['blockers'], list):
        raise core.ContractError('invalid enrollment status')
    for blocker in enrollment['blockers']:
        core.require_text(blocker, 'enrollment blocker')
    if enrollment['enabled'] and enrollment['blockers']:
        raise core.ContractError('enabled profile has unresolved enrollment blockers')
    if enrollment['enabled'] != core_config.get('review_policy', {}).get('enabled'):
        raise core.ContractError('runtime and core enrollment disagree')
    adapter = core.require_object(config['adapter'], 'adapter')
    core.require_exact_keys(adapter, {'artifact_prefix', 'contract'}, set(), 'adapter')
    if adapter['contract'] != 'exact-tuple-comprehensive-v1':
        raise core.ContractError('unsupported trusted adapter contract')
    prefix = core.require_text(adapter['artifact_prefix'], 'artifact prefix', 100)
    if not core.SAFE_ID_RE.fullmatch(prefix):
        raise core.ContractError('unsafe artifact prefix')


def validate_isolation(configs: list[dict[str, Any]]) -> None:
    """Compare public deployment metadata, never credential values.

    Paths are resolved (including existing parent symlinks). Nested roots count
    as overlap; inboxes may nest within their own proof root, never a peer's.
    """
    def paths(c: dict[str, Any]) -> list[Path]:
        return [Path(c['paths'][k]).resolve() for k in ('state_root', 'proof_root', 'blocks_checkout')] + [Path(c['onepassword']['bootstrap_root']).resolve(), Path(c['spark']['terminal_inbox']).resolve(), Path(c['clawsweeper_bridge']['terminal_inbox']).resolve()]
    def scalars(c: dict[str, Any]) -> set[tuple[str, Any]]:
        app = c['github_app']
        values = {('repository', app['repository'].lower()), ('repository_id', app['repository_id']), ('port', c['ingress']['bind_port'])}
        for key in ('app_id', 'installation_id'):
            if app[key] is not None:
                values.add((key, app[key]))
        for key in ('public_hostname',):
            if c['ingress'][key]:
                values.add((key, c['ingress'][key].lower()))
        if c['tunnel']['tunnel_id']:
            values.add(('tunnel', c['tunnel']['tunnel_id']))
        for value in c['credentials'].values():
            values.add(('credential_env', value))
        for domain in c['onepassword']['domains'].values():
            values.add(('vault', domain['runtime_vault']))
            values.add(('capability', domain['capability_id']))
        if c.get('openclaw'):
            values.add(('operator', c['openclaw']['operator_id']))
            values.add(('remote_shelf', c['openclaw']['remote_worktree_shelf']))
        values.add(('session', c['notifications']['session_key']))
        for key in ('discord_target_env', 'signal_target_env'):
            values.add(('destination_env', c['notifications'][key]))
        return values
    for index, config in enumerate(configs):
        resolved = paths(config)
        own = resolved[:4]
        inboxes = resolved[4:]
        if any(path == own[1] or not path.is_relative_to(own[1]) for path in inboxes):
            raise core.ContractError('terminal inbox must be inside its own proof root')
        if inboxes[0].is_relative_to(inboxes[1]) or inboxes[1].is_relative_to(inboxes[0]):
            raise core.ContractError('terminal inboxes must be distinct')
        for i, left in enumerate(own):
            for right in own[i + 1:]:
                if left.is_relative_to(right) or right.is_relative_to(left):
                    raise core.ContractError('profile roots overlap')
        for peer in configs[:index]:
            if scalars(config) & scalars(peer):
                raise core.ContractError('repository profiles share an isolation boundary')
            for left in paths(config):
                for right in paths(peer):
                    if left.is_relative_to(right) or right.is_relative_to(left):
                        raise core.ContractError('repository profile paths overlap')
