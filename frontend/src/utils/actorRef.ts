/**
 * Parse an actor reference into a local username or remote URL.
 *
 * Actor references appear as ActivityPub actor URLs, relative ``/users/<name>``
 * or ``/@<name>`` paths, or the ``urn:songhive:user:<name>`` fallback used when
 * federation is disabled. Local forms map to the ``userProfile`` route; anything
 * else is a remote actor document link.
 */

export const ACTOR_URN_PREFIX = "urn:songhive:user:";

export interface ActorRef {
  /** Local username — link to the ``/@username`` profile route. */
  username: string | null;
  /** Remote actor URL — link out to the remote document. */
  remoteUrl: string | null;
  /** Display handle: ``@user`` or ``@user@domain``. */
  handle: string;
}

export function parseActorRef(
  actorUrl: string,
  instanceDomain: string,
): ActorRef {
  if (actorUrl.startsWith(ACTOR_URN_PREFIX)) {
    const username = actorUrl.slice(ACTOR_URN_PREFIX.length);
    return { username, remoteUrl: null, handle: `@${username}` };
  }

  const localPath = actorUrl.match(/^\/(?:users\/|@)([^/]+)\/?$/);
  if (localPath) {
    return {
      username: localPath[1],
      remoteUrl: null,
      handle: `@${localPath[1]}`,
    };
  }

  try {
    const url = new URL(actorUrl);
    if (url.host === instanceDomain) {
      const match = url.pathname.match(/^\/(?:users\/|@)([^/]+)\/?$/);
      if (match) {
        return { username: match[1], remoteUrl: null, handle: `@${match[1]}` };
      }
    }
    const shortName = (
      url.pathname.split("/").filter(Boolean).pop() ?? url.hostname
    ).replace(/^@/, "");
    return {
      username: null,
      remoteUrl: actorUrl,
      handle: `@${shortName}@${url.hostname}`,
    };
  } catch {
    return { username: null, remoteUrl: null, handle: actorUrl };
  }
}
