from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from history_dashboard import filters
from traffic_store import record, dashboard, normalize
from collect_traffic import collect, validate_url
from backup_observations import backup
import test_app

NOW = 1795781154
URL = 'https://www.waze.com/row-partnerhub-api/feeds-tvt/'+'a'*36+'?id=123'


def route(id='one', **kwargs):
    return {'id':id,'name':'Entrada','line':[{'x':-64.16,'y':-32.17},{'x':-64.14,'y':-32.17}],
            'length':2000,'time':200,'historicTime':180,'jamLevel':0,'subRoutes':[],**kwargs}


def payload(now=NOW, routes=None, **kwargs):
    return {'updateTime':now*1000,'routes':[route()] if routes is None else routes,**kwargs}


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'traffic.sqlite3'

    def read(self,key='',now=NOW+1000):
        return dashboard(self.path,filters('from=2026-11-27&to=2026-11-27','America/Argentina/Cordoba',now),key)

    def test_estimates_unknowns_and_duplicate_source(self):
        self.assertTrue(record(self.path,payload(),NOW))
        self.assertTrue(record(self.path,payload(),NOW+120))
        d=self.read(now=NOW+120)
        self.assertEqual(d['current'][0]['speed'],36)
        self.assertEqual(d['current'][0]['historicSpeed'],40)
        self.assertEqual(d['current'][0]['differenceSeconds'],20)
        self.assertEqual(d['series'][0]['samples'],1)
        self.assertEqual(d['lastStatus'],'repeated');self.assertFalse(d['stale'])
        record(self.path,payload(NOW+240,[route(time=-1,historicTime=0,jamLevel=5)]),NOW+240)
        self.assertIsNone(self.read()['current'][0]['speed'])
        self.assertIsNone(self.read()['current'][0]['historicSpeed'])

    def test_add_remove_and_change_geometry_without_history_loss(self):
        record(self.path,payload(),NOW)
        record(self.path,payload(NOW+120,[route(),route('two',name='Salida')]),NOW+120)
        changed=route(line=[{'x':-64.16,'y':-32.17},{'x':-64.12,'y':-32.17}],length=4000)
        record(self.path,payload(NOW+240,[changed]),NOW+240)
        d=self.read('route:one')
        self.assertEqual(len(d['catalog']),2)
        self.assertFalse(next(r for r in d['catalog'] if r['key']=='route:two')['active'])
        self.assertEqual(len(d['series']),2)
        self.assertEqual({r['length'] for r in d['series']},{2000,4000})
        self.assertEqual(self.read('route:two')['selected']['name'],'Salida')
        record(self.path,payload(NOW+360,[]),NOW+360)
        self.assertEqual(self.read()['current'],[])
        self.assertEqual(len(self.read()['catalog']),2)
        self.assertTrue(all(not r['active'] for r in self.read()['catalog']))
        record(self.path,payload(NOW+480),NOW+480)
        self.assertTrue(self.read()['catalog'][0]['active'])

    def test_failure_old_source_and_invalid_payload_preserve_last_good(self):
        record(self.path,payload(),NOW)
        for data,now in [(payload(NOW-1),NOW+120),(payload(NOW-1000),NOW+240),
                         (payload(NOW+300,[route(line=[])]),NOW+300),
                         (payload(NOW+400,[route(),route()]),NOW+400)]:
            self.assertFalse(record(self.path,data,now))
        d=self.read();self.assertTrue(d['stale']);self.assertEqual(len(d['current']),1)
        self.assertEqual(d['coverage']['failures'],4)
        self.assertEqual(sum(p['samples'] for p in d['series']),1)
        with self.assertRaises(ValueError):normalize(payload(routes=[route(line=[{'x':float('nan'),'y':0},{}])]),NOW)

    def test_subroutes_irregularities_and_relative_counts(self):
        child=route('sub',name='Subruta')
        p=payload(routes=[route(subRoutes=[child])],irregularities=[route('irregular')],usersOnJams=[{'jamLevel':2,'wazersCount':3}],lengthOfJams=[{'jamLevel':2,'jamLength':1000}])
        record(self.path,p,NOW)
        d=self.read();self.assertEqual(d['area']['subroutes'],1);self.assertEqual(d['area']['irregularities'],1)
        self.assertEqual(d['area']['usersOnJams'][0]['wazersCount'],3)
        self.assertEqual(len(d['current']),1)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM traffic_samples').fetchone()[0],3)

    def test_collector_secrets_errors_and_backup(self):
        secret=self.root/'secret';secret.write_text(URL)
        self.assertEqual(validate_url(URL),URL)
        for bad in (URL.replace('https:','http:'),URL+'&x=1',URL.replace('www.waze.com','evil.test'),URL+'#secret'):
            with self.assertRaises(ValueError):validate_url(bad)
        self.assertTrue(collect(secret,self.path,lambda _:payload(),NOW))
        def fail(_):raise RuntimeError(URL)
        self.assertFalse(collect(secret,self.path,fail,NOW+120))
        self.assertNotIn(URL,self.path.read_bytes().decode('utf-8','ignore'))
        self.assertTrue(backup(self.path,self.root/'backups').exists())

    def test_period_boundaries_and_unknown_route(self):
        record(self.path,payload(),NOW)
        with self.assertRaises(ValueError):self.read('missing')
        f=filters('from=2026-11-26&to=2026-11-26','America/Argentina/Cordoba',NOW)
        d=dashboard(self.path,f)
        self.assertEqual(d['series'],[]);self.assertEqual(d['catalog'],[])
        self.assertEqual(d['coverage']['gaps'],1)


class TrafficApiTests(unittest.TestCase):
    setUp=test_app.ServiceTests.setUp
    request=test_app.ServiceTests.request
    login=test_app.ServiceTests.login

    def test_permissions_and_routes(self):
        self.app.traffic_path=Path(self.tmp.name)/'traffic.sqlite3'
        record(self.app.traffic_path,payload(),NOW)
        self.assertEqual(self.request('/waze/api/traffic')['status'],401)
        h,_=self.login()
        self.assertEqual(self.request('/waze/api/traffic',headers=h)['status'],200)
        self.assertEqual(self.request('/waze/api/traffic',headers={**h,'QUERY_STRING':'route=missing'})['status'],400)
        self.assertEqual(self.request('/waze/api/traffic',headers={**h,'QUERY_STRING':'route=a&route=b'})['status'],400)
        for path in ('/traffic','/assets/traffic.js','/assets/traffic.css'):
            self.assertEqual(self.request('/waze'+path)['status'],200)
        self.app.traffic_path=Path(self.tmp.name)/'missing.sqlite3'
        self.assertEqual(self.request('/waze/api/traffic',headers=h)['status'],503)
        self.assertFalse(self.app.traffic_path.exists())
