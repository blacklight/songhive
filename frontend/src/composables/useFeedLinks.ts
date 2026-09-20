import { onUnmounted, toValue, watch, type MaybeRefOrGetter } from "vue";
import type { FeedUrls } from "@/utils/feeds";

const FEED_LINK_SELECTOR =
  'link[rel="alternate"][type="application/rss+xml"], link[rel="alternate"][type="application/atom+xml"]';

const FEED_LINK_DEFS: ReadonlyArray<{
  key: keyof FeedUrls;
  type: string;
  title: string;
}> = [
  { key: "rss", type: "application/rss+xml", title: "RSS feed" },
  { key: "atom", type: "application/atom+xml", title: "Atom feed" },
];

/** A ``rel="alternate"`` head link: an RSS/Atom feed or similar. */
export interface AlternateLink {
  href: string;
  type: string;
  title: string;
}

/**
 * Replace every RSS/Atom ``rel="alternate"`` link in ``document.head`` with
 * ``links`` (or remove them all when ``links`` is undefined).
 */
export function syncAlternateLinks(
  links: ReadonlyArray<AlternateLink> | undefined,
) {
  document.head
    .querySelectorAll(FEED_LINK_SELECTOR)
    .forEach((el) => el.remove());
  if (!links) {
    return;
  }
  for (const def of links) {
    const link = document.createElement("link");
    link.rel = "alternate";
    link.type = def.type;
    link.href = new URL(def.href, document.baseURI).href;
    link.title = def.title;
    document.head.appendChild(link);
  }
}

/**
 * Replace every RSS/Atom ``rel="alternate"`` link in ``document.head`` with
 * links pointing at ``urls`` (or remove them all when ``urls`` is undefined).
 *
 * The backend injects the same tags into the SPA shell for the initially
 * loaded page; this keeps them accurate after client-side navigation so feed
 * readers that evaluate the DOM (and browser feed-discovery extensions) see
 * the feed of the page currently displayed.
 */
export function syncFeedLinks(urls: FeedUrls | undefined) {
  syncAlternateLinks(
    urls &&
      FEED_LINK_DEFS.map((def) => ({
        href: urls[def.key],
        type: def.type,
        title: def.title,
      })),
  );
}

/**
 * Keep ``document.head``'s RSS/Atom ``rel="alternate"`` links in sync with
 * ``links``. Re-runs when ``links`` changes (e.g. same-view navigation to
 * another entity) and removes the links when the component unmounts.
 *
 * Use this for pages whose feed is an external URL — podcast pages point at
 * the upstream RSS source rather than a ``/feeds`` endpoint.
 */
export function useAlternateLinks(
  links: MaybeRefOrGetter<ReadonlyArray<AlternateLink> | undefined>,
) {
  watch(() => toValue(links), syncAlternateLinks, { immediate: true });
  onUnmounted(() => syncAlternateLinks(undefined));
}

/**
 * Keep ``document.head``'s RSS/Atom ``rel="alternate"`` links in sync with
 * the feed URLs of the page currently rendered. Re-runs when ``urls``
 * changes (e.g. same-view navigation to another entity) and removes the
 * links when the component unmounts.
 */
export function useFeedLinks(urls: MaybeRefOrGetter<FeedUrls | undefined>) {
  useAlternateLinks(() => {
    const value = toValue(urls);
    return (
      value &&
      FEED_LINK_DEFS.map((def) => ({
        href: value[def.key],
        type: def.type,
        title: def.title,
      }))
    );
  });
}
