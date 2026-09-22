import asyncio
import json
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from test_event_ai import ModelFixture
import config
import event_ai as ai
import event_adventure


class ProviderTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        for name, value in [('EVENT_AI_PROVIDERS', 'gemini,groq,mistral,openrouter'),
                            ('GROQ_API_KEY', 'groq-secret'), ('MISTRAL_API_KEY', 'mistral-secret'),
                            ('OPENROUTER_API_KEY', 'router-secret')]:
            setting = patch.object(config, name, value)
            setting.start(); self.addCleanup(setting.stop)

    async def test_independent_accounts_are_tried_before_more_gemini_models(self):
        google = AsyncMock(side_effect=ai.EventAIError(429))
        chat = AsyncMock(side_effect=[ai.EventAIError(429), ai.EventAIError(503), 'Recovered'])
        with patch.object(ai, '_request', google), patch.object(ai, '_chat_request', chat):
            self.assertEqual(await ai.generate_text('private prompt'), 'Recovered')
        google.assert_awaited_once()
        self.assertEqual([c.args[0].provider for c in chat.call_args_list], ['groq', 'mistral', 'openrouter'])
        self.assertTrue(all(c.args[1] == 'private prompt' for c in chat.call_args_list))

    async def test_absent_keys_and_paid_router_models_are_never_called(self):
        with patch.object(config, 'GROQ_API_KEY', ''), patch.object(config, 'MISTRAL_API_KEY', ''), \
                patch.object(config, 'OPENROUTER_EVENT_MODEL', 'a-paid-model'), self.assertLogs('event_ai', level='WARNING'):
            targets = ai.targets()
        self.assertEqual([t.provider for t in targets], ['gemini'] * 3)
        with patch.object(config, 'OPENROUTER_EVENT_MODEL', 'vendor/model:free'):
            self.assertIn('vendor/model:free', [t.model for t in ai.targets()])
        with patch.object(config, 'EVENT_AI_PROVIDERS', 'unknown,groq,groq, gemini'):
            self.assertEqual([t.provider for t in ai.targets()], ['groq', 'gemini', 'gemini', 'gemini'])

    async def test_auth_failure_skips_whole_provider_and_retries_with_changed_key(self):
        google = AsyncMock(side_effect=[ai.EventAIError(401), 'Google recovered'])
        chat = AsyncMock(return_value='Other account')
        with patch.object(ai, '_request', google), patch.object(ai, '_chat_request', chat):
            for _ in range(2):
                self.assertEqual(await ai.generate_text('Prompt'), 'Other account')
            google.assert_awaited_once()
            with patch.object(config, 'GEMINI_API_KEY', 'replacement-secret'):
                self.assertEqual(await ai.generate_text('Prompt'), 'Google recovered')

    async def test_empty_configuration_returns_without_a_network_request(self):
        with patch.object(config, 'EVENT_AI_PROVIDERS', ''), patch.object(ai, '_request', AsyncMock()) as google, \
                patch.object(ai, '_chat_request', AsyncMock()) as chat:
            with self.assertRaises(ai.EventAIError):
                await ai.generate_text('Prompt')
            google.assert_not_awaited(); chat.assert_not_awaited()

    async def test_all_exhausted_models_are_not_retried_by_next_event(self):
        google = AsyncMock(side_effect=ai.EventAIError(429, 3600))
        chat = AsyncMock(side_effect=ai.EventAIError(429, 3600))
        with patch.object(ai, '_request', google), patch.object(ai, '_chat_request', chat):
            for _ in range(2):
                with self.assertRaises(ai.EventAIError):
                    await ai.generate_text('Prompt')
        self.assertEqual(google.await_count, 3)
        self.assertEqual(chat.await_count, 3)

    async def test_slow_primary_leaves_time_for_another_provider_and_is_cancelled(self):
        cancelled = []
        async def hung(*args):
            try: await asyncio.Future()
            finally: cancelled.append(True)
        chat = AsyncMock(return_value='Fast backup')
        start = time.monotonic()
        with patch.object(ai, 'TOTAL_TIMEOUT', .12), patch.object(ai, '_request', side_effect=hung), \
                patch.object(ai, '_chat_request', chat):
            self.assertEqual(await ai.generate_text('Prompt'), 'Fast backup')
        self.assertEqual(cancelled, [True])
        self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(chat.call_args.args[0].provider, 'groq')

    async def test_invalid_json_uses_backup_but_filtered_content_does_not(self):
        with patch.object(ai, '_request', AsyncMock(return_value='not JSON')), \
                patch.object(ai, '_chat_request', AsyncMock(return_value='```json\n{"choice":1}\n```')):
            self.assertEqual(await event_adventure._ai_json('JSON please'), {'choice': 1})
        chat = AsyncMock(return_value='Should not be requested')
        with patch.object(ai, '_request', AsyncMock(side_effect=ai.EventAIError(200))), \
                patch.object(ai, '_chat_request', chat), self.assertRaises(ai.EventAIError):
            await ai.generate_text('Prompt')
        chat.assert_not_awaited()

    async def test_truncated_response_can_use_another_provider(self):
        with patch.object(ai, '_request', AsyncMock(side_effect=ai.EventAIOutputError())), \
                patch.object(ai, '_chat_request', AsyncMock(return_value='Complete answer')):
            self.assertEqual(await ai.generate_text('Prompt'), 'Complete answer')

    def session(self, payload, status=200, headers=None):
        response = NS(status=status, headers=headers or {}, json=AsyncMock(return_value=payload))
        context = AsyncMock(); context.__aenter__.return_value = response
        session = Mock(post=Mock(return_value=context))
        session_context = AsyncMock(); session_context.__aenter__.return_value = session
        return session, session_context, context

    async def test_chat_adapters_use_correct_hosts_headers_limits_and_text(self):
        for provider in ('groq', 'mistral', 'openrouter'):
            with self.subTest(provider=provider):
                target = next(t for t in ai.targets() if t.provider == provider)
                content = [{'type': 'text', 'text': 'Polski '}, {'type': 'text', 'text': 'event'}] if provider == 'mistral' else 'Polski event'
                session, ctx, response = self.session({'choices': [{'finish_reason': 'stop',
                    'message': {'content': content, 'reasoning': 'Hidden reasoning'}}]})
                with patch.object(ai.aiohttp, 'ClientSession', return_value=ctx):
                    self.assertEqual(await ai._chat_request(target, 'Prompt', 3), 'Polski event')
                call = session.post.call_args
                self.assertEqual(call.args[0], ai.CHAT_ENDPOINTS[provider])
                self.assertEqual(call.kwargs['headers'], {'Authorization': 'Bearer ' + target.api_key})
                self.assertFalse(call.kwargs['allow_redirects'])
                body = call.kwargs['json']
                self.assertEqual(body['model'], target.model)
                self.assertEqual(body['messages'], [{'role': 'user', 'content': 'Prompt'}])
                self.assertEqual(body.get('max_tokens', body.get('max_completion_tokens')), 2400)
                if provider == 'groq':
                    self.assertFalse(body['include_reasoning'])
                    self.assertEqual(body['reasoning_effort'], 'low')
                ctx.__aexit__.assert_awaited_once(); response.__aexit__.assert_awaited_once()
                self.assertNotIn(target.api_key, repr(target))

    async def test_refusal_truncation_and_empty_content_are_not_game_data(self):
        for message, reason in (({'content': 'Partial'}, 'length'), ({'content': 'Blocked'}, 'content_filter'),
                                ({'content': '', 'reasoning': 'Thinking only'}, 'stop'),
                                ({'content': 'No', 'refusal': 'No'}, 'stop')):
            _, ctx, _ = self.session({'choices': [{'finish_reason': reason, 'message': message}]})
            with patch.object(ai.aiohttp, 'ClientSession', return_value=ctx), self.assertRaises(ai.EventAIError):
                await ai._chat_request(ai.targets()[1], 'Prompt', 3)

    async def test_quota_error_does_not_expose_payload_and_honors_retry_time(self):
        _, ctx, _ = self.session({'error': {'message': 'secret and private narrative'}}, 429, {'retry-after': '180'})
        with patch.object(ai.aiohttp, 'ClientSession', return_value=ctx):
            with self.assertRaises(ai.EventAIError) as caught:
                await ai._chat_request(ai.targets()[1], 'private narrative', 3)
        self.assertEqual((caught.exception.status, caught.exception.retry_after), (429, 180))
        self.assertNotIn('secret', str(caught.exception))
        with patch.object(ai.time, 'time', return_value=0):
            self.assertEqual(ai.retry_delay({'Retry-After': 'Thu, 01 Jan 1970 00:02:00 GMT'}, {}), 120)
            self.assertEqual(ai.retry_delay({'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '3600'}, {}), 3600)
            self.assertEqual(ai.retry_delay({'Retry-After': 'nan'}, {}), 0)
