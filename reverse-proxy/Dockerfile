# Caddy with the rate_limit HTTP handler compiled in.
#
# Stock Caddy has no request rate limiting. The caddy-ratelimit module adds a
# `rate_limit` directive. Caddy modules are compiled in rather than loaded at
# runtime, so this two-stage build compiles a custom binary with xcaddy and
# copies it into the official runtime image.

ARG CADDY_VERSION

FROM docker.io/library/caddy:${CADDY_VERSION}-builder-alpine AS builder
# Pinned to a commit: the module has no recent tagged releases, and pinning
# keeps builds reproducible. Update deliberately (see docs/security.md).
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db

FROM docker.io/library/caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
# Fail the build if the module didn't make it into the binary.
RUN caddy list-modules | grep -q '^http.handlers.rate_limit$'
