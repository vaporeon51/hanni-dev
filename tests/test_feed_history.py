from src.services.feed_history import RecentFeedHistory


def test_history_keeps_each_visitors_most_recent_unique_urls():
    history = RecentFeedHistory(per_visitor_capacity=3)

    history.remember("visitor-a", "one")
    history.remember("visitor-a", "two")
    history.remember("visitor-a", "three")
    history.remember("visitor-a", "two")
    history.remember("visitor-a", "four")

    assert history.recent_urls("visitor-a") == ("three", "two", "four")
    assert history.recent_urls("visitor-b") == ()


def test_history_evicts_the_least_recently_used_visitor():
    history = RecentFeedHistory(per_visitor_capacity=2, visitor_capacity=2)
    history.remember("visitor-a", "one")
    history.remember("visitor-b", "two")
    history.recent_urls("visitor-a")
    history.remember("visitor-c", "three")

    assert history.recent_urls("visitor-a") == ("one",)
    assert history.recent_urls("visitor-b") == ()
    assert history.recent_urls("visitor-c") == ("three",)
