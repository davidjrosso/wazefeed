import json
import sqlite3
import unittest
from pathlib import Path

from app import App
from catalog import CITY
from collect_waze import collect
from history_dashboard import filters, dashboard, replay, add_note
import history_tools as ht
from monitoring import normalize
import test_app
from test_monitoring import alert, URL
from test_history_dashboard import START, ZONE


class HistoryToolsTests(unittest.TestCase):
    request=test_app.ServiceTests.request
    login=test_app.ServiceTests.login

    def setUp(self):
        test_app.ServiceTests.setUp(self)
        self.root=Path(self.tmp.name);self.secret=self.root/'feed.url';self.secret.write_text(URL)
        self.cache=self.root/'current.json';self.archive=self.root/'history.sqlite3'
        self.app.history_path=str(self.archive)
        self.a=alert('a',pubMillis=START*1000,reportRating=4)
        self.b=alert('b',pubMillis=(START+60)*1000,location={'x':-64.0976,'y':-32.1805})
        self.poll([self.a,self.b],START+120)
        self.poll([{**self.a,'reportRating':6,'reliability':9},self.b],START+240)
        collect(self.secret,self.cache,CITY,START+360,lambda _:None)

    def poll(self,alerts,at):
        collect(self.secret,self.cache,CITY,at,lambda _:{'alerts':alerts,'endTimeMillis':at*1000})

    def f(self,extra=''):
        return filters('from=2026-11-26&to=2026-11-26'+extra,ZONE,START+7200)

    def test_reporter_fields_and_unknowns(self):
        a=normalize(self.a,CITY['bounds'],START+100)
        self.assertEqual(a['reportRating'],4);self.assertFalse(a['municipalSource'])
        for value in (None,-1,7,True,'4',4.5):
            self.assertIsNone(normalize({**self.a,'reportRating':value},CITY['bounds'],START+100)['reportRating'])
        self.assertEqual(normalize({**self.a,'reportRating':0},CITY['bounds'],START+100)['reportRating'],0)
        missing={k:v for k,v in self.a.items() if k!='reportByMunicipalityUser'}
        self.assertIsNone(normalize(missing,CITY['bounds'],START+100)['municipalSource'])
        self.assertTrue(normalize({**self.a,'reportByMunicipalityUser':True},CITY['bounds'],START+100)['municipalSource'])

    def test_replay_asof_reading_failure_gap_and_no_future(self):
        p=replay(self.archive,self.f('&at='+str(START+180)))
        self.assertEqual(p['readingAt'],START+120)
        self.assertEqual(next(r for r in p['reports'] if r['id']=='a')['reportRating'],4)
        p=replay(self.archive,self.f('&at='+str(START+240)))
        self.assertEqual(next(r for r in p['reports'] if r['id']=='a')['reportRating'],6)
        p=replay(self.archive,self.f('&at='+str(START+360)))
        self.assertEqual(p['status'],'failed');self.assertIsNone(p['total']);self.assertEqual(p['reports'],[])
        self.assertEqual(replay(self.archive,self.f('&at='+str(START+1200)))['status'],'gap')
        self.assertEqual(replay(self.archive,self.f('&at='+str(START+60)))['status'],'gap')
        with self.assertRaises(ValueError):replay(self.archive,self.f('&at='+str(START+8000)))
        for bad in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValueError):self.f('&at='+bad)

    def test_replay_partial_is_not_complete(self):
        self.poll([self.a,alert('bad',location={})],START+480)
        p=replay(self.archive,self.f('&at='+str(START+480)))
        self.assertEqual(p['status'],'partial');self.assertEqual(p['total'],1)

    def test_zones_filter_dashboard_and_replay(self):
        with self.app.db() as db:
            z=ht.save_zone(db,{'name':'Escuela','lat':-32.180473,'lon':-64.097578,'radius':100},'operador')
            f=self.f('&zone='+str(z['id']));ht.resolve_zone(db,f)
            self.assertEqual(dashboard(self.archive,f,db)['total'],2)
            f['at']=START+240;self.assertEqual(replay(self.archive,f)['total'],2)
            far=ht.save_zone(db,{'name':'Centro','lat':-32.173,'lon':-64.114,'radius':100},'operador')
            f=self.f('&zone='+str(far['id']));ht.resolve_zone(db,f)
            self.assertEqual(dashboard(self.archive,f,db)['total'],0)
            f['at']=START+240;self.assertEqual(replay(self.archive,f)['total'],0)
            with self.assertRaises(ValueError):ht.resolve_zone(db,self.f('&zone=999'))
            for invalid in ({'name':'escuela'},{'lat':0},{'radius':99},{'radius':True},{'name':''},{'lon':float('nan')}):
                with self.assertRaises(ValueError):ht.save_zone(db,{'name':'Otra','lat':-32.18,'lon':-64.10,'radius':100,**invalid},'operador')

    def test_suggestions_manual_review_preserves_reports_and_audit(self):
        with self.app.db() as db:
            a=dashboard(self.archive,self.f(),db)['analysis']
            self.assertEqual(len(a['related']['pairs']),1)
            body={'ids':['b','a'],'decision':'related','revision':0}
            first=ht.save_link(db,self.archive,body,'operador')
            with self.assertRaises(ValueError):ht.save_link(db,self.archive,body,'otro')
            ht.save_link(db,self.archive,{**body,'decision':'separate','revision':first['id']},'otro')
            result=dashboard(self.archive,self.f(),db)
            self.assertEqual(result['total'],2)
            self.assertEqual(result['analysis']['related']['pairs'][0]['decision'],'separate')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history_links').fetchone()[0],2)
            for invalid in ({'ids':['a','a']},{'ids':['a','missing']},{'decision':'wrong'},{'revision':True}):
                with self.assertRaises(ValueError):ht.save_link(db,self.archive,{**body,**invalid},'operador')

    def test_pair_rules_and_verification_denominators(self):
        with self.app.db() as db:
            rows=dashboard(self.archive,self.f())['reports']
            a,b=rows
            for changed in ({'subtype':'OTHER'},{'publishedAt':START-90000},{'point':[-32.17,-64.11]},{'periodFirst':START+500,'periodLast':START+600}):
                self.assertEqual(ht.enrich(db,[a,{**b,**changed}])['related']['pairs'],[])
            one=add_note(db,self.archive,{'id':'a','revision':0,'status':'confirmed','note':'Prueba'},'operador')
            add_note(db,self.archive,{'id':'a','revision':one['revision'],'status':'unverified','note':'Revisado'},'operador')
            v=ht.enrich(db,rows)['verification']
            self.assertEqual(v['total'],2);self.assertEqual(v['reviewed'],1)
            self.assertEqual(v['confirmedPercent'],0)
            self.assertEqual(v['counts']['untouched'],1)
            self.assertEqual(sum(b['unverified'] for b in v['bands']),1)

    def test_api_permissions_csrf_and_persistence(self):
        for path in ('/waze/api/history-tools','/waze/api/history-replay'):
            self.assertEqual(self.request(path)['status'],401)
        h,_=self.login();tools=self.request('/waze/api/history-tools',headers=h)['json']
        self.assertTrue(tools['canWrite']);self.assertEqual(tools['csrf'],h['HTTP_X_CSRF_TOKEN'])
        body={'name':'Prueba','lat':-32.18,'lon':-64.10,'radius':200}
        for path,body in [('/waze/api/history-zones',body),('/waze/api/history-links',{'ids':['a','b'],'decision':'related','revision':0})]:
            self.assertEqual(self.request(path,'POST',body,{'HTTP_COOKIE':h['HTTP_COOKIE']})['status'],403)
            self.assertEqual(self.request(path,'POST',body,{**h,'HTTP_ORIGIN':'https://evil.test'})['status'],403)
            self.assertEqual(self.request(path,'POST',body,h)['status'],200)
        with self.app.db() as db:db.execute('INSERT INTO accounts VALUES(?,?,?,1)',('reader',test_app.HASH,'viewer'))
        r=self.request('/waze/api/login','POST',{'username':'reader','password':test_app.PASSWORD})
        reader={'HTTP_COOKIE':r['headers']['Set-Cookie'].split(';')[0]}
        tool=self.request('/waze/api/history-tools',headers=reader)['json'];reader['HTTP_X_CSRF_TOKEN']=tool['csrf']
        self.assertFalse(tool['canWrite']);self.assertEqual(len(tool['zones']),1)
        for path in ('/waze/api/history-zones','/waze/api/history-links'):
            self.assertEqual(self.request(path,'POST',{},reader)['status'],403)
        query='from=2026-11-26&to=2026-11-26&at='+str(START+180)
        self.assertEqual(self.request('/waze/api/history-replay',headers={**reader,'QUERY_STRING':query})['status'],200)
        self.assertEqual(self.request('/waze/api/history',headers={**reader,'QUERY_STRING':'zone=999'})['status'],400)
