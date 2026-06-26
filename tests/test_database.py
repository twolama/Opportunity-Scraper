import pytest
from app.database import (
    save_opportunity,
    get_opportunity_by_id,
    get_unposted_opportunities,
    search_opportunities,
    get_stats_from_db,
    opportunity_exists,
    opportunities_exist,
    add_admin,
    remove_admin,
    is_admin,
    get_admins,
    add_pending_admin,
    remove_pending_admin,
    get_pending_admins,
    add_invite_token,
    consume_invite_token,
    set_pending_schedule_input,
    pop_pending_schedule_input,
    get_schedule_times,
    add_schedule_time,
    remove_schedule_time,
    parse_time_12h,
    format_time_12h,
)


class TestOpportunityCRUD:
    def test_save_and_retrieve(self):
        opp = {
            "title": "Test Scholarship",
            "link": "https://example.com/test",
            "description": "A test opportunity",
            "deadline": "2026-12-31",
            "thumbnail": "https://example.com/img.jpg",
            "tags": ["education", "scholarship"],
        }
        assert save_opportunity(opp)
        result = get_opportunity_by_id(1)
        assert result is not None
        assert result["title"] == "Test Scholarship"
        assert result["link"] == "https://example.com/test"
        assert "created_at" in result
        assert result["posted_to_telegram"] is False

    def test_duplicate_link(self):
        opp = {
            "title": "Duplicate",
            "link": "https://example.com/dup",
            "description": "",
        }
        assert save_opportunity(opp)
        assert not save_opportunity(opp)

    def test_unposted_list(self):
        unposted = get_unposted_opportunities()
        assert any(o["title"] == "Test Scholarship" for o in unposted)

    def test_search(self):
        result = search_opportunities("Test", 0, 10)
        assert result["total"] >= 1
        assert any("Test" in o["title"] for o in result["results"])

    def test_search_empty(self):
        result = search_opportunities("NonexistentXYZ", 0, 10)
        assert result["total"] == 0

    def test_opportunity_exists(self):
        assert opportunity_exists("Test Scholarship", "https://example.com/test")
        assert not opportunity_exists("Fake", "https://example.com/fake")

    def test_opportunities_exist_batch(self):
        links = ["https://example.com/test", "https://example.com/fake"]
        existing = opportunities_exist(links)
        assert "https://example.com/test" in existing
        assert "https://example.com/fake" not in existing

    def test_stats(self):
        stats = get_stats_from_db()
        assert stats["total"] >= 2
        assert stats["posted"] == 0
        assert stats["unposted"] >= 2


class TestAdminCRUD:
    def test_add_admin(self):
        assert add_admin(100, 12345, "Alice")
        assert is_admin(100)
        assert not add_admin(100, 12345, "Alice")  # duplicate

    def test_get_admins(self):
        admins = get_admins()
        assert any(a["user_id"] == 100 for a in admins)
        assert any(a["user_id"] == 12345 for a in admins)

    def test_remove_admin(self):
        assert remove_admin(100)
        assert not is_admin(100)
        assert not remove_admin(999)  # non-existent

    def test_is_admin_unknown(self):
        assert not is_admin(99999)


class TestPendingAdmin:
    def test_add_and_list(self):
        assert add_pending_admin(200, "Bob")
        pending = get_pending_admins()
        assert any(p["user_id"] == 200 for p in pending)

    def test_duplicate_pending(self):
        assert not add_pending_admin(200, "Bob Again")

    def test_remove_pending(self):
        name = remove_pending_admin(200)
        assert name == "Bob"
        assert remove_pending_admin(200) is None  # already removed


class TestInviteToken:
    def test_create_and_consume(self):
        assert add_invite_token("test_token_1", 12345)
        owner = consume_invite_token("test_token_1")
        assert owner == 12345

    def test_consume_twice(self):
        assert consume_invite_token("test_token_1") is None

    def test_consume_invalid(self):
        assert consume_invite_token("invalid_token") is None


class TestPendingScheduleInput:
    def test_set_and_pop(self):
        set_pending_schedule_input(300, "scrape")
        result = pop_pending_schedule_input(300)
        assert result == "scrape"

    def test_pop_nonexistent(self):
        assert pop_pending_schedule_input(999) is None

    def test_overwrite(self):
        set_pending_schedule_input(400, "scrape")
        set_pending_schedule_input(400, "post")
        assert pop_pending_schedule_input(400) == "post"


class TestScheduleTimes:
    def test_add_and_list(self):
        assert add_schedule_time("09:00", "scrape")
        times = get_schedule_times("scrape")
        assert "09:00" in times

    def test_duplicate(self):
        assert not add_schedule_time("09:00", "scrape")

    def test_remove(self):
        assert remove_schedule_time("09:00", "scrape")
        assert "09:00" not in get_schedule_times("scrape")
        assert not remove_schedule_time("09:00", "scrape")

    def test_list_empty_type(self):
        assert get_schedule_times("nonexistent") == []


class TestTimeHelpers:
    def test_parse_12h_am(self):
        assert parse_time_12h("6:30 AM") == "06:30"

    def test_parse_12h_pm(self):
        assert parse_time_12h("4:59 PM") == "16:59"

    def test_parse_24h(self):
        assert parse_time_12h("06:30") == "06:30"

    def test_parse_invalid(self):
        assert parse_time_12h("foo") is None

    def test_format_12h(self):
        assert format_time_12h("06:30") == "6:30 AM"
        assert format_time_12h("16:59") == "4:59 PM"
        assert format_time_12h("00:00") == "12:00 AM"
