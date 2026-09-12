# CHANGELOG

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- `frontend`: Support for switching theme and accent colors from the
  `/settings` page.

### Fixed

- `frontend`: Editing an activity now refreshes its `ActivityCard`
  immediately everywhere it appears. `store.update` previously only
  patched `store.items`, which powers `ActivityFeed`; cards rendered from
  the profile posts/activity tabs, tag detail activity lists, and
  notifications kept showing the stale copy until reload. The activities
  store now keeps a per-id cache of the latest PATCH result
  (`updatedActivity`) plus a `removedIds` set (`isRemoved`), and
  `ActivityCard` renders the cached copy when present and hides itself
  after deletion.

## 0.1.1

### Added

- `auth`: Browser sessions are now authenticated with server-managed
  `HttpOnly` cookies instead of JavaScript-readable JWTs. Login and
  refresh set `access_token` and `refresh_token` `HttpOnly` cookies
  (the latter scoped to `Path=/api/v1/auth`) plus a readable
  `csrf_token` cookie; logout clears them. `api/cookies.py` centralizes
  cookie issuance with new `auth.cookie_secure` (Secure by default
  outside debug mode), `auth.cookie_samesite` (default `lax`), and
  `auth.cookie_domain` options. Refresh and logout accept the refresh
  token from either the JSON body (API clients) or the cookie
  (browsers), failed refreshes clear stale cookies, and
  `GET /api/v1/auth/sessions` marks the caller's current session by
  hashing the refresh cookie when `current_session_id` is omitted.
- `security`: New `CsrfMiddleware` (`api/middleware/csrf.py`) enforces a
  double-submit check: unsafe requests that carry an auth cookie and no
  `Authorization` header must echo the `csrf_token` cookie in
  `X-CSRF-Token`. Session-lifecycle endpoints (login, register, refresh,
  logout, password reset, OAuth token endpoints) are exempt; bearer
  clients and safe methods are unaffected.
- `api`: Read-only media endpoints (`GET /api/v1/files/{id}/download`,
  `GET /api/v1/tracks/{id}/download`, `GET /api/v1/stream/{id}`) now
  return `Access-Control-Allow-Origin: *` so remote Fediverse web
  clients (e.g. Akkoma/Mangane) can embed audio directly.
  `MediaCorsMiddleware` answers preflights (allowing `Range`) and
  exposes `Content-Range`/`Accept-Ranges`/`Content-Length`; `Vary:
  Origin` plus pass-through for configured `cors_origins` keeps the
  credentialed allowlist working, and the Tornado `StreamHandler` sets
  the same headers itself since it bypasses FastAPI middleware.
- `notifications`: User notifications for follows, likes, boosts, quotes,
  replies, mentions, and shares. `Notification` and
  `NotificationPreference` models back per-type delivery targets (in-app,
  individual email, daily email digest); `services/notifications.py`
  resolves preferences, deduplicates unseen rows, pushes targeted
  WebSocket events (`EventWebSocket.send_to_user`), and enqueues
  notification emails for verified addresses. Authenticated routes under
  `/api/v1/notifications/` provide the paginated list, unread count,
  seen/unseen management, and the preference matrix; scheduled Celery
  tasks send the daily digest and purge seen rows past
  `notifications.retention_days`. Admins can purge manually via
  `POST /api/v1/admin/notifications/purge` (audited), the
  `/admin/tasks` page, or `songhive admin purge-notifications`.
  The frontend gains a
  `/notifications` page with a live unread nav badge, toast alerts for
  incoming events, IntersectionObserver auto-seen, and a per-type
  delivery matrix under `/settings?tab=notifications`.
  Notifications are retracted when their event is undone: local
  unfavorites and share-grant revocations, activity/entity deletion, user
  deletion, and federated `Undo`/`Delete` activities all remove the
  related rows and push a `notification_deleted` WebSocket event to the
  recipient. Recipients can also dismiss notifications individually or in
  bulk, mark selections read/unread, and clear everything via
  `DELETE /notifications/{id}`, `POST /notifications/delete`, and
  `POST /notifications/clear`. The list can be narrowed by type with a
  multi-select pill filter (union semantics, empty = all types) backed by
  the `type` CSV allowlist on `GET /api/v1/notifications/`; incoming
  events of filtered-out types still bump the unread badge but stay off
  the list.
  Notification rows carry a denormalized payload — actor display
  name/avatar, note content snapshots, and resolved local item references —
  so `/notifications` renders rich context: a read-only activity card for
  mentions/replies/quotes, a user card for follows and remote likes/boosts,
  and a title-and-cover item card for shares and local likes/boosts.
  Editing an activity propagates to its notifications: local activity and
  track metadata edits, and incoming federated `Update` activities, refresh
  the stored content/actor/item fields and push a `notification_updated`
  WebSocket event; when an edit removes the notification's basis (an
  unmentioned recipient, a retargeted reply/quote) the row is retracted
  instead.

### Fixed

- `notifications`: Liking another local user's activity
  (`POST /api/v1/activities/{id}/like`) now creates a `like` notification
  for the activity's owner — the hook previously only notified remote
  authors through federation fan-out. The row records the like's own
  object id so retracting the like (unlike) removes it.
- `websockets`: WebSocket events emitted outside the web process (e.g.
  federated follow/like/boost/reply/mention notifications created by the
  Celery worker, or `import.*` broadcasts) now reach connected clients.
  `broadcast`/`send_to_user` publish an envelope to the Redis channel
  `songhive:ws-events`, and the Tornado process fans it out to local
  connections via `ws_event_subscriber`.
- `websockets`: Repeated unauthenticated handshakes (e.g. stale frontend
  builds retrying an expired token every second) are now throttled
  server-side: consecutive auth failures per `(remote IP, token)` delay
  the `4001` close exponentially, capped at 30s.

### Changed

- `frontend`: The SPA no longer stores JWTs in `localStorage` or in a
  JavaScript-readable cookie. `api/client.ts` sends `credentials:
  "same-origin"` on every request and echoes `X-CSRF-Token` on unsafe
  methods (re-reading the rotated cookie after a 401 refresh); the auth
  store persists only the non-sensitive user profile and bootstraps via
  `GET /api/v1/users/me`; stream URLs and the WebSocket handshake carry
  no token — `StreamHandler` and `EventWebSocket` accept the
  `access_token` cookie, with `?token=` still supported on the socket
  for API clients. Bearer authentication and the JSON token pair in
  login/refresh responses are unchanged for non-browser clients.
  Same-origin SPA/API hosting is assumed (production static serving and
  the Vite dev proxy both provide it); upgrading browsers drop the
  legacy localStorage tokens and sign in once to get the new cookies.

## 0.1.0

### Added

- `tags`: Add an "Activities" section to tag detail pages. A new
  `activity_tags` association table links `Activity` and `Tag` rows.
  Activities that contain hashtags are parsed on local creation,
  update, federated track publication, and track metadata resync.
  `GET /api/v1/tags/{tag}/activities` returns a cursor-paginated
  `ActivityListResponse` filtered by activity visibility and containing
  entity access. The frontend `TagDetailView` gains an optional
  `activityLoader` prop and renders matched `ActivityCard`s in a new
  "Activities" tab with a "Load more" button.
- `frontend`: Add activity feeds for entities. A shared
  `EntityActivitiesView` (lazy routes `/{track|album|artist|playlist|library}/{id}/activities`,
  linked from each entity detail page's actions and from the track
  list's per-row context menu) renders
  `components/activities/ActivityFeed.vue` — filter tabs mapping to the
  endpoint's `activity_type`/`source_type` params, keyset `cursor`
  "load more" pagination, and per-activity `ActivityCard`s with a like
  action (`POST /api/v1/activities/{id}/like`) and an owner/admin edit
  modal (`PATCH /api/v1/activities/{id}`). Backed by
  `stores/activities.ts` and `api/activities.ts`; remote activity content
  is stripped to plain text rather than trusting remote-supplied HTML.
- `federation`: Extend `GET /users/{username}/objects/{object_id}` to
  dereference activities in addition to tracks. Activities resolve by
  `local_object_id`/`source_id` under their owner's namespace;
  soft-deleted activities are served as `Tombstone` objects (the shared
  `federation/activities.build_tombstone_object` shape, mirroring the
  object embedded in `Delete` deliveries), live
  payload-bearing activities return their stored AP document — `Create`
  envelopes are unwrapped so the embedded object is served, since the
  activity's `source_id` identifies the object — and
  payload-less content activities are served as a `Note` synthesized by
  `federation/activities.build_activity_object`. Only federating
  visibilities (`mentioned`/`followers`/`public`) are served. Since
  object URLs double as the objects' own `url`, clients not accepting an
  ActivityStreams media type are redirected to the SPA: a track-resolved
  object goes to the track page, an activity-resolved object to the
  entity's activity feed.
- `federation`: `GET /tracks/{track_id}` now redirects ActivityPub fetches
  (303 See Other) to the track's earliest surviving local share when no
  published `Audio` object exists, so a remote URL search (e.g. pasting a
  track link into Mastodon) resolves to the first `Note` post instead of
  answering 404.
- `federation`: Add activity interactions — `POST
  /api/v1/activities/{id}/like` records an idempotent `like` activity that
  inherits the target's visibility, stores an ActivityPub `Like` payload
  (`federation/activities.create_like_activity`), and fans out through
  `services/activities.fan_out_like_activity`.
  `services/activities.can_view_activity` centralizes who may see an
  activity (entity ACL + per-activity visibility).
- `federation`: Add visibility-driven fan-out with per-inbox bookkeeping
  (`services/activities.resolve_audience` and `fan_out_activity`).
  `public`/`followers` activities reach the author's follower inboxes
  (`services/federation.get_follower_inboxes`, backed by
  `pubby.collect_inboxes`) plus remote mentioned actors
  (`services/federation.resolve_actor_inbox` reads the
  `federation_actor_cache` before a signed actor-document fetch);
  `mentioned` activities reach mentioned actors only, and
  `private`/`local` never federate. Each resolved inbox is recorded as an
  `ActivityTarget` row (`sent`/`failed`/`skipped`, with `attempts`,
  `last_error`, and `last_attempt_at`) and delivered via
  `tasks.federation.deliver_activity`; likes on remote activities also
  reach the liked author's inbox.
- `federation`: Add `PATCH /api/v1/activities/{id}` for activity edits.
  Content changes re-run the mention pipeline in
  `services/activities.update_activity` — `@handle`s are re-resolved, the
  rendered `content` and `activity_mentions` rows are replaced, and an
  embedded payload object's `content`/`tag` are rebuilt via
  `pubby.set_object_content` plus the pipeline's `Mention` tags — and fan
  an `Update` carrying the rebuilt object (stamped with `updated`) out to
  the inboxes recorded as `sent` via
  `services/activities.fan_out_activity_update`; inboxes first reached by
  the edit (e.g. newly mentioned actors) receive the stored `Create`
  instead. Visibility changes flow through
  `VisibilityRules.cascade_visibility_update` so inboxes that already
  received the activity get an `Update` or a `Delete(Tombstone)`.
- `federation`: Add a mention pipeline (`services/mentions.py`) that
  extracts `@user`/`@user@domain` handles from activity content, resolves
  local handles against the users table and remote handles via WebFinger
  (`pubby.resolve_actor_url`) with instance allow/block gating, and renders
  safe HTML plus ActivityPub `Mention`/`Hashtag` tags
  (`pubby.render_link_anchor`, `pubby.render_post_html`).
- `federation`: Add the public activity read endpoint `GET
  /api/v1/{entity_type}/{entity_id}/activities` backed by
  `services/activities.list_activities`. Anonymous requesters may read
  `public` activities on publicly accessible entities; authenticated users
  additionally see `local`/`followers` activities, their own activities, and
  `mentioned` activities that name them, with per-activity visibility
  enforced in SQL so keyset pagination on `(published_at, id)` stays
  correct. Supports `activity_type`/`source_type` filters, an opaque
  base64url `cursor`, and `limit` (1–100, default 20).
- `federation`: Add `DELETE /api/v1/activities/{id}` for activity
  retraction. Behind the same `acl.can_manage` gate as `PATCH`, it calls
  `services/activities.retract_activity`: local activities are soft-deleted
  and a `Delete(Tombstone)` is fanned out to every inbox recorded as `sent`
  in `activity_targets`, while remote activities are simply removed
  locally.
- `frontend`: Activity cards gain an owner/admin delete action (confirm
  dialog → `DELETE /api/v1/activities/{id}`) so federated shares can be
  retracted from the feed.
- `federation`: Add post visibility and object-type selection to track
  publication. `POST /api/v1/tracks/{id}/publish` accepts an optional
  `visibility` (default `public`) which is stored on the recorded `create`
  activity and drives the `to`/`cc` addressing of both the `Create`
  envelope and the embedded object via `activity_audience`;
  `public`/`followers`
  reach follower inboxes plus remote mentioned actors, `mentioned` reaches
  only the mentioned actors, and `private`/`local` record the share without
  federating it. An optional `object_type` (default `note`) selects the
  federated object shape: `note` shares the track as a `Create(Note)` —
  the post body renders on remote servers that drop `content` on `Audio`
  objects (e.g. Mastodon) — with the stream embedded as an `Audio`-typed
  attachment linked to the track's canonical object; `audio` republishes
  the canonical `Create(Audio)` media object, minting a fresh
  `federation_object_id` per publication. A `Note` share's `url` is its
  own object id rather than the track page: the track URL is the
  canonical `Audio` object's identity (it dereferences to the `Audio`
  document, which is why a remote URL search returns only that object),
  and giving the share its own `url` keeps the two objects' identities
  distinct. The track page still ends the share's `content` as the
  appended link `normalize_post_content` emits — the page is now passed
  explicitly (`link_href`) since the object's `url` no longer carries it. The one-off `status` now also
  runs through the
  `process_mentions` pipeline — `@handle`s are resolved into
  `activity_mentions` rows, `Mention` tags, and mention-aware `content` —
  so `mentioned` publications have a deliverable audience. The Share →
  Fediverse tab gains a visibility picker and a `Note`/`Audio` selector
  with guidance text, and visibility edits now rewrite
  the stored payload's `to`/`cc` (`cascade_visibility_update`) so
  re-deliveries carry the current audience; `sync_track_publications`
  re-applies the stored audience and `Mention` tags when rebuilding the
  object after a metadata edit.
- `federation`: Make upload-time federation publication opt-in. The
  `/api/v1/files/upload` endpoints and `/{library_id}/tracks` uploads take
  a `publish` flag (default `false`); without it, uploaded tracks stay
  local with no `federation_object_id` and no publication activity. The
  flag is carried through bulk uploads, the synchronous library import
  path, the `process_upload` Celery task (which defaults it to `true` so
  directory scans keep publishing), and the external-duplicate resolution
  token so the choice survives the pending-upload round-trip. The instance
  endpoint exposes `federation_enabled`, and the upload forms show a
  "Publish on the Fediverse" checkbox only when the instance federates and
  the upload visibility is `public`.
- `ui`: Entity detail pages (library, playlist, album, track) now render
  the owner with the `UserLink` component, linking local users to
  `/@{username}` and remote users to their `actor_url`. `useEntityMeta`
  returns the full `UserSummary` owner object, and backend detail responses
  expose `owner_id` for any ACL-authorized viewer and include a nested
  `owner` summary via `?include=owner`.
- `pwa`: Add PWA support. ([`a662a0c`](https://git.platypush.tech/blacklight/songhive/commit/a662a0c013513419489910eb9f8b5bfe1d4e6f23))
- Add systemd units and installer; extend config lookup. ([`832c3b8`](https://git.platypush.tech/blacklight/songhive/commit/832c3b8a39549915565657affd7975afc9b56a43))
- `history`: Add card layout for narrow viewports. ([`8026188`](https://git.platypush.tech/blacklight/songhive/commit/80261888de01c0089a91622115d4ab66b1ac4b77))
- `frontend`: Add bulk track metadata editor. ([`d9c6423`](https://git.platypush.tech/blacklight/songhive/commit/d9c6423993a03c88f403b67e9c9a3d5b4b0a00af))
- `federation`: Fan out actor profile updates to followers. ([`3faf70b`](https://git.platypush.tech/blacklight/songhive/commit/3faf70bf1c1defcd618961ed104f5d3301c779aa))
- `tracks`: Add descriptions and manual ActivityPub publish. ([`dd07a69`](https://git.platypush.tech/blacklight/songhive/commit/dd07a69079b04f7e62dc56f101a496ebb559571b))
- Add federation activity models and migration. ([`f4a1dd7`](https://git.platypush.tech/blacklight/songhive/commit/f4a1dd70c76f28d55f8c2f6090dee78267f9f9bf))
- Add activity service and cascade retraction on entity delete. ([`b934356`](https://git.platypush.tech/blacklight/songhive/commit/b934356391b176d913a5f969e6a3b54a7c26fa60))
- `api`: Include source actor avatar URL in activity list responses. ([`0b31ace`](https://git.platypush.tech/blacklight/songhive/commit/0b31ace444dad9c61ef899d34296d56290581b9d))
- `activities`: Add actor display name and visibility-aware audience. ([`675fe27`](https://git.platypush.tech/blacklight/songhive/commit/675fe27a190219f68cc15c11aac49bcfbf36c672))
- `activity-card`: Add copy URL action button. ([`f8032c9`](https://git.platypush.tech/blacklight/songhive/commit/f8032c92c8780ce90f32af35f3f3bb84bbb4946e))
- Add user profiles, user directory, and tag activity feeds. ([`a54df6a`](https://git.platypush.tech/blacklight/songhive/commit/a54df6a336b4d980cacd931b953e8acd6084b902))
- `search`: Implemented general-purpose search. ([`e5028b6`](https://git.platypush.tech/blacklight/songhive/commit/e5028b685ff60951188ad462a7df739c00131690))
- `frontend`: Replace static input text for user shares with SearchBar. ([`bbed416`](https://git.platypush.tech/blacklight/songhive/commit/bbed4160c4970abf4d8a08f36d9207d3418f510c))
- Add bulk add-to-library/playlist for selected tracks. ([`b580f28`](https://git.platypush.tech/blacklight/songhive/commit/b580f2856dd8034b10a981d5052516e5a81797a8))
- `frontend`: Added links to author and post on ActivityCard. ([`95d0fd4`](https://git.platypush.tech/blacklight/songhive/commit/95d0fd47901cbe746765af4208bb2abf368f4d3e))

### Changed

- `federation`: Track publications are now recorded as local `create`
  activities. Every publish path — `POST /api/v1/tracks/{id}/publish`,
  uploads and imports of public tracks that opt in with `publish=true`,
  bulk library uploads, Celery `process_upload`, and entity visibility
  transitions to `public` — calls
  `services/activities.record_track_publication`, which stores the
  `Create` payload on an `Activity` row (entity- and
  `local_object_id`-linked to the track, `source_id` matching the
  published object URL) and fans it out through `fan_out_activity` so each
  delivered inbox is tracked in `activity_targets`. Publications therefore
  appear in the entity's activity feed and can be retracted per-activity.
  The superseded `services/federation.publish_track_activity` is removed;
  `unpublish_track_activity` still drives track-level `Delete(Tombstone)`
  on visibility loss and entity deletion, and
  `services/activities.retract_track_publications` retracts every live
  publication row — the canonical `Audio` and any `Note` shares — through
  `retract_activity`, delivering a per-publication `Delete(Tombstone)` for
  each `source_id` when a track leaves the fediverse. Metadata edits on a
  published track — `PATCH /api/v1/tracks/{id}` touching `title`,
  `artist_name`, `description`, or `genre` — re-sync the stored object via
  `services/activities.sync_track_publications`, dispatching on the stored
  object `type` (`Audio` or `Note`), and fan an `Update`
  carrying the rebuilt object out to delivered inboxes; a one-off
  `status` used at publication time is preserved as the post body.
- `federation`: Delegate federation primitives to pubby 0.3.2 — domain
  normalization and allow/block matching (`pubby.moderation`), async→sync
  database URL conversion (`pubby.storage.adapters.db.to_sync_url`), actor
  key provisioning (`pubby.crypto.ensure_private_key_file`), content and
  duration rendering (`pubby.content.set_object_content`,
  `pubby.content.format_duration`), follower inbox collection
  (`pubby.collect_inboxes`), one-shot signed delivery
  (`pubby.deliver_activity`), `Like` and `Delete(Tombstone)` payload
  building (`pubby.build_like_activity`, `pubby.build_delete_activity`),
  and remote actor inbox resolution (`pubby.resolve_actor_inbox`).
  Songhive keeps Celery orchestration, the retry policy, per-user actor
  documents, `federation_*` table naming, and the
  `Visibility`-to-audience adapter in `federation.activities`.
- `federation`: The instance-level `/ap` inbox and pubby's outbound fan-out
  now enforce the configured `allowed_instances`/`blocked_instances` lists.
- `federation`: The federated `Audio` object's `name` is now an anchor —
  `<a href="{track_url}">{artist} - {title}</a>` — so Mastodon-family
  servers render the post header as a link to the track page instead of
  the bare title. (Mastodon interpolates `name` unescaped into the
  converted-object `<h2>`; the appended track-URL line it adds on its own
  is not under our control, and is rendered as plain text there because
  Mastodon's link extractor does not recognize the `.music` gTLD.)
- `federation`: Federated `Audio` durations are now emitted as proper
  ISO-8601 strings with hour support (e.g. `PT1H2M3S` for tracks ≥ 1 hour,
  `PT2M` instead of `PT2M0S`).
- `federation`: Outbound deliveries now send a `User-Agent: pubby/<version>`
  header and also sign `Content-Length`; `Accept` is no longer sent.
- `requirements`: Security upgrade for cryptography. ([`e0b3b91`](https://git.platypush.tech/blacklight/songhive/commit/e0b3b917dd233532025d867e0fc92d355b5e6d9f))
- Better dark theme colors and link color consistency. ([`a9ab660`](https://git.platypush.tech/blacklight/songhive/commit/a9ab660c29caa3e13e7299580c225af88f0947a6))
- `activities`: Better responsive design for Activity cards. ([`e1986d0`](https://git.platypush.tech/blacklight/songhive/commit/e1986d0c24d490cf688e7da70ab5fcee0519a6b2))
- `favicon`: More compact favicon, better transparency. ([`986b804`](https://git.platypush.tech/blacklight/songhive/commit/986b804f4367b2b41ac25dec7947f92710013de6))
- `nav`: Keep footer at bottom, apply overflow only to nav content. ([`921bd3d`](https://git.platypush.tech/blacklight/songhive/commit/921bd3d292bfc44c6a39343304bc2c67d1093d4e))

### Fixed

- `federation`: Published `Audio` objects now render their post text on
  Mastodon-family servers: they treat `Audio` as a "converted" object type
  and display `name`/`summary`/`url` instead of `content`, so the rendered
  description (or the one-off `status` from `POST
  /api/v1/tracks/{id}/publish`) is now mirrored into `summary`. The `url`
  links also carry `mimeType` alongside `mediaType` so remote link
  selection picks the `text/html` track page rather than the raw audio
  download URL.
- `federation`: Tracks shared via Share → Fediverse (and any other publish
  path) now appear under `/{entity}/{id}/activities` and in the
  `activities` table. Publication previously only delivered a
  `Create(Audio)` to follower inboxes without recording an `Activity` row,
  so the share was invisible in the feed and could not be retracted.
- `federation`: Line breaks in federated post text are now preserved on
  remote servers. `pubby.render_post_html`/`pubby.render_bio_html` convert
  newlines to `<br>` elements — ActivityPub `content`/`summary` are HTML
  fields, and Mastodon-family renderers collapsed literal newlines into
  spaces. This applies to activity `content`, the `Audio` object's
  `content`/`summary`, and actor bios.
- `federation`: Published track URLs are now resolvable from remote
  servers, so pasting `https://{domain}/tracks/{id}` into e.g. Mastodon's
  search box imports the post. `GET /tracks/{track_id}` content-negotiates:
  clients accepting `application/activity+json`/`application/ld+json`
  receive the track's `Audio` object, while browsers get the SPA shell
  annotated with a `Link: rel="alternate"` header and a
  `<link rel="alternate">` element pointing at the object URL. The object
  is only served while the track is published (`federation_object_id`
  set). Served track objects — at both the track page and
  `/users/{username}/objects/{id}` — now carry a top-level `@context`
  (context-less documents were rejected upstream) and the `public`
  `to`/`cc` audience (audience-less objects imported as direct-only),
  and `attributedTo` leads with the publishing actor instead of the
  non-dereferenceable artist page URL so remote importers resolve the
  author. `docker/nginx.conf` proxies AP `Accept` requests for
  `/tracks/<id>` to the backend while browsers keep getting the SPA.
- `layout`: Respect safe-area insets and raise sidebar z-index. ([`dfb6d21`](https://git.platypush.tech/blacklight/songhive/commit/dfb6d2159f58ba56bce5fd996fe475a1c48a74f1))
- `albums`: Propagate visibility changes to tracks. ([`3c64123`](https://git.platypush.tech/blacklight/songhive/commit/3c641237b6f7847603b0884e532e37255909e307))
- `profile`: Always send updated links on profile save. ([`a89f830`](https://git.platypush.tech/blacklight/songhive/commit/a89f8304d375d8418c52d62bccac8dd229dab399))
- `federation`: Sanitize and linkify actor profile links. ([`9b1e53f`](https://git.platypush.tech/blacklight/songhive/commit/9b1e53f7827e2cecc28475ab743207cb539f3e7c))
- `federation`: Linkify bio URLs and simplify profile link text. ([`acf6be2`](https://git.platypush.tech/blacklight/songhive/commit/acf6be2bd37a5000a26995ac865d5d7ece4818c3))
- `activities`: Return updated activity and audit mutations. ([`de708c2`](https://git.platypush.tech/blacklight/songhive/commit/de708c23b1a9b3ca2730065bf240026b7d039dd1))
- `share`: Map user shares both to user_id and username. ([`1c8343a`](https://git.platypush.tech/blacklight/songhive/commit/1c8343ab759844f5598a45c791fe8d22d42e375b))
- `search`: Increase autocompleteDelay: 300->750 ms. ([`2b199bc`](https://git.platypush.tech/blacklight/songhive/commit/2b199bcf1eeb35380c62950eacf8a3a308b3e1d4))
- `frontend`: Include artist and album info on /@{user}/tracks. ([`5bc55a5`](https://git.platypush.tech/blacklight/songhive/commit/5bc55a5a967d5e7b520cd563e8a2744fc4e22cc4))
- `frontend`: Pagination for /@{user}/(tracks|albums|playlists|libraries). ([`7af69ea`](https://git.platypush.tech/blacklight/songhive/commit/7af69eaf689d52ed6ccca1bb1095fec2ecad463f))
- `share`: Tracks in an album should be shared when the album is shared. ([`380c970`](https://git.platypush.tech/blacklight/songhive/commit/380c970349da666c33c9257fea65fa9c6babf329))
- `share`: Tracks in a playlist should be shared when the playlist is shared. ([`74a505b`](https://git.platypush.tech/blacklight/songhive/commit/74a505beff24eea49e6c04d61558e100df205587))
- `share`: Tracks in a library should be shared when the library is shared. ([`95a3e60`](https://git.platypush.tech/blacklight/songhive/commit/95a3e601122008a16befc1dac365ac1866c02c9d))

## 0.0.15

### Added

- Add external library support with syncing, streaming, upload duplicate
  handling, and a management UI. ([`a4746e3`](https://git.platypush.tech/blacklight/songhive/commit/a4746e35b907a81eb8cc9986be25b716bb7f7532))
- `external-libraries`: Add a local filesystem adapter with filesystem-change
  watchdog, provider templates, and optional source-file deletion for external
  libraries. ([`efda28f`](https://git.platypush.tech/blacklight/songhive/commit/efda28fe61132f2e2ca789d0089410c126faa302))
- Add separate loading states for pagination in track lists. ([`1ad5f3f`](https://git.platypush.tech/blacklight/songhive/commit/1ad5f3f1f258abcac86d209d46620f5eade485c6))
- `files`: Add a cancel upload button for single and bulk in-progress uploads. ([`12dee39`](https://git.platypush.tech/blacklight/songhive/commit/12dee39a59334db1c22b0bcb4d9982636a24ba57))
- `tracks`: Support renaming a track's source filename in the API and UI, and
  harden download filename sanitization. ([`95ae8ca`](https://git.platypush.tech/blacklight/songhive/commit/95ae8ca995b5d8c5a5b520028deccbf7c8e7fa7f))
- Handle duplicate playlist track additions by returning a conflict, allowing an
  override, and adding a confirmation UI. ([`55e46e6`](https://git.platypush.tech/blacklight/songhive/commit/55e46e6b1a586e95f8ce6aaef8ec099d1526ad31))

### Fixed

- `external-libraries`: Correct sync runs, PATCH null handling, and UI polling. ([`695771b`](https://git.platypush.tech/blacklight/songhive/commit/695771b5e3545c8f61a9a87e40acf1d21c3cdd06))
- `music`: Filter tracks and albums by normalised genre associations, ensuring
  all tagged tracks appear in genre listings and API results. ([`57fbaa2`](https://git.platypush.tech/blacklight/songhive/commit/57fbaa29d0f2ca2b9b119045cd5aa2295b7f984f))

## 0.0.14

### Added

- Add playlist track reorder endpoint ([`b73c52c`](https://git.platypush.tech/blacklight/songhive/commit/b73c52ccb1c4948bcddeb1ffb8ad3b9f4e53b3a2))
- `frontend`: Add playlist track reordering UI and API support ([`dbfc31f`](https://git.platypush.tech/blacklight/songhive/commit/dbfc31fdcb391e00ff6ec11f9a2df0a53eee16e8))
- `files`: Add bulk file upload endpoint with size and count limits ([`393db57`](https://git.platypush.tech/blacklight/songhive/commit/393db57d47e702a76ccb350522196735cb29b189))
- `files`: Wire up bulk upload endpoint in the frontend ([`5ebb1ec`](https://git.platypush.tech/blacklight/songhive/commit/5ebb1ec3c6722325e568566abd224f2979158fcd))

### Changed

- `acl`: Batch track access checks with select-in queries ([`c48cea0`](https://git.platypush.tech/blacklight/songhive/commit/c48cea0b54283765d633afbc4ef66579f27b1a24))
- Update config example and architecture config keys ([`0852e58`](https://git.platypush.tech/blacklight/songhive/commit/0852e58d063859b5f901d1795e8784da04aeeb3e))

### Fixed

- `files`: Report upload progress when total is missing ([`a1f0452`](https://git.platypush.tech/blacklight/songhive/commit/a1f04526ff8787f2ecfde8ab59404f0d490c658f))
- `redis`: Use dedicated client for Tornado loop ([`653cdcc`](https://git.platypush.tech/blacklight/songhive/commit/653cdcc1a6fa21d38493d8f43a82684b1971fa77))

## 0.0.13

### Added

- `auth`: Add refresh-token session listing and revocation. ([`b519b11`](https://git.platypush.tech/blacklight/songhive/commit/b519b11a04009721d6fc2ec3d59608af1aebd80f))
- `auth`: Denylist access JWTs on session revocation. ([`f6f8e18`](https://git.platypush.tech/blacklight/songhive/commit/f6f8e184040e012cb619082fce7466c0905ca5dc))

### Fixed

- `stats`: Cast db aggregates to int for Redis cache. ([`8905919`](https://git.platypush.tech/blacklight/songhive/commit/890591928f73d0177519da67956282c69bd0c369))
- `docker`: Preserve client port in proxy Host header. ([`1d88ee0`](https://git.platypush.tech/blacklight/songhive/commit/1d88ee08c0eb51b22611265ff8515a3576219ae7))
- `storage`: Include all stored file references in orphaned cleanup. ([`24425ea`](https://git.platypush.tech/blacklight/songhive/commit/24425eaf7196346d5b63d42445f8c6671195f9ad))

## 0.0.12

### Added

- `i18n`: Add preview label to en locale. ([`f408805`](https://git.platypush.tech/blacklight/songhive/commit/f4088054d095102da4da4177ed80177f6c0a8db9))

### Changed

- `files-view`: Improved progress bar color contrast. ([`2777fd5`](https://git.platypush.tech/blacklight/songhive/commit/2777fd548d590f22d08d97d4c694e8348ac9a061))

### Fixed

- `db`: Use `NullPool` for the async engine by default so Tornado and a2wsgi
  request loops each create fresh asyncpg connections, preventing
  ``Future attached to a different loop`` errors
  during audio streaming. ([`1130aa8`](https://git.platypush.tech/blacklight/songhive/commit/1130aa8619b2e53e00909e887a9ee3f25f364979))

## 0.0.11

### Changed

- Clarify the example configuration's auth secret and SQLite database option. ([`53ff5be`](https://git.platypush.tech/blacklight/songhive/commit/53ff5bee36e0034ebaedcdff50353e7c7df51826))

### Fixed

- `migrations`: Serialize concurrent `ensure_migrated` runs to prevent
  duplicate-key crashes on fresh Docker Compose deployments. ([`877e1f6`](https://git.platypush.tech/blacklight/songhive/commit/877e1f6d5ccde51fd0d57c256064860d255dc811))
- `db`: Reset the async engine and session factory after Celery task event loops
  to prevent asyncpg loop-binding errors during track uploads and other
  background tasks. ([`ab53219`](https://git.platypush.tech/blacklight/songhive/commit/ab53219f93c4b80b721c4135a2cf0127bc1ec2cb))

## 0.0.10

### Changed

- `docker`: Switch Docker Compose to the published `quay.io/blacklight/songhive`
  image and add a `docker/bootstrap.sh` script to fetch compose files and sample
  config. ([`3cd029d`](https://git.platypush.tech/blacklight/songhive/commit/3cd029d3b71f8d52a7720d3f5b5ba8998779ccfd))
- Restructure the README install and run instructions, adding Docker bootstrap,
  local build, expanded pip setup, Celery and admin user steps, and updated
  nginx reverse proxy notes. ([`b397aa5`](https://git.platypush.tech/blacklight/songhive/commit/b397aa553645c8a8bda05dd0e6775ec4df417b27))
- `docker`: Speed up multi-arch image builds with `npm ci` and
  `package-lock.json`, split Python dependency layers, and `buildx` registry
  caching. ([`23d3e05`](https://git.platypush.tech/blacklight/songhive/commit/23d3e053c83feb284636439211f595f36be6ce07))
- `docker`: Replace `apt-get install ffmpeg` with the static
  `mwader/static-ffmpeg` binary to remove the large Debian dependency tree and
  reduce image size. ([`25b7030`](https://git.platypush.tech/blacklight/songhive/commit/25b7030961d2670dadd97ff45bfad82f9556eb66))
- Document SQLite as a database option, with a note that it is not recommended
  for large installations. ([`48ea6b1`](https://git.platypush.tech/blacklight/songhive/commit/48ea6b17aa29e70769738f482a436b28ea795acf))
- `drone`: Drop the multi-architecture `buildx` platform flag to avoid slow
  ARM64 QEMU emulation. ([`6e2790b`](https://git.platypush.tech/blacklight/songhive/commit/6e2790b2cc1f5f47aaf237676b4b01e7fb3aa8de))

### Fixed

- Correct the `config.toml.example` download URL in the Docker bootstrap script. ([`9649cf3`](https://git.platypush.tech/blacklight/songhive/commit/9649cf3007601fd541982b407ea59864ff3a27a2))
- `db`: Dispose the engine after the temporary settings overlay so request loops
  create fresh asyncpg connections on their own event loop. ([`5caf601`](https://git.platypush.tech/blacklight/songhive/commit/5caf60162d90f202532f8c9a3995e7e2a6646046))
- `bootstrap`: Skip downloading `config.toml.example` when `config.toml` already
  exists. ([`6a1c81c`](https://git.platypush.tech/blacklight/songhive/commit/6a1c81ce8767bf38748be587bdcf4db0699f7882))

## 0.0.9

### Fixes

- ci: Updated Python version for Docker image to 3.14.
- ci: Removed armv7 Docker image build process (psycopg2-binary is not supported on armv7).

## 0.0.8

### Fixes

- ci: Fixed Docker image release process.

## 0.0.2

Initial release.
