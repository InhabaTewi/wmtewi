# T08-7 Production Acceptance

**Acceptance date:** 2026-10-02

## Final Production Topology

- Active: `1515 / localhost/inaba-core:6526779`
- Rollback: `1516 / localhost/inaba-core:277fda1`
- Nginx: `/tewi -> 1515`
- PostgreSQL: `revision=20260925_0006`
- Queue: `QUEUED=0`, `CLAIMED=0`

## Runtime and Tooling Provenance

- Core runtime commit/image: `6526779`
- Deployment tooling commit: `f41bdb7`
- Updater SHA-256: `d289d62e0a24b64b0dcebbf432eae5f5934e5a570dbea6dbdb832713975fa6dc`
- Final Nginx switch: byte-exact replacement from `1516` to `1515`
- No database migration ran during the switch.
- PostgreSQL was not restarted or recreated.

## Passed Gates

- Standby restore: `1515 / 6526779`; 180/180 readiness requests returned HTTP 200.
- Blue-green switch: PASS.
- Public domain: `live=200`, `ready=200`.
- Local Nginx Host check: PASS.
- Production smoke: PASS.
- Local-worker path: PASS.
- Worker-offline Cloud fallback: PASS.
- Persistence uniqueness: PASS.
- Cleanup: PASS.
- PostgreSQL identity: unchanged.
- Queue: `QUEUED=0`, `CLAIMED=0`.
- Rollback slot: healthy.

## Timing Evidence

All values below are recorded runtime telemetry fields.

### Cloud Fallback After Switch

- `primary_duration_ms=4`
- `failover_decision_ms=0`
- `fallback_duration_ms=18854`
- `total_provider_ms=18858`
- `total_chat_ms=19189`

### Local-worker Smoke

- `job_create_ms=9`
- `job_queue_ms=409`
- `local_inference_ms=1187`
- `total_provider_ms=1639`
- `total_chat_ms=1995`

### Worker-offline Fallback Final

- `primary_duration_ms=4`
- `failover_decision_ms=0`
- `fallback_duration_ms=20891`
- `total_provider_ms=20895`
- `total_chat_ms=21205`

## Rollback Procedure

Rollback only requires:

1. Change the Nginx upstream from `1515` to `1516`.
2. Run `nginx -t`.
3. Run `systemctl reload nginx`.

No database rollback, migration, Core recreate, or image rollback is required.
`1516` remains the healthy rollback slot.

## Diagnostic History Summary

- `d1ff776`, `04deb5d`, `7b49cf9`, `3aa83b7`, and `f935e28` were diagnostic-only artifacts.
- Some diagnostic artifacts revealed packaging and provenance issues.
- None of those diagnostic images is the current production active image.
- Current active is the clean `6526779` runtime.
- Diagnostic evidence is preserved under the T08-7 evidence locations.
- `c9aace6` remains non-canonical and must not be deployed.
- Incorrect old artifacts remain audit-only and must not be deployed.

## Final Verdict

**T08-7 PRODUCTION ACCEPTANCE: PASS**

Production now running: `1515 / 6526779`

Rollback ready: **YES**

Ready to proceed to next planned stage: **YES**

Do not start T07 in this commit.