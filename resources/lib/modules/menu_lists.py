root_list = [
	{'name': 32028, 'iconImage': 'movies.png', 'mode': 'navigator.main', 'action': 'MovieList'},
	{'name': 32029, 'iconImage': 'tv.png', 'mode': 'navigator.main', 'action': 'TVShowList'},
	{'name': 32452, 'iconImage': 'people.png', 'mode': 'build_popular_people', 'isFolder': 'false'},
	{'name': 32451, 'iconImage': 'discover.png', 'mode': 'navigator.discover_main'},
	{'name': 32450, 'iconImage': 'search.png', 'mode': 'navigator.search'},
	{'name': 'Dropped', 'iconImage': 'tv.png', 'mode': 'build_tvshow_list', 'action': 'dropped_tvshows'},
	{'name': 32455, 'iconImage': 'premium.png', 'mode': 'navigator.premium'},
#	{'name': 32456, 'iconImage': 'tools.png', 'mode': 'navigator.tools'},
	{'name': 32247, 'iconImage': 'settings.png', 'mode': 'navigator.settings'}
]

movie_list = [
	{'name': 32965, 'iconImage': 'trending.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_trending_day'},
	{'name': 32966, 'iconImage': 'most_watched.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_trending'},
	{'name': 32459, 'iconImage': 'popular.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_popular'},
	{'name': 32967, 'iconImage': 'fresh.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_now_playing'},
	{'name': 32469, 'iconImage': 'lists.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_upcoming'},
	{'name': 32968, 'iconImage': 'most_voted.png', 'mode': 'build_movie_list', 'action': 'tmdb_movies_top_rated'},
	{'name': 32470, 'iconImage': 'genres.png', 'mode': 'navigator.genres', 'menu_type': 'movie'},
	{'name': 32472, 'iconImage': 'calender.png', 'mode': 'navigator.years', 'menu_type': 'movie'},
	{'name': 32474, 'iconImage': 'because_you_watched.png', 'mode': 'navigator.because_you_watched', 'menu_type': 'movie'},
	{'name': 32476, 'iconImage': 'player.png', 'mode': 'build_movie_list', 'action': 'in_progress_movies'}
]

tvshow_list = [
	{'name': 32965, 'iconImage': 'trending.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_trending_day'},
	{'name': 32966, 'iconImage': 'most_watched.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_trending'},
	{'name': 32459, 'iconImage': 'popular.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_popular'},
	{'name': 32969, 'iconImage': 'fresh.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_airing_today'},
	{'name': 32970, 'iconImage': 'lists.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_on_the_air'},
	{'name': 32968, 'iconImage': 'most_voted.png', 'mode': 'build_tvshow_list', 'action': 'tmdb_tv_top_rated'},
	{'name': 32470, 'iconImage': 'genres.png', 'mode': 'navigator.genres', 'menu_type': 'tvshow'},
	{'name': 32480, 'iconImage': 'networks.png', 'mode': 'navigator.networks', 'menu_type': 'tvshow'},
	{'name': 32472, 'iconImage': 'calender.png', 'mode': 'navigator.years', 'menu_type': 'tvshow'},
	{'name': 32474, 'iconImage': 'because_you_watched.png', 'mode': 'navigator.because_you_watched', 'menu_type': 'tvshow'},
	{'name': 32481, 'iconImage': 'in_progress_tvshow.png', 'mode': 'build_tvshow_list', 'action': 'in_progress_tvshows'},
	{'name': 32482, 'iconImage': 'player.png', 'mode': 'build_in_progress_episode'},
	{'name': 32483, 'iconImage': 'next_episodes.png', 'mode': 'build_next_episode'}
]

main_menu_items, main_menus, default_menu_items = {
	'RootList': {'name': 32457, 'iconImage': 'pov.png', 'mode': 'navigator.main', 'action': 'RootList'},
	'MovieList': root_list[0],
	'TVShowList': root_list[1]
}, {
	'RootList': root_list,
	'MovieList': movie_list,
	'TVShowList': tvshow_list
}, (
	'RootList',
	'MovieList',
	'TVShowList'
)
