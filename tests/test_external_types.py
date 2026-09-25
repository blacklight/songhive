"""Tests for shared external-library dataclasses."""

from songhive.external.types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistEntry,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)


def test_track_metadata_backward_compatible_defaults() -> None:
    metadata = ExternalTrackMetadata(title="T", artist="A", album="B", album_artist="AA")
    assert metadata.artists == ()
    assert metadata.album_artists == ()
    assert metadata.genres == ()
    assert metadata.cover_url is None
    assert metadata.description is None
    assert metadata.composer is None
    assert metadata.disc_count is None
    assert metadata.provider_ids == {}


def test_track_metadata_entity_fields() -> None:
    metadata = ExternalTrackMetadata(
        title="T",
        artist="A",
        album="B",
        album_artist="AA",
        artists=("A", "B"),
        genres=("rock", "pop"),
        provider_ids={"MusicBrainzTrack": "x"},
    )
    assert metadata.artists == ("A", "B")
    assert metadata.genres == ("rock", "pop")
    assert metadata.provider_ids["MusicBrainzTrack"] == "x"


def test_item_ref_inline_metadata() -> None:
    metadata = ExternalTrackMetadata(title="T", artist="A", album="B", album_artist="AA")
    ref = ExternalItemRef(provider_key="k", display_path="d", metadata=metadata)
    assert ref.metadata is metadata
    bare = ExternalItemRef(provider_key="k", display_path="d")
    assert bare.metadata is None


def test_entity_metadata_dataclasses() -> None:
    album = ExternalAlbumMetadata(
        provider_key="a1",
        title="Al",
        artist_names=("X",),
        artist_provider_keys=("x1",),
        release_year=2001,
        genres=("jazz",),
    )
    assert album.provider_key == "a1"
    assert album.artist_provider_keys == ("x1",)

    artist = ExternalArtistMetadata(provider_key="ar1", name="X", image_url="https://x/img")
    assert artist.name == "X"

    playlist = ExternalPlaylistMetadata(
        provider_key="p1",
        title="Pl",
        entries=(ExternalPlaylistEntry(position=0, track_provider_key="t1"),),
    )
    assert playlist.entries[0].track_provider_key == "t1"


def test_capabilities_entity_flags_default_off() -> None:
    capabilities = ExternalLibraryCapabilities()
    assert capabilities.list_albums is False
    assert capabilities.list_artists is False
    assert capabilities.list_playlists is False
