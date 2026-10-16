import copy
import unittest

from catalog import CITY
from core import empty_state, feed, active_events
from direct import perform
from operations import upgrade, operate, materialize
from test_direct import CAT, ROAD, NOW, body


class MapTemplateTests(unittest.TestCase):
    def setUp(self):
        self.state = upgrade(empty_state())

    def save(self, state=None, **fields):
        state = self.state if state is None else state
        return perform(state, body(state, action='save_map_template', **fields), 'admin', 'admin', CAT, NOW)

    def publish(self, state, **fields):
        t = state['templates'][0]
        return perform(state, body(state, **{'templateId':t['id'], 'templateVersion':t['version'], **fields}), 'op', 'operator', CAT, NOW)

    def test_save_never_publishes_and_schedules_stay_disabled(self):
        result = self.save(schedule={'enabled': True})
        self.assertEqual(feed(result, NOW), {'incidents': []})
        self.assertEqual(result['runs'], [])
        self.assertEqual(result['segments'], self.state['segments'])
        t = result['templates'][0]
        self.assertEqual(t['source'], 'map')
        self.assertEqual(t['cityId'], CITY['id'])
        self.assertFalse(t['enabled'])
        self.assertIsNone(t['schedule'])
        self.assertEqual(materialize(result, NOW), result)
        self.assertEqual(self.state['templates'], [])

    def test_edit_version_preserves_active_snapshot(self):
        result = self.publish(self.save())
        events, runs = copy.deepcopy(result['events']), copy.deepcopy(result['runs'])
        t = result['templates'][0]
        result = self.save(result, templateId=t['id'], templateVersion=1, name='Otro grupo', selection=[{'id': ROAD['id'], 'direction': 'BA'}])
        self.assertEqual(result['templates'][0]['version'], 2)
        self.assertEqual(result['templates'][0]['segments'][0]['points'], list(reversed(ROAD['points'])))
        self.assertEqual(result['events'], events)
        self.assertEqual(result['runs'], runs)
        self.assertEqual(result['runs'][0]['templateId'], t['id'])
        self.assertEqual(result['runs'][0]['version'], 1)

    def test_admin_only_and_invalid_selections(self):
        with self.assertRaises(PermissionError):
            perform(self.state, body(self.state, action='save_map_template'), 'op', 'operator', CAT, NOW)
        for fields in [dict(selection=[]), dict(catalogVersion='stale'), dict(durationMinutes=0), dict(name=''), dict(reason='invalid'), dict(selection=[{'id':'fake', 'direction':'AB'}])]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.save(**fields)

    def test_stale_version_and_duplicate_names(self):
        result = self.save()
        t = result['templates'][0]
        with self.assertRaises(ValueError): self.save(result, name='PRUEBA')
        with self.assertRaises(ValueError): self.save(result, templateId=t['id'], templateVersion=99)
        with self.assertRaises(ValueError): self.publish(result, templateVersion=99)

    def test_catalog_change_requires_admin_review(self):
        result = self.save()
        changed = {**CAT, 'version': 'new-map'}
        t = result['templates'][0]
        payload = body(result, templateId=t['id'], templateVersion=1, catalogVersion='new-map')
        with self.assertRaises(ValueError): perform(result, payload, 'op', 'operator', changed, NOW)
        result = perform(result, {**payload, 'action':'save_map_template'}, 'admin', 'admin', changed, NOW)
        payload = body(result, templateId=t['id'], templateVersion=2, catalogVersion='new-map')
        result = perform(result, payload, 'op', 'operator', changed, NOW)
        self.assertEqual(len(result['events']), 1)

    def test_selection_and_city_cannot_be_swapped(self):
        result = self.save()
        with self.assertRaises(ValueError): self.publish(result, selection=[{'id':ROAD['id'], 'direction':'BA'}])
        result['templates'][0]['cityId'] = 'other-city'
        with self.assertRaises(ValueError): self.publish(result)

    def test_multiple_activations_and_partial_reopen_are_independent(self):
        result = self.publish(self.save())
        result = self.publish(result, durationMinutes=120)
        self.assertEqual(len(active_events(result, NOW)), 2)
        first = result['runs'][0]['id']
        result = perform(result, body(result, action='reopen_roads', runId=first, segmentIds=[ROAD['id']]), 'op', 'operator', CAT, NOW+5)
        self.assertEqual(len(active_events(result, NOW+6)), 1)
        self.assertEqual(result['templates'][0]['durationMinutes'], 60)

    def test_archive_preserves_future_and_active_closures(self):
        result = self.publish(self.save())
        result = self.publish(result, start=NOW+3600)
        t = result['templates'][0]
        payload = body(result, action='archive_map_template', templateId=t['id'], templateVersion=1)
        with self.assertRaises(PermissionError): perform(result, payload, 'op', 'operator', CAT, NOW)
        before = feed(result, NOW)
        archived = perform(result, payload, 'admin', 'admin', CAT, NOW)
        self.assertTrue(archived['templates'][0]['archived'])
        self.assertEqual(feed(archived, NOW), before)
        self.assertEqual(archived['runs'], result['runs'])
        with self.assertRaises(ValueError): self.publish(archived)
        with self.assertRaises(ValueError): self.save(archived, templateId=t['id'], templateVersion=1)

    def test_legacy_actions_cannot_bypass_map_validation(self):
        result = self.save()
        t = result['templates'][0]
        for action in ('activate_template', 'save_template', 'archive_template', 'schedule_enable', 'schedule_pause'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'consola nueva'):
                operate(result, {'action':action, 'revision':result['revision'], 'templateId':t['id'], 'template':{'id':t['id']}}, 'admin', 'admin', NOW)

    def test_retry_does_not_duplicate_save_or_activation_after_archive(self):
        payload = body(self.state, action='save_map_template')
        result = perform(self.state, payload, 'admin', 'admin', CAT, NOW)
        self.assertIs(perform(result, payload, 'admin', 'admin', CAT, NOW+1), result)
        t = result['templates'][0]
        publish = body(result, templateId=t['id'], templateVersion=1)
        result = perform(result, publish, 'op', 'operator', CAT, NOW)
        result = perform(result, body(result, action='archive_map_template', templateId=t['id'], templateVersion=1), 'admin', 'admin', CAT, NOW+2)
        self.assertIs(perform(result, publish, 'op', 'operator', CAT, NOW+3), result)
        self.assertEqual(len(result['runs']), 1)
