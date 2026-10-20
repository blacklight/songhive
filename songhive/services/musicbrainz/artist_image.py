"""Artist image URL resolution — part of the ``musicbrainz`` service."""

import io
import urllib.parse
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import httpx

from ...models.stored_file import StoredFile

if TYPE_CHECKING:
    from ...services.storage import StorageService

import logging

from .metadata import (
    _MAX_COVER_ART_REDIRECTS,
    _guess_image_mime,
    _is_image_relation,
    _is_logo_relation,
    _is_valid_image,
    _looks_like_image_url,
)

logger = logging.getLogger(__name__)


def _artist_wikidata_id(artist: Dict[str, Any]) -> Optional[str]:
    """Find the ``Q``-id of the artist's linked Wikidata entity, if any."""
    relations = (
        artist.get("url-relation-list")
        or artist.get("url-relations")
        or artist.get("relation-list")
        or artist.get("relations")
        or []
    )
    if not isinstance(relations, list):
        return None

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        relation_type = relation.get("type") or ""
        if not isinstance(relation_type, str) or "wikidata" not in relation_type.lower():
            continue
        candidates = []
        target = relation.get("target")
        if isinstance(target, str):
            candidates.append(target)
        url = relation.get("url", {})
        if isinstance(url, dict):
            candidates.extend(u for u in (url.get("resource"), url.get("id")) if isinstance(u, str))
        for candidate in candidates:
            if candidate.startswith("https://www.wikidata.org/wiki/"):
                wikidata_id = candidate.rsplit("/", 1)[-1]
                if wikidata_id and wikidata_id.startswith("Q"):
                    return wikidata_id
    return None


class _ArtistImageResolution:
    if TYPE_CHECKING:
        # Provided by the host class (MusicBrainzService).
        _client: httpx.AsyncClient

    async def _resolve_artist_image_relations(self, artist: Dict[str, Any]) -> Optional[str]:
        """Resolve image-like URL relations from an already-fetched artist."""
        relations = (
            artist.get("url-relation-list")
            or artist.get("url-relations")
            or artist.get("relation-list")
            or artist.get("relations")
            or []
        )
        if not isinstance(relations, list):
            return None

        preferred: List[str] = []
        deferred: List[str] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            if not _is_image_relation(relation):
                continue
            # Logos are less useful as artist images than photos, so they are
            # only tried after every other image-like relation.
            targets = deferred if _is_logo_relation(relation) else preferred
            url = relation.get("url", {})
            if isinstance(url, dict):
                target = url.get("resource") or url.get("id")
                if isinstance(target, str) and target.startswith("http"):
                    targets.append(target)
            target = relation.get("target")
            if isinstance(target, str) and target.startswith("http"):
                targets.append(target)

        for target in preferred + deferred:
            resolved = await self._resolve_image_url(target)
            if resolved:
                return resolved

        return None

    async def _resolve_wikidata_image(self, artist: Dict[str, Any]) -> Optional[str]:
        """Resolve an image from a Wikidata P18 claim linked to the artist."""
        wikidata_id = _artist_wikidata_id(artist)
        if not wikidata_id:
            return None

        try:
            response = await self._client.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json",
                follow_redirects=True,
            )
        except Exception as exc:
            logger.debug("Wikidata request failed for %s: %s", wikidata_id, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except Exception:
            return None

        entity = payload.get("entities", {}).get(wikidata_id, {})
        claims = entity.get("claims", {})
        p18 = claims.get("P18", [])
        if not isinstance(p18, list):
            return None

        for claim in p18:
            if not isinstance(claim, dict):
                continue
            datavalue = claim.get("mainsnak", {}).get("datavalue", {})
            filename = datavalue.get("value")
            if not isinstance(filename, str):
                continue
            filename = filename.replace(" ", "_")
            commons_url = f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(filename)}"
            resolved = await self._wikimedia_file_url(commons_url)
            if resolved:
                return resolved

        return None

    async def _resolve_image_url(self, url: str) -> Optional[str]:
        """Resolve a relation URL to a direct image URL."""
        parsed = urllib.parse.urlparse(url)

        # web.archive.org saved snapshots: extract the archived URL
        if parsed.netloc.endswith("web.archive.org") and "/web/" in (parsed.path or ""):
            parts = (parsed.path or "").split("/")
            if len(parts) >= 4 and parts[1] == "web":
                archived_url = "/".join(parts[3:])
                if archived_url.startswith("http"):
                    return await self._resolve_image_url(archived_url)

        if parsed.netloc.endswith("commons.wikimedia.org"):
            return await self._wikimedia_file_url(url)

        if "scdn.co" in parsed.netloc or parsed.netloc.endswith("mzstatic.com"):
            return url

        if _looks_like_image_url(url):
            return url

        return None

    async def _wikimedia_file_url(self, url: str) -> Optional[str]:
        """Resolve a Wikimedia Commons file page to a direct image URL."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or ""
        if path.startswith("/wiki/Category:") or path.startswith("/wiki/File:"):
            title = path.split(":", 1)[1]
            title = urllib.parse.unquote(title)
            title = title.replace(" ", "_")
            api_url = (
                "https://commons.wikimedia.org/w/api.php"
                f"?action=query&titles=File:{urllib.parse.quote(title)}"
                "&prop=imageinfo&iiprop=url|mime&format=json&origin=*"
            )
            try:
                response = await self._client.get(api_url)
            except Exception as exc:
                logger.debug("Wikimedia API request failed for %s: %s", url, exc)
                return None

            if response.status_code != 200:
                return None

            try:
                payload = response.json()
            except Exception:
                return None

            pages = payload.get("query", {}).get("pages", {})
            for page in pages.values():
                if not isinstance(page, dict):
                    continue
                for info in page.get("imageinfo", []):
                    if isinstance(info, dict):
                        image_url = info.get("url")
                        if isinstance(image_url, str) and image_url.startswith("http"):
                            return image_url

        return None

    async def _download_image(self, url: str) -> Optional[bytes]:
        """Download an image from a URL, following redirects."""
        for _ in range(_MAX_COVER_ART_REDIRECTS):
            try:
                response = await self._client.get(url, follow_redirects=False)
            except Exception as exc:
                logger.debug("Image download failed for %s: %s", url, exc)
                return None

            if 200 <= response.status_code < 300:
                data = response.content
                if isinstance(data, bytes) and data and _is_valid_image(data):
                    return data
                return None

            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    return None
                url = str(location)
                continue

            return None

        logger.debug("Too many redirects for image %s", url)
        return None

    async def _store_image_file(
        self,
        session,
        entity,
        field_name: str,
        data: bytes,
        storage_service: StorageService,
        owner_id: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> Optional[StoredFile]:
        """Store an image for an entity and assign its StoredFile."""
        content_type = _guess_image_mime(data)
        buffer = io.BytesIO(data)

        stored, _ = await storage_service.store_file(
            session,
            buffer,
            content_type,
            prefix="images",
            owner_id=owner_id,
            visibility=visibility or "private",
            return_duplicate=True,
        )
        if stored is None:
            return None

        setattr(entity, field_name, str(stored.id))
        relationship = field_name.replace("_file_id", "_file")
        if hasattr(entity, relationship):
            setattr(entity, relationship, stored)
        return stored
