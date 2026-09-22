"""
Tests for the audio output provider registry and base classes.
"""

import pytest

from songhive.streams.base import AudioOutput
from songhive.streams.driver import OutputDriver
from songhive.streams.fake import FakeOutput
from songhive.streams.registry import (
    get_output,
    is_user_configurable,
    list_output_types,
    register_output,
)
from songhive.streams.types import AudioSource, OutputHealth, TrackMeta


@pytest.mark.asyncio
async def test_registry_round_trip():
    """Registering the fake provider and reading it back works."""
    register_output("fake", FakeOutput)
    assert get_output("fake") is FakeOutput
    assert "fake" in list_output_types()


def test_list_output_types_sorted():
    """Provider type keys are returned sorted."""
    assert list_output_types() == sorted(list_output_types())


def test_is_user_configurable():
    """The fake provider is reported as user-configurable."""
    assert is_user_configurable("fake") is True
    assert is_user_configurable("unknown") is False


def test_get_output_unknown():
    """Requesting an unknown provider raises a KeyError."""
    with pytest.raises(KeyError):
        get_output("no-such-provider")


def test_register_output_requires_non_empty_string():
    """Provider type names must be non-empty strings."""
    with pytest.raises(ValueError):
        register_output("", FakeOutput)
    with pytest.raises(ValueError):
        register_output(123, FakeOutput)  # type: ignore[arg-type]


def test_register_output_rejects_conflicting_class():
    """Registering a different class under the same key raises an error."""
    from songhive.streams.base import AudioOutput

    class OtherFake(AudioOutput):
        provider_type = "fake"
        user_configurable = True

    with pytest.raises(ValueError):
        register_output("fake", OtherFake)


def test_abstract_audio_output_cannot_be_instantiated():
    """The base provider class cannot be instantiated directly."""
    with pytest.raises(TypeError):
        AudioOutput()  # type: ignore[abstract]


def test_abstract_output_driver_cannot_be_instantiated():
    """The base driver class cannot be instantiated directly."""
    with pytest.raises(TypeError):
        OutputDriver({})  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_fake_output_validate_config():
    """Fake output validation returns fixed capabilities."""
    output = FakeOutput()
    caps = await output.validate_config({"items": {}})
    assert caps == output.capabilities()
    assert caps.pause_supported is True
    assert caps.seek_supported is True
    assert caps.metadata_updates is False
    assert caps.user_configurable is True


@pytest.mark.asyncio
async def test_fake_output_validate_config_requires_items():
    """Fake output validation requires the ``items`` key."""
    output = FakeOutput()
    with pytest.raises(ValueError, match='config\\["items"\\] must be a dict'):
        await output.validate_config({})


@pytest.mark.asyncio
async def test_fake_driver_records_commands():
    """The fake driver records every command it receives."""
    driver = FakeOutput().create_driver({"items": {}})
    meta = TrackMeta(track_id="t1", title="Song", artist="Artist")
    source = AudioSource(kind="path", path=None, content_type="audio/mpeg")

    await driver.start()
    await driver.set_source(source, position=0.0, metadata=meta)
    await driver.pause()
    await driver.resume()
    await driver.update_metadata(meta)
    await driver.stop()

    assert driver.commands == [
        "start",
        ("set_source", source, 0.0, meta),
        "pause",
        "resume",
        ("update_metadata", meta),
        "stop",
    ]


@pytest.mark.asyncio
async def test_fake_driver_emits_source_ended():
    """The fake driver can emit a ``source_ended`` event on demand."""
    driver = FakeOutput().create_driver({"items": {}})
    driver.trigger_source_ended()
    event = await driver.events.get()
    assert event["type"] == "source_ended"


@pytest.mark.asyncio
async def test_fake_driver_health():
    """The fake driver always reports healthy."""
    driver = FakeOutput().create_driver({"items": {}})
    health = await driver.health()
    assert isinstance(health, OutputHealth)
    assert health.ok is True


@pytest.mark.asyncio
async def test_redaction_uses_exact_keys():
    """`AudioOutput.sanitize_config_for_response` redacts declared secret keys."""
    output = FakeOutput()
    config = {"host": "localhost", "secret": "hunter2", "password": "secret"}
    redacted = output.sanitize_config_for_response(config)
    assert redacted["host"] == "localhost"
    assert redacted["secret"] == "<redacted>"
    # ``password`` is not in the fake's explicit redaction set, so it is kept.
    assert redacted["password"] == "secret"
