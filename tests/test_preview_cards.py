"""
Tests for the preview card pipeline: URL extraction, OpenGraph fetching,
caching, and the per-activity linkage.
"""

import socket
from datetime import datetime, timedelta, timezone
from typing import Optional

from songhive.models.preview_card import PreviewCard
from songhive.services import preview_cards as pc


class TestNormalizeUrl:
    def test_strips_trailing_punctuation(self):
        assert pc._normalize_url("https://example.com/a?b=1.") == "https://example.com/a?b=1"
        assert pc._normalize_url("https://example.com/a,") == "https://example.com/a"

    def test_strips_unbalanced_trailing_parens(self):
        assert pc._normalize_url("https://example.com/a)") == "https://example.com/a"
        assert pc._normalize_url("https://example.com/a_(b)") == "https://example.com/a_(b)"

    def test_strips_fragment(self):
        assert pc._normalize_url("https://example.com/a#frag") == "https://example.com/a"

    def test_rejects_non_http(self):
        assert pc._normalize_url("ftp://example.com/a") is None
        assert pc._normalize_url("javascript:alert(1)") is None

    def test_rejects_missing_host(self):
        assert pc._normalize_url("https://") is None


class TestExtractFirstUrl:
    def test_picks_first_url_from_source(self):
        source = "see https://one.example/x and https://two.example/y"
        assert pc.extract_first_url(source, None) == "https://one.example/x"

    def test_source_markdown_link(self):
        assert pc.extract_first_url("[label](https://md.example/page) text", None) == "https://md.example/page"

    def test_source_takes_precedence_over_content(self):
        html = '<p><a href="https://other.example/">x</a></p>'
        assert pc.extract_first_url("look https://src.example/a", html) == "https://src.example/a"

    def test_remote_html_skips_mentions_and_hashtags(self):
        html = (
            '<p><a class="u-url mention" href="https://remote.example/@alice">@alice</a> '
            '<a rel="tag" class="mention hashtag" href="https://remote.example/tags/x">#x</a> '
            '<a href="https://remote.example/real">a link</a></p>'
        )
        assert pc.extract_first_url(None, html) == "https://remote.example/real"

    def test_remote_html_only_mentions_yields_none(self):
        html = '<p><a class="mention" href="https://remote.example/@alice">@alice</a></p>'
        assert pc.extract_first_url(None, html) is None

    def test_no_url(self):
        assert pc.extract_first_url("no links here", None) is None
        assert pc.extract_first_url(None, "<p>plain text</p>") is None

    def test_first_unusable_url_falls_through_to_next(self):
        source = "broken https:// and https://ok.example/a"
        assert pc.extract_first_url(source, None) == "https://ok.example/a"


class TestUrlAllowed:
    def test_rejects_ip_literals(self):
        assert pc._url_allowed("http://127.0.0.1/x") is False
        assert pc._url_allowed("http://169.254.169.254/latest") is False
        assert pc._url_allowed("http://[::1]/x") is False

    def test_rejects_private_resolution(self, monkeypatch):
        monkeypatch.setattr(
            pc.socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("10.0.0.5", 0))],
        )
        assert pc._url_allowed("https://internal.example/x") is False

    def test_accepts_public_resolution(self, monkeypatch):
        monkeypatch.setattr(
            pc.socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))],
        )
        assert pc._url_allowed("https://public.example/x") is True

    def test_unresolvable_host_passes_to_fetch(self, monkeypatch):
        def _raise(*a, **k):
            raise socket.gaierror()

        monkeypatch.setattr(pc.socket, "getaddrinfo", _raise)
        assert pc._url_allowed("https://gone.example/x") is True

    def test_rejects_non_http_schemes(self):
        assert pc._url_allowed("file:///etc/passwd") is False
        assert pc._url_allowed("gopher://x.example/") is False


class TestHeadParser:
    def test_parses_og_and_title(self):
        head = pc._parse_head(
            "<html><head>"
            "<title>Page &amp; Title</title>"
            '<meta property="og:title" content="OG Title">'
            '<meta name="og:description" content="OG Desc">'
            '<meta property="og:site_name" content="Site">'
            '<meta property="og:image" content="/img.png">'
            '<meta property="og:type" content="article">'
            "</head><body>ignored</body></html>"
        )
        assert pc._meta(head, "og:title") == "OG Title"
        assert pc._meta(head, "og:description") == "OG Desc"
        assert "".join(head.title_parts) == "Page & Title"

    def test_stops_at_body(self):
        head = pc._parse_head("<head><title>T</title></head>" '<body><meta property="og:title" content="late"></body>')
        assert "og:title" not in head.meta


class TestFetchCardData:
    def _stub_fetch(self, monkeypatch, body: str, content_type: str = "text/html"):
        encoded = body.encode()

        class _Response:
            status_code = 200
            headers = {"Content-Type": content_type}
            is_redirect = False
            is_permanent_redirect = False
            encoding = "utf-8"

            def iter_content(self, _size):
                yield encoded

            def close(self):
                pass

        class _Session:
            def get(self, url, **kwargs):
                return _Response()

            def close(self):
                pass

        monkeypatch.setattr(pc.requests, "Session", lambda: _Session())
        monkeypatch.setattr(pc, "_url_allowed", lambda url: True)

    def test_opengraph_metadata(self, monkeypatch):
        self._stub_fetch(
            monkeypatch,
            "<head>"
            '<meta property="og:title" content="A Song">'
            '<meta property="og:description" content="By a band">'
            '<meta property="og:site_name" content="MusicSite">'
            '<meta property="og:image" content="https://cdn.example/cover.jpg">'
            '<meta property="og:type" content="music.song">'
            "</head>",
        )
        card = pc.fetch_card_data("https://site.example/track/1")
        assert card["title"] == "A Song"
        assert card["description"] == "By a band"
        assert card["site_name"] == "MusicSite"
        assert card["image_url"] == "https://cdn.example/cover.jpg"
        assert card["type"] == "music.song"

    def test_title_fallback(self, monkeypatch):
        self._stub_fetch(monkeypatch, "<head><title>Plain Title</title></head>")
        card = pc.fetch_card_data("https://site.example/page")
        assert card["title"] == "Plain Title"
        assert card["image_url"] is None

    def test_domain_fallback(self, monkeypatch):
        self._stub_fetch(monkeypatch, "not html at all", content_type="text/plain")
        card = pc.fetch_card_data("https://site.example/page")
        assert card["title"] == "site.example"

    def test_network_error_still_yields_domain_card(self, monkeypatch):
        class _Session:
            def get(self, url, **kwargs):
                raise OSError("connection refused")

            def close(self):
                pass

        monkeypatch.setattr(pc.requests, "Session", lambda: _Session())
        monkeypatch.setattr(pc, "_url_allowed", lambda url: True)
        card = pc.fetch_card_data("https://down.example/x")
        assert card is not None
        assert card["title"] == "down.example"

    def test_refused_url_returns_none(self, monkeypatch):
        assert pc.fetch_card_data("http://127.0.0.1/secret") is None

    def test_relative_og_image_resolved(self, monkeypatch):
        self._stub_fetch(
            monkeypatch,
            '<head><meta property="og:image" content="/img/pic.png"></head>',
        )
        card = pc.fetch_card_data("https://site.example/page")
        assert card["image_url"] == "https://site.example/img/pic.png"

    def test_javascript_og_image_rejected(self, monkeypatch):
        self._stub_fetch(
            monkeypatch,
            '<head><meta property="og:image" content="javascript:alert(1)">'
            '<meta property="og:title" content="T"></head>',
        )
        card = pc.fetch_card_data("https://site.example/page")
        assert card["image_url"] is None


def _make_activity(
    db_session,
    *,
    content_source: Optional[str] = None,
    content: Optional[str] = None,
    source_type: str = "local",
    owner_user_id: Optional[str] = None,
):
    from songhive.models.activity import Activity

    activity = Activity(
        entity_type="user",
        entity_id="entity-1",
        activity_type="create",
        source_type=source_type,
        source_actor="urn:songhive:user:tester" if source_type == "local" else "https://remote.example/users/bob",
        source_id="src-1",
        owner_user_id=owner_user_id,
        visibility="public",
        content=content,
        content_source=content_source,
        content_type="text/markdown" if source_type == "local" else "text/html",
        payload={},
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(activity)
    return activity


class TestProcessActivityPreviewCard:
    async def test_fetches_and_links_card(self, db_session, monkeypatch):
        monkeypatch.setattr(pc, "_url_allowed", lambda url: True)
        monkeypatch.setattr(
            pc,
            "fetch_card_data",
            lambda url: {
                "url": url,
                "title": "Fetched Title",
                "description": "desc",
                "image_url": "https://img.example/i.png",
                "site_name": "Site",
                "type": "link",
            },
        )
        activity = _make_activity(db_session, content_source="look https://x.example/page")
        await db_session.flush()

        await pc.process_activity_preview_card(db_session, activity)

        assert activity.preview_card_id is not None
        card = await db_session.get(PreviewCard, activity.preview_card_id)
        assert card.url == "https://x.example/page"
        assert card.title == "Fetched Title"

    async def test_fresh_cache_is_reused(self, db_session, monkeypatch):
        calls = []

        def _fetch(url):
            calls.append(url)
            return {
                "url": url,
                "title": "T",
                "description": None,
                "image_url": None,
                "site_name": None,
                "type": "link",
            }

        monkeypatch.setattr(pc, "fetch_card_data", _fetch)
        first = _make_activity(db_session, content_source="https://shared.example/a")
        second = _make_activity(db_session, content_source="again https://shared.example/a")
        second.entity_id = "entity-2"
        second.source_id = "src-2"
        await db_session.flush()

        await pc.process_activity_preview_card(db_session, first)
        await pc.process_activity_preview_card(db_session, second)

        assert calls == ["https://shared.example/a"]
        assert first.preview_card_id == second.preview_card_id

    async def test_stale_cache_is_refetched(self, db_session, monkeypatch):
        calls = []

        def _fetch(url):
            calls.append(url)
            return {
                "url": url,
                "title": f"T{len(calls)}",
                "description": None,
                "image_url": None,
                "site_name": None,
                "type": "link",
            }

        monkeypatch.setattr(pc, "fetch_card_data", _fetch)
        activity = _make_activity(db_session, content_source="https://stale.example/a")
        await db_session.flush()

        await pc.process_activity_preview_card(db_session, activity)
        card = await db_session.get(PreviewCard, activity.preview_card_id)
        card.fetched_at = datetime.now(timezone.utc) - pc.PREVIEW_CARD_MAX_AGE - timedelta(seconds=1)
        await db_session.flush()

        second = _make_activity(db_session, content_source="https://stale.example/a")
        second.entity_id = "entity-2"
        second.source_id = "src-2"
        await db_session.flush()
        await pc.process_activity_preview_card(db_session, second)

        assert len(calls) == 2
        assert second.preview_card_id == card.id
        assert card.title == "T2"

    async def test_no_url_clears_link(self, db_session, monkeypatch):
        monkeypatch.setattr(
            pc,
            "fetch_card_data",
            lambda url: {
                "url": url,
                "title": "T",
                "description": None,
                "image_url": None,
                "site_name": None,
                "type": "link",
            },
        )
        activity = _make_activity(db_session, content_source="https://x.example/a")
        await db_session.flush()
        await pc.process_activity_preview_card(db_session, activity)
        assert activity.preview_card_id is not None

        activity.content_source = "no links anymore"
        activity.content = "<p>no links anymore</p>"
        await pc.process_activity_preview_card(db_session, activity)
        assert activity.preview_card_id is None

    async def test_opted_out_user_clears_link(self, db_session, make_user, monkeypatch):
        user = await make_user("optee")
        user.preview_cards_enabled = False
        called = []
        monkeypatch.setattr(
            pc,
            "fetch_card_data",
            lambda url: called.append(url) or {},
        )
        activity = _make_activity(
            db_session,
            content_source="https://x.example/a",
            owner_user_id=user.id,
        )
        await db_session.flush()
        await pc.process_activity_preview_card(db_session, activity)
        assert activity.preview_card_id is None
        assert called == []

    async def test_instance_setting_off_is_noop(self, db_session, monkeypatch):
        from songhive.services import settings as settings_service

        await settings_service.set_setting(db_session, None, "preview_cards_enabled", False)
        called = []
        monkeypatch.setattr(pc, "fetch_card_data", lambda url: called.append(url) or {})
        activity = _make_activity(db_session, content_source="https://x.example/a")
        activity.preview_card_id = "keep-me"
        await db_session.flush()

        await pc.process_activity_preview_card(db_session, activity)
        assert activity.preview_card_id == "keep-me"
        assert called == []

    async def test_remote_activity_uses_html_anchors(self, db_session, monkeypatch):
        monkeypatch.setattr(
            pc,
            "fetch_card_data",
            lambda url: {
                "url": url,
                "title": "Remote T",
                "description": None,
                "image_url": None,
                "site_name": None,
                "type": "link",
            },
        )
        html = (
            '<p><a class="mention" href="https://remote.example/@alice">@alice</a> '
            '<a href="https://card.example/story">story</a></p>'
        )
        activity = _make_activity(db_session, content=html, source_type="remote")
        await db_session.flush()
        await pc.process_activity_preview_card(db_session, activity)
        card = await db_session.get(PreviewCard, activity.preview_card_id)
        assert card.url == "https://card.example/story"

    async def test_attachments_suppress_card(self, db_session, monkeypatch):
        monkeypatch.setattr(
            pc,
            "fetch_card_data",
            lambda url: {
                "url": url,
                "title": "T",
                "description": None,
                "image_url": None,
                "site_name": None,
                "type": "link",
            },
        )
        activity = _make_activity(db_session, content_source="https://x.example/a")
        activity.payload = {"object": {"attachment": [{"type": "Document", "url": "https://x.example/f"}]}}
        await db_session.flush()
        await pc.process_activity_preview_card(db_session, activity)
        assert activity.preview_card_id is None


class TestSchedulePreviewCardFetch:
    def test_enqueues_for_url_activity(self, monkeypatch):
        sent = []

        class _Task:
            @staticmethod
            def delay(activity_id):
                sent.append(activity_id)

        import songhive.tasks.preview_cards as task_mod

        monkeypatch.setattr(task_mod, "fetch_preview_card", _Task)
        from songhive.models.activity import Activity

        activity = Activity(
            entity_type="user",
            entity_id="e",
            activity_type="create",
            source_type="local",
            source_actor="urn:songhive:user:t",
            source_id="s",
            visibility="public",
            content_source="see https://x.example/a",
            payload={},
            published_at=datetime.now(timezone.utc),
        )
        pc.schedule_preview_card_fetch(activity)
        assert sent == [str(activity.id)]

    def test_skips_when_no_url(self, monkeypatch):
        import songhive.tasks.preview_cards as task_mod

        called = []
        monkeypatch.setattr(
            task_mod.fetch_preview_card,
            "delay",
            lambda *a: called.append(a),
            raising=False,
        )
        from songhive.models.activity import Activity

        activity = Activity(
            entity_type="user",
            entity_id="e",
            activity_type="create",
            source_type="local",
            source_actor="urn:songhive:user:t",
            source_id="s",
            visibility="public",
            content_source="no links",
            payload={},
            published_at=datetime.now(timezone.utc),
        )
        pc.schedule_preview_card_fetch(activity)
        assert called == []

    def test_skips_for_opted_out_author(self, monkeypatch):
        # The cheap pre-check consults the author object passed by callers.
        from songhive.models.activity import Activity

        class _Author:
            preview_cards_enabled = False

        activity = Activity(
            entity_type="user",
            entity_id="e",
            activity_type="create",
            source_type="local",
            source_actor="urn:songhive:user:t",
            source_id="s",
            visibility="public",
            content_source="https://x.example/a",
            payload={},
            published_at=datetime.now(timezone.utc),
        )
        import songhive.tasks.preview_cards as task_mod

        sent = []

        class _Task:
            @staticmethod
            def delay(activity_id):
                sent.append(activity_id)

        monkeypatch.setattr(task_mod, "fetch_preview_card", _Task)
        pc.schedule_preview_card_fetch(activity, author=_Author())
        assert sent == []
