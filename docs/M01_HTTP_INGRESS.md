# M01 HTTP Path Ingress

## Public and Internal Addresses

M01-8 publishes Core at `http://wmtewi.space/tewi`. Nginx strips `/tewi/` and forwards to the loopback-only Core upstream at `http://127.0.0.1:1515`. The Core container retains internal port `8000`; PostgreSQL remains internal-only.

## Nginx Integration

This host already runs Nginx for `wmtewi.space`. Reuse the existing server block and insert the locations from `deploy/nginx/tewi-location.conf.template`; do not create a second `wmtewi.space` server block. On this host, the target configuration is `/etc/nginx/conf.d/aigchelper.conf`. Do not change its `/`, `/aigc/`, or `/aigc/admin/` routes.

Before editing, make a timestamped backup of that one file. After insertion, run `nginx -t`; only when it succeeds, run `systemctl reload nginx`. Do not restart Nginx.

Set `INABA_CORE_PORT=1515` in `/etc/inaba/inaba.env`, then use `scripts/deploy_prod.sh`. Compose always maps `127.0.0.1:${INABA_CORE_PORT}:8000`; never expose Core publicly.

## Proxy Behavior

`/tewi` redirects to `/tewi/`. The trailing slash in `proxy_pass http://127.0.0.1:1515/` strips the public prefix: `/tewi/health/live` reaches Core as `/health/live`. `^~` ensures unrelated regex locations do not take ownership of the Inaba path.

Nginx forwards `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `Authorization`. Core remains the sole service-token authenticator and must not trust forwarded client-address headers from arbitrary callers.

The path has a 20 MB body limit with 10-second connect and 120-second send/read timeouts, exceeding the current 60-second LLM read timeout. Existing Nginx access logs do not record Authorization or API-key headers.

M01-8 is HTTP only. It adds no TLS listener, certificate, HSTS, or HTTP-to-HTTPS redirect.

## Validation and Rollback

```bash
curl -i http://wmtewi.space/tewi
curl -i http://wmtewi.space/tewi/health/live
curl -i http://wmtewi.space/tewi/health/ready
curl -i http://wmtewi.space/tewi/api/personas/inaba/active
curl -i -H "Authorization: Bearer $SERVICE_TOKEN" http://wmtewi.space/tewi/api/personas/inaba/active
```

The first request returns `301`; health is anonymous; a protected route returns `401` without a token and reaches Core with a valid token. Also check `http://wmtewi.space/`, `/aigc/`, `/aigc/admin/`, and the existing SearXNG container.

To remove the public path, restore the timestamped backup, run `nginx -t`, then reload Nginx. Core and PostgreSQL do not need to be stopped.