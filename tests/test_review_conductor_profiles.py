#!/usr/bin/env python3
"""Offline multi-repository security and exact-tuple adapter regression proof."""
from __future__ import annotations
import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import review_conductor as core
import review_conductor_profiles as profiles
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import review_conductor_userland_launcher as launcher
import test_review_conductor_userland as legacy
import test_review_conductor_activation as adapters

REPO = 'saari-co/openclaw-smcbd-suite'
BASE, HEAD = legacy.BASE, legacy.HEAD


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')
    return path


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.source = self.root / 'source'
        self.contracts = self.source / 'contracts/review-conductor'
        self.contracts.mkdir(parents=True)
        for file in (ROOT / 'contracts/review-conductor').glob('*.json'):
            shutil.copyfile(file, self.contracts / file.name)

    def config(self, enabled=True):
        cp = self.contracts / 'openclaw-smcbd-suite.json'
        rp = self.contracts / 'openclaw-smcbd-suite-userland.json'
        c, r = json.loads(cp.read_text()), json.loads(rp.read_text())
        if enabled:
            c['review_policy'].update(enabled=True, reviewers={'openclaw':'fixture-suite-openclaw', 'clawsweeper':'fixture-suite-clawsweeper'})
            r['enrollment'] = {'enabled':True, 'blockers':[]}
            r['ingress']['public_hostname']='fixture-suite.example.test'
            r['github_app'].update(app_id=900001, installation_id=900002)
            r['tunnel'].update(tunnel_id='00000000-0000-4000-8000-000000000001', tunnel_name='fixture-suite-only')
        write(cp,c); write(rp,r)
        return userland.load_config(rp, home=self.home, source_root=self.source)

    def payload(self, payload):
        result = copy.deepcopy(payload)
        result['repository']={'full_name':REPO,'id':1366416798}
        if 'pull_request' in result:
            result['pull_request']['draft']=False
        if 'workflow_run' in result:
            result['workflow_run']['updated_at']='2026-08-29T20:11:00Z'
            if result['workflow']['name']=='CI':
                result['workflow']['name']='Exact-head review pipeline'
            else:
                result['workflow']={'name':'ClawSweeper exact-tuple review','path':'.github/workflows/clawsweeper-exact-tuple.yml'}
        return result

    def ingest(self, config, kind, delivery, payload):
        return legacy.ingress(config,kind,delivery,payload)

    def enqueue(self, config, pr=7):
        self.ingest(config,'pull_request','pr',self.payload(legacy.pr_payload(pr)))
        self.ingest(config,'workflow_run','ci',self.payload(legacy.ci_payload(pr,701)))
        action = legacy.action(config,pr,'openclaw.enqueue')
        legacy.mark_dispatched(config,action['action_id'])
        return action

    def terminal(self, config, action, **overrides):
        path = adapters.openclaw_artifact(self.root,config,action)
        value=json.loads(path.read_text())
        drop = overrides.pop('_drop', ())
        value.update(
            review_scope='comprehensive',
            reviewer_actor='fixture-suite-openclaw',
            native_max_priority='P3',
            applied_max_priority='P3',
            exact_tuple_qualified=True,
        )
        value.update(overrides)
        for key in drop:
            value.pop(key, None)
        return write(path,value)

    def write_openclaw_status(self, run, action, config, **overrides):
        payload = json.loads(action['payload_json'])
        drop = overrides.pop('_drop', ())
        status = json.loads((run / 'REQUEST_STATUS.json').read_text())
        status.update({
            'operator_id': payload['operator_id'],
            'repository': action['repository'],
            'pr_number': action['pr_number'],
            'base_sha': action['base_sha'],
            'head_sha': action['head_sha'],
            'review_epoch': action['review_epoch'],
            'review_scope': 'comprehensive',
            'reviewer_actor': config['review_policy']['reviewers']['openclaw'],
            'native_max_priority': 'P3',
            'applied_max_priority': 'P3',
            'exact_tuple_qualified': True,
        }, **overrides)
        for key in drop:
            status.pop(key, None)
        (run / 'REQUEST_STATUS.json').write_text(json.dumps(status) + '\n')
        return run

    def state(self, config, pr=7):
        connection=core.open_database(Path(config['paths']['state_root']),REPO)
        try:return dict(core.current_head(connection,REPO,pr))
        finally:connection.close()

    def bundle(self, config, action, **overrides):
        raw=legacy.claw_bundle(run_id=801,pr=7)
        files=userland.bounded_zip_files(raw)
        manifest=json.loads(files['manifest.json'])
        manifest['workflow']['repository']=REPO
        manifest['target']['repo']=REPO
        report=files['review/7.md'].decode().replace('dinkuskit/blocks',REPO)
        fields={'review_epoch':str(action['review_epoch']),'review_scope':'comprehensive','reviewer_actor':'fixture-suite-clawsweeper'}
        fields.update(overrides)
        report=report.replace('review_status: complete','review_status: complete\n'+'\n'.join(f'{k}: {v}' for k,v in fields.items())).encode()
        manifest['files'][0].update(bytes=len(report),sha256=hashlib.sha256(report).hexdigest())
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            archive.writestr('review/7.md',report)
        return output.getvalue()

    def test_inactive_profile_cannot_resolve_credentials_or_mutate(self):
        config=self.config(False)
        self.assertEqual(userland.health(config)['overall'],'not_ready')
        with patch.object(userland,'read_inherited_value',side_effect=AssertionError('credential read')):
            with self.assertRaises(core.ContractError):userland.build_client(config)
            with self.assertRaises(core.ContractError):userland.serve(config)
        with self.assertRaises(core.ContractError):userland.run_tick(config,None,None,dry_run=False)
        with self.assertRaises(core.ContractError):launcher.start(config,config_path=Path('unused'))
        with self.assertRaises(core.ContractError):runtime.GitHubAppClient(config,'fixture')
        body=core.canonical_json(self.payload(legacy.pr_payload(7))).encode()
        import hmac
        signature='sha256='+hmac.new(legacy.SECRET.encode(),body,hashlib.sha256).hexdigest()
        with self.assertRaises(core.ContractError):core.ingest_github_delivery(config_path=Path(config['core_config']),state_root=Path(config['paths']['state_root']),event_type='pull_request',delivery_id='inactive',signature=signature,body=body,secret=legacy.SECRET)
        self.assertFalse(Path(config['paths']['state_root']).exists())

    def test_isolation_rejects_every_shared_boundary_and_nested_or_symlink_roots(self):
        suite=self.config()
        blocks=userland.load_config(self.contracts/'dinkuskit-blocks-userland.json',home=self.home,source_root=self.source)
        profiles.validate_isolation([blocks,suite])
        mutations=[('github_app','app_id'),('github_app','installation_id'),('github_app','repository'),('github_app','repository_id'),('ingress','bind_port'),('ingress','public_hostname'),('tunnel','tunnel_id'),('notifications','session_key'),('notifications','discord_target_env'),('credentials','webhook_secret_fd_env'),('credentials','github_private_key_fd_env'),('openclaw','operator_id'),('openclaw','remote_worktree_shelf')]
        for section,key in mutations:
            with self.subTest(section=section,key=key):
                candidate=copy.deepcopy(suite);candidate[section][key]=blocks[section][key]
                with self.assertRaises(core.ContractError):profiles.validate_isolation([blocks,candidate])
        for key in ('state_root','proof_root','blocks_checkout'):
            candidate=copy.deepcopy(suite);candidate['paths'][key]=blocks['paths'][key]+'/nested'
            with self.assertRaises(core.ContractError):profiles.validate_isolation([blocks,candidate])
        candidate=copy.deepcopy(suite)
        next(iter(candidate['onepassword']['domains'].values()))['runtime_vault']=next(iter(blocks['onepassword']['domains'].values()))['runtime_vault']
        with self.assertRaises(core.ContractError):profiles.validate_isolation([blocks,candidate])
        target=Path(blocks['paths']['proof_root']);target.mkdir(parents=True)
        alias=self.home/'alias';alias.symlink_to(target,target_is_directory=True)
        candidate=copy.deepcopy(suite);candidate['paths']['proof_root']=str(alias)
        with self.assertRaises(core.ContractError):profiles.validate_isolation([blocks,candidate])

    def test_state_database_refuses_cross_repository_before_replay_or_recovery(self):
        config=self.config();self.enqueue(config)
        path=Path(config['paths']['state_root'])
        with self.assertRaises(core.ContractError):core.open_database(path,'dinkuskit/blocks')
        blocks=userland.load_config(self.contracts/'dinkuskit-blocks-userland.json',home=self.home,source_root=self.source)
        blocks['paths']['state_root']=str(path)
        with self.assertRaises(core.ContractError):runtime.recover_abandoned_actions(blocks)
        self.assertEqual(self.state(config)['state'],'openclaw_queued')

    def test_draft_runs_openclaw_and_ready_enables_clawsweeper_without_new_epoch(self):
        config=self.config();p=self.payload(legacy.pr_payload(7));p['pull_request']['draft']=True
        self.ingest(config,'pull_request','draft',p)
        receipt=self.ingest(config,'workflow_run','draft-ci',self.payload(legacy.ci_payload(7,701)))
        self.assertEqual(receipt['result'],'accepted')
        action=legacy.action(config,7,'openclaw.enqueue');legacy.mark_dispatched(config,action['action_id'])
        runtime.bridge_openclaw(config,self.terminal(config,action))
        self.assertEqual(self.state(config)['state'],'openclaw_clean_draft')
        self.assertEqual(core.state_projection(self.state(config))['checks']['ClawSweeper Review Rail'], 'queued')
        epoch=self.state(config)['review_epoch']
        p['action']='ready_for_review';p['pull_request'].update(draft=False,updated_at='2026-08-29T20:12:00Z')
        self.ingest(config,'pull_request','ready',p)
        self.assertEqual(self.state(config)['state'],'clawsweeper_queued')
        self.assertEqual(self.state(config)['review_epoch'],epoch)
        old=legacy.action(config,7,'clawsweeper.dispatch')
        p['action']='converted_to_draft';p['pull_request'].update(draft=True,updated_at='2026-08-29T20:23:00Z')
        self.ingest(config,'pull_request','draft-again',p)
        self.assertEqual(self.state(config)['review_epoch'],old['review_epoch'])
        conn=core.open_database(Path(config['paths']['state_root']))
        self.assertEqual(conn.execute('SELECT status FROM actions WHERE action_id=?',(old['action_id'],)).fetchone()[0],'obsolete');conn.close()
        self.assertEqual(self.state(config)['state'],'openclaw_clean_draft')
        self.assertEqual(self.ingest(config,'pull_request','stale-ready',{**p,'action':'ready_for_review','pull_request':{**p['pull_request'],'draft':False}})['result'],'stale')
        p['action']='ready_for_review';p['pull_request'].update(draft=False,updated_at='2026-08-29T20:24:00Z')
        self.ingest(config,'pull_request','ready-again',p)
        conn=core.open_database(Path(config['paths']['state_root']))
        self.assertEqual(conn.execute('SELECT status FROM actions WHERE action_id=?',(old['action_id'],)).fetchone()[0],'pending');conn.close()
        self.assertEqual(self.state(config)['state'],'clawsweeper_queued')

    def test_status_transition_preserves_inflight_ci_and_first_draft_value(self):
        config=self.config();p=self.payload(legacy.pr_payload(7));p['pull_request']['draft']=True
        self.ingest(config,'pull_request','draft-open',p)
        ci=self.payload(legacy.ci_payload(7,701))
        p['action']='ready_for_review';p['pull_request'].update(draft=False,updated_at='2026-08-29T20:05:00Z')
        self.ingest(config,'pull_request','ready-before-ci-finishes',p)
        self.assertEqual(self.ingest(config,'workflow_run','inflight-ci',ci)['result'],'accepted')
        self.assertEqual(self.state(config)['state'],'openclaw_queued')

        next_head='9'*40
        p=self.payload(legacy.pr_payload(7,next_head));p['action']='converted_to_draft'
        p['pull_request'].update(draft=True,updated_at='2026-08-29T20:30:00Z')
        self.ingest(config,'pull_request','first-event-draft-new-head',p)
        self.assertTrue(self.state(config)['is_draft'])

    def test_clean_adjudication_while_draft_never_dispatches_clawsweeper(self):
        config=self.config();action=self.enqueue(config)
        runtime.bridge_openclaw(config,self.terminal(config,action,review_clean=False,review_finding_count=1))
        p=self.payload(legacy.pr_payload(7));p['action']='converted_to_draft';p['pull_request'].update(draft=True,updated_at='2026-08-29T20:23:00Z')
        self.ingest(config,'pull_request','draft-during-adjudication',p)
        event={'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':'clean-adjudication-draft','type':'adjudication.completed','repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':HEAD,'review_epoch':action['review_epoch'],'request_id':json.loads(action['payload_json'])['queue_request_id'],'rail':'openclaw','classifications':['reject_false_positive'],'reviewer_actor':'fixture-suite-openclaw','proof_ref':'fixture-proof'}
        outcome=core.ingest_internal_event(config_path=Path(config['core_config']),state_root=Path(config['paths']['state_root']),event_payload=event)
        self.assertEqual(outcome['state'],'openclaw_clean_draft')
        conn=core.open_database(Path(config['paths']['state_root']))
        self.assertIsNone(conn.execute("SELECT 1 FROM actions WHERE kind='clawsweeper.dispatch'").fetchone());conn.close()

    def test_closed_projection_filter_removes_only_target_ready_label(self):
        adapters.test_closed_projection_filter_removes_only_target_ready_label(self.root)

    def test_closed_head_without_projection_still_removes_ready_label(self):
        adapters.test_closed_head_without_projection_still_removes_ready_label(self.root)

    def test_clawsweeper_finishing_after_return_to_draft_cannot_clear_merge(self):
        config=self.config();self.enqueue(config)
        openclaw=legacy.action(config,7,'openclaw.enqueue');legacy.mark_dispatched(config,openclaw['action_id'])
        runtime.bridge_openclaw(config,self.terminal(config,openclaw))
        clawsweeper=legacy.action(config,7,'clawsweeper.dispatch');legacy.mark_dispatched(config,clawsweeper['action_id'])
        connection=core.open_database(Path(config['paths']['state_root']),REPO)
        try:
            core.process_internal_event(connection,core.load_config(Path(config['core_config'])),{
                'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':'clawsweeper:801:started','type':'clawsweeper.started',
                'repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':HEAD,
                'review_epoch':clawsweeper['review_epoch'],'workflow_run_id':'801',
            })
            connection.commit()
        finally:connection.close()
        p=self.payload(legacy.pr_payload(7));p['action']='converted_to_draft';p['pull_request'].update(draft=True,updated_at='2026-08-29T20:23:00Z')
        self.ingest(config,'pull_request','draft-while-clawsweeper-runs',p)
        connection=core.open_database(Path(config['paths']['state_root']),REPO)
        try:
            core.process_internal_event(connection,core.load_config(Path(config['core_config'])),{
                'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':'clawsweeper:801:clean','type':'clawsweeper.terminal',
                'repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':HEAD,
                'review_epoch':clawsweeper['review_epoch'],'workflow_run_id':'801','result':'clean',
                'finding_count':0,'review_scope':'comprehensive','reviewer_actor':'fixture-suite-clawsweeper',
                'proof_ref':'proof/clawsweeper/801/PROOF.md',
            })
            connection.commit()
        finally:connection.close()
        self.assertEqual(self.state(config)['state'],'clawsweeper_clean_draft')
        self.assertFalse(core.state_projection(self.state(config))['merge_authorized'])
        p['action']='ready_for_review';p['pull_request'].update(draft=False,updated_at='2026-08-29T20:24:00Z')
        self.ingest(config,'pull_request','ready-after-clawsweeper-clean',p)
        self.assertEqual(self.state(config)['state'],'ready_for_human_merge')

        p['action']='converted_to_draft';p['pull_request'].update(draft=True,updated_at='2026-08-29T20:25:00Z')
        self.ingest(config,'pull_request','draft-after-full-clearance',p)
        self.assertEqual(self.state(config)['state'],'clawsweeper_clean_draft')
        self.assertFalse(core.state_projection(self.state(config))['ready_for_human_label'])

    def test_draft_transition_preserves_dispatched_clawsweeper_until_terminal(self):
        config=self.config();self.enqueue(config)
        openclaw=legacy.action(config,7,'openclaw.enqueue');legacy.mark_dispatched(config,openclaw['action_id'])
        runtime.bridge_openclaw(config,self.terminal(config,openclaw))
        clawsweeper=legacy.action(config,7,'clawsweeper.dispatch');legacy.mark_dispatched(config,clawsweeper['action_id'])
        p=self.payload(legacy.pr_payload(7));p['action']='converted_to_draft';p['pull_request'].update(draft=True,updated_at='2026-08-29T20:23:00Z')
        self.ingest(config,'pull_request','draft-after-dispatch',p)
        self.assertEqual(self.state(config)['state'],'clawsweeper_queued')
        connection=core.open_database(Path(config['paths']['state_root']),REPO)
        try:
            core.process_internal_event(connection,core.load_config(Path(config['core_config'])),{
                'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':'clawsweeper:802:started','type':'clawsweeper.started',
                'repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':HEAD,
                'review_epoch':clawsweeper['review_epoch'],'workflow_run_id':'802',
            })
            outcome=core.process_internal_event(connection,core.load_config(Path(config['core_config'])),{
                'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':'clawsweeper:802:clean','type':'clawsweeper.terminal',
                'repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':HEAD,
                'review_epoch':clawsweeper['review_epoch'],'workflow_run_id':'802','result':'clean',
                'finding_count':0,'review_scope':'comprehensive','reviewer_actor':'fixture-suite-clawsweeper',
                'proof_ref':'proof/clawsweeper/802/PROOF.md',
            })
            connection.commit()
        finally:connection.close()
        self.assertEqual(outcome['state'],'clawsweeper_clean_draft')

    def test_capability_declared_profile_emits_exact_tuple_queue_flags(self):
        config=self.config(); action=self.enqueue(config)
        checkout=self.root/'source-checkout'
        checkout.mkdir()
        core_config=core.load_config(Path(config['core_config']))
        self.assertTrue(core.declares_openclaw_exact_tuple_contract(core_config))
        commands=core.command_preview(action, core_config, checkout)
        queue=commands[1]
        self.assertEqual(
            queue[-4:],
            ['--exact-tuple-contract', 'review-conductor-openclaw-v1', '--review-epoch', str(action['review_epoch'])],
        )

    def test_generalized_openclaw_terminal_requires_applied_p3_qualification(self):
        config=self.config(); action=self.enqueue(config)
        request_id=json.loads(action['payload_json'])['queue_request_id']
        run=legacy.openclaw_run_fixture(self.root, request_id)
        for findings in (False, True):
            with self.subTest(findings=findings):
                overrides={'review_clean': False, 'review_finding_count': 1} if findings else {}
                self.write_openclaw_status(run, action, config, **overrides)
                result=userland.collect_openclaw_terminals(config, dry_run=False, runner=legacy.SparkStatusRunner(run))
                self.assertEqual(result, [{'request_id': request_id, 'result': 'terminal_materialized'}])
                artifact=Path(config['spark']['terminal_inbox']) / f'{request_id}.terminal.json'
                self.assertTrue(artifact.is_file())
                proof=Path(config['paths']['proof_root']) / 'openclaw' / request_id / 'PROOF.md'
                self.assertTrue(proof.is_file())
                value=json.loads(artifact.read_text())
                self.assertEqual(value['review_scope'], 'comprehensive')
                self.assertEqual(value['native_max_priority'], 'P3')
                self.assertEqual(value['applied_max_priority'], 'P3')
                self.assertIs(value['exact_tuple_qualified'], True)
                self.assertEqual(value['review_finding_count'], 1 if findings else 0)
                self.assertEqual(value['review_clean'], not findings)

    def test_generalized_openclaw_unqualified_status_writes_zero_artifacts(self):
        config=self.config(); action=self.enqueue(config)
        request_id=json.loads(action['payload_json'])['queue_request_id']
        run=legacy.openclaw_run_fixture(self.root, request_id)
        inbox=Path(config['spark']['terminal_inbox'])
        proof=Path(config['paths']['proof_root']) / 'openclaw' / request_id
        cases=(
            ('missing_native', {'_drop': ('native_max_priority',)}),
            ('p0_native', {'native_max_priority': 'P0'}),
            ('missing_applied', {'_drop': ('applied_max_priority',)}),
            ('p0_applied', {'applied_max_priority': 'P0'}),
            ('conflicting_priorities', {'native_max_priority': 'P3', 'applied_max_priority': 'P0'}),
            ('missing_qualified', {'_drop': ('exact_tuple_qualified',)}),
            ('false_qualified', {'exact_tuple_qualified': False}),
            ('truthy_qualified', {'exact_tuple_qualified': 1}),
            ('copied_comprehensive_native_p0', {'native_max_priority': 'P0', 'applied_max_priority': 'P0', 'exact_tuple_qualified': False}),
        )
        for findings in (False, True):
            for label, overrides in cases:
                with self.subTest(label=label, findings=findings):
                    extra=dict(overrides)
                    if findings:
                        extra.update(review_clean=False, review_finding_count=1)
                    self.write_openclaw_status(run, action, config, **extra)
                    with self.assertRaises(core.ContractError):
                        userland.collect_openclaw_terminals(config, dry_run=False, runner=legacy.SparkStatusRunner(run))
                    self.assertEqual(list(inbox.glob('*.terminal.json')) if inbox.exists() else [], [])
                    self.assertFalse(proof.exists())

    def test_generalized_openclaw_status_rejects_json_type_coercions(self):
        config=self.config(); action=self.enqueue(config, pr=1)
        request_id=json.loads(action['payload_json'])['queue_request_id']
        run=legacy.openclaw_run_fixture(self.root, request_id)
        inbox=Path(config['spark']['terminal_inbox'])
        self.assertEqual(action['pr_number'], 1)
        self.assertEqual(action['review_epoch'], 0)
        cases=(
            ('false_vs_0', {'review_epoch': False}),
            ('true_vs_1', {'pr_number': True}),
            ('float_vs_int', {'pr_number': 1.0}),
            ('string_vs_int', {'pr_number': '1'}),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                self.write_openclaw_status(run, action, config, **overrides)
                with self.assertRaises(core.ContractError):
                    userland.collect_openclaw_terminals(config, dry_run=False, runner=legacy.SparkStatusRunner(run))
                self.assertEqual(list(inbox.glob('*.terminal.json')) if inbox.exists() else [], [])

    def test_bridge_rejects_direct_artifact_and_internal_event_without_qualification(self):
        config=self.config(); action=self.enqueue(config)
        for override in (
            {'_drop': ('native_max_priority', 'applied_max_priority', 'exact_tuple_qualified')},
            {'applied_max_priority': 'P0'},
            {'native_max_priority': 'P0'},
            {'exact_tuple_qualified': False},
            {'exact_tuple_qualified': 1},
        ):
            with self.subTest(artifact=override):
                with self.assertRaises(core.ContractError):
                    runtime.bridge_openclaw(config, self.terminal(config, action, **override))
                self.assertEqual(self.state(config)['state'], 'openclaw_queued')
        request_id=json.loads(action['payload_json'])['queue_request_id']
        proof = Path(config['paths']['proof_root']) / 'openclaw-internal.md'
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text('internal terminal proof\n', encoding='utf-8')
        base_event={
            'schema':core.INTERNAL_EVENT_SCHEMA,
            'event_id':'direct-openclaw-terminal',
            'type':'openclaw.terminal',
            'repository':REPO,
            'pr_number':7,
            'base_sha':BASE,
            'head_sha':HEAD,
            'review_epoch':action['review_epoch'],
            'request_id':request_id,
            'result':'clean',
            'finding_count':0,
            'reviewer_actor':'fixture-suite-openclaw',
            'proof_ref':str(proof),
            'review_scope':'comprehensive',
        }
        for fields in (
            {},
            {'native_max_priority':'P3','applied_max_priority':'P0','exact_tuple_qualified':True},
            {'native_max_priority':'P3','applied_max_priority':'P3','exact_tuple_qualified':1},
        ):
            with self.subTest(event=fields):
                with self.assertRaises(core.ContractError):
                    core.ingest_internal_event(
                        config_path=Path(config['core_config']),
                        state_root=Path(config['paths']['state_root']),
                        event_payload={**base_event, **fields},
                    )
                self.assertEqual(self.state(config)['state'], 'openclaw_queued')

    def test_cross_repo_stale_epoch_head_base_actor_and_p0_artifacts_fail_closed(self):
        config=self.config();action=self.enqueue(config)
        for override in ({'repository':'dinkuskit/blocks'},{'review_epoch':1},{'head_sha':'3'*40},{'base_sha':'4'*40},{'reviewer_actor':'spark-openclaw'},{'review_scope':'P0-only'},{'operator_id':'review-conductor'}):
            with self.subTest(override=override):
                with self.assertRaises(core.ContractError):runtime.bridge_openclaw(config,self.terminal(config,action,**override))
                self.assertEqual(self.state(config)['state'],'openclaw_queued')
        runtime.bridge_openclaw(config,self.terminal(config,action))
        self.assertEqual(self.state(config)['state'],'clawsweeper_queued')

    def test_openclaw_projection_retains_verified_digests_and_rejects_old_epoch(self):
        config = self.config()
        action = self.enqueue(config)
        artifact = self.terminal(config, action)
        raw_artifact = json.loads(artifact.read_text())
        receipt = runtime.bridge_openclaw(config, artifact)
        connection = core.open_database(Path(config['paths']['state_root']))
        try:
            row = core.current_head(connection, REPO, 7)
            terminal = runtime.accepted_openclaw_terminal(connection, row)
            self.assertEqual(terminal['proof_sha256'], raw_artifact['proof_sha256'])
            self.assertEqual(terminal['artifact_digest'], receipt['artifact_sha256'])
            report = runtime.projection_check_report(
                row, check_name='OpenClaw Review Rail', check_state='success',
                openclaw_terminal=terminal,
            )
            check = runtime.check_payload('OpenClaw Review Rail', HEAD, 'fixture', 'success', report=report)
            self.assertIn(receipt['artifact_sha256'], check['output']['summary'])
            event = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='openclaw.terminal'"
            ).fetchone()[0])
            core_config = core.load_config(Path(config['core_config']))
            for field in ('proof_sha256', 'artifact_digest'):
                for invalid in (True, 'x' * 64, 'a' * 63):
                    with self.assertRaises(core.ContractError):
                        core.validate_internal_event(core_config, {**event, field: invalid})
            # Type-equal Python booleans/floats must not qualify a persisted epoch.
            for invalid in (False, 0.0, '0', None):
                connection.execute("UPDATE events SET payload_json=? WHERE kind='openclaw.terminal'",
                                   (json.dumps({**event, 'review_epoch': invalid}),))
                self.assertIsNone(runtime.accepted_openclaw_terminal(connection, row))
            connection.rollback()
        finally:
            connection.close()
        closed = self.payload(legacy.pr_payload(7))
        closed['action'] = 'closed'
        closed['pull_request']['updated_at'] = '2026-08-29T20:20:00Z'
        self.ingest(config, 'pull_request', 'closed-old-evidence', closed)
        reopened = copy.deepcopy(closed)
        reopened['action'] = 'reopened'
        reopened['pull_request']['updated_at'] = '2026-08-29T20:21:00Z'
        self.ingest(config, 'pull_request', 'reopened-old-evidence', reopened)
        connection = core.open_database(Path(config['paths']['state_root']))
        try:
            row = core.current_head(connection, REPO, 7)
            self.assertEqual(row['review_epoch'], 1)
            self.assertIsNone(runtime.accepted_openclaw_terminal(connection, row))
            report = runtime.projection_check_report(
                row, check_name='OpenClaw Review Rail', check_state='queued',
                openclaw_terminal=runtime.accepted_openclaw_terminal(connection, row),
            )
            self.assertNotIn('artifact_digest', report)
            self.assertNotIn('request_id', report)
        finally:
            connection.close()

    def test_closed_and_superseded_retract_all_owned_status_labels(self):
        config = self.config()
        self.enqueue(config)
        class GitHub(adapters.FakeGitHub):
            def __init__(self):
                super().__init__()
                self.labels = set(runtime.result_projection.OWNED_LABELS) | {'docs', 'P3'}

            def remove_ready_label(self, pr, **kwargs):
                super().remove_ready_label(pr)
                self.labels.discard(runtime.READY_LABEL)

            def remove_owned_label(self, pr, name, **kwargs):
                self.calls.append(('remove_owned', pr, name))
                self.labels.discard(name)

        github = GitHub()
        runtime.reconcile_projection(config, github)
        # Both closure and later head supersession must retract every owned status.
        for index, action in enumerate(('closed', 'reopened', 'synchronize')):
            github.labels |= set(runtime.result_projection.OWNED_LABELS)
            payload = self.payload(legacy.pr_payload(7))
            payload['action'] = action
            payload['pull_request']['updated_at'] = f'2026-08-29T20:{20 + index}:00Z'
            if action == 'synchronize':
                payload['pull_request']['head']['sha'] = '3' * 40
            self.ingest(config, 'pull_request', f'label-cleanup-{action}', payload)
            runtime.reconcile_projection(config, github)
            if action != 'reopened':
                self.assertEqual(github.labels, {'docs', 'P3'})

    def test_full_trusted_artifact_chain_and_replay(self):
        config=self.config();action=self.enqueue(config)
        artifact=self.terminal(config,action)
        runtime.bridge_openclaw(config,artifact)
        self.assertEqual(runtime.bridge_openclaw(config,artifact)['result'],'duplicate_event')
        claw=legacy.action(config,7,'clawsweeper.dispatch');legacy.mark_dispatched(config,claw['action_id'])
        self.ingest(config,'workflow_run','claw-workflow',self.payload(legacy.claw_workflow_payload(801)))
        for fields in ({'review_scope':'P0-only'},{'review_epoch':'8'},{'reviewer_actor':'clawsweeper'}):
            with self.assertRaises(core.ContractError):userland.parse_clawsweeper_bundle(self.bundle(config,claw,**fields),workflow_run_id=801,action=claw,policy=config['review_policy'])
        class GitHub(legacy.FakeGitHub):
            def list_run_artifacts(self,run_id):
                return [{'id':9001,'name':f'smcbd-suite-review-{run_id}-1','expired':False}]
        github=GitHub(801,self.bundle(config,claw))
        userland.collect_clawsweeper_terminals(config,github,dry_run=False)
        artifact=Path(config['clawsweeper_bridge']['terminal_inbox'])/'801.terminal.json'
        runtime.bridge_clawsweeper(config,artifact)
        self.assertEqual(self.state(config)['state'],'ready_for_human_merge')
        self.assertFalse(core.state_projection(self.state(config))['merge_authorized'])
        self.assertEqual(runtime.bridge_clawsweeper(config,artifact)['result'],'duplicate_event')

    def test_suite_findings_preserve_authority_and_two_cycle_stop(self):
        config=self.config()
        for cycle in range(3):
            head=str(cycle+2)*40
            pr=self.payload(legacy.pr_payload(7,head))
            pr['action']='opened' if cycle==0 else 'synchronize'
            pr['pull_request']['updated_at']=f'2026-08-{29+cycle}T20:00:00Z'
            self.ingest(config,'pull_request',f'pr-{cycle}',pr)
            ci=self.payload(legacy.ci_payload(7,701+cycle,head))
            ci['workflow_run'].update(created_at=f'2026-08-{29+cycle}T20:01:00Z',updated_at=f'2026-08-{29+cycle}T20:11:00Z')
            self.ingest(config,'workflow_run',f'ci-{cycle}',ci)
            connection=core.open_database(Path(config['paths']['state_root']))
            action=connection.execute("SELECT * FROM actions WHERE head_sha=? AND kind='openclaw.enqueue'",(head,)).fetchone();connection.close()
            legacy.mark_dispatched(config,action['action_id'])
            artifact=self.terminal(config,action,review_clean=False,review_finding_count=1)
            runtime.bridge_openclaw(config,artifact)
            event={'schema':core.INTERNAL_EVENT_SCHEMA,'event_id':f'fixture-adjudication-{cycle}','type':'adjudication.completed','repository':REPO,'pr_number':7,'base_sha':BASE,'head_sha':head,'review_epoch':action['review_epoch'],'request_id':json.loads(action['payload_json'])['queue_request_id'],'rail':'openclaw','classifications':['required_fix'],'reviewer_actor':'fixture-suite-openclaw','proof_ref':'fixture-proof'}
            with self.assertRaises(core.ContractError):
                core.ingest_internal_event(config_path=Path(config['core_config']),state_root=Path(config['paths']['state_root']),event_payload={**event,'reviewer_actor':'untrusted'})
            outcome=core.ingest_internal_event(config_path=Path(config['core_config']),state_root=Path(config['paths']['state_root']),event_payload=event)
            self.assertEqual(outcome['state'],'repair_required' if cycle<2 else 'waiting_human')
            self.assertFalse(outcome['merge_dispatched'])

    def test_app_dispatch_is_repository_workflow_epoch_bound_without_merge_authority(self):
        config=self.config();calls=[]
        def transport(method,url,headers,body,timeout):calls.append((method,url,json.loads(body) if body else None));return 204,b''
        client=runtime.GitHubAppClient(config,'fixture-key',transport=transport)
        client._token='fixture-token';client._token_expires=9999999999
        for path in ('/repos/dinkuskit/blocks/check-runs',f'/repos/{REPO}/actions/workflows/clawsweeper-native-canary.yml/dispatches',f'/repos/{REPO}/pulls/7/merge'):
            with self.assertRaises(core.ContractError):client._call('POST',path,{},expected={204})
        with self.assertRaises(core.ContractError):client.dispatch_clawsweeper(pr_number=7,base_sha=BASE,head_sha=HEAD,publish=True)
        self.assertEqual(calls,[])
        client.dispatch_clawsweeper(pr_number=7,base_sha=BASE,head_sha=HEAD,publish=True,review_epoch=3)
        self.assertEqual(calls[0][1],f'https://api.github.com/repos/{REPO}/actions/workflows/clawsweeper-exact-tuple.yml/dispatches')
        self.assertEqual(calls[0][2]['inputs']['review_epoch'],'3')
        self.assertEqual(calls[0][2]['inputs']['expected_head_sha'],HEAD)

    def test_missing_reviewer_prevents_activation_and_candidate_ids_are_pinned(self):
        config=self.config()
        path=Path(config['core_config']);value=json.loads(path.read_text());value['review_policy']['reviewers']['openclaw']=None;write(path,value)
        with self.assertRaises(core.ContractError):core.load_config(path)
        self.assertEqual(profiles.capabilities(config)[0],'review-conductor.openclaw-smcbd-suite.webhook-verify')
        candidate=json.loads((ROOT/'contracts/review-conductor/openclaw-smcbd-suite-userland.json').read_text())
        core_candidate=json.loads((ROOT/'contracts/review-conductor/openclaw-smcbd-suite.json').read_text())
        self.assertEqual(core_candidate['review_policy']['reviewers'],{'openclaw':'spark-openclaw','clawsweeper':'saari-clawsweeper'})
        self.assertFalse(core_candidate['review_policy']['enabled'])
        self.assertEqual(candidate['github_app']['app_id'],4916376)
        self.assertEqual(candidate['github_app']['installation_id'],161027021)
        self.assertEqual(candidate['github_app']['permissions']['contents'],'read')
        self.assertFalse(candidate['enrollment']['enabled'])
        self.assertNotIn('Authoritative repository-specific reviewer identities are not enrolled.', candidate['enrollment']['blockers'])
        self.assertIsNone(candidate['tunnel']['tunnel_id'])

    def test_enabled_standalone_profile_does_not_require_a_tunnel_id(self):
        cp = self.contracts / 'openclaw-smcbd-suite.json'
        rp = self.contracts / 'openclaw-smcbd-suite-userland.json'
        core_profile, runtime_profile = json.loads(cp.read_text()), json.loads(rp.read_text())
        core_profile['review_policy']['enabled'] = True
        runtime_profile['enrollment'] = {'enabled': True, 'blockers': []}
        self.assertIsNone(runtime_profile['tunnel']['tunnel_id'])
        write(cp, core_profile)
        write(rp, runtime_profile)
        config = userland.load_config(rp, home=self.home, source_root=self.source)
        self.assertEqual(config['review_policy']['reviewers'], {'openclaw': 'spark-openclaw', 'clawsweeper': 'saari-clawsweeper'})
        self.assertIsNone(config['tunnel']['tunnel_id'])
        self.assertTrue(config['tunnel']['tunnel_name'])
        runtime_profile['ingress']['public_hostname'] = 'fixture.invalid'
        write(rp, runtime_profile)
        with self.assertRaises(core.ContractError):
            userland.load_config(rp, home=self.home, source_root=self.source)

    def test_enabled_standalone_profile_rejects_empty_tunnel_name(self):
        cp = self.contracts / 'openclaw-smcbd-suite.json'
        rp = self.contracts / 'openclaw-smcbd-suite-userland.json'
        core_profile, runtime_profile = json.loads(cp.read_text()), json.loads(rp.read_text())
        core_profile['review_policy']['enabled'] = True
        runtime_profile['enrollment'] = {'enabled': True, 'blockers': []}
        write(cp, core_profile)
        for name in ('', None):
            runtime_profile['tunnel']['tunnel_name'] = name
            write(rp, runtime_profile)
            with self.assertRaises(core.ContractError):
                userland.load_config(rp, home=self.home, source_root=self.source)
        runtime_profile['tunnel']['tunnel_name'] = 'fixture-suite-only'
        runtime_profile['tunnel']['tunnel_id'] = None
        write(rp, runtime_profile)
        config = userland.load_config(rp, home=self.home, source_root=self.source)
        self.assertIsNone(config['tunnel']['tunnel_id'])
        self.assertEqual(config['tunnel']['tunnel_name'], 'fixture-suite-only')


class OpenClawTerminalMutationTests(unittest.TestCase):
    """Each applied-P3 guard bites: a precise disposable-copy mutant fails its intended test."""

    MUTANTS = (
        (
            "omit native_max_priority P3",
            'tools/review_conductor_userland.py',
            ', "native_max_priority": "P3", "applied_max_priority": "P3", "exact_tuple_qualified": True}',
            ', "applied_max_priority": "P3", "exact_tuple_qualified": True}',
            'test_review_conductor_profiles.ProfilesTest.test_generalized_openclaw_unqualified_status_writes_zero_artifacts',
        ),
        (
            "omit applied_max_priority P3",
            'tools/review_conductor_userland.py',
            ', "native_max_priority": "P3", "applied_max_priority": "P3", "exact_tuple_qualified": True}',
            ', "native_max_priority": "P3", "exact_tuple_qualified": True}',
            'test_review_conductor_profiles.ProfilesTest.test_generalized_openclaw_unqualified_status_writes_zero_artifacts',
        ),
        (
            "omit exact_tuple_qualified identity",
            'tools/review_conductor_userland.py',
            ', "native_max_priority": "P3", "applied_max_priority": "P3", "exact_tuple_qualified": True}',
            ', "native_max_priority": "P3", "applied_max_priority": "P3"}',
            'test_review_conductor_profiles.ProfilesTest.test_generalized_openclaw_unqualified_status_writes_zero_artifacts',
        ),
    )

    def test_precise_openclaw_applied_p3_mutants(self):
        for label, relative, old, new, test_id in self.MUTANTS:
            with self.subTest(mutant=label):
                with tempfile.TemporaryDirectory(prefix='review-conductor-mutant-') as temp:
                    copy_root = Path(temp) / 'copy'
                    for name in ('tools', 'tests', 'contracts'):
                        shutil.copytree(ROOT / name, copy_root / name)
                    target = copy_root / relative
                    source = target.read_text()
                    self.assertEqual(source.count(old), 1, f'mutant anchor drifted: {label}')
                    target.write_text(source.replace(old, new, 1))
                    completed = subprocess.run(
                        [sys.executable, '-m', 'unittest', '-q', test_id],
                        cwd=copy_root / 'tests',
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env={'PATH': '/usr/bin:/bin', 'HOME': temp, 'PYTHONDONTWRITEBYTECODE': '1'},
                    )
                self.assertNotEqual(completed.returncode, 0, f'mutant survived: {label}\n{completed.stderr}')
                self.assertIn('Ran 1 test', completed.stderr, f'intended test did not run: {label}\n{completed.stderr}')
                self.assertRegex(
                    completed.stderr,
                    r'(FAIL|ERROR): test_generalized_openclaw_unqualified_status_writes_zero_artifacts',
                    f'failure was not the intended test: {label}\n{completed.stderr}',
                )


if __name__=='__main__':unittest.main(verbosity=2)
