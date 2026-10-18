"""
Artist credit model/service tests: ordering, dedup, joinphrase defaults,
``artist_id``/``extra_artists`` sync and federated serialization.
"""

from sqlalchemy import select

from songhive.models import Track, Visibility
from songhive.models.artist import Artist
from songhive.models.artist_credit import ArtistCredit, joined_artist_credit
from songhive.services.music import (
    reconcile_primary_credit,
    set_artist_credits,
    set_track_artist,
)


async def _track(db_session, **kwargs) -> Track:
    kwargs.setdefault("visibility", Visibility.PRIVATE.value)
    track = Track(**kwargs)
    db_session.add(track)
    await db_session.commit()
    return track


async def _artists(db_session, *names: str) -> list:
    artists = [Artist(name=n) for n in names]
    db_session.add_all(artists)
    await db_session.commit()
    return artists


class TestSetArtistCredits:
    async def test_full_list_written_in_order(self, db_session, regular_user):
        a, b, c = await _artists(db_session, "A", "B", "C")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_artist_credits(
            db_session,
            track=track,
            credits=[
                {"artist": a, "joinphrase": " feat. "},
                {"artist": b, "joinphrase": " & "},
                {"artist": c},
            ],
        )
        await db_session.commit()

        assert [row.artist_id for row in credits] == [str(a.id), str(b.id), str(c.id)]
        assert [row.index for row in credits] == [0, 1, 2]
        assert [row.joinphrase for row in credits] == [" feat. ", " & ", ""]
        assert joined_artist_credit(credits) == "A feat. B & C"
        assert str(track.artist_id) == str(a.id)
        assert track.extra_artists == ["B", "C"]

    async def test_joinphrase_defaults(self, db_session, regular_user):
        a, b = await _artists(db_session, "A", "B")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_artist_credits(db_session, track=track, credits=[{"artist": a}, {"artist": b}])
        assert [row.joinphrase for row in credits] == [", ", ""]
        assert joined_artist_credit(credits) == "A, B"

    async def test_duplicate_artists_dropped(self, db_session, regular_user):
        a, b = await _artists(db_session, "A", "B")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_artist_credits(
            db_session,
            track=track,
            credits=[{"artist": a}, {"artist": a}, {"artist": b}],
        )
        assert [row.artist_id for row in credits] == [str(a.id), str(b.id)]

    async def test_replace_keeps_primary_and_reorders(self, db_session, regular_user):
        a, b, c = await _artists(db_session, "A", "B", "C")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))
        await set_artist_credits(db_session, track=track, credits=[{"artist": a}, {"artist": b}])

        # Reorder: B becomes primary, C replaces nothing-the-extra list.
        credits = await set_artist_credits(
            db_session,
            track=track,
            credits=[{"artist": b, "joinphrase": " feat. "}, {"artist": a}, {"artist": c}],
        )
        await db_session.commit()

        assert str(track.artist_id) == str(b.id)
        assert [row.artist_id for row in credits] == [str(b.id), str(a.id), str(c.id)]
        assert [row.joinphrase for row in credits] == [" feat. ", ", ", ""]
        assert track.extra_artists == ["A", "C"]

    async def test_credit_alias_syncs_extra_artists(self, db_session, regular_user):
        a, b = await _artists(db_session, "Real Name", "Guest")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_artist_credits(
            db_session,
            track=track,
            credits=[
                {"artist": a, "credit": "Alias A", "joinphrase": " feat. "},
                {"artist": b},
            ],
        )
        assert joined_artist_credit(credits) == "Alias A feat. Guest"
        assert track.extra_artists == ["Guest"]

    async def test_clear_extras(self, db_session, regular_user):
        a, b = await _artists(db_session, "A", "B")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))
        await set_artist_credits(db_session, track=track, credits=[{"artist": a}, {"artist": b}])

        credits = await set_artist_credits(db_session, track=track, credits=[{"artist": a}])
        assert len(credits) == 1
        assert credits[0].joinphrase == ""
        assert track.extra_artists is None

        rows = (
            (await db_session.execute(select(ArtistCredit).where(ArtistCredit.track_id == str(track.id))))
            .scalars()
            .all()
        )
        assert len(rows) == 1


class TestSetTrackArtist:
    async def test_names_resolve_or_create(self, db_session, regular_user):
        a, b = await _artists(db_session, "A", "B")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_track_artist(db_session, track, a, ["B", "New Artist"])
        await db_session.commit()

        assert [row.index for row in credits] == [0, 1, 2]
        names = [c.artist.name for c in credits]
        assert names == ["A", "B", "New Artist"]
        # ``B`` resolved to the existing row rather than a duplicate.
        b_count = (await db_session.execute(select(Artist).where(Artist.name == "B"))).scalars().all()
        assert len(b_count) == 1
        new = (await db_session.execute(select(Artist).where(Artist.name == "New Artist"))).scalar_one()
        assert credits[2].artist_id == str(new.id)

    async def test_extra_matching_primary_dropped(self, db_session, regular_user):
        (a,) = await _artists(db_session, "A")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        credits = await set_track_artist(db_session, track, a, ["a"])
        assert len(credits) == 1


class TestReconcilePrimaryCredit:
    async def test_follows_artist_id_reassignment(self, db_session, regular_user):
        a, b = await _artists(db_session, "A", "B")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))
        await set_artist_credits(
            db_session,
            track=track,
            credits=[{"artist": a, "credit": "Alias", "joinphrase": " feat. "}, {"artist": b}],
        )
        await db_session.commit()

        # Raw ``artist_id`` reassignment (e.g. enrichment) must reconcile.
        track.artist_id = str(b.id)
        await reconcile_primary_credit(db_session, track)
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(ArtistCredit).where(ArtistCredit.track_id == str(track.id)).order_by(ArtistCredit.index)
                )
            )
            .scalars()
            .all()
        )
        assert str(rows[0].artist_id) == str(b.id)
        assert rows[0].credit is None


class TestCreditSerialization:
    async def test_federated_track_doc_uses_credits(self, db_session, regular_user):
        """``artist_credit``/``artists``/``name`` all reflect the credit rows."""
        from songhive.federation.serializers import (
            track_to_audio_object,
            track_to_music_track_object,
        )

        a, b = await _artists(db_session, "Primary", "Guest")
        track = await _track(
            db_session,
            title="Song",
            artist_id=str(a.id),
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        )
        await set_artist_credits(
            db_session,
            track=track,
            credits=[
                {"artist": a, "joinphrase": " feat. "},
                {"artist": b, "credit": "DJ Guest"},
            ],
        )
        await db_session.commit()
        await db_session.refresh(track, ["artist", "artist_credits"])

        doc = track_to_music_track_object(track, a, "music.example.com", "https://music.example.com/users/regular")
        assert [c["artist"]["name"] for c in doc["artist_credit"]] == ["Primary", "Guest"]
        assert doc["artist_credit"][1]["credit"] == "DJ Guest"
        assert doc["artist_credit"][1]["joinphrase"] == ""
        assert doc["artist_credit"][0]["joinphrase"] == " feat. "
        assert [a["name"] for a in doc["artists"]] == ["Primary", "Guest"]

        audio = track_to_audio_object(track, a, "music.example.com")
        assert audio is not None
        assert audio["name"] == "Primary feat. DJ Guest - Song"

    async def test_fallback_without_credit_rows(self, db_session, regular_user):
        """Tracks without credit rows still emit the primary-only credit."""
        from songhive.federation.serializers import track_to_music_track_object

        (a,) = await _artists(db_session, "Solo")
        track = await _track(
            db_session,
            title="Song",
            artist_id=str(a.id),
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        )
        await db_session.refresh(track, ["artist", "artist_credits"])

        doc = track_to_music_track_object(track, a, "music.example.com", "https://music.example.com/users/regular")
        assert len(doc["artist_credit"]) == 1
        assert doc["artist_credit"][0]["artist"]["name"] == "Solo"


class TestArtistCreditsApi:
    async def test_patch_artist_credits(self, client, db_session, regular_user, auth_headers):
        """``artist_credits`` replaces the credit list end to end."""
        (a,) = await _artists(db_session, "Primary")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        response = client.patch(
            f"/api/v1/tracks/{track.id}",
            json={
                "artist_credits": [
                    {"artist_id": str(a.id), "credit": "Alias", "joinphrase": " feat. "},
                    {"artist_name": "Guest"},
                ]
            },
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        credits = response.json()["artist_credits"]
        assert [c["artist_name"] for c in credits] == ["Primary", "Guest"]
        assert credits[0]["credit"] == "Alias"
        assert credits[0]["joinphrase"] == " feat. "
        assert credits[1]["joinphrase"] == ""
        # ``extra_artists`` stays in sync with the extra credits.
        assert response.json()["extra_artists"] == ["Guest"]

        rows = (
            (await db_session.execute(select(ArtistCredit).where(ArtistCredit.track_id == str(track.id))))
            .scalars()
            .all()
        )
        assert len(rows) == 2

    async def test_patch_extra_artists_writes_credits(self, client, db_session, regular_user, auth_headers):
        """The legacy ``extra_artists`` field maps onto credit rows."""
        (a,) = await _artists(db_session, "Primary")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))

        response = client.patch(
            f"/api/v1/tracks/{track.id}",
            json={"extra_artists": ["Guest"]},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        credits = response.json()["artist_credits"]
        assert [c["artist_name"] for c in credits] == ["Primary", "Guest"]
        assert [c["joinphrase"] for c in credits] == [", ", ""]

    async def test_patch_artist_name_reconciles_primary(self, client, db_session, regular_user, auth_headers):
        """Renaming the primary artist moves the index-0 credit along."""
        a, b = await _artists(db_session, "Primary", "Guest")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))
        await set_artist_credits(db_session, track=track, credits=[{"artist": a}, {"artist": b}])
        await db_session.commit()

        response = client.patch(
            f"/api/v1/tracks/{track.id}",
            json={"artist_name": "New Primary"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        credits = response.json()["artist_credits"]
        assert [c["artist_name"] for c in credits] == ["New Primary", "Guest"]

    async def test_patch_artist_credits_validated(self, client, db_session, regular_user, auth_headers):
        (a,) = await _artists(db_session, "Primary")
        track = await _track(db_session, title="T", artist_id=str(a.id), owner_id=str(regular_user.id))
        headers = auth_headers(regular_user)

        for payload in (
            {"artist_credits": []},
            {"artist_credits": [{}]},
            {"artist_credits": [{"artist_id": "nonexistent"}]},
        ):
            response = client.patch(f"/api/v1/tracks/{track.id}", json=payload, headers=headers)
            assert response.status_code == 422, payload
