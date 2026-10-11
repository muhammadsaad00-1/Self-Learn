# 0007. Authentication: local accounts with Argon2 and short-lived JWT access tokens

- Status: accepted
- Date: 2026-10-10

## Context
Phase 1 needs login. The spec targets Cognito or an enterprise IdP, but that requires AWS (Phase 7). We still want the API's auth contract fixed now, so nothing changes when the provider does.

## Decision
- Local email + password accounts. Passwords are hashed with **Argon2id** (`pwdlib`), and hashes are upgraded transparently on login when parameters change.
- **Stateless JWT access tokens** (HS256, 30 min) sent as `Authorization: Bearer`. Claims: `sub`, `iss`, `iat`, `exp` only. Algorithm pinned on decode.
- The token identifies the **user only, not the org**. The org is chosen per request and membership is checked against the DB every time (Phase 1c), so role changes and removals apply immediately.
- The user is reloaded on every request, so a disabled account is locked out at once despite stateless tokens.
- Login is limited per email via a Redis fixed window (10 per 15 min), checked before hashing.
- `AGENTFLOW_JWT_SECRET` is mandatory outside local/test; Settings refuses to start otherwise.

## Consequences
- **No refresh tokens yet.** Users log in again after 30 minutes. *Update (1e):* the dashboard keeps the access token in `sessionStorage` (survives reload, cleared with the tab) and logs out on any 401. Any script on the page can read it, which is acceptable at a 30-minute TTL with no third-party scripts. Upgrade path: an httpOnly, SameSite refresh cookie plus an in-memory access token.
- Tokens can't be revoked before expiry except by disabling the user. Acceptable at a 30-minute TTL.
- Cognito later: swap `decode_access_token` to verify Cognito's RS256 tokens via JWKS; `CurrentUser` and every endpoint stay the same.

## Alternatives considered
- Server-side sessions in Redis: revocable, but a lookup on every request and less natural for API clients and agents.
- Cognito now: blocked on AWS, and hides the mechanics we want to learn first.
- bcrypt: fine, but Argon2id is the current OWASP first choice (memory-hard).
