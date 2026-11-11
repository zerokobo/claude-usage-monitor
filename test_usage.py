import json
import pathlib
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

from usage import (USAGE_URL, ClaudeUsageClient, ClaudeUsageError, History, QuotaAlerts, RateLimited, Window,
                   credentials_path, load_accounts, parse_usage)


class ParseTests(unittest.TestCase):
    def test_api_response(self):
        data = {
            'five_hour': {'utilization': 27.0, 'resets_at': '2026-10-13T00:19:59.729882+00:00'},
            'seven_day': {'utilization': 98.0, 'resets_at': '2026-10-12T22:59:59.729904+00:00'},
            'seven_day_opus': None,
        }
        now = 1791844800  # 2026-10-12T22:40:00Z
        windows = parse_usage(data)
        self.assertEqual([(w.label, w.remaining(now)) for w in windows], [('5時間', 73), ('週次', 2)])
        self.assertEqual(int(windows[1].reset), 1791845999)

    def test_unused_five_hour_window_is_full(self):
        windows = parse_usage({'five_hour': {'utilization': 0, 'resets_at': None}})
        self.assertEqual((windows[0].remaining(), windows[0].reset), (100, None))

    def test_missing_windows_and_bad_values(self):
        self.assertEqual(parse_usage({'five_hour': None}), [])
        self.assertEqual(parse_usage([]), [])
        self.assertEqual(parse_usage({'five_hour': {'utilization': True}}), [])
        windows = parse_usage({'five_hour': {'utilization': 5, 'resets_at': 'soon'}})
        self.assertIsNone(windows[0].reset)

    def test_past_reset_has_no_remaining(self):
        self.assertIsNone(Window('5時間', 10, time.time() - 1).remaining())


class TokenTests(unittest.TestCase):
    def client(self, oauth):
        path = pathlib.Path(tempfile.mkdtemp()) / '.credentials.json'
        if oauth is not None:
            path.write_text(json.dumps({'claudeAiOauth': oauth}), encoding='utf-8')
        return ClaudeUsageClient(path)

    def test_missing_and_expired_token_do_not_call_api(self):
        with patch('urllib.request.urlopen') as urlopen:
            for oauth in (None, {'accessToken': 'x', 'expiresAt': (time.time() - 60) * 1000}):
                with self.assertRaises(ClaudeUsageError):
                    self.client(oauth).fetch()
            urlopen.assert_not_called()

    def test_rate_limit_is_distinguished(self):
        client = self.client({'accessToken': 'abc', 'expiresAt': (time.time() + 600) * 1000})
        error = urllib.error.HTTPError(USAGE_URL, 429, 'Too Many Requests', {}, None)
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RateLimited):
                client.fetch()

    def test_token_goes_only_to_the_usage_url(self):
        client = self.client({'accessToken': 'abc', 'expiresAt': (time.time() + 600) * 1000})
        with patch('urllib.request.urlopen', side_effect=OSError) as urlopen:
            with self.assertRaises(ClaudeUsageError):
                client.fetch()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, USAGE_URL)
        self.assertEqual(request.get_header('Authorization'), 'Bearer abc')

    def test_config_dir(self):
        self.assertEqual(credentials_path('C:/x'), pathlib.Path('C:/x/.credentials.json'))
        with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': '/tmp/c'}):
            self.assertEqual(credentials_path(''), pathlib.Path('/tmp/c/.credentials.json'))


class AccountsTests(unittest.TestCase):
    def test_default_and_cleanup(self):
        self.assertEqual(load_accounts({}), [{'name': 'Claude', 'config_dir': ''}])
        rows = [{'name': ' 仕事 ', 'config_dir': 'D:/w'}, {'name': '仕事'}, {'name': ''}, 'x']
        self.assertEqual(load_accounts({'accounts': rows}), [{'name': '仕事', 'config_dir': 'D:/w'}])


class AlertTests(unittest.TestCase):
    def test_reminder_rules(self):
        alerts = QuotaAlerts()
        now = 1_000_000
        self.assertFalse(alerts.due('a', Window('週次', 50, None), now))
        self.assertTrue(alerts.due('a', Window('週次', 92, None), now))
        self.assertFalse(alerts.due('a', Window('週次', 93, None), now + 60))
        self.assertTrue(alerts.due('a', Window('週次', 95, None), now + 120))
        self.assertTrue(alerts.due('a', Window('週次', 100, None), now + 180))
        self.assertTrue(alerts.due('a', Window('週次', 100, None), now + 180 + 7200))


class HistoryTests(unittest.TestCase):
    def test_duplicates_ignored_and_accounts_separate(self):
        history = History(':memory:')
        history.add('a', 10, [Window('5時間', 20, None)])
        history.add('a', 10, [Window('5時間', 20, None)])
        history.add('b', 11, [Window('5時間', 50, None)])
        self.assertEqual(history.rows('a', 0), [(10, '5時間', 80, None)])
        self.assertEqual(len(history.rows('b', 0)), 1)


if __name__ == '__main__':
    unittest.main()
