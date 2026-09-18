/**
 * RSS/Atom feed URL builders for Songhive object pages.
 *
 * Feed routes live outside the versioned API under ``/feeds`` and mirror the
 * ``<link rel="alternate">`` tags the backend injects into the SPA shell.
 */

export interface FeedUrls {
  rss: string;
  atom: string;
}

const feedUrls = (path: string): FeedUrls => ({
  rss: `${path}.rss`,
  atom: `${path}.atom`,
});

/** Feed of a user's latest posts. */
export const userFeedUrls = (username: string): FeedUrls =>
  feedUrls(`/feeds/users/${encodeURIComponent(username)}`);

/** Feed of an artist's latest releases. */
export const artistFeedUrls = (id: string): FeedUrls =>
  feedUrls(`/feeds/artists/${encodeURIComponent(id)}`);

/** Feed of the tracks most recently added to a playlist. */
export const playlistFeedUrls = (id: string): FeedUrls =>
  feedUrls(`/feeds/playlists/${encodeURIComponent(id)}`);

/** Feed of the tracks most recently added to a library. */
export const libraryFeedUrls = (id: string): FeedUrls =>
  feedUrls(`/feeds/libraries/${encodeURIComponent(id)}`);

/** Feed of the entities and activities carrying a tag. */
export const tagFeedUrls = (name: string): FeedUrls =>
  feedUrls(`/feeds/tags/${encodeURIComponent(name)}`);

/** Feed of the most recent tracks and albums in a genre. */
export const genreFeedUrls = (name: string): FeedUrls =>
  feedUrls(`/feeds/genres/${encodeURIComponent(name)}`);

/** Feed of the activities attached to an entity (``/activities`` page). */
export const activityFeedUrls = (plural: string, id: string): FeedUrls =>
  feedUrls(
    `/feeds/${encodeURIComponent(plural)}/${encodeURIComponent(id)}/activities`,
  );
