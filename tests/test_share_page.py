"""
Tests for the public share-link HTML renderer (api/share_page.py).
"""

from songhive.api import share_page
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.radio import Radio
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User


class _Request:
    """Minimal stand-in for ``fastapi.Request`` (only ``base_url`` is used)."""

    base_url = "https://music.example/"


REQUEST = _Request()
TOKEN = "tok-123"


def test_h_escapes_and_handles_none():
    assert share_page._h("<b>&\"'</b>") == "&lt;b&gt;&amp;&quot;&#x27;&lt;/b&gt;"
    assert share_page._h(None) == ""
    assert share_page._h(42) == "42"


def test_tag_drops_none_attributes_and_escapes():
    tag = share_page._tag("meta", {"property": "og:title", "content": None, "data-x": '<"'})
    assert tag == '<meta property="og:title" data-x="&lt;&quot;">'


def test_base_and_abs_url():
    assert share_page._base_url(REQUEST) == "https://music.example"
    assert share_page._abs_url(REQUEST, "/x") == "https://music.example/x"


def test_file_url():
    assert (
        share_page._file_url("f1", "tok", "attachment") == "/api/v1/files/f1/download?token=tok&disposition=attachment"
    )


def test_format_duration():
    assert share_page._format_duration(None) == ""
    assert share_page._format_duration(0) == "0:00"
    assert share_page._format_duration(61.9) == "1:01"
    assert share_page._format_duration(3600) == "60:00"


def test_human_size():
    assert share_page._human_size(None) == ""
    assert share_page._human_size(512) == "512 B"
    assert share_page._human_size(2048) == "2.0 KB"
    assert share_page._human_size(5 * 1024 * 1024) == "5.0 MB"
    assert share_page._human_size(1024**4) == "1.0 TB"
    assert share_page._human_size(3 * 1024**5) == "3.0 PB"


def _make_track(**kwargs) -> Track:
    track = Track(title=kwargs.pop("title", "Song"))
    for key, value in kwargs.items():
        setattr(track, key, value)
    return track


def test_track_audio_url_plain_file():
    track = _make_track(audio_file_id="af-1")
    assert share_page._track_audio_url(track, TOKEN) == (
        f"/api/v1/files/af-1/download?token={TOKEN}&disposition=inline"
    )
    assert share_page._track_audio_url(track, TOKEN, "attachment").endswith("disposition=attachment")


def test_track_audio_url_no_file():
    track = _make_track()
    track.audio_file_id = None
    assert share_page._track_audio_url(track, TOKEN) is None


def test_track_audio_url_external_streamable():
    track = _make_track()
    track.id = "t-ext"
    track.audio_file_id = None
    assert share_page._track_audio_url(track, TOKEN, streamable_ids={"t-ext"}) == (
        f"/api/v1/stream/t-ext?token={TOKEN}"
    )
    # External bytes are never offered as a download through share links.
    assert share_page._track_audio_url(track, TOKEN, "attachment", streamable_ids={"t-ext"}) is None
    # Tracks outside the streamable set get no URL.
    assert share_page._track_audio_url(track, TOKEN, streamable_ids=set()) is None


def test_track_audio_url_gated():
    track = _make_track(audio_file_id="af-1")
    track.id = "t-gated"
    gated = {"t-gated"}
    assert share_page._track_audio_url(track, TOKEN, gated_ids=gated) == (
        f"/api/v1/payments/stream/{track.id}?token={TOKEN}"
    )
    # Gated tracks never expose a download URL.
    assert share_page._track_audio_url(track, TOKEN, "attachment", gated_ids=gated) is None
    # Ungated tracks are unaffected by a gated set.
    assert share_page._track_audio_url(track, TOKEN, gated_ids=set()).startswith("/api/v1/files/")


def test_track_cover_url_from_album():
    album = Album(title="A", cover_url="https://cdn.example/cover.jpg")
    track = _make_track()
    track.album = album
    assert share_page._track_cover_url(track) == "https://cdn.example/cover.jpg"
    track.album = None
    assert share_page._track_cover_url(track) is None


def test_render_track_page():
    artist = Artist(name="The <Band>")
    album = Album(title="Alb", artist=artist)
    track = _make_track(title="Song <One>", duration=200.0, audio_file_id="af-1")
    track.artist = artist
    track.album = album

    html = share_page.render_share_page(track, "track", TOKEN, REQUEST)
    assert "Song &lt;One&gt;" in html
    assert "The &lt;Band&gt;" in html
    assert 'property="og:type" content="music.song"' in html
    assert 'property="og:audio"' in html
    assert f"/api/v1/files/af-1/download?token={TOKEN}&amp;disposition=inline" in html
    assert "3:20" in html
    assert "Download" in html


def test_render_track_without_file_or_relations():
    track = _make_track()
    track.audio_file_id = None
    track.artist = None
    track.album = None

    html = share_page.render_share_page(track, "track", TOKEN, REQUEST)
    assert "Unknown artist" in html
    assert "<audio" not in html
    assert 'property="og:audio"' not in html


def test_render_external_track_uses_stream_endpoint():
    track = _make_track(title="Remote", duration=95.0)
    track.id = "t-ext"
    track.audio_file_id = None

    html = share_page.render_share_page(track, "track", TOKEN, REQUEST, streamable_ids={"t-ext"})
    assert f'<audio controls src="/api/v1/stream/t-ext?token={TOKEN}"' in html
    assert 'property="og:audio" content="https://music.example/api/v1/stream/t-ext' in html
    assert "Download" not in html


def test_render_track_gated_uses_stream_endpoint():
    track = _make_track(title="Gated", audio_file_id="af-9")
    track.id = "t-9"
    html = share_page.render_share_page(track, "track", TOKEN, REQUEST, gated_ids={"t-9"})
    assert f"/api/v1/payments/stream/t-9?token={TOKEN}" in html
    assert "af-9" not in html
    assert "Download" not in html


def test_render_album_page():
    artist = Artist(name="Alb Artist")
    album = Album(
        title="Alb",
        artist=artist,
        release_year=1999,
        description="An album.",
        cover_url="https://cdn.example/alb.jpg",
    )
    t1 = _make_track(title="Second", track_number=2, disc_number=1)
    t2 = _make_track(title="First", track_number=1, disc_number=1)
    t1.artist = artist
    t2.artist = artist
    album.tracks = [t1, t2]

    html = share_page.render_share_page(album, "album", TOKEN, REQUEST)
    assert 'property="og:type" content="music.album"' in html
    assert 'property="og:image" content="https://cdn.example/alb.jpg"' in html
    assert "1999" in html
    assert "An album." in html
    # Tracks are sorted by disc/track number.
    assert html.index("First") < html.index("Second")


def test_render_album_cover_from_stored_file():
    album = Album(title="Alb", cover_url=None)
    album.cover_file_id = "cf-1"
    album.tracks = []
    html = share_page.render_share_page(album, "album", TOKEN, REQUEST)
    assert f"https://music.example/api/v1/files/cf-1/download?token={TOKEN}" in html


def test_render_artist_page():
    artist = Artist(name="Art", image_url="/images/a.jpg")
    track = _make_track(title="Song", track_number=1)
    track.artist = artist
    track.album = None
    artist.tracks = [track]

    html = share_page.render_share_page(artist, "artist", TOKEN, REQUEST)
    assert 'property="og:type" content="music.artist"' in html
    # Relative image URLs are made absolute.
    assert 'property="og:image" content="https://music.example/images/a.jpg"' in html
    assert "Song" in html


def test_render_playlist_page():
    owner = User(username="dj", email="dj@example.com", password_hash="x")
    playlist = Playlist(name="Mix", description="A mix.", owner=owner)
    track = _make_track(title="Listed")
    pt = PlaylistTrack(position=0)
    pt.track = track
    playlist.tracks = [pt]

    html = share_page.render_share_page(playlist, "playlist", TOKEN, REQUEST)
    assert 'property="og:type" content="music.playlist"' in html
    assert "Mix" in html
    assert "dj" in html
    assert "Listed" in html


def test_render_library_page():
    from songhive.models.library import Library

    owner = User(username="lib", email="lib@example.com", password_hash="x")
    library = Library(name="Crate", owner=owner)
    track = _make_track(title="LibSong")
    library.tracks = [track]

    html = share_page.render_share_page(library, "library", TOKEN, REQUEST)
    assert "Crate" in html
    assert "LibSong" in html


def test_render_radio_page():
    owner = User(username="host", email="h@example.com", password_hash="x")
    radio = Radio(
        name="Waves",
        description="All day.",
        owner=owner,
        visibility=Visibility.PUBLIC.value,
    )
    html = share_page.render_share_page(radio, "radio", TOKEN, REQUEST)
    assert "Waves" in html
    assert "Radio station" in html
    assert "host" in html


def test_render_file_page_audio():
    item = StoredFile(
        original_filename="tune.mp3",
        content_type="audio/mpeg",
        size=2048,
    )
    html = share_page.render_share_page(item, "file", TOKEN, REQUEST)
    assert "tune.mp3" in html
    assert 'property="og:type" content="audio"' in html
    assert "<audio" in html
    assert "2.0 KB" in html
    assert "Download" in html


def test_render_file_page_non_audio():
    item = StoredFile(
        original_filename="cover.png",
        content_type="image/png",
        size=100,
    )
    html = share_page.render_share_page(item, "file", TOKEN, REQUEST)
    assert "cover.png" in html
    assert 'property="og:type" content="website"' in html
    assert "<audio" not in html


def test_render_file_without_filename():
    item = StoredFile(original_filename=None, content_type="application/pdf", size=10)
    item.id = "f-x"
    html = share_page.render_share_page(item, "file", TOKEN, REQUEST)
    assert "File f-x" in html


def test_render_missing_for_none_and_unknown_type():
    html = share_page.render_share_page(None, "track", TOKEN, REQUEST)
    assert "Link not found" in html
    assert "invalid, expired, or has been revoked" in html

    html = share_page.render_share_page(object(), "bogus", TOKEN, REQUEST)
    assert "Link not found" in html


def test_render_escapes_untrusted_values():
    track = _make_track(title='<script>alert("x")</script>')
    track.artist = Artist(name='<img src=x onerror="x">')
    track.album = None
    html = share_page.render_share_page(track, "track", TOKEN, REQUEST)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
