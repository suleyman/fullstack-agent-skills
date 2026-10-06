# Release workflow

How native builds, OTA updates, and store releases fit together on EAS.

## Mental model

```
             ┌──────────── native runtime (binary) ────────────┐
EAS Build ──▶│ iOS/Android code, native modules, app config,   │──▶ EAS Submit ──▶ store review ──▶ users
             │ runtimeVersion = fingerprint of all of the above │
             └──────────────────────────────────────────────────┘
                                   ▲
EAS Update ── JS bundle + assets ──┘  delivered only to binaries whose channel AND runtimeVersion match
```

- A **build** is slow (minutes to build, a day or more for review), and you can't recall it from devices.
- An **update** is fast (minutes), can be rolled back, and is limited to what the installed binary already supports.
- A **channel** is baked into a build at build time (`production`, `preview`). Updates are published to a channel, or to a branch the channel points at.

## `app.config.ts`

```ts
import type { ConfigContext, ExpoConfig } from "expo/config";

const VARIANT = (process.env.APP_VARIANT ?? "development") as "development" | "preview" | "production";
const IS_PROD = VARIANT === "production";
const SUFFIX = IS_PROD ? "" : `.${VARIANT}`;

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: IS_PROD ? "Acme Events" : `Acme Events (${VARIANT})`,
  slug: "acme-events",
  version: "1.6.0",                                  // marketing version: bump for each store release
  scheme: IS_PROD ? "acmeevents" : `acmeevents-${VARIANT}`,
  runtimeVersion: { policy: "fingerprint" },
  updates: { url: "https://u.expo.dev/<eas-project-id>" },
  ios: {
    bundleIdentifier: `com.example.events${SUFFIX}`, // variants install side by side
    associatedDomains: IS_PROD ? ["applinks:events.example.com"] : [],
  },
  android: {
    package: `com.example.events${SUFFIX}`,
    intentFilters: IS_PROD
      ? [{
          action: "VIEW",
          autoVerify: true,
          data: [{ scheme: "https", host: "events.example.com", pathPrefix: "/e/" }],
          category: ["BROWSABLE", "DEFAULT"],
        }]
      : [],
    blockedPermissions: ["android.permission.RECORD_AUDIO"], // added transitively, unused
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    ["expo-camera", { cameraPermission: "Acme Events uses the camera to scan tickets at the door." }],
    "expo-notifications",
  ],
  extra: { eas: { projectId: "<eas-project-id>" } },
});
```

## `eas.json`

```json
{
  "cli": { "version": ">= 16.0.0", "appVersionSource": "remote" },
  "build": {
    "development": {
      "developmentClient": true,
      "distribution": "internal",
      "channel": "development",
      "environment": "development",
      "env": { "APP_VARIANT": "development" }
    },
    "preview": {
      "distribution": "internal",
      "channel": "preview",
      "environment": "preview",
      "env": { "APP_VARIANT": "preview" }
    },
    "production": {
      "channel": "production",
      "environment": "production",
      "autoIncrement": true,
      "env": { "APP_VARIANT": "production" }
    }
  },
  "submit": {
    "production": {
      "ios": { "ascAppId": "<app-store-connect-app-id>" },
      "android": { "track": "internal" }
    }
  }
}
```

- `appVersionSource: "remote"` + `autoIncrement` lets EAS own build numbers (`buildNumber` / `versionCode`), so two machines can't produce the same build number.
- Keep variables in EAS environment variables, scoped per environment. Remember that `EXPO_PUBLIC_*` values are inlined into the JS bundle, so they're public.

## Does this change need a new build?

Ask in this order:

1. Did `package.json` gain or upgrade a dependency with native code (`ios/`/`android/` dirs, an `expo-module.config.json`, or a config plugin)? → **Build.**
2. Did `app.config.ts` change anything native: plugins or their options, permissions, usage strings, icons, splash, identifiers, scheme, associated domains, intent filters? → **Build.**
3. Did the Expo SDK or React Native version change? → **Build.**
4. Otherwise (JS/TS, styles, JS-imported assets, `EXPO_PUBLIC_*` values) → **Update.**

With `runtimeVersion: { policy: "fingerprint" }`, a native change produces a new runtime version automatically. An update published after it only reaches binaries built from the same native state. That's a safety net, not a substitute for classifying changes before you merge.

## JS-only release (EAS Update)

```bash
# 1. Verify on a preview build that has the same runtime as production
eas update --channel preview --message "Fix crash on empty order list (Android)"

# 2. Publish to production gradually
eas update --channel production --rollout-percentage=10 --message "Fix crash on empty order list (Android)"

# 3. Watch crash-free sessions and error rates for this update, then widen
eas update:edit                          # raise the rollout percentage

# If it's bad
eas update:revert-update-rollout         # during a rollout
eas update:rollback                      # after full rollout: republish the previous update or the embedded bundle
```

- Upload source maps for every update to your error tracker, tagged with the update ID.
- With default settings, `expo-updates` checks on launch and applies the update on the **next** cold start. For urgent fixes, check with `Updates.checkForUpdateAsync()` and `fetchUpdateAsync()`, then offer a restart (`reloadAsync()`) at a safe moment, never in the middle of a user's task.
- After a rollback, the next publish goes to every client on that channel again.

## Native release (store)

```bash
# 1. Bump the marketing version in app.config.ts (e.g. 1.6.0 → 1.7.0); EAS increments build numbers
# 2. Build both platforms
eas build --profile production --platform all

# 3. Smoke test: TestFlight (iOS) and the Play internal testing track (Android)
eas submit --profile production --platform ios --latest
eas submit --profile production --platform android --latest

# 4. Release with staged rollout: phased release in App Store Connect, staged rollout percentage in Play Console
```

- Check the API's backward compatibility before release. The previous app version will be in use for weeks.
- If the release includes an API change that old versions can't handle, coordinate the API's `minSupportedVersion` instead of breaking old clients.
- Store reviews reject vague permission strings, missing account deletion (if accounts can be created), apps whose privacy labels don't match their data collection, and user-generated content apps without reporting and blocking.

## Post-release monitoring

- Crash-free sessions and users per build and per update.
- API error rate by `X-App-Version`, which catches contract breaks that only hit some versions.
- Version adoption, which decides when to raise `minSupportedVersion` and when contract cleanup (contract migrations, removed fields) is safe.
