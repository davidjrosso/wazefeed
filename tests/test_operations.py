import copy
import json
import unittest
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from core import empty_state, feed, active_events, change, TZ
from operations import upgrade, operate, materialize
from test_app import ServiceTests, HASH, PASSWORD

NOW = datetime(2026, 10, 7, 7, 30, tzinfo=TZ).timestamp()


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.state = upgrade(empty_state())
        self.ids = [s['id'] for s in self.state['segments'][:2]]

    def do(self, action, role='admin', now=NOW, **data):
        self.state = operate(self.state, {'action': action, 'revision': self.state['revision'], **data}, 'tester', role, now)
        return self.state

    def template(self, name='Lluvias', schedule=None):
        self.do('save_template', template={'name': name, 'reason': 'Inundación', 'description': 'Cierre por inundación',
                'durationMinutes': 60, 'segmentIds': self.ids, 'schedule': schedule})
        return self.state['templates'][-1]['id']

    def calendar(self):
        return {'from':'2026-10-07', 'until':'2026-10-31', 'time':'08:00', 'weekdays':[0,1,2,3,4], 'exceptions':['2026-10-08']}

    def test_upgrade_preserves_every_legacy_field_and_feed(self):
        old = change(empty_state(),'activate',0,'test',now=NOW,segment_ids=self.ids)
        new = upgrade(old)
        self.assertEqual(feed(old,NOW),feed(new,NOW))
        for key in ('segments','events','history'):self.assertEqual(new[key],old[key])
        self.assertEqual(new['revision'],old['revision']+1)
        self.assertIs(upgrade(new),new)

    def test_save_never_activates_and_snapshots_are_immutable(self):
        tid=self.template()
        self.assertEqual(feed(self.state,NOW),{'incidents':[]})
        self.state['segments'][0]['street']='Registro editado después'
        self.do('activate_template',role='operator',templateId=tid)
        self.assertNotEqual(self.state['runs'][0]['segments'][0]['street'],'Registro editado después')
        self.assertEqual(len(active_events(self.state,NOW)),2)

    def test_operator_cannot_configure(self):
        tid=self.template()
        for action in ('save_template','archive_template','schedule_enable','schedule_pause','import_segments'):
            with self.subTest(action=action),self.assertRaises(PermissionError):
                self.do(action,role='operator',templateId=tid)

    def test_overlap_finish_does_not_reopen_other_run(self):
        a=self.template();b=self.template('Escuelas')
        self.do('activate_template',templateId=a);self.do('activate_template',templateId=b)
        rid=self.state['runs'][0]['id']
        other=copy.deepcopy(self.state['events'][2:])
        self.do('finish_run',role='operator',runId=rid,now=NOW+100)
        self.assertEqual(self.state['events'][2:],other)
        self.assertEqual(len(active_events(self.state,NOW+101)),2)
        with self.assertRaises(ValueError):
            change(self.state,'reopen',self.state['revision'],'test',now=NOW+102,segment_ids=self.ids)

    def test_double_activation_rejected(self):
        tid=self.template();self.do('activate_template',templateId=tid)
        with self.assertRaises(ValueError):self.do('activate_template',templateId=tid)
        self.assertEqual(len(self.state['runs']),1)

    def test_extend_preserves_ids_start_and_other_runs(self):
        tid=self.template();self.do('activate_template',templateId=tid)
        before=copy.deepcopy(self.state['events']);rid=self.state['runs'][0]['id']
        self.do('extend_run',runId=rid,minutes=60,role='operator',now=NOW+100)
        for a,b in zip(before,self.state['events']):
            self.assertEqual(a['feedId'],b['feedId']);self.assertEqual(a['start'],b['start']);self.assertEqual(b['end'],a['end']+3600)
        for minutes in (True,0,-1,1441,'60'):
            with self.assertRaises(ValueError):self.do('extend_run',runId=rid,minutes=minutes)

    def test_schedule_is_explicit_and_polling_idempotent(self):
        tid=self.template(schedule=self.calendar())
        self.assertIs(materialize(self.state,NOW),self.state)
        self.do('schedule_enable',templateId=tid)
        runs=self.state['runs'];self.assertTrue(runs)
        for r in runs:
            d=datetime.fromtimestamp(r['start'],TZ)
            self.assertEqual(d.hour,8);self.assertLess(d.weekday(),5);self.assertNotEqual(d.date().isoformat(),'2026-10-08')
        self.assertIs(materialize(self.state,NOW+10),self.state)
        self.assertTrue(all(i['starttime'].endswith('-03:00') for i in feed(self.state,NOW)['incidents']))

    def test_cancel_one_date_never_regenerates(self):
        tid=self.template(schedule=self.calendar());self.do('schedule_enable',templateId=tid)
        run=self.state['runs'][0];count=len(feed(self.state,NOW)['incidents'])
        self.do('finish_run',runId=run['id'])
        self.assertIs(materialize(self.state,NOW+10),self.state)
        self.assertEqual(len(feed(self.state,NOW)['incidents']),count)
        cancelled=[e for e in self.state['events'] if e.get('cancelled')]
        self.assertEqual(len(cancelled),2)
        self.assertTrue(all(e['start'] < e['end'] <= NOW for e in cancelled))

    def test_pause_preserves_active_and_resume_restores_future(self):
        tid=self.template(schedule=self.calendar());self.do('schedule_enable',templateId=tid)
        now=NOW+2400
        self.do('schedule_pause',templateId=tid,now=now)
        self.assertEqual(len(active_events(self.state,now)),2)
        self.assertEqual(len([r for r in self.state['runs'] if r['closedAt'] is None]),1)
        self.do('schedule_enable',templateId=tid,now=now)
        self.assertGreater(len([r for r in self.state['runs'] if r['closedAt'] is None]),1)
        self.assertEqual(len(active_events(self.state,now)),2)

    def test_no_historical_replay_after_outage(self):
        tid=self.template(schedule=self.calendar());self.do('schedule_enable',templateId=tid)
        later=NOW+20*86400
        updated=materialize(self.state,later)
        for run in updated['runs'][len(self.state['runs']):]:self.assertGreater(run['end'],later)

    def test_invalid_template_and_schedule_atomic(self):
        for changes in ({'segmentIds':[]},{'durationMinutes':False},{'reason':'invalid'}, {'schedule':{'from':'bad'}}, {'segmentIds':['bad']}):
            before=copy.deepcopy(self.state)
            with self.assertRaises(ValueError):self.do('save_template',template={'name':'x','reason':'Obras','description':'Obras','durationMinutes':60,'segmentIds':self.ids,**changes})
            self.assertEqual(before,self.state)

    def test_import_atomic_and_duplicate_does_not_publish(self):
        seg={'street':'Prueba','from':'A','to':'B','direction':'BOTH_DIRECTIONS','wazeSegmentId':'987654', 'points':[[-32.17,-64.12],[-32.171,-64.12]]}
        bundle={'format':'municipal-closures-segments','schema':1,'segments':[seg]}
        self.do('import_segments',bundle=bundle);count=len(self.state['segments'])
        self.do('import_segments',bundle=bundle);self.assertEqual(len(self.state['segments']),count)
        self.assertEqual(feed(self.state,NOW),{'incidents':[]})
        with self.assertRaises(ValueError):self.do('import_segments',bundle={**bundle,'segments':[{**seg,'points':[[0,0],[1,1]]}]})


class OperationAPITests(ServiceTests):
    # Reuse the HTTP harness, not the inherited test methods.
    def operator_login(self):
        with self.app.db() as db:
            db.execute('INSERT INTO accounts(username,password_hash,role) VALUES(?,?,?)',('defensa',HASH,'operator'))
        r=self.request('/waze/api/login','POST',{'username':'defensa','password':PASSWORD})
        self.assertEqual(r['status'],200)
        headers={'HTTP_COOKIE':r['headers']['Set-Cookie'].split(';')[0]}
        s=self.request('/waze/api/state',headers=headers)['json']
        return {**headers,'HTTP_X_CSRF_TOKEN':s['csrf']},s

    def test_roles_enforced_server_side_and_named_history(self):
        headers,s=self.operator_login()
        self.assertEqual(s['role'],'operator')
        for action in ('save_segment','activate','reopen','import_segments','save_template','schedule_enable'):
            result=self.request('/waze/api/state','POST',{'action':action,'revision':s['revision']},headers)
            self.assertEqual(result['status'],403,action)
        admin,st=self.login()
        self.assertEqual(st['role'],'admin')
        r=self.request('/waze/api/state','POST',{'action':'save_template','revision':st['revision'],'template':{'name':'Lluvias','reason':'Inundación','description':'Inundación','durationMinutes':60,'segmentIds':[st['segments'][0]['id']]}},admin)
        s=r['json'];payload={'action':'activate_template','revision':s['revision'],'templateId':s['templates'][0]['id']}
        def activate(_):return self.request('/waze/api/state','POST',payload,headers)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(activate,range(2))),[200,409])
        state=self.request('/waze/api/state',headers=headers)['json']
        self.assertEqual(state['history'][0]['actor'],'defensa')

    def test_disabled_account_session_revoked(self):
        headers,s=self.operator_login()
        with self.app.db() as db:db.execute("UPDATE accounts SET enabled=0 WHERE username='defensa'")
        self.assertEqual(self.request('/waze/api/state',headers=headers)['status'],401)

    def test_user_roles_not_in_public_feed(self):
        self.operator_login()
        self.assertEqual(self.request('/waze/feed.json')['json'],{'incidents':[]})

    def test_concurrent_workers_migrate_old_sessions_once(self):
        from app import App
        with self.app.db() as db:
            db.execute('DROP TABLE sessions')
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL, auth_tag TEXT NOT NULL)')
        with ThreadPoolExecutor(max_workers=2) as pool:
            instances=list(pool.map(lambda _:App(self.config),range(2)))
        self.assertEqual(len(instances),2)
        self.login()


if __name__=='__main__':unittest.main()
