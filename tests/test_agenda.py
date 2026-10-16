import copy
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agenda import materialize_map, preview, validate_schedule, window
from catalog import CITY
from core import empty_state, feed, active_events
from direct import perform
from operations import upgrade, operate, materialize
from test_direct import body, CAT, ROAD

NOW = datetime(2026,10,8,7,30,tzinfo=ZoneInfo(CITY['timezone'])).timestamp()


class AgendaTests(unittest.TestCase):
    def setUp(self):
        s=upgrade(empty_state())
        self.state=perform(s,body(s,action='save_map_template'),'admin','admin',CAT,NOW)
        self.tid=self.state['templates'][0]['id']

    def config(self, **values):
        return {'from':'2026-10-08','until':'2026-10-31','time':'07:45','endTime':'08:15',
                'weekdays':[0,1,2,3,4],'exceptions':[],**values}

    def do(self, action, role='admin', now=NOW, **values):
        payload=body(self.state,**{'action':action,'templateId':self.tid,'templateVersion':self.state['templates'][0]['version'],**values})
        self.state=perform(self.state,payload,role,role,CAT,now)
        return self.state

    def enabled(self):
        self.do('save_map_schedule',schedule=self.config())
        return self.do('enable_map_schedule')

    def test_save_and_preview_never_publish(self):
        self.do('save_map_schedule',schedule=self.config())
        original=copy.deepcopy(self.state)
        p=preview(self.state['templates'][0],CAT,NOW)
        self.assertTrue(p['occurrences'])
        self.assertFalse(p['enabled'])
        self.assertEqual(self.state,original)
        self.assertEqual(feed(self.state,NOW),{'incidents':[]})
        self.assertIs(materialize_map(self.state,CAT,NOW),self.state)

    def test_enable_rolling_window_timezone_and_idempotence(self):
        self.enabled()
        self.assertTrue(self.state['runs'])
        for r in self.state['runs']:
            start=datetime.fromtimestamp(r['start'],ZoneInfo(CITY['timezone']))
            self.assertEqual((start.hour,start.minute),(7,45))
            self.assertLess(start.weekday(),5)
            self.assertLessEqual(start.day,15)
            self.assertEqual(r['end']-r['start'],1800)
            self.assertEqual(r['source'],'map-schedule')
        self.assertIs(materialize_map(self.state,CAT,NOW+10),self.state)
        self.assertIs(materialize(self.state,NOW+10),self.state)  # Legacy scheduler cannot duplicate.

    def test_pause_preserves_active_and_manual_and_resume_exclusions(self):
        self.enabled()
        self.do('publish_closure',start=NOW+86400)  # Manual run with same template.
        manual=copy.deepcopy(self.state['runs'][-1])
        self.do('exclude_map_date',role='operator',date='2026-10-09')
        at=NOW+1800  # 08:00, first calendar slot in progress.
        self.do('pause_map_schedule',now=at)
        self.assertEqual(len(active_events(self.state,at)),1)
        self.assertEqual(self.state['runs'][-1],manual)
        self.do('enable_map_schedule',now=at+1)
        pending=[r for r in self.state['runs'] if r['closedAt'] is None and r.get('source')=='map-schedule']
        self.assertEqual(sum(r['scheduleDate']=='2026-10-08' for r in pending),1)
        self.assertNotIn('2026-10-09',[r['scheduleDate'] for r in pending])

    def test_single_date_outside_horizon_is_not_generated_later(self):
        self.enabled()
        self.do('exclude_map_date',date='2026-10-23',role='operator')
        self.assertNotIn('2026-10-23',[r.get('scheduleDate') for r in self.state['runs']])
        self.state=materialize_map(self.state,CAT,NOW+15*86400)
        self.assertNotIn('2026-10-23',[r.get('scheduleDate') for r in self.state['runs']])

    def test_exclusion_updates_existing_incident_not_deletes_it(self):
        self.enabled()
        r=next(r for r in self.state['runs'] if r['scheduleDate']=='2026-10-09')
        event=next(e for e in self.state['events'] if e.get('runId')==r['id'])
        eid=event['feedId']
        self.do('exclude_map_date',date='2026-10-09')
        event=next(e for e in self.state['events'] if e['feedId']==eid)
        self.assertEqual(event['closedAt'],NOW)
        self.assertEqual(event['end'],NOW)
        self.assertLess(event['start'],event['end'])
        self.assertIn(eid,[i['id'] for i in feed(self.state,NOW)['incidents']])

    def test_edit_or_archive_enabled_template_is_blocked(self):
        self.enabled()
        for action in ('save_map_template','archive_map_template','save_map_schedule'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError,'Pausá'):
                self.do(action,schedule=self.config())
        self.do('pause_map_schedule')
        schedule=copy.deepcopy(self.state['templates'][0]['schedule'])
        self.do('save_map_template',durationMinutes=120)
        self.assertEqual(self.state['templates'][0]['schedule'],schedule)
        self.assertFalse(self.state['templates'][0]['enabled'])

    def test_catalog_change_stops_new_dates_and_blocks_enable(self):
        self.enabled()
        new={**CAT,'version':'new'}
        self.assertIs(materialize_map(self.state,new,NOW+86400*5),self.state)
        self.assertIsNotNone(preview(self.state['templates'][0],new,NOW)['problem'])
        self.do('pause_map_schedule')
        with self.assertRaises(ValueError):
            perform(self.state,body(self.state,action='enable_map_schedule',templateId=self.tid,templateVersion=1),'admin','admin',new,NOW)

    def test_no_replay_of_expired_slots_after_outage(self):
        self.enabled()
        count=len(self.state['runs']);later=NOW+20*86400
        result=materialize_map(self.state,CAT,later)
        self.assertTrue(result['runs'][count:])
        self.assertTrue(all(r['end']>later for r in result['runs'][count:]))

    def test_operator_permissions_and_explicit_start_guard(self):
        for action in ('save_map_schedule','enable_map_schedule','pause_map_schedule'):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                self.do(action,role='operator',schedule=self.config())
        self.enabled()
        with self.assertRaises(ValueError):self.do('exclude_map_date',role='operator',date='2026-10-08',now=NOW+1800)
        with self.assertRaises(ValueError):self.do('exclude_map_date',date='2026-10-10')  # Saturday.

    def test_invalid_schedules_are_atomic(self):
        original=copy.deepcopy(self.state)
        for change in ({'weekdays':[]},{'weekdays':[True]},{'weekdays':[7]}, {'from':'bad'}, {'until':'2028-01-01'}, {'time':'25:00'}, {'endTime':'7:00'}, {'time':'08:15'}, {'exceptions':['2026-11-01']}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.do('save_map_schedule',schedule=self.config(**change))
            self.assertEqual(self.state,original)

    def test_night_slot_ends_on_next_day(self):
        s=validate_schedule(self.config(time='23:30',endTime='00:15'),NOW)
        from datetime import date
        start,end=window(s,date(2026,10,8))
        self.assertEqual(end-start,2700)
        self.assertEqual(datetime.fromtimestamp(end,ZoneInfo(CITY['timezone'])).day,9)

    def test_dst_ambiguous_or_missing_times_require_exceptions(self):
        with patch.dict(CITY,timezone='Europe/Berlin'):
            with self.assertRaisesRegex(ValueError,'ambigua'):
                validate_schedule(self.config(weekdays=[6],time='02:30',endTime='03:30'),NOW)
            clean=validate_schedule(self.config(weekdays=[6],time='02:30',endTime='03:30',exceptions=['2026-10-25']),NOW)
            self.assertEqual(clean['timezone'],'Europe/Berlin')

    def test_finish_date_survives_pause_edit_resume(self):
        self.enabled()
        rid=self.state['runs'][0]['id']
        self.state=operate(self.state,{'action':'finish_run','revision':self.state['revision'],'runId':rid},'op','operator',NOW)
        self.assertIn('2026-10-08',self.state['templates'][0]['schedule']['exceptions'])
        self.do('pause_map_schedule')
        self.do('save_map_template',name='Escuelas revisada')
        self.do('enable_map_schedule')
        self.assertFalse(any(r['closedAt'] is None and r.get('scheduleDate')=='2026-10-08' for r in self.state['runs']))

    def test_retry_enable_after_lost_response_does_not_duplicate(self):
        self.do('save_map_schedule',schedule=self.config())
        b=body(self.state,action='enable_map_schedule',templateId=self.tid,templateVersion=1)
        self.state=perform(self.state,b,'admin','admin',CAT,NOW)
        self.assertIs(perform(self.state,b,'admin','admin',CAT,NOW+1),self.state)

    def test_schedule_change_after_pause_preserves_old_incident_ids(self):
        self.enabled()
        old_ids={e['feedId'] for e in self.state['events']}
        self.do('pause_map_schedule')
        self.do('save_map_schedule',schedule=self.config(time='09:00',endTime='09:15'))
        self.do('enable_map_schedule')
        self.assertTrue(old_ids <= {e['feedId'] for e in self.state['events']})
        self.assertTrue(all(e['closedAt']==NOW for e in self.state['events'] if e['feedId'] in old_ids))
