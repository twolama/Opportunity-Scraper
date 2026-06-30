import json
import os
from app.telegraph import build_telegraph_content, TelegraphError
from app.utils import format_teaser, format_condensed_post, format_telegram_message


SAMPLE_OPP = {
    "id": 1,
    "title": "Full Scholarship 2026",
    "link": "https://example.com/apply",
    "description": "This is a fully funded scholarship for international students.\n\n"
                   "It covers tuition, accommodation, and travel expenses.\n\n"
                   "Applicants must have a bachelor's degree.",
    "deadline": "December 31, 2026",
    "thumbnail": "",
    "tags": ["Scholarship", "International"],
}


LONG_OPP = {
    "id": 2,
    "title": "Long Opportunity With Extended Details",
    "link": "https://example.com/apply",
    "description": "Paragraph one with lots of details about the opportunity. " * 50 + "\n\n"
                   "Paragraph two with even more information for applicants. " * 50 + "\n\n"
                   "Paragraph three with final important notes about the application. " * 50,
    "deadline": "December 31, 2026",
    "thumbnail": "",
    "tags": ["Test"],
}


OPP_WITH_THUMB = {
    **SAMPLE_OPP,
    "thumbnail": "https://example.com/image.jpg",
}


class TestBuildTelegraphContent:
    def test_basic_structure(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        assert isinstance(nodes, list)
        assert len(nodes) >= 6  # h4 + 3p + hr + blockquote + hr + h4 + p + p

    def test_starts_with_details_header(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        assert nodes[0]["tag"] == "h4"
        assert nodes[0]["children"][0] == "Details"

    def test_description_in_paragraphs(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        paras = [n for n in nodes if n["tag"] == "p" and n["children"] and isinstance(n["children"][0], str)]
        assert any("fully funded scholarship" in str(p["children"]) for p in paras)

    def test_thumbnail_as_first_element(self):
        nodes = build_telegraph_content(OPP_WITH_THUMB)
        first = nodes[0]
        assert first["tag"] == "img"
        assert first["attrs"]["src"] == "https://example.com/image.jpg"

    def test_deadline_in_blockquote(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        bq = [n for n in nodes if n["tag"] == "blockquote"]
        assert len(bq) >= 1
        assert "December 31, 2026" in json.dumps(bq[0])

    def test_hr_separators_before_and_after_deadline(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        hrs = [n for n in nodes if n["tag"] == "hr"]
        assert len(hrs) >= 2

    def test_apply_now_link(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        all_text = json.dumps(nodes)
        assert "example.com/apply" in all_text
        assert "Apply Now" in all_text

    def test_footer_has_join_link(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        all_text = json.dumps(nodes)
        assert "Join Opportunity Spot" in all_text
        assert "t.me/opportunityspots" in all_text

    def test_how_to_apply_section(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        h4s = [n for n in nodes if n["tag"] == "h4"]
        headers = [json.dumps(h) for h in h4s]
        assert any("How to Apply" in h for h in headers)

    def test_empty_description(self):
        opp = {**SAMPLE_OPP, "description": ""}
        nodes = build_telegraph_content(opp)
        assert len(nodes) >= 4  # deadline + apply + footer

    def test_no_deadline(self):
        opp = {**SAMPLE_OPP, "deadline": None}
        nodes = build_telegraph_content(opp)
        all_text = json.dumps(nodes)
        assert "Deadline" not in all_text

    def test_no_thumbnail_no_image(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        imgs = [n for n in nodes if n["tag"] == "img"]
        assert len(imgs) == 0

    def test_content_is_serializable(self):
        nodes = build_telegraph_content(SAMPLE_OPP)
        json.dumps(nodes)


class TestFormatTeaser:
    def test_short_text_unchanged(self):
        assert format_teaser("Hello world", 100) == "Hello world"

    def test_truncates_at_word_boundary(self):
        result = format_teaser("Hello world this is a test", 15)
        assert len(result) <= 18  # Allow "..."
        assert result.endswith("...")
        assert "Hello" in result

    def test_exact_length(self):
        text = "A" * 250
        result = format_teaser(text, 250)
        assert result == text

    def test_html_escaped(self):
        result = format_teaser("<b>bold</b>", 100)
        assert "&lt;b&gt;" in result
        assert "<b>" not in result

    def test_empty_string(self):
        assert format_teaser("") == ""


class TestFormatCondensedPost:
    def test_contains_telegraph_link(self):
        post = format_condensed_post(SAMPLE_OPP, "https://telegra.ph/Test-12-31")
        assert "Read more" in post
        assert "telegra.ph/Test-12-31" in post

    def test_contains_title(self):
        post = format_condensed_post(SAMPLE_OPP, "https://telegra.ph/Test-12-31")
        assert "Full Scholarship" in post
        assert "Scholarship 2026" in post

    def test_contains_deadline(self):
        post = format_condensed_post(SAMPLE_OPP, "https://telegra.ph/Test-12-31")
        assert "December 31, 2026" in post

    def test_contains_tags(self):
        post = format_condensed_post(SAMPLE_OPP, "https://telegra.ph/Test-12-31")
        assert "#Opportunities" in post or "#Scholarships" in post

    def test_under_4096_chars(self):
        post = format_condensed_post(LONG_OPP, "https://telegra.ph/Test-12-31")
        # Condensed post should be well under telegram limit
        assert len(post) <= 4096, f"Condensed post too long: {len(post)} chars"

    def test_teaser_included(self):
        post = format_condensed_post(SAMPLE_OPP, "https://telegra.ph/Test-12-31")
        assert "fully funded" in post  # teaser from description


class TestFormatTelegramMessageUnchanged:
    def test_full_message_for_short_opp(self):
        msg = format_telegram_message(SAMPLE_OPP)
        assert "Full Scholarship 2026" in msg
        assert "fully funded" in msg
        assert "December 31, 2026" in msg
        assert "#Opportunities" in msg
        assert len(msg) <= 4096
