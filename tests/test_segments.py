import copy
import json
import math
import unittest
from concurrent.futures import ThreadPoolExecutor

from app import App
from core import change, empty_state, migrate_state, feed, geojson, active_events, SEGMENTS, iso
import test_app


def draft():
    return {'street': 'Tramo de prueba', 'from': 'Esquina A', 'to': 'Esquina B',
            'direction': 'ONE_DIRECTION', 'wazeSegmentId': '',
            'points': [[-32.175, -64.12], [-32.1755, -64.1195], [-32.176, -64.119]]}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state()
        self.ids = [s['id'] for s in self.state['segments']]

    def mutate(self, action, **kwargs):
        self.state = change(self.state, action, self.state['revision'], 'test', now=1000, **kwargs)
        return self.state

    def test_legacy_migration_keeps_feed_identifiers_and_times(self):
        legacy = {'revision': 7, 'events': [{'id':'old-event','start':1000,'end':4600,'updated':1000,'closedAt':None}], 'history':[]}
        migrated = migrate_state(legacy)
        self.assertEqual(migrated['revision'], 8)
        self.assertEqual(len(migrated['segments']), 4)
        self.assertEqual(len(active_events(migrated, 2000)), 4)
        entries = feed(migrated, 2000)['incidents']
        for entry, segment in zip(entries, SEGMENTS):
            self.assertEqual(entry['id'], 'rio3-old-event-' + str(segment['id']))
            self.assertEqual(entry['starttime'], iso(1000))
            self.assertEqual(entry['endtime'], iso(4600))
            self.assertEqual(entry['polyline'], ' '.join(f'{a:.7f} {b:.7f}' for a,b in segment['points']))
        self.assertEqual(migrate_state(migrated), migrated)
        self.assertNotIn('schema', legacy)

    def test_add_edit_archive_restore_does_not_create_closures(self):
        self.mutate('save_segment', segment=draft())
        sid=self.state['segments'][-1]['id']
        self.mutate('save_segment', segment={**draft(), 'id':sid, 'street':'Nombre corregido'})
        self.mutate('archive_segment', segment_id=sid)
        self.assertEqual(len(geojson(self.state)['features']), 4)
        self.mutate('restore_segment', segment_id=sid)
        self.assertEqual(len(geojson(self.state)['features']), 5)
        self.assertEqual(self.state['segments'][-1]['street'], 'Nombre corregido')
        self.assertEqual(feed(self.state, 1000), {'incidents':[]})
        self.assertEqual(len(self.state['history']), 4)

    def test_independent_closures_and_partial_reopen(self):
        self.mutate('activate', segment_ids=self.ids[:2])
        first=copy.deepcopy(self.state['events'])
        self.mutate('activate', segment_ids=[self.ids[2]])
        self.mutate('reopen', segment_ids=[self.ids[0]])
        self.assertEqual({e['segmentId'] for e in active_events(self.state,1000)}, set(self.ids[1:3]))
        self.assertEqual(self.state['events'][0]['feedId'], first[0]['feedId'])
        self.assertEqual(self.state['events'][0]['start'], first[0]['start'])
        self.assertEqual(self.state['events'][1], first[1])
        self.assertEqual(len(feed(self.state, 1000)['incidents']), 3)

    def test_snapshot_does_not_change_after_reopen_edit_archive(self):
        self.mutate('activate', segment_ids=[self.ids[0]])
        with self.assertRaises(ValueError):
            self.mutate('archive_segment', segment_id=self.ids[0])
        with self.assertRaises(ValueError):
            self.mutate('save_segment', segment={**self.state['segments'][0], 'street':'No debe cambiar'})
        self.mutate('reopen', segment_ids=[self.ids[0]])
        closed_feed=feed(self.state,1000)
        self.mutate('save_segment', segment={**self.state['segments'][0], 'street':'Nombre nuevo', 'direction':'BOTH_DIRECTIONS'})
        self.mutate('archive_segment', segment_id=self.ids[0])
        self.assertEqual(feed(self.state,1000), closed_feed)
        self.assertGreater(len(self.state['history']),0)

    def test_bulk_operation_is_atomic_and_requires_selection(self):
        self.mutate('activate', segment_ids=[self.ids[0]])
        before=copy.deepcopy(self.state)
        for ids in (None, [], [self.ids[0],self.ids[1]], [self.ids[1],'missing'], [self.ids[1],self.ids[1]]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.mutate('activate', segment_ids=ids)
            self.assertEqual(self.state,before)

    def test_archived_segment_cannot_be_activated(self):
        self.mutate('archive_segment', segment_id=self.ids[0])
        with self.assertRaises(ValueError):self.mutate('activate', segment_ids=[self.ids[0]])

    def test_invalid_geometries_and_metadata_rejected(self):
        variants=[{'points':[]}, {'points':[[-32.17,-64.11]]}, {'points':[[math.nan,-64.1],[-32.2,-64.2]]},
                  {'points':[[True,-64.1],[-32.2,-64.2]]}, {'points':[[0,0],[1,1]]},
                  {'points':[[-32.17,-64.11],[-32.17,-64.11]]}, {'direction':'UNKNOWN'},
                  {'street':''}, {'wazeSegmentId':'not-an-id'}, {'points':draft()['points']*40}]
        for invalid in variants:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.mutate('save_segment', segment={**draft(),**invalid})

    def test_duplicate_geometry_and_waze_id_rejected(self):
        self.mutate('save_segment', segment=draft())
        with self.assertRaises(ValueError):self.mutate('save_segment', segment=draft())
        with self.assertRaises(ValueError):self.mutate('save_segment', segment={**draft(),'points':list(reversed(draft()['points']))})
        with self.assertRaises(ValueError):self.mutate('save_segment', segment={**draft(),'wazeSegmentId':self.ids[0]})

    def test_archive_and_restore_collision_checked(self):
        self.mutate('save_segment', segment=draft());sid=self.state['segments'][-1]['id']
        self.mutate('archive_segment', segment_id=sid)
        self.mutate('save_segment', segment=draft())
        with self.assertRaises(ValueError):self.mutate('restore_segment', segment_id=sid)

    def test_stale_revision_cannot_edit_or_close(self):
        self.mutate('save_segment', segment=draft())
        for action,args in [('save_segment',{'segment':draft()}),('activate',{'segment_ids':self.ids})]:
            with self.assertRaises(ValueError):change(self.state,action,0,'test',now=1000,**args)

    def test_both_directions_and_expiry(self):
        self.mutate('save_segment', segment={**draft(),'direction':'BOTH_DIRECTIONS'})
        self.mutate('activate', segment_ids=[self.state['segments'][-1]['id']])
        self.assertEqual(feed(self.state, 1000)['incidents'][0]['direction'],'BOTH_DIRECTIONS')
        self.assertEqual(active_events(self.state,4600),[])
        self.assertEqual(feed(self.state,4600+48*3600),{'incidents':[]})
        self.assertEqual(len(self.state['events']),1)  # Audit survives feed expiry.


class RegistryAPITests(unittest.TestCase):
    def setUp(self):
        self.helper=test_app.ServiceTests('test_public_routes_and_prefix');self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.headers,self.state=self.helper.login()

    def send(self, body, headers=None):
        return self.helper.request('/waze/api/state','POST',body,self.headers if headers is None else headers)

    def test_editor_auth_csrf_and_private_registry(self):
        body={'action':'save_segment','revision':0,'segment':draft()}
        self.assertEqual(self.send(body,{})['status'],401)
        self.assertEqual(self.send(body,{'HTTP_COOKIE':self.headers['HTTP_COOKIE']})['status'],403)
        self.assertEqual(self.send(body)['status'],200)
        cfg={k:v for k,v in self.helper.config.items() if k!='WAZE_INIT_DB'}
        restarted=App(cfg)
        read=self.helper.request('/waze/api/state',headers=self.headers,app=restarted)['json']
        self.assertEqual(len(read['segments']),5)
        self.assertEqual(read['events'],[])
        self.assertEqual(len(self.helper.request('/waze/sector.geojson')['json']['features']),5)

    def test_legacy_tab_without_selection_is_rejected(self):
        self.assertEqual(self.send({'action':'activate','revision':0})['status'],409)
        self.assertEqual(self.helper.request('/waze/feed.json')['json'],{'incidents':[]})

    def test_concurrent_registry_edits_do_not_overwrite(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.send({'action':'save_segment','revision':0,'segment':draft()})['status'],range(2)))
        self.assertEqual(sorted(results),[200,409])

    def test_legacy_database_migrates_transactionally(self):
        old={'revision':2,'events':[],'history':[]}
        with self.helper.app.db() as db:db.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(old),))
        restarted=App(self.helper.config)
        read=self.helper.request('/waze/api/state',headers=self.headers,app=restarted)['json']
        self.assertEqual(read['schema'],3);self.assertEqual(read['revision'],4)
        self.assertEqual(len(read['segments']),4)
        self.assertEqual(read['events'],[])
        # Repeated startup must not advance revision again.
        again=App(self.helper.config)
        self.assertEqual(self.helper.request('/waze/api/state',headers=self.headers,app=again)['json']['revision'],4)
