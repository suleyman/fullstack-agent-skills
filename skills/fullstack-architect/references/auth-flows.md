# Authentication flows

These flows assume the API issues its own tokens and uses Google and Apple only as identity providers. If you use an external IdP instead (Auth0, Clerk, Cognito, Keycloak), clients use the IdP's SDKs, and the API only verifies IdP-issued JWTs through JWKS and provisions users on first request, keyed by the IdP's `sub`.

## Token model

| Token | Format | Lifetime | Stored where | Sent how |
|---|---|---|---|---|
| Access | JWT (ES256/RS256, `kid` header) | 5–15 min | Web: encrypted httpOnly cookie (server side only). Mobile/iOS: memory | `Authorization: Bearer` |
| Refresh | Opaque random, ≥ 256 bits | 30–90 days, sliding | Web: same encrypted cookie. Mobile: SecureStore. iOS: Keychain (`ThisDeviceOnly`) | Body of `/v1/auth/refresh` and `/v1/auth/logout` only |

Access token claims: `sub` (user ID), `sid` (session ID), `iss`, `aud`, `iat`, `exp`. No email, no PII, no roles. The API loads the user per request (a primary key lookup), so role changes and bans take effect at once.

## Tables

```sql
CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client text NOT NULL CHECK (client IN ('web', 'ios', 'android')),
    device_name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE refresh_tokens (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    token_hash bytea NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash)
);
CREATE INDEX ix_refresh_tokens_session_id ON refresh_tokens (session_id);

-- Push registrations belong to a session, so logout and revocation remove them.
CREATE TABLE devices (
    installation_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('ios', 'android')),
    push_token text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_devices_session_id ON devices (session_id);
```

Refresh tokens are high-entropy, so a fast hash (SHA-256) is correct for them. Argon2 is for passwords.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/auth/oauth/{provider}` `{idToken, nonce?, client, deviceName?}` | Verify the provider ID token, find or create the user, create a session, return `{accessToken, refreshToken, user}` |
| `POST /v1/auth/refresh` `{refreshToken}` | Rotate: return a new pair |
| `POST /v1/auth/logout` `{refreshToken}` | Revoke the session and delete its device rows |
| `GET /v1/me/sessions`, `DELETE /v1/me/sessions/{id}` | Let users see and revoke their sessions |

## Provider ID token verification (API)

- Fetch the provider's JWKS (Google: `https://www.googleapis.com/oauth2/v3/certs`; Apple: `https://appleid.apple.com/auth/keys`) with caching, using `jwt.PyJWKClient`.
- Verify the signature, `iss` (Google: `https://accounts.google.com` or `accounts.google.com`; Apple: `https://appleid.apple.com`), `aud` (**your** client IDs: iOS bundle ID, Android/web client IDs), and `exp`. Verify `nonce` when the client sent one.
- Key users by `(provider, provider_subject)` in a `user_identities` table. **Never link accounts by email alone.** Link only through an explicit, authenticated flow, and only when the provider asserts `email_verified`.
- Apple sends the user's name only on the first authorization. The client must forward it on that first sign-in.

## Flow: mobile sign-in (Expo or iOS)

```
App ── native Sign in with Apple / Google SDK (or system browser + PKCE) ──▶ Provider
App ◀────────────────────────── ID token ────────────────────────────────── Provider
App ── POST /v1/auth/oauth/apple {idToken, nonce, client: "ios"} ──▶ API
API ── verify JWKS signature, iss, aud, exp, nonce; upsert user; create session ──▶ DB
App ◀── {accessToken, refreshToken, user} ── API
App: refreshToken → SecureStore/Keychain; accessToken → memory
```

## Flow: web sign-in (Next.js BFF)

```
Browser ── GET /auth/google ──▶ Next.js Route Handler: create state + PKCE verifier (short-lived httpOnly cookie), redirect
Browser ── provider consent ──▶ Google ── redirect /auth/callback?code&state ──▶ Next.js
Next.js: verify state, exchange code (+ PKCE verifier, client secret) for the provider ID token
Next.js ── POST /v1/auth/oauth/google {idToken, client: "web"} ──▶ API ── {accessToken, refreshToken}
Next.js: set encrypted `__Host-session` cookie (httpOnly, Secure, SameSite=Lax, Path=/), redirect to app
```

- The provider client secret lives only in Next.js server env.
- **Refresh on the web** happens in `proxy.ts` (before rendering, when the access token is near expiry) and in the BFF proxy Route Handler. Server Components can't set cookies, so they must never be responsible for refreshing.
- **CSRF:** SameSite=Lax cookies, state changes only through non-GET methods, Server Actions' built-in Origin check, and an explicit `Origin` check in the BFF proxy for non-GET requests.

## Flow: refresh with rotation and reuse detection (API)

```
BEGIN;
SELECT ... FROM refresh_tokens WHERE token_hash = sha256($token) FOR UPDATE;
  not found                          → 401
  used_at IS NOT NULL                → reuse detected: revoke the session (all its tokens) → COMMIT → 401
  expired, or session revoked        → 401
  otherwise:
    UPDATE refresh_tokens SET used_at = now() WHERE id = ...;
    INSERT new refresh token for the same session;
    UPDATE auth_sessions SET last_used_at = now();
COMMIT;
return new access + refresh token
```

- Clients must **single-flight** refreshes: one refresh in flight, with concurrent 401s waiting for it. Two parallel refreshes with the same token look exactly like token theft.
- Optional: a grace window of a few seconds in which presenting the just-rotated token returns the already-issued successor instead of revoking. This helps with flaky networks that lose the refresh response.
- Clients sign out only when refresh returns 401/400. A network failure keeps the session, and the app shows cached data offline.

## Signing keys

- Sign access tokens with ES256 or RS256, with a `kid` header. Keep the private key only in the API's secret store.
- **Rotation:** publish the new public key, start signing with it, and remove the old key after the access-token lifetime has passed. If other services verify tokens, serve keys at `/.well-known/jwks.json`.
- HS256 is acceptable only when the API is the sole issuer **and** the sole verifier.

## Revocation scenarios

| Event | Action |
|---|---|
| User logs out | Revoke the session. Its device rows cascade |
| User changes password or removes a sign-in method | Revoke all other sessions |
| Admin bans a user | Set `users.disabled_at`. `get_current_user` rejects on the next request, and sessions are revoked |
| Refresh token reuse | Revoke the session and log a security event |
| Account deletion | Revoke all sessions, then enqueue the deletion job (database rows, object storage, analytics, push) |
