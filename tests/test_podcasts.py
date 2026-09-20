"""
Tests for the podcasts feature: feed parsing, subscriptions, API and OPML.
"""

from datetime import datetime, timedelta, timezone

import pytest

from songhive.models.podcast import Podcast
from songhive.services import podcasts as podcasts_service
from songhive.services.podcasts import FeedFetchError, FetchResult

RSS_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Show</title>
    <link>https://pod.example/</link>
    <description>A test podcast.</description>
    <language>en</language>
    <itunes:author>Jane Doe</itunes:author>
    <itunes:image href="https://pod.example/art.jpg"/>
    <itunes:category text="Music"><itunes:category text="Indie"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <item>
      <title>Episode One</title>
      <guid>ep-1</guid>
      <description>First episode.</description>
      <link>https://pod.example/ep1</link>
      <pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate>
      <enclosure url="https://cdn.pod.example/ep1.mp3" type="audio/mpeg" length="12345"/>
      <itunes:duration>12:34</itunes:duration>
      <itunes:episode>1</itunes:episode>
      <itunes:season>2</itunes:season>
    </item>
    <item>
      <title>Episode Two</title>
      <guid>ep-2</guid>
      <pubDate>Mon, 08 Sep 2025 10:00:00 GMT</pubDate>
      <enclosure url="https://cdn.pod.example/ep2.mp3" type="audio/mpeg" length="999"/>
      <itunes:duration>3600</itunes:duration>
    </item>
    <item>
      <title>No audio</title>
      <guid>ep-skip</guid>
      <description>This item has no enclosure and is skipped.</description>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <title>Atom Show</title>
  <link rel="alternate" href="https://atom.example/"/>
  <link rel="icon" href="https://atom.example/icon.png"/>
  <subtitle>An atom podcast.</subtitle>
  <author><name>Atom Author</name></author>
  <entry>
    <id>atom-ep-1</id>
    <title>Atom Episode</title>
    <published>2025-09-01T10:00:00Z</published>
    <link rel="enclosure" href="https://cdn.atom.example/a1.mp3" type="audio/mpeg" length="42"/>
    <itunes:duration>1:02:03</itunes:duration>
  </entry>
</feed>
"""

FEED_URL = "https://pod.example/feed.xml"


def _fetch_result(body: bytes = RSS_FEED, **kwargs) -> FetchResult:
    return FetchResult(body=body, final_url=FEED_URL, **kwargs)


def _stub_fetch(monkeypatch, result=RSS_FEED):
    """Patch ``fetch_feed`` to return ``result`` without network access."""
    if isinstance(result, Exception):

        def _raise(*_args, **_kwargs):
            raise result

        monkeypatch.setattr(podcasts_service, "fetch_feed", _raise)
        return
    if isinstance(result, bytes):
        result = _fetch_result(body=result)

    monkeypatch.setattr(podcasts_service, "fetch_feed", lambda *a, **kw: result)


def test_parse_rss_feed_extracts_metadata_and_episodes():
    feed = podcasts_service.parse_feed(RSS_FEED, FEED_URL)

    assert feed.title == "Test Show"
    assert feed.author == "Jane Doe"
    assert feed.description == "A test podcast."
    assert feed.link == "https://pod.example/"
    assert feed.image_url == "https://pod.example/art.jpg"
    assert feed.language == "en"
    assert feed.categories == ["Music", "Indie"]
    assert feed.explicit is False

    assert [e.title for e in feed.episodes] == ["Episode One", "Episode Two"]
    ep = feed.episodes[0]
    assert ep.guid == "ep-1"
    assert ep.audio_url == "https://cdn.pod.example/ep1.mp3"
    assert ep.audio_type == "audio/mpeg"
    assert ep.audio_length == 12345
    assert ep.duration_seconds == 754
    assert ep.episode_number == 1
    assert ep.season_number == 2
    assert ep.published_at == datetime(2025, 9, 1, 10, 0, tzinfo=timezone.utc)


def test_parse_atom_feed():
    feed = podcasts_service.parse_feed(ATOM_FEED, "https://atom.example/feed")

    assert feed.title == "Atom Show"
    assert feed.author == "Atom Author"
    assert feed.image_url == "https://atom.example/icon.png"
    assert len(feed.episodes) == 1
    ep = feed.episodes[0]
    assert ep.guid == "atom-ep-1"
    assert ep.audio_url == "https://cdn.atom.example/a1.mp3"
    assert ep.duration_seconds == 3723


def test_parse_feed_strips_html_from_descriptions():
    feed_doc = RSS_FEED.replace(
        b"<description>A test podcast.</description>",
        b"<description>&lt;p&gt;A &lt;b&gt;test&lt;/b&gt; podcast &amp;amp; more.&lt;/p&gt;</description>",
    ).replace(
        b"<description>First episode.</description>",
        b"<description>&lt;p&gt;First episode.&lt;br/&gt;Second line."
        b"&lt;script&gt;x()&lt;/script&gt;&lt;/p&gt;</description>",
    )
    feed = podcasts_service.parse_feed(feed_doc, FEED_URL)

    assert feed.description == "A test podcast & more."
    assert feed.episodes[0].description == "First episode. Second line."


def test_parse_feed_uses_content_encoded_fallback():
    feed_doc = RSS_FEED.replace(
        b'<rss version="2.0"',
        b'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"',
    ).replace(
        b"<description>First episode.</description>",
        b"<content:encoded>&lt;p&gt;Encoded &lt;i&gt;body&lt;/i&gt;.&lt;/p&gt;</content:encoded>",
    )
    feed = podcasts_service.parse_feed(feed_doc, FEED_URL)

    assert feed.episodes[0].description == "Encoded body."


def test_parse_feed_rejects_non_xml():
    with pytest.raises(FeedFetchError):
        podcasts_service.parse_feed(b"this is not xml", FEED_URL)


def test_parse_feed_rejects_doctype():
    doc = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "y">]>' + RSS_FEED.split(b"?>", 1)[1]
    with pytest.raises(FeedFetchError):
        podcasts_service.parse_feed(doc, FEED_URL)


def test_parse_feed_rejects_feed_without_title():
    with pytest.raises(FeedFetchError):
        podcasts_service.parse_feed(b"<rss><channel></channel></rss>", FEED_URL)


def test_normalize_feed_url_rejects_bad_urls():
    for bad in ("", "ftp://x/feed", "not a url", "https:///no-host"):
        with pytest.raises(FeedFetchError):
            podcasts_service.normalize_feed_url(bad)
    assert podcasts_service.normalize_feed_url("  https://a.example/f.xml ") == "https://a.example/f.xml"


def test_url_allowed_rejects_private_hosts():
    assert podcasts_service.url_allowed("http://127.0.0.1/feed") is False
    assert podcasts_service.url_allowed("http://169.254.169.254/latest") is False
    assert podcasts_service.url_allowed("file:///etc/passwd") is False
    assert podcasts_service.url_allowed("https://pod.example/feed") is True


async def test_subscribe_creates_podcast_episodes_and_subscription(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch, _fetch_result(etag='"abc"', last_modified="Mon, 01 Sep 2025"))

    podcast, created = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    assert created is True
    assert podcast.title == "Test Show"
    assert podcast.author == "Jane Doe"
    assert podcast.etag == '"abc"'
    assert podcast.last_fetched_at is not None
    assert podcast.last_error is None

    episodes, total = await podcasts_service.list_episodes(db_session, podcast.id)
    assert total == 2
    assert {e.guid for e in episodes} == {"ep-1", "ep-2"}

    assert await podcasts_service.is_subscribed(db_session, regular_user, podcast.id)


async def test_subscribe_is_idempotent(db_session, regular_user, config, monkeypatch):
    calls = []

    def _fetch(*a, **kw):
        calls.append(1)
        return _fetch_result()

    monkeypatch.setattr(podcasts_service, "fetch_feed", _fetch)

    podcast1, created1 = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    podcast2, created2 = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    assert created1 is True and created2 is False
    assert podcast1.id == podcast2.id
    assert len(calls) == 1


async def test_subscribe_second_user_shares_podcast_row(db_session, regular_user, other_user, config, monkeypatch):
    _stub_fetch(monkeypatch)

    podcast1, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    podcast2, created = await podcasts_service.subscribe(db_session, other_user, FEED_URL, config)

    assert created is True
    assert podcast1.id == podcast2.id
    subs = await podcasts_service.list_subscribed_podcasts(db_session, other_user)
    assert [entry.podcast.id for entry in subs[0]] == [podcast1.id]


async def test_subscribe_fetch_failure_drops_new_podcast(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch, FeedFetchError("boom"))

    with pytest.raises(FeedFetchError):
        await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    assert await podcasts_service.get_podcast_by_feed_url(db_session, FEED_URL) is None


async def test_refresh_skips_when_not_due(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    def _fail(*a, **kw):
        raise AssertionError("fetch_feed should not be called")

    monkeypatch.setattr(podcasts_service, "fetch_feed", _fail)
    assert await podcasts_service.refresh_podcast(db_session, podcast, config) == 0


async def test_refresh_304_keeps_catalog(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch, _fetch_result(etag='"abc"'))
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    podcast.last_fetched_at = datetime.now(timezone.utc) - timedelta(days=1)

    monkeypatch.setattr(podcasts_service, "fetch_feed", lambda *a, **kw: FetchResult(not_modified=True))
    assert await podcasts_service.refresh_podcast(db_session, podcast, config) == 0
    _episodes, total = await podcasts_service.list_episodes(db_session, podcast.id)
    assert total == 2
    assert podcast.last_error is None


async def test_refresh_updates_episodes(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    podcast.last_fetched_at = datetime.now(timezone.utc) - timedelta(days=1)

    updated = RSS_FEED.replace(b"ep-2", b"ep-2b").replace(
        b"<title>Episode Two</title>", b"<title>Episode Two B</title>"
    )
    monkeypatch.setattr(podcasts_service, "fetch_feed", lambda *a, **kw: _fetch_result(body=updated))

    new_count = await podcasts_service.refresh_podcast(db_session, podcast, config)
    assert new_count == 1
    episodes, _total = await podcasts_service.list_episodes(db_session, podcast.id)
    assert {e.title for e in episodes} == {"Episode One", "Episode Two B"}


async def test_refresh_failure_records_error(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    monkeypatch.setattr(
        podcasts_service,
        "fetch_feed",
        lambda *a, **kw: (_ for _ in ()).throw(FeedFetchError("HTTP 500")),
    )
    with pytest.raises(FeedFetchError):
        await podcasts_service.refresh_podcast(db_session, podcast, config, force=True)
    assert podcast.last_error == "HTTP 500"


async def test_due_podcast_ids_only_returns_followed_stale_feeds(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    interval = timedelta(hours=1)
    assert await podcasts_service.due_podcast_ids(db_session, interval) == []

    podcast.last_fetched_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.flush()
    assert await podcasts_service.due_podcast_ids(db_session, interval) == [podcast.id]

    # Unfollowed podcasts are never refreshed.
    await podcasts_service.unsubscribe(db_session, regular_user, podcast.id)
    await db_session.flush()
    assert await podcasts_service.due_podcast_ids(db_session, interval) == []


def test_opml_roundtrip():
    p1 = Podcast(feed_url="https://a.example/feed", title="Alpha", link="https://a.example")
    p2 = Podcast(feed_url="https://b.example/rss", title="Beta & Gamma")

    document = podcasts_service.render_opml("My podcasts", [p1, p2])
    entries = podcasts_service.parse_opml(document.encode())

    assert entries == [
        ("https://a.example/feed", "Alpha"),
        ("https://b.example/rss", "Beta & Gamma"),
    ]


def test_parse_opml_nested_folders_and_dedupe():
    opml = b"""<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Folder">
        <outline type="rss" text="A" xmlUrl="https://a.example/f"/>
      </outline>
      <outline type="rss" text="B" xmlUrl="https://b.example/f"/>
      <outline type="rss" text="A2" xmlUrl="https://a.example/f"/>
      <outline text="https://not-a-feed.example"/>
    </body></opml>"""
    entries = podcasts_service.parse_opml(opml)
    assert entries == [("https://a.example/f", "A"), ("https://b.example/f", "B")]


def test_parse_opml_rejects_non_opml():
    with pytest.raises(FeedFetchError):
        podcasts_service.parse_opml(b"<html></html>")
    with pytest.raises(FeedFetchError):
        podcasts_service.parse_opml(b"not xml")


def test_follow_podcast_api(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    response = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Test Show"
    assert body["feed_url"] == FEED_URL
    assert body["following"] is True
    assert body["episode_count"] == 2


def test_follow_podcast_requires_auth(client):
    response = client.post("/api/v1/podcasts/", json={"feed_url": FEED_URL})
    assert response.status_code == 401


def test_follow_invalid_feed_url_returns_422(client, regular_user, auth_headers):
    response = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": "not-a-url"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_follow_unreachable_feed_returns_422(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch, FeedFetchError("connection refused"))
    response = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_list_podcasts_returns_only_own_subscriptions(client, regular_user, other_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    )

    mine = client.get("/api/v1/podcasts/", headers=auth_headers(regular_user))
    assert [p["title"] for p in mine.json()] == ["Test Show"]

    theirs = client.get("/api/v1/podcasts/", headers=auth_headers(other_user))
    assert theirs.json() == []


def test_episodes_endpoint_lists_remote_audio(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    ).json()

    response = client.get(f"/api/v1/podcasts/{podcast['id']}/episodes", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    episodes = response.json()
    # Newest first.
    assert [e["title"] for e in episodes] == ["Episode Two", "Episode One"]
    assert episodes[0]["audio_url"] == "https://cdn.pod.example/ep2.mp3"
    assert episodes[0]["duration_seconds"] == 3600


def test_unfollow_podcast(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    ).json()

    response = client.delete(f"/api/v1/podcasts/{podcast['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 204
    assert client.get("/api/v1/podcasts/", headers=auth_headers(regular_user)).json() == []
    # The shared podcast row stays — other subscribers would be unaffected.
    assert (
        client.get(f"/api/v1/podcasts/{podcast['id']}", headers=auth_headers(regular_user)).json()["following"] is False
    )


def test_unfollow_not_subscribed_returns_404(client, regular_user, other_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    ).json()

    response = client.delete(f"/api/v1/podcasts/{podcast['id']}", headers=auth_headers(other_user))
    assert response.status_code == 404


def test_refresh_endpoint(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    ).json()

    response = client.post(f"/api/v1/podcasts/{podcast['id']}/refresh", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["episode_count"] == 2


def test_opml_export_and_import(client, regular_user, other_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    client.post(
        "/api/v1/podcasts/",
        json={"feed_url": FEED_URL},
        headers=auth_headers(regular_user),
    )

    exported = client.get("/api/v1/podcasts/opml", headers=auth_headers(regular_user))
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/x-opml")
    assert "attachment" in exported.headers["content-disposition"]
    assert FEED_URL in exported.text

    imported = client.post(
        "/api/v1/podcasts/opml/import",
        files={"file": ("subs.opml", exported.content, "text/x-opml")},
        headers=auth_headers(other_user),
    )
    assert imported.status_code == 200
    result = imported.json()
    assert result["subscribed"] == 1
    assert result["failed"] == 0

    theirs = client.get("/api/v1/podcasts/", headers=auth_headers(other_user))
    assert [p["feed_url"] for p in theirs.json()] == [FEED_URL]


def test_opml_import_bad_document(client, regular_user, auth_headers):
    response = client.post(
        "/api/v1/podcasts/opml/import",
        files={"file": ("subs.opml", b"<html>not opml</html>", "text/x-opml")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_podcasts_disabled_returns_404(client, regular_user, auth_headers, app):
    app.state.config.podcasts.enabled = False
    try:
        response = client.get("/api/v1/podcasts/", headers=auth_headers(regular_user))
        assert response.status_code == 404
    finally:
        app.state.config.podcasts.enabled = True


RSS_FEED_B = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Another Show</title>
    <description>Second podcast.</description>
    <item>
      <title>B Episode</title>
      <guid>b-ep-1</guid>
      <pubDate>Wed, 01 Oct 2025 10:00:00 GMT</pubDate>
      <enclosure url="https://cdn.b.example/e1.mp3" type="audio/mpeg" length="5"/>
    </item>
  </channel>
</rss>
"""

FEED_URL_B = "https://b.example/feed.xml"


def _follow(client, headers, feed_url=FEED_URL):
    response = client.post("/api/v1/podcasts/", json={"feed_url": feed_url}, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_list_subscribed_podcasts_returns_stats(db_session, regular_user, other_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)

    entries, total = await podcasts_service.list_subscribed_podcasts(db_session, regular_user)
    assert total == 1
    entry = entries[0]
    assert entry.podcast.id == podcast.id
    assert entry.stats.episode_count == 2
    assert entry.stats.unplayed_count == 2
    assert entry.stats.latest_episode_at == datetime(2025, 9, 8, 10, 0, tzinfo=timezone.utc)

    episodes, _ = await podcasts_service.list_episodes(db_session, podcast.id)
    await podcasts_service.mark_episode_played(db_session, regular_user, episodes[0].id)
    stats = await podcasts_service.podcast_stats(db_session, regular_user, [podcast.id])
    assert stats[podcast.id].unplayed_count == 1

    # Another user's plays do not affect this user's counters.
    stats_other = await podcasts_service.podcast_stats(db_session, other_user, [podcast.id])
    assert stats_other[podcast.id].unplayed_count == 2


async def test_mark_episode_played_idempotent_and_unplayed(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    episodes, _ = await podcasts_service.list_episodes(db_session, podcast.id)
    episode = episodes[0]

    assert await podcasts_service.mark_episode_played(db_session, regular_user, episode.id) is not None
    assert await podcasts_service.mark_episode_played(db_session, regular_user, episode.id) is not None
    assert await podcasts_service.played_episode_ids(db_session, regular_user, [episode.id]) == {episode.id}

    assert await podcasts_service.mark_episode_unplayed(db_session, regular_user, episode.id) is not None
    assert await podcasts_service.played_episode_ids(db_session, regular_user, [episode.id]) == set()

    assert await podcasts_service.mark_episode_played(db_session, regular_user, "missing") is None


async def test_list_subscribed_podcasts_search_and_sort(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch, RSS_FEED)
    podcast_a, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    _stub_fetch(monkeypatch, RSS_FEED_B)
    await podcasts_service.subscribe(db_session, regular_user, FEED_URL_B, config)

    async def _titles(**kwargs):
        entries, _ = await podcasts_service.list_subscribed_podcasts(db_session, regular_user, **kwargs)
        return [entry.podcast.title for entry in entries]

    # Default: newest episode first (B's episode is newer than A's).
    assert await _titles() == ["Another Show", "Test Show"]
    assert await _titles(sort_by="name") == ["Another Show", "Test Show"]
    assert await _titles(sort_by="name", sort_dir="desc") == ["Test Show", "Another Show"]
    assert await _titles(sort_by="episodes") == ["Test Show", "Another Show"]
    assert await _titles(sort_by="episodes", sort_dir="asc") == ["Another Show", "Test Show"]

    # Search matches title, author and description.
    assert await _titles(search="test") == ["Test Show"]
    assert await _titles(search="another") == ["Another Show"]
    assert await _titles(search="zzz") == []

    # Unplayed sorting reacts to played marks.
    episodes_a, _ = await podcasts_service.list_episodes(db_session, podcast_a.id)
    for episode in episodes_a:
        await podcasts_service.mark_episode_played(db_session, regular_user, episode.id)
    assert await _titles(sort_by="unplayed") == ["Another Show", "Test Show"]

    entries, _ = await podcasts_service.list_subscribed_podcasts(db_session, regular_user, search="Test")
    assert entries[0].stats.unplayed_count == 0


def test_list_podcasts_search_and_sort_api(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch, RSS_FEED)
    _follow(client, auth_headers(regular_user))
    _stub_fetch(monkeypatch, RSS_FEED_B)
    _follow(client, auth_headers(regular_user), FEED_URL_B)

    response = client.get("/api/v1/podcasts/", headers=auth_headers(regular_user))
    assert [p["title"] for p in response.json()] == ["Another Show", "Test Show"]
    assert response.json()[1]["latest_episode_at"].startswith("2025-09-08")

    response = client.get("/api/v1/podcasts/?q=test", headers=auth_headers(regular_user))
    assert [p["title"] for p in response.json()] == ["Test Show"]

    response = client.get("/api/v1/podcasts/?sort_by=episodes", headers=auth_headers(regular_user))
    assert [p["title"] for p in response.json()] == ["Test Show", "Another Show"]

    response = client.get("/api/v1/podcasts/?sort_by=bogus", headers=auth_headers(regular_user))
    assert response.status_code == 422


def test_episode_played_state_api(client, regular_user, other_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = _follow(client, auth_headers(regular_user))
    episodes = client.get(f"/api/v1/podcasts/{podcast['id']}/episodes", headers=auth_headers(regular_user)).json()
    episode = episodes[0]
    assert episode["played"] is False
    assert podcast["unplayed_count"] == 2

    response = client.post(f"/api/v1/podcasts/episodes/{episode['id']}/played", headers=auth_headers(regular_user))
    assert response.status_code == 204
    # Idempotent.
    assert (
        client.post(f"/api/v1/podcasts/episodes/{episode['id']}/played", headers=auth_headers(regular_user)).status_code
        == 204
    )

    episodes = client.get(f"/api/v1/podcasts/{podcast['id']}/episodes", headers=auth_headers(regular_user)).json()
    assert [e["played"] for e in episodes] == [e["id"] == episode["id"] for e in episodes]

    listed = client.get("/api/v1/podcasts/", headers=auth_headers(regular_user)).json()
    assert listed[0]["unplayed_count"] == 1

    # Play marks are per-user.
    theirs = client.get(f"/api/v1/podcasts/{podcast['id']}/episodes", headers=auth_headers(other_user)).json()
    assert all(e["played"] is False for e in theirs)

    response = client.delete(f"/api/v1/podcasts/episodes/{episode['id']}/played", headers=auth_headers(regular_user))
    assert response.status_code == 204
    listed = client.get("/api/v1/podcasts/", headers=auth_headers(regular_user)).json()
    assert listed[0]["unplayed_count"] == 2


def test_episode_played_unknown_episode_404(client, regular_user, auth_headers):
    response = client.post("/api/v1/podcasts/episodes/nope/played", headers=auth_headers(regular_user))
    assert response.status_code == 404
    assert client.delete("/api/v1/podcasts/episodes/nope/played", headers=auth_headers(regular_user)).status_code == 404


def test_episode_played_requires_auth(client):
    assert client.post("/api/v1/podcasts/episodes/x/played").status_code == 401


def test_get_episode_api(client, regular_user, auth_headers, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast = _follow(client, auth_headers(regular_user))
    episodes = client.get(f"/api/v1/podcasts/{podcast['id']}/episodes", headers=auth_headers(regular_user)).json()
    episode = episodes[0]

    response = client.get(f"/api/v1/podcasts/episodes/{episode['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["id"] == episode["id"]
    assert response.json()["played"] is False

    client.post(f"/api/v1/podcasts/episodes/{episode['id']}/played", headers=auth_headers(regular_user))
    response = client.get(f"/api/v1/podcasts/episodes/{episode['id']}", headers=auth_headers(regular_user))
    assert response.json()["played"] is True

    assert client.get("/api/v1/podcasts/episodes/nope", headers=auth_headers(regular_user)).status_code == 404
    assert client.get(f"/api/v1/podcasts/episodes/{episode['id']}").status_code == 401
