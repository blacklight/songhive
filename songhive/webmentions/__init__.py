"""
Webmention (W3C) integration built on the ``webmentions`` library.

Incoming mentions are accepted at ``POST /webmentions`` and processed by the
``process_incoming_webmention`` Celery task: the library re-fetches the source
document, verifies the target link, parses Microformats2 metadata, and fires
callbacks that materialize the mention as a ``webmention`` activity on the
resolved Songhive entity (user, track, album, artist, playlist, library,
radio, or activity) plus a notification for the entity owner.

Outgoing mentions are produced by the ``process_outgoing_webmentions`` Celery
task, enqueued when a local activity containing URLs (or a Webmention
interaction marker) is created, updated, or retracted. The task renders a
Microformats2 ``h-entry`` source page (served by
``GET /webmentions/source/{activity_id}``) and delegates endpoint discovery
and delivery to the library's outgoing processor.
"""
