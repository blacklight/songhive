import type { ShareItemType } from "@/api/shares";
import { toAbsoluteUrl } from "./share";

export type EmbedFormat = "audio" | "html" | "markdown" | "script" | "iframe";

/**
 * Collections with this many elements or more never offer the inline
 * ``<audio>``-list embed: the generated markup would be unusably large.
 */
export const EMBED_LIST_MAX_TRACKS = 250;

export interface EmbedTrack {
  title?: string | null;
  filename?: string | null;
  artistName?: string | null;
  audioUrl?: string | null;
}

const TRACK_IFRAME_HEIGHT = 152;
const COLLECTION_IFRAME_HEIGHT = 420;

function origin(): string {
  return typeof window !== "undefined" ? window.location.origin : "";
}

/** URL of the embeddable page served by Songhive (the iframe/script target). */
export function getEmbedPageUrl(
  itemType: ShareItemType,
  itemId: string,
): string {
  return `${origin()}/embed/${itemType}/${itemId}`;
}

/** URL of the static script that renders ``data-songhive-embed`` placeholders. */
export function getEmbedScriptUrl(): string {
  return `${origin()}/embed.js`;
}

/** Default pixel height for the no-JS ``<iframe>`` fallback. */
export function embedIframeHeight(itemType: ShareItemType): number {
  return itemType === "track" ? TRACK_IFRAME_HEIGHT : COLLECTION_IFRAME_HEIGHT;
}

/**
 * Link text for a track: ``{artist} - {title}`` when both are available,
 * then ``{title}``, then ``{filename}`` as a fallback.
 */
export function trackLinkText(track: EmbedTrack): string {
  const title = track.title?.trim() || "";
  const artist = track.artistName?.trim() || "";
  if (artist && title) return `${artist} - ${title}`;
  if (title) return title;
  const filename = track.filename?.trim() || "";
  if (filename) return filename;
  return artist;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeMarkdownLabel(value: string): string {
  return value.replace(/[[\]]/g, (ch) => `\\${ch}`);
}

/** ``[{label}]({url})`` markdown snippet. */
export function markdownEmbed(label: string, url: string): string {
  return `[${escapeMarkdownLabel(label)}](${url})`;
}

/**
 * A standalone ``<audio>`` tag wrapped in a ``<p>`` with the track metadata
 * rendered as a link back to the Songhive page.
 */
export function audioEmbed(options: {
  pageUrl: string;
  audioUrl: string;
  label: string;
}): string {
  const href = escapeHtml(options.pageUrl);
  const src = escapeHtml(toAbsoluteUrl(options.audioUrl));
  const label = escapeHtml(options.label);
  return (
    `<p><a href="${href}">${label}</a><br />` +
    `<audio src="${src}" controls preload="none"></audio></p>`
  );
}

/**
 * A ``<div>`` with the full list of ``<audio>`` tracks of a collection.
 * Only offered for collections below ``EMBED_LIST_MAX_TRACKS`` elements.
 */
export function trackListEmbed(options: {
  pageUrl: string;
  title: string;
  tracks: { label: string; audioUrl: string }[];
}): string {
  const items = options.tracks
    .map(
      (track) =>
        `    <li>${escapeHtml(track.label)}<br />` +
        `<audio src="${escapeHtml(toAbsoluteUrl(track.audioUrl))}" controls preload="none"></audio></li>`,
    )
    .join("\n");
  return (
    `<div class="songhive-tracklist">\n` +
    `  <p><a href="${escapeHtml(options.pageUrl)}">${escapeHtml(options.title)}</a></p>\n` +
    `  <ol>\n${items}\n  </ol>\n</div>`
  );
}

/**
 * The ``<script>`` embed: a placeholder div rendered by ``/embed.js`` into the
 * same embeddable Songhive element as the ``<iframe>`` fallback.
 */
export function scriptEmbed(itemType: ShareItemType, itemId: string): string {
  return (
    `<div class="songhive-embed" data-type="${itemType}" ` +
    `data-id="${escapeHtml(itemId)}"></div>\n` +
    `<script async src="${escapeHtml(getEmbedScriptUrl())}" charset="utf-8"></script>`
  );
}

/** The no-JS ``<iframe>`` embed served by Songhive. */
export function iframeEmbed(
  itemType: ShareItemType,
  itemId: string,
  title: string,
): string {
  return (
    `<iframe src="${escapeHtml(getEmbedPageUrl(itemType, itemId))}" ` +
    `width="100%" height="${embedIframeHeight(itemType)}" frameborder="0" ` +
    `loading="lazy" allow="encrypted-media; autoplay" ` +
    `title="${escapeHtml(title)}"></iframe>`
  );
}
