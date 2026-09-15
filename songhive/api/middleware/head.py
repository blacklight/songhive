"""
HEAD request handling.

FastAPI's ``APIRoute`` registers only the methods it is given, so ``GET``
endpoints do not implicitly accept ``HEAD`` the way plain Starlette routes
do — every API route answered HEAD with 405. Worse, response bodies emitted
for HEAD made Tornado's ``WSGIContainer`` bridge raise ``HTTPOutputError``
("Tried to write more data than Content-Length"), surfacing upstream as a
502. Crawlers that probe pages or ``og:image`` URLs with HEAD therefore
failed outright.

This middleware serves HEAD as a bodyless GET: routing, dependencies and
headers (including ``Content-Length``) are identical to GET, and response
body bytes are dropped on the way out. The suppression is redundant with
what ASGI servers such as uvicorn already do, but is required for the
Tornado/a2wsgi bridge, which forwards whatever the application emits.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HeadToGetMiddleware:
    """Answer ``HEAD`` requests as ``GET`` responses with an empty body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = dict(message, body=b"")
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)
