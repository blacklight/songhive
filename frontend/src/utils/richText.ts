export interface TextToken {
  type: "text";
  value: string;
}

export interface UrlToken {
  type: "url";
  url: string;
  label: string;
}

export interface MentionToken {
  type: "mention";
  handle: string;
  username: string;
  domain?: string;
  remote: boolean;
  url?: string;
}

export interface TagToken {
  type: "tag";
  name: string;
  display: string;
}

export type RichTextToken = TextToken | UrlToken | MentionToken | TagToken;

const URL_RE = /https?:\/\/[^\s<>"']+/g;
const MENTION_RE = /(?<![\w@/])@([a-zA-Z0-9_.-]+(?:@[a-zA-Z0-9.-]+)?)\b/g;
const TAG_RE = /(?<![\w&#])#[A-Za-z0-9_]+/g;

const TOKEN_RE = new RegExp(
  `${URL_RE.source}|${MENTION_RE.source}|${TAG_RE.source}`,
  "g",
);

const TRAILING_PUNCTUATION = ".,;:!?'\"";
const BRACKET_PAIRS: Record<string, string> = {
  ")": "(",
  "]": "[",
  "}": "{",
};
const LOCAL_USERNAME_RE = /^[a-zA-Z0-9_-]+$/;
const VALID_TAG_RE = /^(?=.*[a-z])[a-z0-9_]+$/;

function countChar(str: string, char: string): number {
  let count = 0;
  for (let i = 0; i < str.length; i++) {
    if (str[i] === char) count++;
  }
  return count;
}

function splitTrailingPunctuation(raw: string): [string, string] {
  let trailing = "";
  while (raw) {
    const last = raw[raw.length - 1];
    if (
      TRAILING_PUNCTUATION.includes(last) ||
      (last in BRACKET_PAIRS &&
        countChar(raw, last) > countChar(raw, BRACKET_PAIRS[last]))
    ) {
      trailing = last + trailing;
      raw = raw.slice(0, -1);
    } else {
      break;
    }
  }
  return [raw, trailing];
}

function isLinkableUrl(url: string): boolean {
  if (!url || /[\s<>"']/.test(url)) return false;
  try {
    const parsed = new URL(url);
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      !!parsed.hostname
    );
  } catch {
    return false;
  }
}

function displayUrl(url: string): string {
  const withoutScheme = url.replace(/^https?:\/\//, "");
  if (
    withoutScheme.endsWith("/") &&
    !withoutScheme.slice(0, -1).includes("/")
  ) {
    return withoutScheme.slice(0, -1);
  }
  return withoutScheme;
}

function isRemoteDomain(domain: string, instanceDomain?: string): boolean {
  if (instanceDomain) {
    return domain.toLowerCase() !== instanceDomain.toLowerCase();
  }
  return domain.includes(".");
}

function parseMention(
  raw: string,
  instanceDomain?: string,
): MentionToken | TextToken {
  const body = raw.slice(1);
  const [username, domain] = body.includes("@")
    ? (body.split("@", 2) as [string, string])
    : ([body, undefined] as [string, undefined]);

  if (!username) {
    return { type: "text", value: raw };
  }

  const normalizedUsername = username.toLowerCase();

  if (domain) {
    const normalizedDomain = domain.toLowerCase();
    if (isRemoteDomain(domain, instanceDomain)) {
      return {
        type: "mention",
        handle: `@${normalizedUsername}@${normalizedDomain}`,
        username: normalizedUsername,
        domain: normalizedDomain,
        remote: true,
        url: `https://${normalizedDomain}/@${normalizedUsername}`,
      };
    }
    if (!LOCAL_USERNAME_RE.test(username)) {
      return { type: "text", value: raw };
    }
    return {
      type: "mention",
      handle: `@${normalizedUsername}`,
      username: normalizedUsername,
      remote: false,
    };
  }

  if (!LOCAL_USERNAME_RE.test(username)) {
    return { type: "text", value: raw };
  }

  return {
    type: "mention",
    handle: `@${normalizedUsername}`,
    username: normalizedUsername,
    remote: false,
  };
}

function parseTag(raw: string): TagToken | TextToken {
  const name = raw.slice(1).toLowerCase();
  if (!VALID_TAG_RE.test(name)) {
    return { type: "text", value: raw };
  }
  return { type: "tag", name, display: raw };
}

function parseUrl(raw: string): UrlToken | TextToken {
  const [url] = splitTrailingPunctuation(raw);
  if (!isLinkableUrl(url)) {
    return { type: "text", value: raw };
  }
  return { type: "url", url, label: displayUrl(url) };
}

export function parseRichText(
  text: string,
  instanceDomain?: string,
): RichTextToken[] {
  const tokens: RichTextToken[] = [];
  let pos = 0;

  for (const match of text.matchAll(TOKEN_RE)) {
    const index = match.index ?? 0;
    if (index > pos) {
      tokens.push({ type: "text", value: text.slice(pos, index) });
    }

    const raw = match[0];

    if (raw.startsWith("http")) {
      const parsed = parseUrl(raw);
      tokens.push(parsed);
      if (parsed.type === "url" && parsed.url !== raw) {
        const trailing = raw.slice(parsed.url.length);
        if (trailing) {
          tokens.push({ type: "text", value: trailing });
        }
      }
    } else if (raw.startsWith("@")) {
      tokens.push(parseMention(raw, instanceDomain));
    } else if (raw.startsWith("#")) {
      tokens.push(parseTag(raw));
    }

    pos = index + raw.length;
  }

  if (pos < text.length) {
    tokens.push({ type: "text", value: text.slice(pos) });
  }

  return tokens;
}
