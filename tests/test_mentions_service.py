"""
Mention service tests - extraction, local and remote resolution, blocked
domain gating, safe HTML rendering, and the ``process_mentions`` pipeline.
"""

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.services import mentions
from songhive.services.mentions import (
    ResolvedMention,
    extract_mentions,
    process_mentions,
    render_mentions,
    resolve_mention,
    resolve_mentions,
)


def _fed_config(tmp_path, **overrides) -> SonghiveConfig:
    """Build a config with federation enabled on ``local.example``."""
    federation = {"enabled": True, "instance_domain": "local.example"}
    federation.update(overrides)
    return SonghiveConfig(
        server={"host": "127.0.0.1", "port": 8000, "debug": True},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation=federation,
        auth={"secret_key": "a" * 64},
        storage={"local_path": str(tmp_path / "media"), "backend": "local"},
    )


def _fake_resolve(monkeypatch):
    """Replace Pubby's WebFinger resolver with a deterministic stub."""
    calls = []

    def fake(username, domain, timeout=10):
        calls.append((username, domain))
        return f"https://{domain}/users/{username}"

    monkeypatch.setattr(mentions, "resolve_actor_url", fake)
    return calls


def test_extract_mentions_local_and_remote():
    """Both ``@user`` and ``@user@domain`` handles are extracted."""
    text = "hi @alice and @bob@remote.example"
    assert extract_mentions(text) == ["@alice", "@bob@remote.example"]


def test_extract_mentions_ignores_emails_and_urls():
    """Email addresses and handles inside URLs are not mentions."""
    assert extract_mentions("mail me at alice@example.com") == []
    assert extract_mentions("see https://site.example/@alice") == []
    assert extract_mentions("no @@double") == []


def test_extract_mentions_dedupes_case_insensitively():
    """Repeated handles keep their first-seen surface form."""
    assert extract_mentions("@Alice @ALICE @alice") == ["@Alice"]


def test_extract_mentions_allows_hyphens_and_dots():
    """Handles carry the username charset of local and remote conventions."""
    assert extract_mentions("@an-a @b.c@remote.example") == ["@an-a", "@b.c@remote.example"]


@pytest.mark.asyncio
async def test_resolve_mention_local_user(db_session, other_user, config):
    """A bare ``@user`` handle resolves to the local user row."""
    other_user.actor_url = "https://local.example/users/other"
    resolved = await resolve_mention(db_session, "@other", config)

    assert resolved is not None
    assert resolved.handle == "@other"
    assert resolved.user_id == other_user.id
    assert resolved.actor_url == "https://local.example/users/other"


@pytest.mark.asyncio
async def test_resolve_mention_local_user_case_insensitive(db_session, other_user, config):
    """Local handles resolve case-insensitively and canonicalize the handle."""
    resolved = await resolve_mention(db_session, "@OTHER", config)
    assert resolved is not None
    assert resolved.handle == "@other"
    assert resolved.user_id == other_user.id


@pytest.mark.asyncio
async def test_resolve_mention_local_user_derives_actor_url(db_session, other_user, tmp_path):
    """An unprovisioned local user gets the canonical instance actor URL."""
    config = _fed_config(tmp_path)
    resolved = await resolve_mention(db_session, "@other", config)
    assert resolved is not None
    assert resolved.actor_url == "https://local.example/users/other"


@pytest.mark.asyncio
async def test_resolve_mention_local_user_no_actor_url(db_session, other_user, config):
    """Without an instance domain there is no URL to link to."""
    resolved = await resolve_mention(db_session, "@other", config)
    assert resolved is not None
    assert resolved.user_id == other_user.id
    assert resolved.actor_url is None


@pytest.mark.asyncio
async def test_resolve_mention_unknown_user(db_session, config):
    """Unknown local handles resolve to nothing."""
    assert await resolve_mention(db_session, "@ghost", config) is None


@pytest.mark.asyncio
async def test_resolve_mention_inactive_user(db_session, inactive_user, config):
    """Inactive users cannot be mentioned."""
    assert await resolve_mention(db_session, "@inactive", config) is None


@pytest.mark.asyncio
async def test_resolve_mention_remote(db_session, tmp_path, monkeypatch):
    """Remote handles resolve through Pubby's WebFinger client."""
    config = _fed_config(tmp_path)
    calls = _fake_resolve(monkeypatch)

    resolved = await resolve_mention(db_session, "@bob@remote.example", config)

    assert resolved is not None
    assert resolved.handle == "@bob@remote.example"
    assert resolved.actor_url == "https://remote.example/users/bob"
    assert resolved.user_id is None
    assert calls == [("bob", "remote.example")]


@pytest.mark.asyncio
async def test_resolve_mention_remote_uses_fallback(db_session, tmp_path, monkeypatch):
    """A failed WebFinger lookup yields Pubby's fallback actor URL."""
    config = _fed_config(tmp_path)
    monkeypatch.setattr(
        mentions,
        "resolve_actor_url",
        lambda username, domain, timeout=10: f"https://{domain}/@{username}",
    )

    resolved = await resolve_mention(db_session, "@bob@remote.example", config)
    assert resolved is not None
    assert resolved.actor_url == "https://remote.example/@bob"


@pytest.mark.asyncio
async def test_resolve_mention_blocked_domain_dropped(db_session, tmp_path, monkeypatch):
    """Handles on blocked instances are dropped without any lookup."""
    config = _fed_config(tmp_path, blocked_instances=["blocked.example"])
    calls = _fake_resolve(monkeypatch)

    assert await resolve_mention(db_session, "@eve@blocked.example", config) is None
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_mention_federation_disabled_drops_remote(db_session, config, monkeypatch):
    """Remote handles are dropped when federation is disabled."""
    calls = _fake_resolve(monkeypatch)

    assert await resolve_mention(db_session, "@bob@remote.example", config) is None
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_mention_local_domain_resolves_locally(db_session, tmp_path, other_user, monkeypatch):
    """``@user@local-domain`` handles resolve against local users."""
    config = _fed_config(tmp_path)
    calls = _fake_resolve(monkeypatch)

    resolved = await resolve_mention(db_session, "@other@local.example", config)

    assert resolved is not None
    assert resolved.handle == "@other"
    assert resolved.user_id == other_user.id
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_mention_undotted_domain_dropped(db_session, tmp_path, monkeypatch):
    """Remote handles require a dotted domain, matching Pubby's semantics."""
    config = _fed_config(tmp_path)
    calls = _fake_resolve(monkeypatch)

    assert await resolve_mention(db_session, "@bob@localhost", config) is None
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_mentions_combines_local_and_remote(db_session, tmp_path, other_user, monkeypatch):
    """A mixed text resolves local and remote handles in first-seen order."""
    config = _fed_config(tmp_path)
    calls = _fake_resolve(monkeypatch)

    resolved = await resolve_mentions(db_session, "hi @other and @bob@remote.example", config)

    assert [m.handle for m in resolved] == ["@other", "@bob@remote.example"]
    assert resolved[0].user_id == other_user.id
    assert resolved[1].actor_url == "https://remote.example/users/bob"
    assert calls == [("bob", "remote.example")]


@pytest.mark.asyncio
async def test_resolve_mentions_dedupes_resolved_handles(db_session, tmp_path, other_user):
    """``@user`` and ``@user@local-domain`` collapse to a single mention."""
    config = _fed_config(tmp_path)

    resolved = await resolve_mentions(db_session, "@other @other@local.example", config)

    assert [m.handle for m in resolved] == ["@other"]


def test_render_mentions_links_resolved_handles():
    """Resolved mentions render as anchors pointing at the actor URL."""
    resolved = [ResolvedMention(handle="@bob", actor_url="https://remote.example/users/bob")]

    rendered = render_mentions("hi @bob!", resolved)

    assert rendered.html == 'hi <a href="https://remote.example/users/bob">@bob</a>!'


def test_render_mentions_escapes_text():
    """Surrounding text is HTML-escaped and URLs are linkified."""
    resolved = [ResolvedMention(handle="@bob", actor_url="https://remote.example/users/bob")]

    rendered = render_mentions("<b>@bob</b> https://x.example/y", resolved)

    assert rendered.html == (
        '&lt;b&gt;<a href="https://remote.example/users/bob">@bob</a>'
        '&lt;/b&gt; <a href="https://x.example/y">x.example/y</a>'
    )


def test_render_mentions_unresolved_stays_inert():
    """Unresolved or linkless mentions remain escaped plain text."""
    assert render_mentions("hi @ghost", []).html == "hi @ghost"
    mention = ResolvedMention(handle="@bob")
    assert render_mentions("hi @bob", [mention]).html == "hi @bob"


def test_render_mentions_collects_tags():
    """Tags in the text are linkified and collected."""
    rendered = render_mentions("hey @bob #Music #music", [], domain="local.example")

    assert rendered.hashtags == ["music"]
    assert 'href="https://local.example/tags/music"' in rendered.html


def test_render_mentions_uses_relative_tag_links_without_domain():
    """Without an instance domain, tag links fall back to local paths."""
    rendered = render_mentions("#music", [])
    assert rendered.html == '<a href="/tags/music" rel="tag">#music</a>'


@pytest.mark.asyncio
async def test_process_mentions_end_to_end(db_session, tmp_path, other_user, monkeypatch):
    """The pipeline returns mentions, rendered HTML, tags, and AP tags."""
    config = _fed_config(tmp_path)
    _fake_resolve(monkeypatch)

    processed = await process_mentions(
        db_session,
        "hi @other and @bob@remote.example #tunes",
        config,
    )

    assert [m.handle for m in processed.mentions] == ["@other", "@bob@remote.example"]
    assert '<a href="https://local.example/users/other">@other</a>' in processed.html
    assert '<a href="https://remote.example/users/bob">@bob@remote.example</a>' in processed.html
    assert processed.tag_names == ["tunes"]
    assert processed.tags == [
        {"type": "Mention", "href": "https://local.example/users/other", "name": "@other"},
        {"type": "Mention", "href": "https://remote.example/users/bob", "name": "@bob@remote.example"},
        {"type": "Hashtag", "name": "#tunes", "href": "https://local.example/tags/tunes"},
    ]


@pytest.mark.asyncio
async def test_process_mentions_dropped_handles_render_inert(db_session, tmp_path, monkeypatch):
    """Blocked mentions are dropped and render as inert text."""
    config = _fed_config(tmp_path, blocked_instances=["blocked.example"])
    calls = _fake_resolve(monkeypatch)

    processed = await process_mentions(db_session, "hi @eve@blocked.example", config)

    assert processed.mentions == []
    assert processed.html == "hi @eve@blocked.example"
    assert processed.tags == []
    assert calls == []


def test_resolved_mention_as_dict_matches_create_local_activity():
    """as_dict returns the row shape create_local_activity persists."""
    mention = ResolvedMention(handle="@bob", actor_url="https://x.example/u/bob", user_id="u1")
    assert mention.as_dict() == {
        "handle": "@bob",
        "actor_url": "https://x.example/u/bob",
        "user_id": "u1",
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_mentions_markdown_renders_formatting(db_session, config, other_user):
    """Markdown statuses render formatting, mentions, and hashtags."""
    other_user.actor_url = "https://local.example/users/other"
    processed = await process_mentions(
        db_session,
        "hi **@other** #music\n\n- a\n- b",
        config,
        content_type="text/markdown",
    )

    assert "<strong>" in processed.html
    assert '<a href="https://local.example/users/other">@other</a>' in processed.html
    assert 'rel="tag">#music</a>' in processed.html
    assert "<ul>" in processed.html
    assert processed.tag_names == ["music"]
    assert processed.mentions[0].user_id == other_user.id
    assert processed.tags[0]["type"] == "Mention"


@pytest.mark.asyncio
async def test_process_mentions_markdown_escapes_inline_html(db_session, config):
    """Raw HTML in a Markdown status is escaped, not emitted verbatim."""
    processed = await process_mentions(
        db_session,
        'text <script>alert(1)</script> and <a href="javascript:x">click</a> and [x](javascript:y)',
        config,
        content_type="text/markdown",
    )

    assert "<script>" not in processed.html
    assert 'href="javascript:' not in processed.html
    assert "&lt;script&gt;" in processed.html
    assert "#harmful-link" in processed.html


@pytest.mark.asyncio
async def test_process_mentions_markdown_skips_code_spans(db_session, config, other_user):
    """Handles and tags inside code spans are not linkified or extracted."""
    other_user.actor_url = "https://local.example/users/other"
    processed = await process_mentions(
        db_session,
        "`@other #code`",
        config,
        content_type="text/markdown",
    )

    assert "<code>" in processed.html
    assert "<a" not in processed.html
    assert processed.tag_names == []
    # The handle still resolves (the source text mentions it), it just does
    # not render as a link inside the code span.
    assert [m.handle for m in processed.mentions] == ["@other"]


@pytest.mark.asyncio
async def test_process_mentions_markdown_links_and_urls(db_session, config):
    """Bare URLs and Markdown links render as anchors; link text is safe."""
    processed = await process_mentions(
        db_session,
        "see https://example.com/x and [a link](https://example.com)",
        config,
        content_type="text/markdown",
    )

    assert 'href="https://example.com/x"' in processed.html
    assert '<a href="https://example.com">a link</a>' in processed.html


@pytest.mark.asyncio
async def test_process_mentions_markdown_unresolved_handle_inert(db_session, config):
    """Unresolved handles in Markdown render as plain text."""
    processed = await process_mentions(
        db_session,
        "hi @ghost",
        config,
        content_type="text/markdown",
    )

    assert "@ghost" in processed.html
    assert "<a" not in processed.html
    assert processed.mentions == []
