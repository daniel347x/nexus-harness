"""Offline GPT binding caches and fixed usage route. All native/provider IO is injected."""
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import chatgpt_accounts as ca


class GptUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.registry = {'quota-a': ca.Account('quota-a', self.home / 'a', 'cred_a'),
                         'quota-b': ca.Account('quota-b', self.home / 'b', 'cred_b')}
        self.active, self.now, self.jobs, self.reads = 'cred_a', 1000.0, [], []

    def native(self):
        return {'available': True, 'accounts': [{'id': 'cred_a', 'label': 'Saved A'}, {'id': 'cred_b', 'label': 'Another ☀'}],
                'active': {'known': True, 'credential_id': self.active, 'credential_label': 'Native ' + self.active, 'detail': 'fixture'}}

    def reading(self, label, **extra):
        a = self.registry[label]
        return {'chatgpt': {'ok': True, 'account': ca.account_stamp(a), 'account_key': 'PRIVATE-ID',
                            'cache_tag': a.cache_tag, 'token_expires_at': 123, 'access_token': 'SECRET',
                            'observed_at': self.now - 27, 'age_seconds': 27, 'plan': 'pro',
                            'windows': [{'label': 'weekly', 'percent': 12 if label == 'quota-a' else 85,
                                         'resets_at': self.now + 10000, 'token': 'SECRET'}],
                            'models': {'Astra': {'available': True, 'access_token': 'SECRET'}},
                            'reset_credits': [{'expires_at': self.now + 4000, 'token': 'SECRET'}], **extra}}

    def cache(self, **fields):
        def run(label):
            self.reads.append(label)
            return self.reading(label)
        return server.UsageReadings(
            run=lambda: self.fail('no Claude/default read'), run_account=lambda label: self.fail('no Claude read'),
            account_reader=lambda: {}, label_reader=lambda label: {},
            chatgpt_registry_reader=lambda: self.registry, chatgpt_native_reader=self.native,
            run_chatgpt_account=fields.pop('run_chatgpt_account', run),
            clock=lambda: self.now, start=fields.pop('start', self.jobs.append), **fields)

    def test_named_cache_singleflight_independent_bindings_and_true_provider_age(self):
        cache = self.cache()
        self.assertTrue(cache.chatgpt_snapshot('cred_b')['refreshing'])
        cache.chatgpt_snapshot('cred_b', force=True)
        self.assertEqual(len(self.jobs), 1)
        self.jobs.pop()()
        b = cache.chatgpt_snapshot('cred_b')
        self.assertEqual(b['chatgpt']['windows'][0]['percent'], 85)
        self.assertEqual(b['ageSeconds'], 27)
        self.now += 8
        self.assertEqual(cache.chatgpt_snapshot('cred_b')['ageSeconds'], 35)
        cache.chatgpt_snapshot('cred_a')
        self.jobs.pop()()
        self.assertEqual(cache.chatgpt_snapshot('cred_a')['chatgpt']['windows'][0]['percent'], 12)
        self.assertEqual(self.reads, ['quota-b', 'quota-a'])

    def test_active_and_named_gpt_view_share_the_binding_cache_and_active_projection_is_fresh(self):
        cache = self.cache(start=lambda work: work())
        body = cache.chatgpt_snapshot()
        self.assertTrue(body['chatgpt']['account']['active'])
        self.assertEqual(self.reads, ['quota-a'])
        cache.chatgpt_snapshot('cred_a')
        self.active = 'cred_b'
        self.assertFalse(cache.chatgpt_snapshot('cred_a')['chatgpt']['account']['active'])
        b = cache.chatgpt_snapshot()
        self.assertEqual((b['credentialId'], b['chatgpt']['account']['label']), ('cred_b', 'quota-b'))
        self.assertEqual(self.reads, ['quota-a', 'quota-b'])

    def test_queued_old_binding_is_not_read_after_remap(self):
        cache = self.cache()
        cache.chatgpt_snapshot('cred_b')
        self.registry['quota-b'] = ca.Account('quota-b', self.home / 'new-b', 'cred_b')
        self.jobs.pop()()
        self.assertEqual(self.reads, [])
        fresh = cache.chatgpt_snapshot('cred_b')
        self.assertIsNone(fresh['chatgpt'])
        self.jobs.pop()()
        self.assertTrue(cache.chatgpt_snapshot('cred_b')['chatgpt']['ok'])

    def test_inflight_remap_drops_answer_and_old_good_reading(self):
        def run(label):
            result = self.reading(label)
            self.registry[label] = ca.Account(label, self.home / 'rebound', 'cred_b')
            return result
        cache = self.cache(run_chatgpt_account=run)
        cache.chatgpt_snapshot('cred_b')
        self.jobs.pop()()
        body = cache.chatgpt_snapshot('cred_b')
        self.assertIsNone(body['chatgpt'])
        self.assertTrue(body['refreshing'])

    def test_active_change_during_read_never_publishes_old_quota_as_new_active(self):
        def run(label):
            result = self.reading(label)
            self.active = 'cred_b'
            return result
        cache = self.cache(run_chatgpt_account=run, start=lambda work: work())
        body = cache.chatgpt_snapshot()
        self.assertFalse(body['chatgpt']['ok'])
        self.assertNotIn('windows', body['chatgpt'])
        self.assertIn('changed', body['chatgpt']['error'])
        named = cache.chatgpt_snapshot('cred_a')
        self.assertEqual(named['chatgpt']['windows'][0]['percent'], 12)
        self.assertFalse(named['chatgpt']['account']['active'])

    def test_foreign_marker_home_id_and_private_cache_tag_never_publish(self):
        for wrong in ('id', 'home', 'label', 'tag'):
            with self.subTest(wrong=wrong):
                def run(label):
                    r = self.reading(label)
                    if wrong == 'id': r['chatgpt']['account']['opencode_credential_id'] = 'cred_a'
                    if wrong == 'home': r['chatgpt']['account']['codex_home'] = '/wrong/private'
                    if wrong == 'label': r['chatgpt']['account']['label'] = 'quota-a'
                    if wrong == 'tag': r['chatgpt']['cache_tag'] = 'wrong-tag'
                    return r
                cache = self.cache(run_chatgpt_account=run, start=lambda work: work())
                body = cache.chatgpt_snapshot('cred_b')
                self.assertIsNone(body['chatgpt'])
                self.assertIn('another binding', body['error'])

    def test_projection_drops_all_private_paths_keys_tokens_nested_extras(self):
        cache = self.cache(start=lambda work: work())
        body = cache.chatgpt_snapshot('cred_b')
        wire = json.dumps(body)
        for private in (str(self.home), 'codex_home', 'cache_tag', 'PRIVATE-ID', 'SECRET', 'access_token', 'token_expires_at'):
            self.assertNotIn(private, wire)
        self.assertIn('operator-configured', body['chatgpt']['account']['binding'])

    def test_exception_path_is_not_in_browser_error(self):
        def run(label):
            raise RuntimeError('could not read /home/private/.codex/auth.json')
        body = self.cache(run_chatgpt_account=run, start=lambda work: work()).chatgpt_snapshot('cred_b')
        self.assertIn('[local path]', body['error'])
        self.assertNotIn('/home/private', body['error'])

    def test_missing_mapping_never_borrows_legacy_or_another_login(self):
        self.registry = None
        cache = self.cache(start=lambda work: work(), run_chatgpt=lambda: self.fail('named read cannot borrow legacy'))
        body = cache.chatgpt_snapshot('cred_b')
        self.assertEqual(body['quotaState'], 'unmapped')
        self.assertFalse(body['chatgpt']['ok'])
        self.assertEqual(self.reads, [])
        self.registry = {'quota-a': ca.Account('quota-a', self.home / 'a', 'cred_a')}
        self.assertEqual(cache.chatgpt_snapshot('cred_b')['quotaState'], 'unmapped')

    def test_invalid_registry_keeps_quota_unknown_and_never_runs_script(self):
        cache = self.cache(start=lambda work: work())
        def invalid():
            raise ca.RegistryError('invalid private mapping at /home/private/registry.json')
        cache.gpt_registry = invalid
        body = cache.chatgpt_snapshot('cred_b')
        self.assertEqual(body['quotaState'], 'invalid')
        self.assertFalse(body['chatgpt']['ok'])
        self.assertNotIn('/home/private', json.dumps(body))
        self.assertEqual(self.reads, [])

    def test_legacy_default_is_separate_unverified_and_invalidated_on_active_change(self):
        self.registry = None
        reads = []
        def legacy():
            reads.append(self.active)
            return {'chatgpt': {'ok': True, 'windows': [], 'account': ca.account_stamp(binding='UNVERIFIED')}}
        cache = self.cache(run_chatgpt=legacy, start=lambda work: work())
        first = cache.chatgpt_snapshot()
        self.assertEqual(first['quotaState'], 'unverified')
        self.assertIn('UNVERIFIED', first['chatgpt']['account']['binding'])
        self.active = 'cred_b'
        cache.chatgpt_snapshot()
        self.assertEqual(reads, ['cred_a', 'cred_b'])

    def test_backoff_and_force_minimum_are_per_gpt_binding(self):
        def limited(label):
            return self.reading(label, ok=False, stale=True, age_seconds=700, retry_at=1600, rate_limited=True)
        cache = self.cache(run_chatgpt_account=limited)
        cache.chatgpt_snapshot('cred_b')
        self.jobs.pop()()
        for delay in (1, 30, 60):
            self.now += delay
            body = cache.chatgpt_snapshot('cred_b', force=True)
            self.assertEqual(body['retryAt'], 1600)
            self.assertEqual(self.jobs, [])
        cache.chatgpt_snapshot('cred_a', force=True)
        self.assertEqual(len(self.jobs), 1, 'one account backoff cannot block another')
        self.now = 1601
        cache.chatgpt_snapshot('cred_b', force=True)
        self.assertEqual(len(self.jobs), 2)

    def test_named_command_is_provider_chatgpt_with_exact_quota_label_no_refresh_codex(self):
        cache = self.cache(start=lambda work: work())
        cache.run_gpt_account = None
        with patch.object(server, '_usage_script', return_value=self.reading('quota-b')) as run:
            cache.chatgpt_snapshot('cred_b', force=True)
        self.assertEqual(run.call_args.args, ('--provider', 'chatgpt', '--account', 'quota-b'))

    def test_removed_native_entry_is_not_a_quota_target(self):
        cache = self.cache(start=lambda work: work())
        with self.assertRaises(server.ReaderError):
            cache.chatgpt_snapshot('not-a-saved-id')
        self.assertEqual(self.reads, [])


class GptUsageRouteTests(unittest.TestCase):
    def test_fixed_query_guard_and_force_only_reach_injected_gpt_facade(self):
        calls = []
        class Usage:
            def chatgpt_snapshot(self, **args):
                calls.append(args)
                return {'schemaVersion': 1, 'backend': 'chatgpt-usage', 'credentialId': args['credential_id']}
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.usage_readings = Usage()
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            def request(query, origin=None, method='GET'):
                c = http.client.HTTPConnection(*httpd.server_address, timeout=3)
                c.request(method, '/api/chatgpt/usage' + query, headers={'Origin': origin} if origin else {})
                r = c.getresponse(); data = json.loads(r.read()); c.close()
                return r.status, data
            self.assertEqual(request('?credentialId=cred_b&refresh=1')[0], 200)
            self.assertEqual(calls, [{'credential_id': 'cred_b', 'force': True}])
            for q in ('', '?account=work', '?credentialId=cred_b&refresh=0', '?credentialId=cred_b&credentialId=cred_a',
                      '?credentialId=cred_b&provider=claude'):
                self.assertEqual(request(q)[0], 400)
            self.assertEqual(request('?credentialId=cred_a', origin='https://foreign.invalid')[0], 403)
            self.assertEqual(request('?credentialId=cred_a', method='POST')[0], 405)
            self.assertEqual(len(calls), 1)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
