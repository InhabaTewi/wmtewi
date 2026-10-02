# T09 Productization Plan

## Background and Baseline

T08-7 latency hardening is the productization baseline. `t09-productization` descends from the final-acceptance tag `t08-7-latency-hardening` at `a73b17d498a562e5097e6d9811d943ea2b912bb7`. Production remains active on Core port `1515` with `localhost/inaba-core:6526779`, rollback on `1516` with `localhost/inaba-core:277fda1`, Nginx `/tewi -> 1515`, database revision `20260925_0006`, and an empty inference queue at the start of T09 planning.

T09 turns the existing backend capability into a user-accessible, manageable product while preserving the Core as the authority for Persona, Memory, Knowledge, provider routing, Worker Registry, and trace persistence. T09-0 makes no production or application change.

## Current Capability and Gap

The Core persists sessions/messages, retrieves persona-scoped memory and knowledge, and writes trace payloads. It can route inference to cloud or a local Worker with fallback. All `/api/*` endpoints are protected by one `SERVICE_TOKEN`, however, and Chat trusts request-supplied `user_id` and `session_id`. Consequently, a Web browser cannot safely use the current API as a user-facing interface.

The immediate gap is product identity and ownership, not chat inference. There is no users table, login state, user token, owner foreign key, session listing/message history API, frontend, admin UI, or QQ adapter in this monorepo.

## Milestones

### T09-1 User and Session Foundation

- Add user identities and local development authentication.
- Add authenticated session ownership without removing current service-token/internal flows.
- Establish explicit user-token versus service-token dependencies.
- Add forward-only migration(s) from `20260925_0006`, tested locally only.

### T09-2 Web Chat API

- Add user-scoped create/list/read chat-session APIs.
- Add user-scoped message history and message-send API.
- Reserve a stable streaming response contract, while keeping the first implementation non-streaming if necessary.
- Continue to invoke the existing `ChatService`, `ContextBuilder`, Persona, Memory, Knowledge, and ProviderRouter rather than duplicating orchestration.

### T09-3 Web Frontend

- Create the first browser application only after T09-2 defines stable user APIs.
- Include login, session list, chat transcript, composer, error/retry state, and baseline mobile layout.
- Keep browser tokens and service tokens separate; a browser must never receive a service token.

### T09-4 Administration

- Add explicit administrator authorization before adding management screens.
- Provide Persona, Knowledge, Memory, Worker, and provider status views.
- Add trace/feedback viewing only with a defined privacy/redaction policy.
- Keep Worker registration, heartbeats, job claims, and job completion service-only.

### T09-5 QQ Channel

- Implement a dedicated adapter after the Web path proves the user/session boundary.
- Map QQ sender identity to a Core user identity and map QQ conversation identity to a Core session.
- Support text first, then images/media under a separately defined contract.
- Reuse the same ChatService/Memory/Knowledge path as Web; do not fork business logic into the bot.

### T09-6 Frontend and E2E Tests

- Cover login, ownership enforcement, session creation/listing, history, message send, retry/error display, and admin smoke.
- Add QQ adapter contract smoke tests with a fake channel transport.
- Run browser tests only against local/test infrastructure, never production.

This order differs from the original specification only in making the Web API contract precede the frontend and QQ adapter. The repository has no existing browser or QQ implementation to preserve, while the current Core needs an ownership boundary before either channel can safely access it.

## T09-1 Minimum Implementation Plan

### Goals

T09-1 adds authenticated user identity and session ownership while preserving the existing service-token endpoints, Worker protocol, provider behavior, and current API smokes. It does not add QQ OAuth, deploy production changes, or implement a frontend.

### Candidate Data Model

Names below are candidates; existing table names are retained where present.

| Candidate | Intended fields | Decision rationale |
| --- | --- | --- |
| New `users` | UUID `id`, `display_name`, nullable unique `username` and/or email, `auth_type`, `is_active`, timestamps | Establishes the first durable authenticated identity |
| New `auth_identities` or `oauth_accounts` | UUID `id`, `user_id` FK, provider, provider subject, timestamps, unique provider/subject | Supports local identity now and QQ/OAuth identity later without putting provider IDs on `users` |
| New `auth_sessions` | UUID `id`, `user_id` FK, token hash only, expiry, revocation/last-used timestamps | Provides revocable browser/dev login state; never persist raw bearer tokens |
| Existing `sessions` | Add nullable `owner_user_id` FK, `channel_user_key`, optional title, timestamps already exist | `owner_user_id` is authoritative for Web access. Nullable supports a staged backfill and preserves existing channel/service sessions. Existing `user_id` remains a legacy/external subject field during transition. |
| Existing `messages` | Keep `session_id` FK as the ownership path; optionally add nullable `author_user_id` only if product audit requires actor-level attribution | Do not duplicate owner data prematurely. Session authorization controls message access; assistant messages do not have a human author. |

T09-1 should choose UUIDs for `users` and auth tables to align with existing entity IDs. It should add indexes for `sessions.owner_user_id` plus the list ordering path, and database constraints that prevent an auth session or identity from pointing to a missing user. The migration must be a new forward-only revision after `20260925_0006`; it is not applied to production in this task.

### Ownership and Channel Policy

For Web-originated sessions, derive the authenticated user from the token and set `sessions.owner_user_id`; never accept an owner ID from the browser request. A user may read or send messages only to sessions they own, unless an explicit future sharing policy exists.

`channel` remains the source channel (`web`, later `qq`, etc.). `channel_user_key` is a channel-scoped external identity for adapter mapping and is distinct from the authenticated Web owner. Existing `sessions.user_id` is preserved as legacy subject data until a tested migration/backfill policy is approved. Memory subject selection must be derived from the authenticated owner for Web paths, not supplied unchecked by the browser.

### Authentication Strategy

T09-1 should start with local development login, such as a development username flow issuing a short-lived user token or an opaque auth-session token. Password storage, external OAuth, and QQ OAuth are explicitly deferred.

Use separate dependencies and token namespaces:

- Service token: existing Core-to-Worker, deployment, CLI-adjacent, and internal/channel routes; behavior must remain compatible.
- User token: new browser-facing API routes only; resolves an active `users` row and never grants service/internal access.
- Administrator authorization: defer its role model until T09-4; no management endpoint becomes browser-public merely because user login exists.

The current blanket `/api/*` service-token middleware will require an implementation change in T09-1 so designated user routes can use the user-token dependency while existing routes retain service-token protection. That change must be covered by regressions proving service-only routes reject a user token and user routes reject a service token unless an intentional bridge is documented.

### Compatibility Strategy

- Preserve existing `/api/chat`, memory, knowledge, workers, and inference-job contracts for service callers during T09-1.
- Do not change ProviderRouter, readiness, local/cloud fallback, Worker behavior, or worker credentials.
- Do not retrofit user login into service-token automation.
- Do not expose raw trace payloads, provider errors, service credentials, or Worker claim endpoints to browsers.
- Make legacy sessions readable only through existing service APIs until an explicit ownership/backfill decision exists.

### Migration Strategy

1. Add a single or small coherent set of Alembic revisions after `20260925_0006`.
2. Test upgrade/downgrade/re-upgrade against a dedicated local PostgreSQL test database with pgvector.
3. Test SQLite behavior only if the application continues to claim SQLite unit-test support for the new constraints.
4. Do not apply the migration to production in T09-1 planning or implementation preparation.
5. Document an explicit production backfill/legacy-session policy before any deployment.

### Test Strategy

T09-1 minimum coverage:

- User model uniqueness/active-state tests.
- Authentication dependency tests for missing, invalid, expired, revoked, and valid user tokens.
- Regression tests for existing service-token authentication and Worker/inference endpoints.
- Session ownership tests: user A cannot list/read/send to user B sessions; browser body cannot select another owner.
- Chat session creation tests proving new Web flows populate `owner_user_id` and use the owner-derived memory subject.
- Existing ChatService, ContextBuilder, provider, Memory, Knowledge, readiness, and Worker tests.
- Dedicated PostgreSQL migration tests from `20260925_0006` to the new head.

## Risks

- A user-token feature added under the current catch-all `/api/*` service middleware could accidentally widen internal endpoints. Route grouping and explicit dependencies are required.
- The current free-form `sessions.user_id` is already used by Memory and channels. Replacing it abruptly would break existing behavior; use an additive transition.
- Browser history and trace visibility can expose sensitive content. Ownership, redaction, retention, and admin policy must precede trace browsing.
- The shared service token is appropriate for the existing Worker protocol but unsuitable for browsers. Never put it in frontend code or browser storage.
- QQ account identity and group/direct-message semantics need their own mapping rules; they should not be guessed from the Web schema.

## Non-Goals

- No production deployment, Nginx change, PostgreSQL production migration, or Worker startup in T09-0.
- No T07 trace/feedback implementation work.
- No QQ OAuth, QQ bot startup, or external bot reconfiguration in T09-1.
- No frontend project before user-facing API contract work.
- No alteration of `t08-7-latency-hardening` or deletion of T08 diagnostic evidence.

## Exit Criteria for T09-1

T09-1 is ready to hand off to T09-2 when a user can authenticate in local development, create a session that has authenticated ownership, access only their own session history, send chat through the existing Core orchestration path, and all existing service-token/Worker/provider regressions still pass. No production migration is implied by that local milestone.