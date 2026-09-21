"""Quota failover, bounded requests and safe game behavior after all models fail."""
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock,patch

from test_regressions import DatabaseFixture
import config
import db
import i18n
import event_ai as ai
import event_adventure as flow
import event_drafts
from cogs import events


class ModelFixture:
    def setUp(self):
        super().setUp()
        ai._unavailable_until.clear();self.addCleanup(ai._unavailable_until.clear)
        for name,value in [('GEMINI_MODEL','primary'),('GEMINI_FALLBACK_MODELS','backup,final')]:
            setting=patch.object(config,name,value);setting.start();self.addCleanup(setting.stop)


class FailoverTests(ModelFixture,unittest.IsolatedAsyncioTestCase):
    async def test_quota_switches_model_skips_until_retry_after_then_recovers(self):
        clock=[100.]
        request=AsyncMock(side_effect=[ai.EventAIError(429,120),'backup response','backup again','primary recovered'])
        with patch.object(ai.time,'monotonic',side_effect=lambda:clock[0]),patch.object(ai,'_request',request):
            self.assertEqual(await ai.generate_text('Same prompt'),'backup response')
            clock[0]=161
            self.assertEqual(await ai.generate_text('Next scene'),'backup again')
            clock[0]=221
            self.assertEqual(await ai.generate_text('Later scene'),'primary recovered')
        self.assertEqual([c.args[0] for c in request.call_args_list],['primary','backup','backup','primary'])
        self.assertEqual(request.call_args_list[0].args[1],request.call_args_list[1].args[1])

    async def test_primary_success_does_not_call_backups_and_names_are_deduplicated(self):
        with patch.object(config,'GEMINI_MODEL',' models/primary '),patch.object(config,'GEMINI_FALLBACK_MODELS',' primary, ,models/backup,backup,final '):
            self.assertEqual(ai.models(),['primary','backup','final'])
        request=AsyncMock(return_value='OK')
        with patch.object(ai,'_request',request):self.assertEqual(await ai.generate_text('Prompt'),'OK')
        self.assertEqual(request.await_count,1)

    async def test_unavailable_and_timeout_continue_but_credentials_and_rejections_do_not(self):
        for error in (ai.EventAIError(503),ai.EventAIError(404),TimeoutError()):
            ai._unavailable_until.clear()
            with patch.object(ai,'_request',AsyncMock(side_effect=[error,'Recovered'])):
                self.assertEqual(await ai.generate_text('Prompt'),'Recovered')
        for status in (400,401,403,200):
            ai._unavailable_until.clear()
            request=AsyncMock(side_effect=ai.EventAIError(status))
            with patch.object(ai,'_request',request),self.assertRaises(ai.EventAIError):await ai.generate_text('Prompt')
            self.assertEqual(request.await_count,1)

    async def test_all_exhausted_returns_promptly_and_does_not_repeat_requests(self):
        request=AsyncMock(side_effect=ai.EventAIError(429))
        with patch.object(ai,'_request',request):
            for _ in range(2):
                with self.assertRaises(ai.EventAIError):await ai.generate_text('Prompt')
        self.assertEqual([c.args[0] for c in request.call_args_list],['primary','backup','final'])

    async def test_timeout_is_cancelled_before_next_request_and_total_time_is_bounded(self):
        cancelled=[]
        async def request(model,prompt,timeout):
            if model=='backup':return 'Backup answer'
            try:await asyncio.Future()
            finally:cancelled.append(model)
        with patch.object(ai,'REQUEST_TIMEOUT',.01),patch.object(ai,'_request',side_effect=request):
            self.assertEqual(await ai.generate_text('Prompt'),'Backup answer')
        self.assertEqual(cancelled,['primary'])
        ai._unavailable_until.clear()
        async def hung(*args):await asyncio.Future()
        with patch.object(ai,'TOTAL_TIMEOUT',.01),patch.object(ai,'_request',AsyncMock(side_effect=hung)) as call:
            with self.assertRaises(ai.EventAIError):await ai.generate_text('Prompt')
            self.assertEqual(call.await_count,1)

    async def test_caller_cancellation_never_requests_another_model(self):
        request=AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(ai,'_request',request),self.assertRaises(asyncio.CancelledError):await ai.generate_text('Prompt')
        self.assertEqual(request.await_count,1)

    async def test_rest_request_parses_quota_then_response_and_closes_sessions(self):
        quota=NS(status=429,headers={'Retry-After':'90'},json=AsyncMock(return_value={
            'error':{'status':'RESOURCE_EXHAUSTED','message':'private prompt',
                     'details':[{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'120s'}]}}))
        response=NS(status=200,headers={},json=AsyncMock(return_value={
            'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'hidden','thought':True},{'text':'Answer'}]}}]}))
        contexts=[]
        for reply in (quota,response):
            context=AsyncMock();context.__aenter__.return_value=reply;contexts.append(context)
        session=Mock(post=Mock(side_effect=contexts))
        session_context=AsyncMock();session_context.__aenter__.return_value=session
        with patch.object(ai.aiohttp,'ClientSession',return_value=session_context),self.assertLogs('event_ai',level='WARNING') as logged:
            self.assertEqual(await ai.generate_text('private prompt'),'Answer')
        self.assertEqual(session_context.__aexit__.await_count,2)
        self.assertTrue(all(c.__aexit__.await_count==1 for c in contexts))
        calls=session.post.call_args_list
        self.assertIn('/models/primary:generateContent',calls[0].args[0])
        self.assertIn('/models/backup:generateContent',calls[1].args[0])
        self.assertEqual(calls[0].kwargs['json'],calls[1].kwargs['json'])
        self.assertEqual(calls[0].kwargs['headers']['x-goog-api-key'],config.GEMINI_API_KEY)
        self.assertNotIn('private prompt',' '.join(logged.output))
        self.assertNotIn(config.GEMINI_API_KEY,' '.join(logged.output))


class EventFallbackTests(ModelFixture,DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def nation(self):return self.balances()[1]

    async def test_draft_batch_and_owner_language_use_backup_without_placeholder(self):
        i18n.set_user_language(2,'pl')
        request=AsyncMock(side_effect=[ai.EventAIError(429),'Opowieść.\nEFFECTS: {"stability":1}',
                                     'Story A.\nEFFECTS: {}','Opowieść B.\nEFFECTS: {}'])
        with patch.object(ai,'_request',request):
            text,effects=await events._generate_event(self.nation(),strict=True)
            self.assertEqual(text,'Opowieść.');self.assertEqual(json.loads(effects),{'stability':1})
            self.assertIn('special_note in Polish',request.call_args_list[1].args[1])
            result=await event_drafts.generate_all(events._generate_event)
        self.assertTrue(all(r['status']=='created' for r in result))
        self.assertEqual([c.args[0] for c in request.call_args_list],['primary','backup','backup','backup'])

    async def test_scene_custom_action_and_consequences_share_backup_and_keep_bounds(self):
        state=dict(lang='pl',opening='Opowieść',history=[],base_effects={'treasury':30},text='Scena')
        request=AsyncMock(side_effect=[ai.EventAIError(429),json.dumps({'text':'Scena zapasowa','choices':['A','B','C']}),
                                     '```json\n{"choice":2}\n```','{"stability":0,"treasury":-1,"resources":{},"reason":"Strata"}',
                                     '{"stability":0,"treasury":999,"resources":{},"reason":"Za dużo"}'])
        with patch.object(ai,'_request',request):
            self.assertEqual(await flow.scene(state),('Scena zapasowa',['A','B','C']))
            self.assertEqual(await flow.classify_custom(state,'Atak'),(2,False))
            impact,reason,fallback=await flow.assess_consequence(state,'Atak',2)
            self.assertEqual(impact['treasury'],-1);self.assertFalse(fallback)
            impact,_,fallback=await flow.assess_consequence(state,'Atak',2)
            self.assertTrue(fallback);self.assertLessEqual(abs(impact['treasury']),1.5)
        self.assertEqual([c.args[0] for c in request.call_args_list],['primary','backup','backup','backup','backup'])

    async def test_all_models_failed_preserves_playable_event_and_batch_does_not_save_placeholder(self):
        before=self.balances()
        state=dict(lang='pl',opening='Opowieść',history=[],base_effects={'treasury':30},text='Scena')
        request=AsyncMock(side_effect=ai.EventAIError(429))
        with patch.object(ai,'_request',request):
            text,choices=await flow.scene(state)
            self.assertEqual(len(choices),3)
            self.assertEqual(await flow.classify_custom(state,'Własna decyzja'),(1,True))
            impact,_,fallback=await flow.assess_consequence(state,'A',0)
            self.assertTrue(fallback);self.assertEqual(impact['treasury'],.5)
            with self.assertLogs('root',level='ERROR'):
                results=await event_drafts.generate_all(events._generate_event)
        self.assertTrue(all(r['status']=='failed' for r in results))
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM events');self.assertEqual(c.fetchone()['n'],0)
        self.assertEqual(self.balances(),before)
        self.assertEqual(request.await_count,3)
