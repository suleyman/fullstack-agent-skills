---
name: expo-react-native
description: Opinionated rules for production Expo and React Native apps using Expo Router, TanStack Query, Zustand, EAS Build, EAS Update, and EAS Submit, including secure token storage, push notifications, deep linking, permissions, native dependencies, and App Store / Play Store release workflows. Use when building or reviewing Expo screens, navigation, data fetching, auth, notifications, links, or permissions, when adding a native dependency, or when deciding whether a change ships as an OTA update or a new store build.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# Expo / React Native

Build iOS and Android apps with Expo (development builds, Continuous Native Generation), Expo Router, TanStack Query, and Zustand, shipped through EAS. The app consumes the same API as the web app (see `fullstack-architect`).

## When to use

- Building screens, layouts, and navigation with Expo Router.
- Fetching, caching, and mutating API data. Handling auth tokens.
- Adding or upgrading a dependency, especially one with native code.
- Push notifications, deep links and universal links, or runtime permissions.
- Preparing a release: EAS Build, EAS Update, EAS Submit, versioning, rollout, rollback.

## Core rules

1. **EAS Update and native builds are separate deployment mechanisms.** A native build (EAS Build → store review) ships the native runtime. An update (EAS Update) ships JavaScript and assets to builds with a **matching runtime version**. Classify every change before shipping. If it touches native code or native config, it needs a new build. Updates cannot add native code.
2. **Verify whether every new dependency needs a new development build.** A package with native code (iOS/Android sources, or a config plugin that changes native projects) means a new dev build for the team, a new runtime version, and a store release before any JavaScript that uses it can go out over the air.
3. **Continuous Native Generation.** `ios/` and `android/` are generated from `app.config.ts` and config plugins (`npx expo prebuild`). They're gitignored and never hand-edited. Native customization goes through config plugins.
4. **Development builds, not Expo Go.** Use Expo Go for throwaway prototypes only. Push notifications, custom native modules, and many config plugins need a dev build (`expo-dev-client`).
5. **State ownership matches the web:** TanStack Query for server state, Zustand for client UI state, route params for navigation state, SecureStore for secrets. Never put server data in Zustand.
6. **Tokens live only in `expo-secure-store`** (Keychain/Keystore). Never in AsyncStorage, unencrypted MMKV, persisted Zustand, or the query cache.
7. **The client is untrusted and old.** Binaries stay installed for months. The API must stay backward compatible, the app must tolerate unknown fields and enum values, and a minimum-version check must be able to force an upgrade. The API enforces authorization, never only the app.
8. **Mobile networks fail.** Every screen handles loading, empty, error-with-retry, and offline. Requests have timeouts. Retries back off and never retry 4xx.

## Architecture

```
mobile/
├── app.config.ts              # dynamic config: per-environment IDs, plugins, runtimeVersion
├── eas.json                   # build profiles → channels
├── app/                       # Expo Router routes only (thin)
│   ├── _layout.tsx            # providers: QueryClient, session bootstrap, splash
│   ├── sign-in.tsx
│   ├── (app)/
│   │   ├── _layout.tsx        # Stack.Protected guard={isSignedIn}
│   │   ├── (tabs)/{_layout,index,orders}.tsx
│   │   └── events/[id].tsx
│   └── +not-found.tsx
└── src/
    ├── features/<feature>/{api.ts, components/, hooks/, lib/}
    ├── lib/
    │   ├── api/               # fetch client: auth header, single-flight refresh, timeouts, errors
    │   ├── auth/              # SecureStore token storage, session store
    │   ├── query-client.ts    # QueryClient + focus/online managers
    │   └── env.ts
    └── components/
```

- **Route files stay thin.** They read params, validate them, and render a feature screen.
- **Every route is a deep link.** A screen loads its own data from the ID in its params. Never pass whole objects through route params or rely on in-memory state from the previous screen. To render instantly from list data, seed the detail query with `placeholderData` from the list cache.
- **Route params are untrusted strings,** and can be `string[]`. Validate them before use.
- **Gate auth in one layout** with `Stack.Protected guard={...}` (or `<Redirect>`), not per screen. Keep the intended destination so a signed-out deep link resumes after sign-in.
- **Install with `npx expo install <pkg>`**, which picks SDK-compatible versions. Run `npx expo-doctor` after upgrades and before release builds.

Client setup code (QueryClient with focus/online managers, token storage, a fetch client with single-flight refresh, push registration, notification taps) is in [references/client-setup.md](references/client-setup.md).

## Data fetching

- Wire `focusManager` to `AppState` and `onlineManager` to NetInfo, so queries refetch on foreground and pause offline.
- Infinite lists use `useInfiniteQuery` with `initialPageParam` and `getNextPageParam: (last) => last.nextCursor ?? undefined`, rendered with FlashList `onEndReached`. Guard with `hasNextPage && !isFetchingNextPage`.
- Pull-to-refresh calls `refetch()` and tracks its own `refreshing` state, so focus refetches don't show the spinner.
- Normalize noisy inputs before they become query keys: debounce and trim search text, round GPS coordinates (3 decimals ≈ 110 m). Otherwise every keystroke or small location change creates a new cache entry and a new request.
- When you persist the query cache for offline reads (`PersistQueryClientProvider`), set `maxAge`, use a `buster` tied to the app version, and exclude sensitive queries with `shouldDehydrateQuery`.
- Sign-out clears everything: SecureStore, the session store, `queryClient.clear()`, the persisted cache, and the server-side push token registration.

## Releases: EAS Build vs EAS Update

| Change | Ships via |
|---|---|
| JS/TS logic, styling, copy, JS-imported images | EAS Update |
| Adding or upgrading a package with native code | New build |
| Config plugin changes, permissions or usage strings, icon, splash, bundle ID, `scheme`, associated domains, intent filters | New build |
| Expo SDK or React Native upgrade | New build |
| `EXPO_PUBLIC_*` value change | EAS Update (values are inlined at bundle time, so publish with the right environment) |

- Set `runtimeVersion: { policy: "fingerprint" }`, so the runtime version changes automatically whenever the native layer changes. An update then can't reach an incompatible binary.
- Map channels to build profiles (`development`, `preview`, `production`). Test every update on a `preview` build with the production runtime before publishing to `production`.
- Roll out gradually: `eas update --rollout-percentage=10`, widen it with `eas update:edit`, revert with `eas update:revert-update-rollout`, or roll back fully with `eas update:rollback`.
- Upload source maps to your error tracker for every build **and** every update. Without them, stack traces from OTA bundles are unreadable.
- Use OTA updates for fixes and improvements within the reviewed app. Features that materially change the app go through store review.

Full workflow (`eas.json`, `app.config.ts`, versioning, store submission, staged rollouts) is in [references/release-workflow.md](references/release-workflow.md).

## Push notifications

- Use `expo-notifications` in a development build, and test on physical devices.
- **Ask for permission in context**, after an action the notification will serve ("Notify me when someone replies"), never at first launch. iOS shows the system prompt only once.
- Android: create a notification channel before requesting a token. Android 13+ requires the runtime permission.
- Register the token with the API (`PUT /v1/me/devices/{installationId}`) on every launch and on token change, and remove it on sign-out. The server stores one row per device.
- The server sends through the Expo Push Service (or APNs/FCM directly), **checks push receipts**, and deletes tokens that report `DeviceNotRegistered`.
- The payload carries a route and an ID (`{ url: "/orders/123" }`). Handle taps on cold start and warm start, and validate the path before navigating. Never put sensitive content in the payload, because it shows on the lock screen.

## Deep linking

- Use a custom `scheme` for development and internal links. Production links that people share use **universal links** (iOS `associatedDomains` + `apple-app-site-association`) and **Android App Links** (`intentFilters` with `autoVerify` + `assetlinks.json`). Any app can claim a custom scheme.
- Treat link parameters as untrusted input. Validate IDs, and never perform a state-changing action straight from a link without user confirmation.
- Test cold start, warm start, signed-out, and unknown-route paths: `npx uri-scheme open <url> --ios|--android`, `xcrun simctl openurl booted <url>`, `adb shell am start -W -a android.intent.action.VIEW -d <url>`.

## Permissions

- Request at the moment of need, after an in-app explanation screen.
- Handle every state: granted, denied with `canAskAgain` (ask again later), blocked (`canAskAgain: false`, offer `Linking.openSettings()`), iOS limited photo access, and approximate location.
- Set specific iOS usage strings through config plugin options (e.g. `expo-camera`'s `cameraPermission`). Vague strings get rejected in review.
- Request background location only if the core feature can't work without it. Both stores apply extra review.
- Remove permissions that libraries add transitively and you don't use (`android.blockedPermissions`).

## Patterns to avoid

| Avoid | Do instead |
|---|---|
| Shipping a native dependency change via `eas update` | New build with a new runtime version (fingerprint policy) |
| Editing `ios/` or `android/` by hand in a CNG project | Config plugins |
| Tokens in AsyncStorage, MMKV, or persisted Zustand | `expo-secure-store` |
| Secrets in `EXPO_PUBLIC_*` or `extra` | The API holds secrets. Anything in the bundle is public |
| `ScrollView` + `.map()` for unbounded lists | FlashList (or FlatList) with stable keys |
| Passing objects through route params | Pass IDs and load or seed from the query cache |
| Permission prompts at first launch | In-context requests after an explanation |
| Login in an embedded WebView | System browser via `expo-auth-session` + PKCE, or native provider SDKs |
| `fetch` without a timeout | An `AbortController` timeout in the API client |
| Profiling a dev build | Profile release-like builds (`preview` profile) |

## Debugging

- **"Native module not found" / `Cannot find native module`:** the JS references native code missing from the installed binary. Rebuild the dev build, and confirm the runtime version of OTA updates.
- **An update isn't applied:** check the build's channel and runtime version against the update's. With default settings, an update downloads on launch and applies on the **next** launch.
- **Works in development, crashes in release:** look for missing env at bundle time, dev-only code paths, or Hermes/minification issues. Reproduce with a `preview` build and read symbolicated crash reports.
- **Push token is null:** simulator, missing `projectId`, denied permission, or (Android) no notification channel created.
- **Universal link opens the browser:** the AASA or `assetlinks.json` file isn't served correctly (exact path, `application/json`, no redirects), or the app ID or signing fingerprint doesn't match.

## Performance

- Long lists: FlashList, memoized row components, stable `keyExtractor`, and server-provided thumbnails at display size (don't render 4000 px images in 80 px avatars). Use `expo-image` for caching.
- Run animations on the UI thread with Reanimated. Don't animate with JS-thread state updates.
- Startup: keep the root layout light, keep the splash screen visible (`expo-splash-screen`) until fonts and session restoration finish, and defer non-critical SDK initialization.
- Select narrowly from Zustand and keep list item callbacks stable.
- Measure on a low-end Android device with a release build.

## Security

- Refresh tokens live in SecureStore with `keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`. Access tokens live in memory only.
- OAuth uses Authorization Code + PKCE through the system browser, or native Sign in with Apple / Google SDKs whose ID token the API verifies. Never ship client secrets in the app.
- Everything in the JS bundle and app config is readable. Ship only public identifiers.
- Avoid certificate pinning unless your threat model demands it. A rotated certificate can brick installed apps.
- Jailbreak/root detection doesn't protect server data. Server-side authorization does.

## Testing

- **Unit:** Jest with the `jest-expo` preset for pure logic and hooks.
- **Components:** React Native Testing Library. Cover loading, empty, error, offline, and permission-denied states.
- **E2E:** Maestro flows for sign-in, the core feature, a deep link cold start, and a push tap, run on release-like builds.
- **Manual pass before each store release:** a physical low-end Android, a small iPhone, slow network (Network Link Conditioner), permission denial paths, and an OTA update applied to the release build.

## Production-readiness checklist

- [ ] Development builds are in use. `npx expo-doctor` passes.
- [ ] `runtimeVersion` uses the fingerprint policy. Channels map to build profiles.
- [ ] Per-environment bundle IDs and package names so builds coexist, with separate push credentials.
- [ ] Tokens are only in SecureStore. Sign-out clears tokens, caches, and the push registration.
- [ ] No secrets in `EXPO_PUBLIC_*` or the app config.
- [ ] Every screen handles loading, empty, error-with-retry, and offline.
- [ ] Deep links are verified on cold and warm start, signed in and signed out. AASA and `assetlinks.json` are deployed.
- [ ] Permission requests are in context, denial paths are handled, and usage strings are specific.
- [ ] Push: channel created, permission timing set, token registration and rotation work, receipts are processed on the server.
- [ ] Error tracking has source maps for builds and updates.
- [ ] A minimum supported version check is wired to an update screen.
- [ ] Privacy manifests, App Privacy labels, and the Play Data safety form match actual data collection. In-app account deletion exists if accounts can be created.
- [ ] The release build is tested on physical devices before submission.
