"""API package."""

# Avoid importing `create_app` at package level: it pulls in all API routes and
# can create circular imports when `users.tokens` imports
# `api.middleware.auth.create_access_token` during CLI startup.
