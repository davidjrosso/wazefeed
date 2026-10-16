import copy
import unittest
import uuid
from unittest.mock import patch
from core import empty_state, feed, active_events
from operations import upgrade, operate
from direct import perform
from catalog import build_catalog, load_catalog
from test_app import ServiceTests

NOW = 1791465600
ROAD = {'id':'osm-1-2-3','osmWayId':1,'street':'Prueba','from':'Primera','to':'Segunda',
        'points':[[-32.174,-64.116],[-32.173,-64.115]],'direction':'BOTH_DIRECTIONS',
        'oneway':False,'length':145,'source':'OpenStreetMap','warnings':[],'wazeSegmentId':'','archived':False}
CAT = {'version':'fixture','segments':[ROAD]}
def body(state, **kwargs):
    return {'action':'publish_closure','revision':state['revision'],'requestId':str(uuid.uuid4()),
            'catalogVersion':'fixture','selection':[{'id':ROAD['id'],'direction':'BOTH'}],
            'reason':'Obras','name':'Prueba','start':'NOW','endMode':'duration','durationMinutes':60,**kwargs}

class DirectTests(unittest.TestCase):
    def setUp(self): self.state=upgrade(empty_state())
    def publish(self, state=None, **kwargs):
        state=state or self.state
        return perform(state,body(state,**kwargs),'operador','operator',CAT,NOW)
    def test_publish_feed_and_immutable_snapshot(self):
        result=self.publish()
        self.assertEqual(self.state['events'],[])
        item=feed(result,NOW)['incidents'][0]
        self.assertEqual(item['street'],'Prueba')
        self.assertEqual(item['direction'],'BOTH_DIRECTIONS')
        self.assertEqual(item['polyline'],'-32.1740000 -64.1160000 -32.1730000 -64.1150000')
        self.assertEqual(result['events'][0]['end'],NOW+3600)
        self.assertEqual(result['segments'],self.state['segments'])
    def test_idempotent_retry_after_other_operator(self):
        request=body(self.state)
        result=perform(self.state,request,'operador','operator',CAT,NOW)
        result=self.publish(result)
        again=perform(result,request,'operador','operator',CAT,NOW+3)
        self.assertIs(again,result)
        with self.assertRaises(ValueError): perform(result,{**request,'reason':'Escuelas'},'operador','operator',CAT,NOW)
    def test_receipt_keeps_run_id_after_expiry_and_retry(self):
        request=body(self.state)
        result=perform(self.state,request,'operador','operator',CAT,NOW)
        receipt=result['requests'][0]
        self.assertEqual(receipt['runId'],result['runs'][0]['id'])
        self.assertEqual(receipt['action'],'publish_closure')
        again=perform(result,request,'operador','operator',CAT,NOW+86400)
        self.assertIs(again,result)
        self.assertEqual(len(again['events']),1)
        self.assertEqual(active_events(again,NOW+86400),[])
    def test_revision_conflict(self):
        with self.assertRaises(ValueError): self.publish(revision=999)
    def test_reverse_direction(self):
        result=self.publish(selection=[{'id':ROAD['id'],'direction':'BA'}])
        self.assertEqual(result['events'][0]['snapshot']['points'],list(reversed(ROAD['points'])))
        self.assertEqual(feed(result,NOW)['incidents'][0]['direction'],'ONE_DIRECTION')
    def test_cannot_forge_geometry_or_city(self):
        result=self.publish(selection=[{'id':ROAD['id'],'direction':'BOTH','points':[[1,2],[3,4]]}])
        self.assertEqual(result['events'][0]['snapshot']['points'],ROAD['points'])
        cat=copy.deepcopy(CAT);cat['segments'][0]['points'][0]=[40,20]
        with self.assertRaises(ValueError): perform(self.state,body(self.state),'a','operator',cat,NOW)
    def test_invalid_selection_and_schedule(self):
        for fields in [dict(selection=[]),dict(selection=[{}]),dict(selection=[{'id':'missing','direction':'BOTH'}]),dict(catalogVersion='old'),dict(durationMinutes=True),dict(durationMinutes=10081),dict(start=NOW-1),dict(start=float('nan')),dict(endMode='at',end=NOW-10),dict(reason='fake')]:
            with self.subTest(fields=fields),self.assertRaises(ValueError): self.publish(**fields)
        for edit in [{'oneway':True},{'warnings':['Sin nombre']}]:
            cat=copy.deepcopy(CAT);cat['segments'][0].update(edit)
            with self.assertRaises(ValueError): perform(self.state,body(self.state),'a','operator',cat,NOW)
    def test_overlap_partial_reopen_extend_does_not_resurrect(self):
        s=self.publish(); first=s['runs'][0]['id'];s=self.publish(s); second=s['runs'][1]['id']
        s=perform(s,body(s,action='reopen_roads',runId=first,segmentIds=[ROAD['id']]),'operador','operator',CAT,NOW+30)
        self.assertEqual(len(active_events(s,NOW+31)),1)
        self.assertEqual(active_events(s,NOW+31)[0]['runId'],second)
        self.assertEqual(len(feed(s,NOW+31)['incidents']),2)
        s=operate(s,{'action':'finish_run','revision':s['revision'],'runId':second},'operador','operator',NOW+50)
        self.assertEqual(active_events(s,NOW+51),[])
    def test_partial_reopen_only_selected_and_extend(self):
        cat=copy.deepcopy(CAT);cat['segments'].append({**copy.deepcopy(ROAD),'id':'second'})
        s=perform(self.state,body(self.state,selection=[{'id':i,'direction':'BOTH'} for i in [ROAD['id'],'second']]),'op','operator',cat,NOW)
        rid=s['runs'][0]['id']
        s=perform(s,body(s,action='reopen_roads',runId=rid,segmentIds=[ROAD['id']]),'op','operator',cat,NOW+5)
        s=operate(s,{'action':'extend_run','revision':s['revision'],'runId':rid,'minutes':60},'op','operator',NOW+10)
        self.assertEqual(s['events'][0]['end'],NOW+5)
        self.assertEqual(s['events'][1]['end'],NOW+7200)
    def test_future_and_expiry(self):
        s=self.publish(start=NOW+300,endMode='at',end=NOW+900)
        self.assertEqual(active_events(s,NOW),[])
        self.assertEqual(len(feed(s,NOW)['incidents']),1)
        self.assertEqual(len(active_events(s,NOW+400)),1)
        self.assertEqual(active_events(s,NOW+901),[])
    def test_issue_permissions(self):
        s=perform(self.state,body(self.state,action='report_issue',segmentId=ROAD['id'],note='Sentido incorrecto'),'op','operator',CAT,NOW)
        self.assertEqual(feed(s,NOW),{'incidents':[]})
        b=body(s,action='resolve_issue',issueId=s['issues'][0]['id'])
        with self.assertRaises(PermissionError): perform(s,b,'op','operator',CAT,NOW)
        s=perform(s,b,'admin','admin',CAT,NOW)
        self.assertEqual(s['issues'][0]['status'],'resolved')
    def test_real_catalog_integrity(self):
        cat=load_catalog();self.assertGreater(len(cat['segments']),1000)
        self.assertEqual(len({s['id'] for s in cat['segments']}),len(cat['segments']))
        self.assertTrue(all(len(s['points'])>=2 for s in cat['segments']))
    def test_junction_split_reverse_and_bridges(self):
        raw={'elements':[{'type':'node','id':i,'lat':-32.17+i*.001,'lon':-64.1} for i in range(1,5)]+[
            {'type':'way','id':10,'nodes':[1,2,3],'tags':{'highway':'residential','name':'A','oneway':'-1'}},
            {'type':'way','id':11,'nodes':[2,4],'tags':{'highway':'residential','name':'B'}}]}
        c=build_catalog(raw)
        self.assertEqual(len(c['segments']),3)
        self.assertTrue(c['segments'][0]['oneway'])
        self.assertGreater(c['segments'][0]['points'][0][0],c['segments'][0]['points'][-1][0])

class DirectAPITests(ServiceTests):
    def test_calendar_preview_auth_and_feed_materialization_without_browser(self):
        from test_agenda import NOW as at
        self.app.catalog=CAT
        with patch('time.time',return_value=at):
            headers,state=self.login()
            state=self.request('/waze/api/state','POST',body(state,action='save_map_template'),headers)['json']
            tid=state['templates'][0]['id']
            path='/waze/api/agenda/'+tid
            self.assertEqual(self.request(path)['status'],401)
            self.assertEqual(self.request('/waze/api/agenda/missing',headers=headers)['status'],404)
            schedule={'from':'2026-10-08','until':'2026-10-31','time':'07:45','endTime':'08:15','weekdays':[0,1,2,3,4],'exceptions':[]}
            data=body(state,action='save_map_schedule',templateId=tid,templateVersion=1,schedule=schedule)
            self.assertEqual(self.request('/waze/api/state','POST',data,{'HTTP_COOKIE':headers['HTTP_COOKIE']})['status'],403)
            with patch.object(self.app,'session',return_value={'username':'op','role':'operator','csrf':state['csrf']}):
                self.assertEqual(self.request('/waze/api/state','POST',data,headers)['status'],403)
            state=self.request('/waze/api/state','POST',data,headers)['json']
            self.assertEqual(self.request('/waze/feed.json')['json'],{'incidents':[]})
            p=self.request(path,headers=headers)
            self.assertEqual(p['status'],200)
            self.assertTrue(p['json']['occurrences'])
            enabled=self.request('/waze/api/state','POST',body(state,action='enable_map_schedule',templateId=tid,templateVersion=1),headers)
            self.assertEqual(enabled['status'],200)
            initial={r['id'] for r in enabled['json']['runs']}
        with patch('time.time',return_value=at+5*86400):
            self.assertEqual(self.request('/waze/feed.json')['status'],200)
            with self.app.db() as db:
                result=self.app.state(db)
                self.assertTrue(any(r['id'] not in initial for r in result['runs']))
                rev=result['revision']
            self.assertEqual(self.request('/waze/feed.json')['status'],200)
            with self.app.db() as db:self.assertEqual(self.app.state(db)['revision'],rev)

    def test_template_api_auth_csrf_save_publish_and_archive(self):
        self.app.catalog=CAT
        headers,state=self.login()
        payload=body(state,action='save_map_template')
        self.assertEqual(self.request('/waze/api/state','POST',payload)['status'],401)
        self.assertEqual(self.request('/waze/api/state','POST',payload,{'HTTP_COOKIE':headers['HTTP_COOKIE']})['status'],403)
        with patch.object(self.app,'session',return_value={'username':'other','role':'operator','csrf':state['csrf']}):
            self.assertEqual(self.request('/waze/api/state','POST',payload,headers)['status'],403)
        saved=self.request('/waze/api/state','POST',payload,headers)
        self.assertEqual(saved['status'],200)
        self.assertEqual(self.request('/waze/feed.json')['json'],{'incidents':[]})
        t=saved['json']['templates'][0]
        with patch.object(self.app,'session',return_value={'username':'other','role':'operator','csrf':state['csrf']}):
            result=self.request('/waze/api/state','POST',body(saved['json'],templateId=t['id'],templateVersion=1),headers)
        self.assertEqual(result['status'],200)
        self.assertEqual(result['json']['runs'][0]['templateId'],t['id'])
        archived=self.request('/waze/api/state','POST',body(result['json'],action='archive_map_template',templateId=t['id'],templateVersion=1),headers)
        self.assertEqual(archived['status'],200)
        self.assertTrue(archived['json']['templates'][0]['archived'])
        self.assertEqual(len(self.request('/waze/feed.json')['json']['incidents']),1)

    def test_receipt_requires_auth_and_is_private_to_actor(self):
        self.app.catalog=CAT
        headers,state=self.login()
        payload=body(state)
        path='/waze/api/requests/'+payload['requestId']
        self.assertEqual(self.request(path)['status'],401)
        self.assertEqual(self.request(path,headers=headers)['json'],{'completed':False,'runId':None})
        result=self.request('/waze/api/state','POST',payload,headers)
        receipt=self.request(path,headers=headers)
        self.assertEqual(receipt['json'],{'completed':True,'runId':result['json']['runs'][0]['id']})
        self.assertEqual(receipt['headers']['Cache-Control'],'no-store')
        self.assertEqual(self.request('/waze/api/requests/not-a-uuid',headers=headers)['status'],400)
        self.assertEqual(self.request(path,'POST',{},headers)['status'],404)
        with patch.object(self.app,'session',return_value={'username':'another-operator','role':'operator'}):
            self.assertEqual(self.request(path,headers=headers)['json'],{'completed':False,'runId':None})
    def test_old_receipt_without_run_id_remains_readable(self):
        import json
        headers,state=self.login()
        key=str(uuid.uuid4())
        with self.app.db() as db:
            saved=self.app.state(db)
            saved['requests']=[{'id':key,'actor':'operador','digest':'old','at':NOW}]
            db.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(saved),))
        self.assertEqual(self.request('/waze/api/requests/'+key,headers=headers)['json'],{'completed':True,'runId':None})
    def test_direct_public_feed_and_retry(self):
        self.app.catalog=CAT
        headers,state=self.login()
        request=body(state)
        first=self.request('/waze/api/state','POST',request,headers)
        self.assertEqual(first['status'],200)
        self.assertNotIn('requests',first['json'])
        self.assertEqual(len(self.request('/waze/feed.json')['json']['incidents']),1)
        again=self.request('/waze/api/state','POST',request,headers)
        self.assertEqual(again['status'],200)
        self.assertEqual(first['json']['revision'],again['json']['revision'])
        self.assertEqual(self.request('/waze/api/catalog')['status'],401)
        self.assertEqual(self.request('/waze/api/catalog',headers=headers)['json']['version'],'fixture')
        self.assertEqual(self.request('/waze/roads.json')['status'],200)
