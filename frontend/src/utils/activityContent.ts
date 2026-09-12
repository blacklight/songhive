/**
 * Render activity ``content`` (sanitized HTML for local activities, arbitrary
 * remote-supplied HTML otherwise) as a flat list of safe segments.
 *
 * The HTML is parsed with ``DOMParser`` and walked: only text, line breaks and
 * links survive — ``<a href>`` values are kept so mentions and URLs in remote
 * notes stay clickable with their real targets, while every other tag is
 * flattened to its text. Bare ``@handle`` mentions and ``#tags`` in plain text
 * are linkified through ``parseRichText``, and the activity's ``mentions``
 * entries supply real actor URLs where the text alone could only guess.
 */

import { parseRichText } from "./richText";
import { parseActorRef } from "./actorRef";

export interface MentionEntry {
  handle: string;
  actor_url?: string | null;
  user_id?: string | null;
}

export type ContentSegment =
  | { type: "text"; value: string }
  /** ``username`` routes to the local profile; ``url`` links out remotely. */
  | { type: "mention"; handle: string; username?: string; url?: string }
  | { type: "tag"; name: string; display: string }
  /** ``to`` is a local route path; ``url`` an external http(s) target. */
  | { type: "link"; label: string; url?: string; to?: string };

export interface ContentOptions {
  instanceDomain?: string;
  mentions?: MentionEntry[];
}

// Elements whose contents must never surface as text.
const SKIP_ELEMENTS = new Set([
  "SCRIPT",
  "STYLE",
  "TEMPLATE",
  "NOSCRIPT",
  "IFRAME",
  "OBJECT",
  "HEAD",
]);

// Elements that introduce a line break before and after their content.
const BLOCK_ELEMENTS = new Set([
  "P",
  "DIV",
  "LI",
  "UL",
  "OL",
  "BLOCKQUOTE",
  "PRE",
  "TR",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
]);

function isSafeHref(href: string): boolean {
  if (href.startsWith("/")) return true;
  try {
    const scheme = new URL(href).protocol;
    return scheme === "http:" || scheme === "https:";
  } catch {
    return false;
  }
}

/** Local route path for ``href`` when it points at this instance, else null. */
function localPathFor(href: string, instanceDomain?: string): string | null {
  if (href.startsWith("/")) return href;
  try {
    const url = new URL(href);
    if (instanceDomain && url.host === instanceDomain) {
      return url.pathname + url.search + url.hash;
    }
  } catch {
    // ignore
  }
  return null;
}

function tagSegment(display: string, href?: string): ContentSegment {
  let name = display.replace(/^#/, "");
  if (!name && href) {
    name = href.split("/").filter(Boolean).pop() ?? "";
  }
  return { type: "tag", name: name.toLowerCase(), display };
}

/**
 * Extract an anchor's visible label, honoring the Mastodon convention of
 * hiding URL chrome in ``.invisible`` spans and marking truncation with
 * ``.ellipsis``.
 */
function anchorLabel(anchor: Element): string {
  let label = "";
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      label += node.textContent ?? "";
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as Element;
    if (el.classList.contains("invisible")) return;
    if (el.classList.contains("ellipsis")) {
      walkChildren(el);
      label += "…";
      return;
    }
    walkChildren(el);
  };
  const walkChildren = (el: Element) => el.childNodes.forEach(walk);
  walkChildren(anchor);
  return label.trim();
}

function classifyAnchor(
  anchor: Element,
  mentionByUrl: Map<string, MentionEntry>,
  instanceDomain?: string,
): ContentSegment[] {
  const href = anchor.getAttribute("href") ?? "";
  const label = anchorLabel(anchor);
  if (!href || !isSafeHref(href)) {
    return label ? [{ type: "text", value: label }] : [];
  }

  const rel = anchor.getAttribute("rel") ?? "";
  const localPath = localPathFor(href, instanceDomain);
  const tagMatch = localPath?.match(/^\/tags\/([^/?#]+)\/?$/);
  if (rel.split(/\s+/).includes("tag") || tagMatch) {
    return [tagSegment(label || `#${tagMatch?.[1] ?? ""}`, href)];
  }

  // Mentions: local actor references route to the profile page; remote ones
  // (or anchors the activity's ``mentions`` list identifies) link out.
  const actor = parseActorRef(href, instanceDomain ?? "");
  if (actor.username) {
    return [
      {
        type: "mention",
        handle: label || actor.handle,
        username: actor.username,
      },
    ];
  }
  const isMention =
    mentionByUrl.has(href) ||
    anchor.classList.contains("mention") ||
    anchor.classList.contains("u-url") ||
    label.startsWith("@");
  if (isMention) {
    return [{ type: "mention", handle: label || actor.handle, url: href }];
  }
  if (localPath) {
    return [{ type: "link", label: label || localPath, to: localPath }];
  }
  return [{ type: "link", label: label || href, url: href }];
}

export function parseActivityContent(
  raw: string,
  options: ContentOptions = {},
): ContentSegment[] {
  const { instanceDomain, mentions = [] } = options;
  const mentionByHandle = new Map(
    mentions.map((m) => [m.handle.toLowerCase(), m]),
  );
  const mentionByUrl = new Map(
    mentions.filter((m) => m.actor_url).map((m) => [m.actor_url as string, m]),
  );

  const segments: ContentSegment[] = [];

  const pushText = (value: string) => {
    if (!value) return;
    const last = segments[segments.length - 1];
    if (last?.type === "text") {
      last.value += value;
    } else {
      segments.push({ type: "text", value });
    }
  };

  const mentionSegment = (
    handle: string,
    username?: string,
    url?: string,
  ): ContentSegment => {
    const entry = mentionByHandle.get(handle.toLowerCase());
    if (entry) {
      const actor = entry.actor_url
        ? parseActorRef(entry.actor_url, instanceDomain ?? "")
        : null;
      if (actor?.username) username = actor.username;
      else if (entry.actor_url) url = entry.actor_url;
      else if (entry.user_id) username = handle.replace(/^@/, "").split("@")[0];
    }
    return { type: "mention", handle, username, url };
  };

  const walkText = (text: string) => {
    for (const token of parseRichText(text, instanceDomain)) {
      if (token.type === "text") {
        pushText(token.value);
      } else if (token.type === "url") {
        const local = localPathFor(token.url, instanceDomain);
        segments.push(
          local
            ? { type: "link", label: token.label, to: local }
            : { type: "link", label: token.label, url: token.url },
        );
      } else if (token.type === "mention") {
        segments.push(
          mentionSegment(
            token.handle,
            token.remote ? undefined : token.username,
            token.remote ? token.url : undefined,
          ),
        );
      } else {
        segments.push({
          type: "tag",
          name: token.name,
          display: token.display,
        });
      }
    }
  };

  // A block box starts on a new line unless we are already at one — e.g.
  // ``…text<p><a>link</a></p>`` must break before the link, not only after.
  const atLineStart = () => {
    const last = segments[segments.length - 1];
    return !last || (last.type === "text" && last.value.endsWith("\n"));
  };

  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      walkText(node.textContent ?? "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as Element;
    if (SKIP_ELEMENTS.has(el.tagName)) return;
    if (el.tagName === "BR") {
      pushText("\n");
      return;
    }
    if (el.tagName === "A") {
      for (const segment of classifyAnchor(el, mentionByUrl, instanceDomain)) {
        if (segment.type === "text") pushText(segment.value);
        else segments.push(segment);
      }
      return;
    }
    const isBlock = BLOCK_ELEMENTS.has(el.tagName);
    if (isBlock && !atLineStart()) pushText("\n");
    el.childNodes.forEach(walk);
    if (isBlock) pushText("\n");
  };

  const doc = new DOMParser().parseFromString(raw, "text/html");
  doc.body.childNodes.forEach(walk);

  // Tidy whitespace artifacts introduced by block-element flattening.
  for (const segment of segments) {
    if (segment.type === "text") {
      segment.value = segment.value.replace(/\n{3,}/g, "\n\n");
    }
  }
  if (segments[0]?.type === "text") {
    segments[0].value = segments[0].value.replace(/^\s+/, "");
  }
  const last = segments[segments.length - 1];
  if (last?.type === "text") {
    last.value = last.value.replace(/\s+$/, "");
  }
  return segments.filter(
    (segment) => segment.type !== "text" || segment.value !== "",
  );
}
