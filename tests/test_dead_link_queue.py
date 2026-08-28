from src.services.dead_link_queue import BoundedUrlQueue


def test_queue_is_fifo_and_deduplicates_pending_urls():
    queue = BoundedUrlQueue(3)

    assert queue.enqueue("one") is True
    assert queue.enqueue("two") is True
    assert queue.enqueue("one") is False

    assert queue.take(2) == ["one", "two"]
    assert len(queue) == 0


def test_full_queue_retains_the_most_recent_urls():
    queue = BoundedUrlQueue(3)
    for url in ("one", "two", "three", "four"):
        assert queue.enqueue(url) is True

    assert len(queue) == 3
    assert queue.take(10) == ["two", "three", "four"]
