from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor
from app.scheduler import _telegram_failures, _TELEGRAM_CIRCUIT_BREAKER_MAX
import app.scheduler as sched_module


def _reset():
    import app.scheduler as sched
    sched._telegram_failures = 0


class TestCircuitBreaker:
    def test_skips_when_circuit_open(self):
        _reset()
        import app.scheduler as sched
        sched._telegram_failures = _TELEGRAM_CIRCUIT_BREAKER_MAX
        with patch("app.scheduler.get_unposted_opportunities") as mock_get:
            with patch("app.scheduler.get_schedule_times") as mock_times:
                sched.run_post()
                mock_get.assert_not_called()
                mock_times.assert_not_called()

    def test_gradually_decays(self):
        _reset()
        import app.scheduler as sched
        sched._telegram_failures = _TELEGRAM_CIRCUIT_BREAKER_MAX + 2
        with patch("app.scheduler.get_unposted_opportunities") as mock_get:
            sched.run_post()
            assert sched._telegram_failures == _TELEGRAM_CIRCUIT_BREAKER_MAX + 1
            mock_get.assert_not_called()

    def test_resets_on_success(self):
        _reset()
        import app.scheduler as sched
        sched._telegram_failures = 3
        with patch("app.scheduler.get_unposted_opportunities") as mock_get:
            mock_get.return_value = [{"id": 1, "title": "Test", "link": "https://x.com"}]
            with patch("app.scheduler.get_schedule_times") as mock_times:
                mock_times.return_value = ["12:00"]
                with patch("app.scheduler._remaining_post_slots_today") as mock_rem:
                    mock_rem.return_value = 1
                    with patch("app.scheduler._post_batch") as mock_post:
                        mock_post.return_value = 1
                        sched.run_post()
                        assert sched._telegram_failures == 0

    def test_increments_on_failure(self):
        _reset()
        import app.scheduler as sched
        sched._telegram_failures = 0
        with patch("app.scheduler.get_unposted_opportunities") as mock_get:
            mock_get.return_value = [{"id": 1, "title": "Test"}]
            with patch("app.scheduler.get_schedule_times") as mock_times:
                mock_times.return_value = ["12:00"]
                with patch("app.scheduler._remaining_post_slots_today") as mock_rem:
                    mock_rem.return_value = 1
                    with patch("app.scheduler._post_batch") as mock_post:
                        mock_post.return_value = 0
                        sched.run_post()
                        assert sched._telegram_failures == 1

    def test_thread_safe_increment(self):
        """Verify _telegram_failures_lock prevents data races."""
        _reset()
        def _fake_post():
            sched_module._telegram_failures += 1
        def _run():
            nonlocal_sched = sched_module
            with nonlocal_sched._telegram_failures_lock:
                _fake_post()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: _run(), range(100)))
        assert sched_module._telegram_failures == 100
