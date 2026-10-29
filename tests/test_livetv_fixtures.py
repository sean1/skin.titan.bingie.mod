import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from http.client import IncompleteRead
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fixtures_under_test', ROOT / 'resources/lib/modules/livetv_fixtures.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def entry(home, away, league='Serie A', status='1H'):
	return {'fixture': {'date': '2026-10-29T19:45:00+00:00', 'status': {'short': status}}, 'league': {'name': league}, 'teams': {'home': {'name': home}, 'away': {'name': away}}, 'goals': {'home': 4, 'away': 2}}


class FixtureTests(unittest.TestCase):
	def setUp(self):
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.tmp.cleanup)
		self.profile = self.tmp.name
		self.key = 'private-test-key'
		fixtures.save_key(self.profile, self.key)
		self.payload = {'errors': [], 'response': [entry('Fiorentina', 'Atalanta')]}

	def test_private_key_and_cache_never_include_key_or_scores(self):
		self.assertEqual(os.stat(os.path.join(self.profile, fixtures.KEY_FILE)).st_mode & 0o777, 0o600)
		items, status = fixtures.get(self.profile, now=1000, fetch=lambda key: self.payload)
		self.assertEqual(items[0]['league'], 'Serie A')
		content = (Path(self.profile) / fixtures.CACHE_FILE).read_text()
		self.assertNotIn(self.key, content)
		self.assertNotIn('goals', content)
		self.assertNotIn(self.key, status)

	def test_profile_key_overrides_bundled_and_removal_restores_it(self):
		bundled = Path(self.profile) / 'bundled-key.txt'
		bundled.write_text('bundled-private-key')
		self.assertEqual(fixtures.read_key(self.profile, str(bundled)), self.key)
		fixtures.remove_key(self.profile)
		self.assertEqual(fixtures.read_key(self.profile, str(bundled)), 'bundled-private-key')
		fetch = Mock(return_value=self.payload)
		fixtures.get(self.profile, bundled=str(bundled), now=1000, fetch=fetch)
		fetch.assert_called_once_with('bundled-private-key')
		self.assertNotIn('bundled-private-key', (Path(self.profile) / fixtures.CACHE_FILE).read_text())

	def test_fresh_cache_and_failure_backoff_limit_requests(self):
		fetch = Mock(side_effect=[self.payload, OSError('secret')])
		self.assertEqual(len(fixtures.get(self.profile, now=1000, fetch=fetch)[0]), 1)
		self.assertEqual(len(fixtures.get(self.profile, now=1100, fetch=fetch)[0]), 1)
		self.assertEqual(fixtures.get(self.profile, now=1300, fetch=fetch), ([], 'temporarily unavailable'))
		self.assertEqual(fixtures.get(self.profile, now=1400, fetch=fetch), ([], 'temporarily unavailable'))
		self.assertEqual(fetch.call_count, 2)

	def test_api_error_and_rate_limit_do_not_promote_stale_cache(self):
		fetch = Mock(side_effect=[self.payload, {'errors': {'requests': 'limit'}, 'response': []}])
		fixtures.get(self.profile, now=1000, fetch=fetch)
		self.assertEqual(fixtures.get(self.profile, now=1301, fetch=fetch), ([], 'temporarily unavailable'))
		self.assertEqual(fetch.call_count, 2)

	def test_http_429_is_backed_off_without_exposing_error(self):
		fetch = Mock(side_effect=HTTPError('https://example.test', 429, 'quota', {}, None))
		self.assertEqual(fixtures.get(self.profile, now=1000, fetch=fetch), ([], 'temporarily unavailable'))
		self.assertEqual(fixtures.get(self.profile, now=1001, fetch=fetch), ([], 'temporarily unavailable'))
		self.assertEqual(fetch.call_count, 1)

	def test_truncated_response_keeps_dashboard_listings(self):
		live = load_livetv()
		live.ku.profile_path = self.profile
		live.ku.addon_path = self.profile
		live.ku.set_property = Mock()
		live.ku.get_property = lambda *args: ''
		live.ku.build_url = lambda params: repr(params)
		streams = [{'stream_id': 1, 'name': 'Live | Alpha vs Beta | US: SOCCER'}]
		with patch.object(live, '_load', return_value={'server': 'https://example.test', 'username': 'user', 'password': 'secret'}), patch.object(live, '_catalog', return_value=streams), patch.object(live._fixture_service, '_fetch', side_effect=IncompleteRead(b'partial')), patch.object(live, '_item') as item, patch.object(live, '_finish'):
			live.soccer({'league': 'All'})
		self.assertEqual(item.call_args.kwargs['extra_properties']['LiveTV.Recognition'], 'Unverified')
		live.ku.set_property.assert_any_call('LiveTV.Soccer.FixtureStatus', 'Match recognition: temporarily unavailable')

	def test_quota_cap_persists_across_key_changes_and_resets_next_utc_day(self):
		path = Path(self.profile) / fixtures.CACHE_FILE
		path.write_text(json.dumps({'key': 'old-key-fingerprint', 'day': '1970-01-01', 'count': fixtures.DAILY_LIMIT}))
		fetch = Mock(return_value=self.payload)
		self.assertEqual(fixtures.get(self.profile, now=2000, fetch=fetch), ([], 'daily limit reached'))
		self.assertEqual(len(fixtures.get(self.profile, now=86401, fetch=fetch)[0]), 1)
		self.assertEqual(fetch.call_count, 1)

	def test_matching_requires_unique_fixture_and_correct_women_youth_scope(self):
		data = [dict(home='Fiorentina', away='Atalanta', league='Serie A', date='2026-10-29T19:45:00+00:00'), dict(home='Manchester City W', away='Paris Saint Germain W', league='UEFA Champions League Women', date='2026-10-29T20:00:00+00:00'), dict(home='Spain U17', away='USA U17', league='World Cup Women U17', date='2026-10-29T20:00:00+00:00')]
		self.assertEqual(fixtures.match(('fiorentina', 'atalanta'), '', data)['league'], 'Serie A')
		self.assertEqual(fixtures.match(('manchester city wfc', 'paris saint-germain fc'), '', data)['league'], 'UEFA Champions League Women')
		self.assertIsNone(fixtures.match(('manchester city', 'paris saint-germain'), '', data))
		self.assertIsNone(fixtures.match(('spain', 'usa'), '', data))
		self.assertIsNone(fixtures.match(('spain u-17', 'usa u17'), '', data))
		self.assertEqual(fixtures.match(('spain w u-17', 'usa u17'), '', data)['league'], 'World Cup Women U17')
		self.assertIsNone(fixtures.match(('fiorentina', 'atalanta'), '', [data[0], data[0]]))
		self.assertIsNone(fixtures.match(('fiorentina', 'atalanta'), 'premier league', data))
		self.assertIsNone(fixtures.match(('fiorentina', 'atalanta'), '2026-10-28', data))
		self.assertIsNone(fixtures.match(('fiorentina', 'atalanta'), '29 oct', data))
		ligue = [dict(home='Paris', away='Lyon', league='Ligue 1', date='2026-10-29T20:00:00+00:00')]
		self.assertEqual(fixtures.match(('paris', 'lyon'), 'ligue 1', ligue)['league'], 'Ligue 1')
		self.assertIsNone(fixtures.match(('paris', 'lyon'), '29/10/2026', ligue))

	def test_known_alias_requires_exact_opponent_and_unique_fixture(self):
		fixture = dict(home='Amiens', away='Thionville Lusitanos', league='Ligue 3', date='2026-10-29T19:45:00+00:00')
		self.assertEqual(fixtures.match(('amiens', 'thionville'), '', [fixture]), fixture)
		self.assertEqual(fixtures.match(('thionville', 'amiens'), '', [dict(fixture, home='Thionville Lusitanos', away='Amiens')])['league'], 'Ligue 3')
		self.assertIsNone(fixtures.match(('amiens', 'thionvill'), '', [fixture]))
		self.assertIsNone(fixtures.match(('amiens', 'thionville'), '', [fixture, dict(fixture, away='Thionville')]))
		self.assertIsNone(fixtures.match(('amiens', 'thionville'), 'serie a', [fixture]))
		self.assertIsNone(fixtures.match(('amiens', 'thionville'), '', [dict(fixture, league='Ligue 3 Women')]))
		self.assertIsNone(fixtures.match(('amiens', 'thth'), '', [dict(fixture, away='Thth United')]))
		self.assertIsNone(fixtures.match(('amiens', 'thionville'), '', [dict(fixture, home='Amiens Sporting', away='Thionville Lusitanos')]))
		self.assertIsNone(fixtures.match(('thionville', 'amiens'), '', [fixture]))
		for suffix in ('II', 'B', 'Reserves', 'Athletic'):
			self.assertIsNone(fixtures.match(('amiens', 'thionville'), '', [dict(fixture, away='Thionville ' + suffix)]))
		self.assertIsNone(fixtures.match(('other club', 'manchester united'), '', [dict(fixture, home='Other Club', away='Manchester United II')]))

	def test_settings_cancel_and_blank_keep_existing_key(self):
		live = load_livetv()
		live.ku.profile_path = self.profile
		live.ku.addon_path = self.profile
		live.ku.dialog.select.return_value = -1
		live.fixture_settings({})
		self.assertEqual(fixtures.read_key(self.profile), self.key)
		live.ku.dialog.select.return_value = 0
		live.ku.dialog.input.return_value = ''
		live.fixture_settings({})
		self.assertEqual(fixtures.read_key(self.profile), self.key)

	def test_failed_key_replace_preserves_existing_key(self):
		with patch.object(fixtures.os, 'replace', side_effect=OSError('write failed')):
			with self.assertRaises(OSError): fixtures.save_key(self.profile, 'new-private-key')
		self.assertEqual(fixtures.read_key(self.profile), self.key)

	def test_dashboard_uses_fixture_league_and_preserves_unmatched(self):
		live = load_livetv()
		live.ku.profile_path = self.profile
		live.ku.set_property = Mock()
		live.ku.get_property = lambda *args: ''
		live.ku.build_url = lambda params: repr(params)
		streams = [{'stream_id': 1, 'name': 'Live | Fiorentina vs Atalanta | US: SOCCER'}, {'stream_id': 2, 'name': 'Live | Alpha vs Beta | US: SOCCER'}]
		data = [{'home': 'Fiorentina', 'away': 'Atalanta', 'league': 'Serie A', 'date': '2026-10-29T19:45:00+00:00'}]
		with patch.object(live, '_load', return_value={'server': 'https://example.test', 'username': 'user', 'password': 'secret'}), patch.object(live, '_catalog', return_value=streams), patch.object(live._fixture_service, 'get', return_value=(data, 'updated 12:00')), patch.object(live, '_item') as item, patch.object(live, '_finish'):
			live.soccer({'league': 'All'})
		self.assertEqual(item.call_args_list[0].kwargs['extra_properties']['LiveTV.League'], 'Serie A')
		self.assertEqual(item.call_args_list[0].kwargs['extra_properties']['LiveTV.Recognition'], 'Fixture matched')
		self.assertEqual(item.call_args_list[1].kwargs['extra_properties']['LiveTV.Recognition'], 'Unverified')
		self.assertTrue(item.call_args_list[0].kwargs['extra_properties']['LiveTV.Kickoff'].startswith('Kickoff '))
		self.assertEqual(item.call_args_list[1].kwargs['extra_properties']['LiveTV.Kickoff'], '')

	@unittest.skipUnless(hasattr(time, 'tzset'), 'Timezone override requires tzset')
	def test_kickoff_uses_local_time_and_rejects_ambiguous_dates(self):
		live = load_livetv()
		try:
			with patch.dict(os.environ, {'TZ': 'PST8PDT,M3.2.0,M11.1.0'}):
				time.tzset()
				self.assertEqual(live._soccer_kickoff({'date': '2026-10-29T19:45:00+00:00'}), 'Kickoff Thu 12:45')
				self.assertEqual(live._soccer_kickoff({'date': '2026-10-29T19:45:00Z'}), 'Kickoff Thu 12:45')
				self.assertEqual(live._soccer_kickoff({'date': '2026-10-29T19:45:00'}), '')
				self.assertEqual(live._soccer_kickoff({'date': 'not a date'}), '')
				self.assertEqual(live._soccer_kickoff(None), '')
		finally: time.tzset()


if __name__ == '__main__': unittest.main()
