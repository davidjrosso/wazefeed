from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from app import App
from catalog import CITY
from collect_waze import collect
from history_dashboard import dashboard, detail, filters
from monitoring import merge
import test_app
from test_monitoring import URL, alert

ZONE = CITY['timezone']
START = datetime(2026,11,26,0,tzinfo=ZoneInfo(ZONE)).timestamp()


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=self.root/'current.json';self.db=self.root/'history.sqlite3'
        self.secret=self.root/'feed.url';self.secret.write_text(URL)
        self.a=alert('one',street='Sáenz Peña',pubMillis=(START+3600)*1000,reliability=5)
        self.b=alert('two',street='Belgrano',type='ACCIDENT',subtype='',pubMillis=(START-3600)*1000,reliability=9)
        self.poll([self.a,self.b],START+7200)
        self.poll([self.a,self.b],START+7320)
        self.poll([{**self.a,'reliability':7}],START+10800)
        self.poll([{**self.a,'reliability':10,'type':'ACCIDENT','subtype':''}],START+86400+7200)

    def poll(self,alerts,at):
        self.assertTrue(collect(self.secret,self.cache,CITY,at,lambda _: {'alerts':alerts,'endTimeMillis':at*1000}))

    def f(self,extra=''):
        return filters('from=2026-11-26&to=2026-11-26'+extra,ZONE,START+3*86400)

    def test_unique_reports_not_poll_count_and_asof_version(self):
        result=dashboard(self.db,self.f())
        self.assertEqual(result['metrics']['unique'],2)
        self.assertEqual(result['metrics']['published'],1)
        self.assertEqual(result['metrics']['averageReliability'],8)
        self.assertEqual(result['coverage']['successful'],3)
        self.assertEqual(len(result['series']),24)
        self.assertEqual(result['series'][2]['observed'],2)
        self.assertEqual(result['series'][3]['observed'],1)
        one=next(r for r in result['reports'] if r['id']=='one')
        self.assertEqual(one['reliability'],7)
        self.assertEqual(one['category'],'pothole')
        self.assertEqual(one['observations'],3)

    def test_filter_accents_type_and_minimum_reliability(self):
        self.assertEqual(dashboard(self.db,self.f('&street=saenz&reliability=7'))['total'],1)
        self.assertEqual(dashboard(self.db,self.f('&type=accident'))['reports'][0]['id'],'two')
        result=dashboard(self.db,self.f('&reliability=9'))
        self.assertEqual(result['series'][2]['observed'],1)
        self.assertEqual(result['series'][3]['observed'],0)
        self.assertEqual(result['coverage']['successful'],3) # Health is not filtered by street.

    def test_argentina_date_boundaries(self):
        self.assertEqual(self.f()['start'],START)
        self.assertEqual(self.f()['end'],START+86400)
        f=filters('from=2026-11-26&to=2026-11-27',ZONE,START+3*86400)
        result=dashboard(self.db,f)
        self.assertEqual(len(result['series']),2)
        self.assertEqual([r['observed'] for r in result['series']],[2,1])
        self.assertEqual(result['total'],2)

    def test_before_collection_is_not_a_zero_traffic_day(self):
        f=filters('from=2026-11-25&to=2026-11-25',ZONE,START+86400)
        result=dashboard(self.db,f)
        self.assertEqual(result['total'],0)
        self.assertTrue(result['coverage']['beforeArchive'])
        self.assertEqual(result['coverage']['successful'],0)
        self.assertTrue(all(b['readings']==0 for b in result['series']))

    def test_failed_and_partial_readings_count_separately(self):
        at=START+14400
        collect(self.secret,self.cache,CITY,at,lambda _: None)
        # Older-than-archive sources fail too; future test reading is the new day.
        at=START+86400+10800
        self.poll([alert('invalid',location={})],at)
        f=filters('from=2026-11-26&to=2026-11-27',ZONE,START+3*86400)
        c=dashboard(self.db,f)['coverage']
        self.assertEqual(c['failed'],1);self.assertEqual(c['partial'],1)
        self.assertGreater(c['gaps'],0)

    def test_detail_limits_events_to_period_and_rejects_unknown(self):
        d=detail(self.db,'one',self.f())
        self.assertEqual(d['report']['reliability'],7)
        self.assertEqual([e['kind'] for e in d['events']],['new','changed'])
        self.assertIsNone(detail(self.db,'unknown',self.f()))
        with self.assertRaises(ValueError):detail(self.db,'../anything',self.f())

    def test_validation_and_empty_filters(self):
        for q in ('from=bad','from=2025-01-01&to=2026-11-26','from=2026-11-27&to=2026-11-26','type=bogus','offset=-1','reliability=11','from=2026-11-26&from=2026-11-26','to=2099-01-01'):
            with self.subTest(query=q),self.assertRaises(ValueError):filters(q,ZONE,START+86400)
        self.assertEqual(dashboard(self.db,self.f('&street=unknown'))['total'],0)

    def test_pagination_keeps_global_metrics(self):
        self.poll([alert(str(i),pubMillis=(START+86400)*1000) for i in range(61)],START+86400+8000)
        f=filters('from=2026-11-27&to=2026-11-27&offset=50',ZONE,START+3*86400)
        result=dashboard(self.db,f)
        self.assertEqual(result['total'],62)
        self.assertEqual(len(result['reports']),12)
        self.assertEqual(result['metrics']['unique'],62)

    def test_unknown_reliability_is_not_zero_and_decimals_not_lost(self):
        self.poll([alert('unknown',pubMillis=START*1000,reliability=None),alert('decimal',pubMillis=START*1000,reliability=5.5)],START+86400+8000)
        f=filters('from=2026-11-27&to=2026-11-27&street=Igualdad',ZONE,START+3*86400)
        result=dashboard(self.db,f)
        self.assertEqual(result['metrics']['reliabilityKnown'],1)
        self.assertEqual(result['metrics']['averageReliability'],5.5)
        self.assertEqual(sum(r['count'] for r in result['reliability']),1)

    def test_import_is_not_counted_as_new_detection(self):
        root=self.root/'import';root.mkdir();cache=root/'current.json'
        payload={'alerts':[self.a],'endTimeMillis':(START+7200)*1000}
        cache.write_text(json.dumps(merge({},payload,CITY['bounds'],START+7200)))
        collect(self.secret,cache,CITY,START+7320,lambda _: {**payload,'endTimeMillis':(START+7320)*1000})
        result=dashboard(root/'history.sqlite3',self.f())
        self.assertEqual(result['metrics']['firstDetected'],0)
        self.assertTrue(result['reports'][0]['imported'])

    def test_comparison_requires_coverage_and_uses_previous_asof_values(self):
        f=filters('from=2026-11-27&to=2026-11-27&reliability=9',ZONE,START+86400+10800)
        a=dashboard(self.db,f)['analysis']
        self.assertEqual(a['comparison']['current'],1)
        self.assertEqual(a['comparison']['previous'],1) # two, not tomorrow's version of one
        self.assertFalse(a['comparison']['comparable'])
        self.assertIsNone(a['comparison']['changePercent'])
        self.assertEqual(a['comparison']['previousEnd'],START+10800)
        self.assertTrue(a['days'][0]['inProgress'])

    def test_recurrence_and_hour_counts_do_not_count_poll_repeats(self):
        a=dashboard(self.db,self.f())['analysis']
        self.assertEqual(sum(c['reports'] for c in a['locations']),2)
        self.assertEqual(a['hours'][2]['count'],2)
        self.assertEqual(a['hours'][3]['count'],1)
        self.assertEqual(a['days'][0]['observed'],2)
        self.assertEqual(a['days'][0]['published'],1)
        self.assertEqual(a['duration']['medianSeconds'],1860)
        f=filters('from=2026-11-26&to=2026-11-27',ZONE,START+3*86400)
        a=dashboard(self.db,f)['analysis']
        self.assertEqual(a['hours'][2]['count'],3) # same UUID on another day is a report-day
        self.assertEqual(max(c['days'] for c in a['locations']),2)
        self.assertEqual(sum(c['reports'] for c in a['locations']),2)

    def test_signal_history_and_absence_do_not_leak_future(self):
        one=detail(self.db,'one',self.f())['evolution']
        self.assertEqual([p['reliability'] for p in one['points']],[5,7])
        self.assertEqual(one['points'][0]['until'],START+7320)
        self.assertEqual(one['spanSeconds'],3600)
        self.assertEqual(one['gaps'],1)
        self.assertEqual(one['lastState']['kind'],'changed')
        two=detail(self.db,'two',self.f())['evolution']
        self.assertEqual(two['lastState']['kind'],'absent')
        self.assertEqual(two['lastState']['at'],START+10800)

    def test_comparable_windows_and_zero_baseline(self):
        root=self.root/'continuous';root.mkdir();cache=root/'current.json'
        for i in range(0,2*86400+601,120):
            collect(self.secret,cache,CITY,START+i,lambda _,i=i:{'alerts':[] if i<86400 else [self.a], 'endTimeMillis':(START+i)*1000})
        f=filters('from=2026-11-27&to=2026-11-27',ZONE,START+2*86400+600)
        a=dashboard(root/'history.sqlite3',f)['analysis']['comparison']
        self.assertTrue(a['comparable'])
        self.assertEqual((a['current'],a['previous']),(1,0))
        self.assertIsNone(a['changePercent'])


class DashboardAPITests(unittest.TestCase):
    request=test_app.ServiceTests.request
    login=test_app.ServiceTests.login

    def setUp(self):
        test_app.ServiceTests.setUp(self)
        self.config['WAZE_HISTORY_PATH']=str(Path(self.tmp.name)/'missing.sqlite3')
        self.app=App(self.config)

    def test_authentication_and_missing_archive(self):
        for path in ('/waze/api/history','/waze/api/history/one'):
            self.assertEqual(self.request(path)['status'],401)
        h,_=self.login()
        self.assertEqual(self.request('/waze/api/history',headers=h)['status'],503)
        self.assertFalse(Path(self.config['WAZE_HISTORY_PATH']).exists())

    def test_bad_parameters_and_static_page(self):
        h,_=self.login()
        self.assertEqual(self.request('/waze/api/history',headers={**h,'QUERY_STRING':'from=bad'})['status'],400)
        for path in ('/waze/history','/waze/assets/history.js','/waze/assets/history.css'):
            self.assertEqual(self.request(path)['status'],200)
        self.assertIn('/waze/history',self.request('/waze/')['body'].decode())

    def test_viewer_can_read_archive_and_cannot_write(self):
        with self.app.db() as db:db.execute('INSERT INTO accounts VALUES(?,?,?,1)',('reader',test_app.HASH,'viewer'))
        r=self.request('/waze/api/login','POST',{'username':'reader','password':test_app.PASSWORD})
        h={'HTTP_COOKIE':r['headers']['Set-Cookie'].split(';')[0]}
        self.assertEqual(self.request('/waze/api/history',headers=h)['status'],503) # Authorized, missing file.
        self.assertEqual(self.request('/waze/api/history','POST',{},h)['status'],404)

    def archive(self):
        root=Path(self.tmp.name);secret=root/'feed.url';secret.write_text(URL)
        collect(secret,root/'current.json',CITY,START+7200,lambda _:{'alerts':[alert('archived',pubMillis=START*1000)],'endTimeMillis':(START+7200)*1000})
        self.app.history_path=str(root/'history.sqlite3')

    def test_research_auth_csrf_validation_and_append_only(self):
        self.archive();h,_=self.login()
        body={'id':'archived','revision':0,'status':'investigating','note':'Revisé el lugar <script>sin ejecutar</script>'}
        path='/waze/api/history-notes'
        self.assertEqual(self.request(path,'POST',body)['status'],401)
        self.assertEqual(self.request(path,'POST',body,{'HTTP_COOKIE':h['HTTP_COOKIE']})['status'],403)
        self.assertEqual(self.request(path,'POST',body,{**h,'HTTP_ORIGIN':'https://evil.test'})['status'],403)
        first=self.request(path,'POST',body,h)
        self.assertEqual(first['status'],200)
        self.assertEqual(first['json']['entries'][0]['actor'],'operador')
        self.assertEqual(self.request(path,'POST',body,h)['status'],409) # stale or duplicate submit
        revised={**body,'revision':first['json']['revision'],'status':'unverified','note':'No se pudo comprobar'}
        second=self.request(path,'POST',revised,h)
        self.assertEqual(len(second['json']['entries']),2)
        self.assertEqual(second['json']['entries'][1]['note'],body['note'])
        for invalid in ({'note':''},{'note':'x'*2001},{'status':'resolved'},{'id':'unknown'},{'revision':True}):
            self.assertEqual(self.request(path,'POST',{**revised,**invalid},h)['status'],409)
        query={'QUERY_STRING':'from=2026-11-26&to=2026-11-26',**h}
        result=self.request('/waze/api/history/archived',headers=query)
        self.assertEqual(result['status'],200)
        self.assertEqual(result['json']['research']['entries'][0]['status'],'unverified')
        self.assertTrue(result['json']['canWrite'])
        self.assertEqual(result['json']['csrf'],h['HTTP_X_CSRF_TOKEN'])

    def test_viewer_cannot_add_research_notes(self):
        self.archive()
        with self.app.db() as db:db.execute('INSERT INTO accounts VALUES(?,?,?,1)',('reader',test_app.HASH,'viewer'))
        r=self.request('/waze/api/login','POST',{'username':'reader','password':test_app.PASSWORD})
        h={'HTTP_COOKIE':r['headers']['Set-Cookie'].split(';')[0]}
        state=self.request('/waze/api/state',headers=h)['json'];h['HTTP_X_CSRF_TOKEN']=state['csrf']
        self.assertEqual(self.request('/waze/api/history-notes','POST',{'id':'archived','revision':0,'status':'pending','note':'No permitido'},h)['status'],403)
        result=self.request('/waze/api/history/archived',headers={**h,'QUERY_STRING':'from=2026-11-26&to=2026-11-26'})
        self.assertFalse(result['json']['canWrite'])
        self.assertEqual(result['json']['research']['entries'],[])
