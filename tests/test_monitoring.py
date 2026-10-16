import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
import test_app
from app import App
from catalog import CITY
from collect_waze import collect, validate_url
from monitoring import merge, listing, read_cache, RETENTION

NOW=1791542478
URL='https://www.waze.com/row-partnerhub-api/partners/123/waze-feeds/00000000-0000-4000-8000-000000000000?format=1'


def alert(uid='sample-1', **extra):
    return {'uuid':uid,'type':'HAZARD','subtype':'HAZARD_ON_ROAD_POT_HOLE','street':'Igualdad',
            'pubMillis':(NOW-100)*1000,'location':{'x':-64.097578,'y':-32.180473},
            'reportByMunicipalityUser':'false','reliability':5,'confidence':0,'nThumbsUp':0,**extra}


def snapshot(alerts=None,now=NOW):
    return {'endTimeMillis':now*1000,'alerts':[alert()] if alerts is None else alerts}


class MonitoringUnitTests(unittest.TestCase):
    def test_normalization(self):
        data=merge({},snapshot(),CITY['bounds'],NOW)
        a=data['alerts'][0]
        self.assertEqual(a['point'],[-32.180473,-64.097578]);self.assertFalse(a['municipal'])
        self.assertEqual(a['thumbsUp'],0);self.assertEqual(a['firstSeen'],NOW)
        self.assertNotIn('url',json.dumps(data))

    def test_repeat_is_not_duplicate_and_first_seen_preserved(self):
        first=merge({},snapshot(),CITY['bounds'],NOW)
        second=merge(first,snapshot(now=NOW+120),CITY['bounds'],NOW+120)
        self.assertEqual(len(second['alerts']),1);self.assertEqual(second['alerts'][0]['firstSeen'],NOW)
        self.assertEqual(second['alerts'][0]['lastSeen'],NOW+120)

    def test_disappearance_and_return_same_id(self):
        first=merge({},snapshot(),CITY['bounds'],NOW)
        second=merge(first,snapshot([],NOW+120),CITY['bounds'],NOW+120)
        self.assertFalse(second['alerts'][0]['present']);self.assertEqual(second['alerts'][0]['lastSeen'],NOW)
        third=merge(second,snapshot(now=NOW+240),CITY['bounds'],NOW+240)
        self.assertTrue(third['alerts'][0]['present']);self.assertEqual(third['alerts'][0]['firstSeen'],NOW)

    def test_invalid_record_preserves_previous_presence(self):
        first=merge({},snapshot(),CITY['bounds'],NOW)
        second=merge(first,snapshot([alert(location={})],NOW+120),CITY['bounds'],NOW+120)
        self.assertTrue(second['alerts'][0]['present']);self.assertEqual(second['meta']['invalidCount'],1)

    def test_outside_area_and_unsafe_values(self):
        data=merge({},snapshot([alert('outside',location={'x':0,'y':0}),alert('bad',location={'x':float('nan'),'y':-32}),alert('bad/id')]),CITY['bounds'],NOW)
        self.assertEqual(data['alerts'],[]);self.assertEqual(data['meta']['outsideCount'],1)
        self.assertEqual(data['meta']['invalidCount'],2)

    def test_invalid_and_old_envelopes(self):
        previous=merge({},snapshot(),CITY['bounds'],NOW)
        for payload in ({},[],{'alerts':{}},snapshot(now=NOW-601),snapshot(now=NOW+301),snapshot(now=NOW-1)):
            with self.assertRaises(ValueError):merge(previous,payload,CITY['bounds'],NOW)

    def test_retention(self):
        previous=merge({},snapshot(),CITY['bounds'],NOW)
        data=merge(previous,snapshot([],NOW+RETENTION+1),CITY['bounds'],NOW+RETENTION+1)
        self.assertEqual(data['alerts'],[])

    def test_nearby_reports_not_merged(self):
        data=merge({},snapshot([alert('one'),alert('two')]),CITY['bounds'],NOW)
        self.assertEqual(len(data['alerts']),2)

    def test_url_allowlist(self):
        self.assertEqual(validate_url(URL),URL)
        for value in ('http://www.waze.com/x','https://evil.test/x',URL.replace('www.waze.com','www.waze.com@evil.test'),URL+'&other=1',URL+'#secret',URL.replace('www.waze.com','www.waze.com:443')):
            with self.assertRaises(ValueError):validate_url(value)

    def test_collector_failure_keeps_data_and_hides_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret=Path(tmp)/'secret';output=Path(tmp)/'current.json';secret.write_text(URL)
            self.assertTrue(collect(secret,output,CITY,NOW,lambda _:snapshot()))
            def fail(_):raise RuntimeError('Secret '+URL)
            self.assertFalse(collect(secret,output,CITY,NOW+120,fail))
            data=read_cache(output);self.assertEqual(len(data['alerts']),1)
            self.assertEqual(data['meta']['lastSuccess'],NOW);self.assertEqual(data['meta']['error'],'fetch_failed')
            self.assertNotIn(URL,output.read_text());self.assertEqual(list(Path(tmp).glob('.monitor-*')),[])

    def test_corrupt_cache_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'file';p.write_text('{broken')
            self.assertEqual(read_cache(p)['meta']['error'],'unavailable')


class MonitoringAPITests(unittest.TestCase):
    request=test_app.ServiceTests.request
    login=test_app.ServiceTests.login

    def setUp(self):
        test_app.ServiceTests.setUp(self)
        self.cache=Path(self.tmp.name)/'monitor.json'
        now=time.time()
        self.cache.write_text(json.dumps(merge({},snapshot(now=now),CITY['bounds'],now)))
        self.config['WAZE_MONITOR_PATH']=str(self.cache)
        self.app=App(self.config)

    def user(self,role):
        with self.app.db() as db:db.execute('INSERT INTO accounts VALUES(?,?,?,1)',(role,test_app.HASH,role))
        response=self.request('/waze/api/login','POST',{'username':role,'password':test_app.PASSWORD})
        h={'HTTP_COOKIE':response['headers']['Set-Cookie'].split(';')[0]}
        h['HTTP_X_CSRF_TOKEN']=self.request('/waze/api/state',headers=h)['json']['csrf'];return h

    def change(self,h,**extra):
        return self.request('/waze/api/monitor','POST',{'id':'sample-1','version':0,'status':'verified','area':'','note':'Verificado por cámara',**extra},h)

    def test_private_endpoints(self):
        for p in ('/api/monitor','/api/monitor/sample-1'):
            self.assertEqual(self.request('/waze'+p)['status'],401)
        self.assertEqual(self.change({})['status'],401)

    def test_viewer_read_only(self):
        h=self.user('viewer')
        self.assertEqual(self.request('/waze/api/monitor',headers=h)['status'],200)
        self.assertEqual(self.change(h)['status'],403)

    def test_operator_audit_and_no_closure_changes(self):
        h=self.user('operator')
        with self.app.db() as db:before=self.app.state(db)
        feed=self.request('/waze/feed.json')['body']
        response=self.change(h)
        self.assertEqual(response['status'],200)
        case=response['json']['alerts'][0]['case'];self.assertEqual(case['status'],'verified');self.assertEqual(case['actor'],'operator')
        self.assertEqual(self.request('/waze/api/monitor/sample-1',headers=h)['json']['history'][0]['note'],'Verificado por cámara')
        self.assertEqual(feed,self.request('/waze/feed.json')['body'])
        with self.app.db() as db:self.assertEqual(before,self.app.state(db))

    def test_origin_csrf(self):
        h,_=self.login()
        self.assertEqual(self.change({**h,'HTTP_X_CSRF_TOKEN':''})['status'],403)
        self.assertEqual(self.change({**h,'HTTP_ORIGIN':'https://evil.test'})['status'],403)

    def test_conflict_does_not_overwrite(self):
        h,_=self.login();self.assertEqual(self.change(h)['status'],200)
        self.assertEqual(self.change(h,status='progress')['status'],409)
        self.assertEqual(self.request('/waze/api/monitor',headers=h)['json']['alerts'][0]['case']['status'],'verified')

    def test_resolution_requires_note_assignment_requires_area(self):
        h,_=self.login()
        for extra in ({'status':'resolved','note':' '},{'status':'assigned','area':''},{'status':'bogus'},{'area':'bogus'},{'note':'a'*1001},{'id':'unknown'}):
            self.assertEqual(self.change(h,**extra)['status'],409)

    def test_resolve_and_reopen_independent_of_waze(self):
        h,_=self.login()
        r=self.change(h,status='resolved',note='Reparado y verificado en campo')
        self.assertTrue(r['json']['alerts'][0]['present'])
        self.assertEqual(self.change(h,version=1,status='pending',note='Nueva revisión')['status'],200)
        self.assertEqual(len(self.request('/waze/api/monitor/sample-1',headers=h)['json']['history']),2)

    def test_case_survives_cache_retention_and_missing_source(self):
        h,_=self.login();self.change(h);self.cache.unlink()
        data=self.request('/waze/api/monitor',headers=h)['json']
        self.assertTrue(data['meta']['stale']);self.assertFalse(data['alerts'][0]['present'])
        self.assertEqual(data['alerts'][0]['case']['status'],'verified')
        self.assertEqual(self.change(h,version=1,status='progress')['status'],200)

    def test_restart_keeps_case_history(self):
        h,_=self.login();self.change(h);self.app=App(self.config)
        self.assertEqual(self.request('/waze/api/monitor',headers=h)['json']['alerts'][0]['case']['version'],1)
        self.assertEqual(len(self.request('/waze/api/monitor/sample-1',headers=h)['json']['history']),1)

    def test_stale_cache_and_assets(self):
        data=json.loads(self.cache.read_text());data['meta']['lastSuccess']=time.time()-900;self.cache.write_text(json.dumps(data))
        h,_=self.login();self.assertTrue(self.request('/waze/api/monitor',headers=h)['json']['meta']['stale'])
        for path in ('/assets/monitor.js','/assets/monitor.css'):
            self.assertEqual(self.request('/waze'+path)['status'],200)
