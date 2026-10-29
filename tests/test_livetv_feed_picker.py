import json
import unittest

from tests import test_livetv_failover as failover_tests


class LiveTVFeedPickerTests(failover_tests.LiveTVFailoverTests):
	def setUp(self):
		super().setUp()
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'one', 'id': '1', 'alternatives': ['2', '3', '4'], 'feeds': [{'id': str(i), 'name': 'Broadcast %d' % i} for i in range(1, 13)]})
		self.start()

	def test_picker_can_select_feed_beyond_automatic_limit_and_switch_back(self):
		self.ku.dialog.select = lambda heading, labels: 11
		self.assertTrue(self.module.request_manual_switch())
		self.player.tick()
		self.assertEqual(self.player.played[-1], self.player._url('12'))
		self.player.onAVStarted()
		self.player.tick()
		self.ku.dialog.select = lambda heading, labels: 0
		self.assertTrue(self.module.request_manual_switch())
		self.player.tick()
		self.assertEqual(self.player.played[-1], self.player._url('1'))

	def test_cancel_and_current_selection_do_not_switch(self):
		for choice in (-1, 0):
			self.ku.dialog.select = lambda heading, labels: choice
			self.assertFalse(self.module.request_manual_switch())
			self.player.tick()
		self.assertFalse(self.player.played)

	def test_changed_session_while_dialog_open_discards_choice(self):
		def choose(heading, labels):
			self.properties[self.module.FEED_CHOICES] = json.dumps({'token': 'new', 'current': '9', 'feeds': []})
			return 1
		self.ku.dialog.select = choose
		self.assertFalse(self.module.request_manual_switch())
		self.assertNotIn(self.module.SWITCH_REQUEST, self.properties)

	def test_service_rejects_stale_or_foreign_feed_request(self):
		for request in ({'token': 'old', 'current': '1', 'id': '2'}, {'token': 'one', 'current': '2', 'id': '3'}, {'token': 'one', 'current': '1', 'id': '999'}):
			self.properties[self.module.SWITCH_REQUEST] = json.dumps(request)
			self.player.tick()
		self.assertFalse(self.player.played)

	def test_request_cannot_switch_an_unrelated_player(self):
		self.properties[self.module.SWITCH_REQUEST] = json.dumps({'token': 'one', 'current': '1', 'id': '2'})
		self.player.file = 'unrelated-video'
		self.player.tick()
		self.assertFalse(self.player.played)


if __name__ == '__main__': unittest.main()
