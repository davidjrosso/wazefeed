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
