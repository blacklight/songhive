"""
Metadata extraction tests.
"""

import base64
import builtins
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import mutagen
import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

from songhive.music.metadata import AudioMetadata as MusicAudioMetadata
from songhive.music.metadata import extract_metadata as music_extract_metadata
from songhive.services.metadata import (
    AudioMetadata,
    _extract_apic_frame,
    _extract_cover_art,
    _extract_covr_atom,
    _extract_id3_tags,
    _extract_metadata_block_picture,
    _extract_pictures_attr,
    _extract_raw_tags,
    _extract_vorbis_tags,
    _first,
    _guess_image_mime,
    _int,
    _stringify,
    _year,
    extract_metadata,
)


def _make_silence(tmp_path: Path, ext: str) -> Path:
    """Generate a short sine-wave audio file in the requested format."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    path = tmp_path / f"sample.{ext}"
    codec = {"mp3": "libmp3lame", "flac": "flac", "ogg": "libvorbis", "m4a": "aac"}[ext]
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def tagged_mp3(tmp_path):
    """Create a minimal MP3 with ID3 tags and embedded cover art."""
    path = _make_silence(tmp_path, "mp3")
    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags["TIT2"] = TIT2(encoding=3, text="Test Title")
    audio.tags["TPE1"] = TPE1(encoding=3, text="Test Artist")
    audio.tags["TALB"] = TALB(encoding=3, text="Test Album")
    audio.tags["APIC"] = APIC(
        encoding=3,
        mime="image/jpeg",
        type=3,
        desc="cover",
        data=b"\xff\xd8\xff\xe0fake cover",
    )
    audio.save()
    return path


@pytest.fixture
def tagged_flac(tmp_path):
    """Create a minimal FLAC with vorbis comments and an embedded picture."""
    path = _make_silence(tmp_path, "flac")
    audio = FLAC(str(path))
    audio["TITLE"] = "FLAC Title"
    audio["ARTIST"] = "FLAC Artist"
    audio["ALBUM"] = "FLAC Album"
    audio["TRACKNUMBER"] = "5/10"
    audio["DISCNUMBER"] = "1/2"
    audio["GENRE"] = "Rock"
    audio["DATE"] = "2018-07-04"

    pic = Picture()
    pic.type = 3
    pic.mime = "image/png"
    pic.desc = "cover"
    pic.data = b"\x89PNG\r\n\x1a\nflac"
    audio.add_picture(pic)
    audio.save()
    return path


@pytest.fixture
def tagged_ogg(tmp_path):
    """Create a minimal Ogg Vorbis file with a METADATA_BLOCK_PICTURE."""
    path = _make_silence(tmp_path, "ogg")
    audio = OggVorbis(str(path))
    audio["TITLE"] = "Ogg Title"
    audio["ARTIST"] = "Ogg Artist"
    audio["ALBUM"] = "Ogg Album"
    audio["TRACKNUMBER"] = "2/10"
    audio["DISCNUMBER"] = "1/2"
    audio["GENRE"] = "Pop"
    audio["DATE"] = "2020/06"

    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.desc = "cover"
    pic.data = b"\xff\xd8\xff\xe0ogg"
    audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()
    return path


@pytest.fixture
def tagged_mp4(tmp_path):
    """Create a minimal M4A/MP4 file with a covr atom."""
    path = _make_silence(tmp_path, "m4a")
    audio = MP4(str(path))
    audio["\xa9nam"] = ["MP4 Title"]
    audio["\xa9ART"] = ["MP4 Artist"]
    audio["\xa9alb"] = ["MP4 Album"]
    audio["trkn"] = [(3, 10)]
    audio["disk"] = [(1, 2)]
    audio["\xa9gen"] = ["Jazz"]
    audio["\xa9day"] = ["2017-03-25"]
    audio["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0mp4", imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
    return path


@pytest.fixture
def empty_flac(tmp_path):
    """Create a FLAC file with no tags or pictures."""
    path = _make_silence(tmp_path, "flac")
    audio = FLAC(str(path))
    audio.delete()
    audio.save()
    return path


def test_extract_metadata_nonexistent():
    """Test metadata extraction gracefully handles missing files."""
    result = extract_metadata(Path("/nonexistent/file.mp3"))
    assert isinstance(result, AudioMetadata)
    assert result.title is None


def test_extract_metadata_bad_audio(tmp_path):
    """Non-audio files are handled gracefully and return an empty result."""
    path = tmp_path / "noise.mp3"
    path.write_text("not an audio file")
    result = extract_metadata(path)
    assert isinstance(result, AudioMetadata)
    assert result.title is None
    assert result.mimetype is None
    assert result.raw_tags == {}


def test_extract_metadata_missing_mutagen(monkeypatch, tmp_path):
    """Missing mutagen results in an empty AudioMetadata object."""
    original = builtins.__import__

    def _fail(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mutagen" and not fromlist:
            raise ImportError("No module named 'mutagen'")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fail)
    result = extract_metadata(tmp_path / "whatever.mp3")
    assert isinstance(result, AudioMetadata)
    assert result.title is None
    assert result.cover_art is None
    assert result.raw_tags == {}


def test_extract_metadata_raw_load_fails(monkeypatch, tagged_mp3):
    """A failing raw-tag load still allows easy tags to be returned."""
    original_file = mutagen.File

    def _fake(filename, easy=False):
        if easy:
            return original_file(filename, easy=True)
        raise ValueError("raw load failed")

    monkeypatch.setattr(mutagen, "File", _fake)
    result = extract_metadata(tagged_mp3)
    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.cover_art is None
    assert result.raw_tags == {}


def test_extract_metadata_easy_is_none(monkeypatch, tagged_mp3):
    """When easy tags are unavailable, fall back to raw tag information."""
    real_raw = mutagen.File(str(tagged_mp3), easy=False)

    def _fake(filename, easy=False):
        if easy:
            return None
        return real_raw

    monkeypatch.setattr(mutagen, "File", _fake)
    result = extract_metadata(tagged_mp3)
    assert result.title is None
    assert result.artist is None
    assert result.mimetype == "audio/mp3"
    assert result.duration is not None
    assert result.cover_art == b"\xff\xd8\xff\xe0fake cover"
    assert result.cover_art_mime == "image/jpeg"
    assert "TIT2" in result.raw_tags


def test_extract_metadata_parses_tags_and_cover(tagged_mp3):
    """extract_metadata returns tags, cover art and raw tags."""
    result = extract_metadata(tagged_mp3)
    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.cover_art == b"\xff\xd8\xff\xe0fake cover"
    assert result.cover_art_mime == "image/jpeg"
    assert "TIT2" in result.raw_tags
    assert "APIC" not in result.raw_tags
    assert result.mimetype == "audio/mp3"


def test_extract_metadata_flac(tagged_flac):
    """FLAC vorbis comments, pictures and common fields are parsed."""
    result = extract_metadata(tagged_flac)
    assert result.title == "FLAC Title"
    assert result.artist == "FLAC Artist"
    assert result.album == "FLAC Album"
    assert result.track_number == 5
    assert result.disc_number == 1
    assert result.genre == "Rock"
    assert result.year == 2018
    assert result.cover_art == b"\x89PNG\r\n\x1a\nflac"
    assert result.cover_art_mime == "image/png"
    assert "title" in result.raw_tags
    assert result.mimetype == "audio/flac"


def test_extract_metadata_ogg(tagged_ogg):
    """Ogg Vorbis METADATA_BLOCK_PICTURE and tags are parsed."""
    result = extract_metadata(tagged_ogg)
    assert result.title == "Ogg Title"
    assert result.artist == "Ogg Artist"
    assert result.album == "Ogg Album"
    assert result.track_number == 2
    assert result.disc_number == 1
    assert result.genre == "Pop"
    assert result.year == 2020
    assert result.cover_art == b"\xff\xd8\xff\xe0ogg"
    assert result.cover_art_mime == "image/jpeg"
    assert "metadata_block_picture" in result.raw_tags
    assert result.mimetype == "audio/vorbis"


def test_extract_metadata_mp4(tagged_mp4):
    """MP4 covr atom and iTunes-style tags are parsed."""
    result = extract_metadata(tagged_mp4)
    assert result.title == "MP4 Title"
    assert result.artist == "MP4 Artist"
    assert result.album == "MP4 Album"
    assert result.track_number == 3
    assert result.disc_number == 1
    assert result.genre == "Jazz"
    assert result.year == 2017
    assert result.cover_art == b"\xff\xd8\xff\xe0mp4"
    assert result.cover_art_mime == "image/jpeg"
    assert "\xa9nam" in result.raw_tags
    assert "covr" not in result.raw_tags
    assert result.mimetype == "audio/mp4"


def test_extract_metadata_empty_tags(empty_flac):
    """A file with no metadata returns a minimal but valid result."""
    result = extract_metadata(empty_flac)
    assert result.title is None
    assert result.artist is None
    assert result.duration == 0.5
    assert result.raw_tags == {}


def test_music_metadata_module(tagged_mp3):
    """songhive.music.metadata re-exports a working extract_metadata."""
    result = music_extract_metadata(tagged_mp3)
    assert isinstance(result, MusicAudioMetadata)
    assert result.title == "Test Title"
    assert result.cover_art == b"\xff\xd8\xff\xe0fake cover"


def test_first():
    """_first returns the first value as a string, handling non-strings."""
    assert _first([]) is None
    assert _first(["x"]) == "x"
    assert _first([123]) == "123"
    assert _first([b"abc"]) == "b'abc'"


def test_int():
    """_int parses the first numeric component, ignoring totals."""
    assert _int(["2/10"]) == 2
    assert _int(["10"]) == 10
    assert _int(["foo"]) is None
    assert _int([""]) is None
    assert _int([]) is None


def test_year():
    """_year extracts a four-digit year from various date formats."""
    assert _year(["2022/04"]) == 2022
    assert _year(["2022-05-15"]) == 2022
    assert _year(["2018"]) == 2018
    assert _year(["no-year"]) is None
    assert _year([""]) is None
    assert _year([]) is None


def test_guess_image_mime():
    """_guess_image_mime identifies JPEG and PNG magic bytes."""
    assert _guess_image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert _guess_image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert _guess_image_mime(b"unknown") == "image/jpeg"


def test_stringify():
    """_stringify converts values to JSON-safe strings."""
    assert _stringify(b"abc") == base64.b64encode(b"abc").decode("ascii")
    assert _stringify([1, "two"]) == "1,two"
    assert _stringify((1, "two")) == "1,two"
    assert _stringify(None) is None
    assert _stringify("x") == "x"
    assert _stringify(123) == "123"


def test_extract_pictures_attr():
    """_extract_pictures_attr reads the first FLAC-style picture."""
    pic = Picture()
    pic.data = b"x"
    pic.mime = "image/png"
    audio = SimpleNamespace(pictures=[pic])
    data, mime = _extract_pictures_attr(audio)
    assert data == b"x"
    assert mime == "image/png"

    assert _extract_pictures_attr(SimpleNamespace(pictures=[])) is None
    assert _extract_pictures_attr(SimpleNamespace(pictures=None)) is None
    assert _extract_pictures_attr(object()) is None


def test_extract_metadata_block_picture():
    """_extract_metadata_block_picture decodes a base64 FLAC picture block."""
    pic = Picture()
    pic.type = 3
    pic.mime = "image/png"
    pic.desc = "cover"
    pic.data = b"\x89PNG\r\n\x1a\npic"
    b64 = base64.b64encode(pic.write()).decode("ascii")

    audio = SimpleNamespace(tags={"METADATA_BLOCK_PICTURE": [b64]})
    data, mime = _extract_metadata_block_picture(audio)
    assert data == pic.data
    assert mime == pic.mime


def test_extract_metadata_block_picture_invalid():
    """_extract_metadata_block_picture handles invalid or missing picture data."""
    audio = SimpleNamespace(tags={"METADATA_BLOCK_PICTURE": ["not-base64!!!"]})
    assert _extract_metadata_block_picture(audio) is None

    audio2 = SimpleNamespace(tags={"METADATA_BLOCK_PICTURE": ["aGVsbG8="]})
    assert _extract_metadata_block_picture(audio2) is None

    audio3 = SimpleNamespace(tags={})
    assert _extract_metadata_block_picture(audio3) is None


def test_extract_covr_atom():
    """_extract_covr_atom reads the first covr atom and guesses the MIME type."""
    jpeg = b"\xff\xd8\xff\xe0fake"
    png = b"\x89PNG\r\n\x1a\nfake"

    assert _extract_covr_atom(SimpleNamespace(tags={"covr": [jpeg]})) == (jpeg, "image/jpeg")
    assert _extract_covr_atom(SimpleNamespace(tags={"covr": [png]})) == (png, "image/png")
    assert _extract_covr_atom(SimpleNamespace(tags={})) is None


def test_extract_apic_frame():
    """_extract_apic_frame returns the first APIC frame data and MIME type."""
    id3 = ID3()
    data = b"\xff\xd8\xff\xe0fake"
    id3["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=data)
    audio = SimpleNamespace(tags=id3)
    assert _extract_apic_frame(audio) == (data, "image/jpeg")


def test_extract_apic_frame_no_apic():
    """_extract_apic_frame returns None when no APIC frame is present."""
    id3 = ID3()
    id3["TIT2"] = TIT2(encoding=3, text="title")
    audio = SimpleNamespace(tags=id3)
    assert _extract_apic_frame(audio) is None
    assert _extract_apic_frame(SimpleNamespace(tags=None)) is None


def test_extract_apic_frame_import_error(monkeypatch):
    """_extract_apic_frame tolerates a failing mutagen.id3 import."""
    original = builtins.__import__

    def _fail(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mutagen.id3" and fromlist and "ID3" in fromlist:
            raise ImportError
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fail)
    audio = SimpleNamespace(tags=None)
    assert _extract_apic_frame(audio) is None


def test_extract_cover_art():
    """_extract_cover_art dispatches to each supported picture source."""
    pic = Picture()
    pic.data = b"pic"
    pic.mime = "image/png"
    audio = SimpleNamespace(pictures=[pic])
    assert _extract_cover_art(audio) == (b"pic", "image/png")

    pic2 = Picture()
    pic2.type = 3
    pic2.mime = "image/jpeg"
    pic2.desc = "cover"
    pic2.data = b"\xff\xd8\xff\xe0mbp"
    b64 = base64.b64encode(pic2.write()).decode("ascii")
    audio2 = SimpleNamespace(tags={"METADATA_BLOCK_PICTURE": [b64]})
    assert _extract_cover_art(audio2) == (pic2.data, pic2.mime)

    jpeg = b"\xff\xd8\xff\xe0covr"
    audio3 = SimpleNamespace(tags={"covr": [jpeg]})
    assert _extract_cover_art(audio3) == (jpeg, "image/jpeg")

    id3 = ID3()
    apic_data = b"\xff\xd8\xff\xe0apic"
    id3["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=apic_data)
    audio4 = SimpleNamespace(tags=id3)
    assert _extract_cover_art(audio4) == (apic_data, "image/jpeg")

    assert _extract_cover_art(None) == (None, None)
    assert _extract_cover_art(SimpleNamespace(tags={})) == (None, None)


class _FakeTextFrame:
    FrameID = "TIT2"
    text = ["Title"]


class _FakeSkippedFrame:
    FrameID = "APIC"
    text = ["ignored"]


class _FakeOtherFrame:
    FrameID = "POPM"

    def __str__(self):
        return "popm_value"


class _FakeRawTags:
    def __init__(self, frames):
        self._frames = frames

    def keys(self):
        return list(self._frames.keys())

    def getall(self, key):
        return self._frames[key]


def test_extract_id3_tags():
    """_extract_id3_tags skips binary frames and converts text or stringifies."""
    frames = {
        "TIT2": [_FakeTextFrame()],
        "APIC": [_FakeSkippedFrame()],
        "POPM": [_FakeOtherFrame()],
    }
    tags = _extract_id3_tags(_FakeRawTags(frames))
    assert tags == {"TIT2": ["Title"], "POPM": ["popm_value"]}
    assert _extract_id3_tags({}) == {}


def test_extract_vorbis_tags():
    """_extract_vorbis_tags base64-encodes bytes, joins lists and skips covr."""
    raw = {
        "covr": [b"ignored"],
        "FOO": [b"\x00\x01", ("a", "b"), None, "str"],
        "EMPTY": [None, ""],
        "LIST": [[1, 2, 3]],
    }
    assert _extract_vorbis_tags(raw) == {
        "FOO": ["AAE=", "a,b", "str"],
        "LIST": ["1,2,3"],
    }


def test_extract_raw_tags_empty():
    """_extract_raw_tags returns an empty dict for missing or empty tags."""
    assert _extract_raw_tags(None) == {}
    assert _extract_raw_tags(SimpleNamespace(tags=None)) == {}
    assert _extract_raw_tags(SimpleNamespace(tags={})) == {}


def test_extract_raw_tags_id3(tagged_mp3):
    """_extract_raw_tags extracts ID3 text frames for MP3 files."""
    mp3 = mutagen.File(str(tagged_mp3), easy=False)
    tags = _extract_raw_tags(mp3)
    assert "TIT2" in tags
    assert "APIC" not in tags


def test_extract_raw_tags_vorbis(tagged_flac):
    """_extract_raw_tags falls back to vorbis-style extraction."""
    flac = mutagen.File(str(tagged_flac), easy=False)
    tags = _extract_raw_tags(flac)
    assert "title" in tags
    assert "artist" in tags
