from caches import BaseCache, watched_db
from modules import settings
from modules.utils import sort_for_article, paginate_list

INSERT_DROP = 'INSERT INTO dropped VALUES (?, ?, ?)'
DELETE_DROP = 'DELETE FROM dropped WHERE db_type = ? and tmdb_id = ?'
SELECT_DROP = 'SELECT tmdb_id, title FROM dropped WHERE db_type = ?'

class Dropped(BaseCache):
	db_file = watched_db

	def get(self, mediatype):
		self.dbcur.execute(SELECT_DROP, ('tvshow',))
		result = self.dbcur.fetchall()
		return [{'tmdb_id': str(i[0]), 'title': str(i[1])} for i in result]

	def add(self, mediatype, tmdb_id, title):
		try:
			self.dbcur.execute(INSERT_DROP, ('tvshow', str(tmdb_id), title))
			return True
		except: return False

	def remove(self, mediatype, tmdb_id, title):
		try:
			self.dbcur.execute(DELETE_DROP, ('tvshow', str(tmdb_id)))
			return True
		except: return False

def get_dropped(watched_info, mediatype, page_no):
	paginate = settings.paginate()
	limit = settings.page_limit()
	data = Dropped().get(mediatype)
	data = sort_for_article(data, 'title', settings.ignore_articles())
	original_list = [{'media_id': i['tmdb_id'], 'title': i['title']} for i in data]
	if paginate: return paginate_list(original_list, page_no, limit)
	return original_list, 1

def get_hidden_items(list_type):
	data = Dropped().get(list_type)
	return [int(i['tmdb_id']) for i in data]
