# Client setup

Reference code for the mobile app's data and auth plumbing. Types come from the generated OpenAPI package (`@acme/api-types`, generated with `openapi-typescript`).

## 1. Token storage and session state

```ts
// src/lib/auth/token-storage.ts
import * as SecureStore from "expo-secure-store";

const REFRESH_TOKEN_KEY = "auth.refreshToken";

export const tokenStorage = {
  getRefreshToken: () => SecureStore.getItemAsync(REFRESH_TOKEN_KEY),
  setRefreshToken: (token: string) =>
    SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token, {
      keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY, // not in backups or device transfers
    }),
  clear: () => SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY),
};
```

```ts
// src/lib/auth/session-store.ts
import { create } from "zustand";

type SessionState = {
  status: "restoring" | "signedIn" | "signedOut";
  accessToken: string | null; // memory only: never persisted
  setAccessToken: (token: string) => void;
  signOutLocal: () => void;
};

export const useSession = create<SessionState>()((set) => ({
  status: "restoring",
  accessToken: null,
  setAccessToken: (accessToken) => set({ accessToken, status: "signedIn" }),
  signOutLocal: () => set({ accessToken: null, status: "signedOut" }),
}));
```

## 2. API client: timeouts, typed errors, single-flight refresh

```ts
// src/lib/api/client.ts
import * as Application from "expo-application";
import { Platform } from "react-native";
import { env } from "@/lib/env";
import { tokenStorage } from "@/lib/auth/token-storage";
import { useSession } from "@/lib/auth/session-store";
import type { components } from "@acme/api-types";

type Problem = components["schemas"]["Problem"];
type TokenPair = components["schemas"]["TokenPair"];

export class ApiError extends Error {
  constructor(readonly status: number, readonly problem: Problem | null) {
    super(problem?.title ?? `Request failed with ${status}`);
  }
  get code() {
    return this.problem?.code ?? "unknown_error";
  }
}

export class SessionExpiredError extends Error {}

const DEFAULT_TIMEOUT_MS = 15_000;

function withTimeout(parent: AbortSignal | undefined | null, ms: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  if (parent?.aborted) controller.abort();
  parent?.addEventListener("abort", () => controller.abort(), { once: true });
  return { signal: controller.signal, done: () => clearTimeout(timer) };
}

let refreshInFlight: Promise<string> | null = null;

/** One refresh at a time: concurrent 401s wait for the same promise instead of racing rotation. */
function refreshAccessToken(): Promise<string> {
  refreshInFlight ??= (async () => {
    try {
      const refreshToken = await tokenStorage.getRefreshToken();
      if (!refreshToken) throw new SessionExpiredError();

      const res = await fetch(`${env.apiUrl}/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ refreshToken }),
      });
      // A network failure throws above and does NOT sign the user out.
      if (res.status === 401 || res.status === 400) throw new SessionExpiredError();
      if (!res.ok) throw new ApiError(res.status, null);

      const tokens = (await res.json()) as TokenPair;
      await tokenStorage.setRefreshToken(tokens.refreshToken); // rotation: always persist the new one
      useSession.getState().setAccessToken(tokens.accessToken);
      return tokens.accessToken;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}, isRetry = false): Promise<T> {
  const { signal, done } = withTimeout(init.signal, DEFAULT_TIMEOUT_MS);
  const accessToken = useSession.getState().accessToken;

  let res: Response;
  try {
    res = await fetch(`${env.apiUrl}${path}`, {
      ...init,
      signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        "X-App-Version": Application.nativeApplicationVersion ?? "unknown",
        "X-Platform": Platform.OS,
        ...init.headers,
      },
    });
  } finally {
    done();
  }

  if (res.status === 401 && !isRetry) {
    try {
      await refreshAccessToken();
    } catch (error) {
      if (error instanceof SessionExpiredError) {
        await signOut(); // see section 5
      }
      throw error;
    }
    return apiFetch<T>(path, init, true); // body is a string, so it can be resent
  }

  if (!res.ok) {
    const problem = res.headers.get("content-type")?.includes("json") ? ((await res.json()) as Problem) : null;
    throw new ApiError(res.status, problem);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}
```

## 3. QueryClient wired to app state and connectivity

```ts
// src/lib/query-client.ts
import NetInfo from "@react-native-community/netinfo";
import { focusManager, onlineManager, QueryClient } from "@tanstack/react-query";
import { AppState } from "react-native";
import { ApiError } from "@/lib/api/client";

onlineManager.setEventListener((setOnline) =>
  NetInfo.addEventListener((state) => setOnline(!!state.isConnected)),
);

focusManager.setEventListener((handleFocus) => {
  const subscription = AppState.addEventListener("change", (status) => handleFocus(status === "active"));
  return () => subscription.remove();
});

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
        return failureCount < 2;
      },
    },
    mutations: { retry: false }, // retry writes only when the endpoint is idempotent (Idempotency-Key)
  },
});
```

## 4. Session restore and protected routes

```tsx
// app/_layout.tsx
import { QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { queryClient } from "@/lib/query-client";
import { useSession } from "@/lib/auth/session-store";
import { restoreSession } from "@/lib/auth/restore";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const status = useSession((s) => s.status);

  useEffect(() => {
    restoreSession(); // refresh token → access token; offline keeps cached data, invalid → signedOut
  }, []);

  useEffect(() => {
    if (status !== "restoring") SplashScreen.hideAsync();
  }, [status]);

  if (status === "restoring") return null;

  return (
    <QueryClientProvider client={queryClient}>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Protected guard={status === "signedIn"}>
          <Stack.Screen name="(app)" />
        </Stack.Protected>
        <Stack.Protected guard={status === "signedOut"}>
          <Stack.Screen name="sign-in" />
        </Stack.Protected>
      </Stack>
    </QueryClientProvider>
  );
}
```

## 5. Sign-out clears everything

```ts
// src/lib/auth/sign-out.ts
let signingOut = false;

export async function signOut() {
  if (signingOut) return; // concurrent 401s must not trigger several sign-outs
  signingOut = true;
  try {
    const refreshToken = await tokenStorage.getRefreshToken();

    // Clear local state first, so nothing below can trigger another refresh.
    await tokenStorage.clear();
    queryClient.clear();
    useSession.getState().signOutLocal();

    if (refreshToken) {
      // Logout uses the refresh token, not the (possibly expired) access token. The server revokes the
      // session and deletes the push registrations bound to it. Best effort: we may be offline.
      await fetch(`${env.apiUrl}/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refreshToken }),
      }).catch(() => {});
    }
  } finally {
    signingOut = false;
  }
}
```

## 6. Push registration

Call this only after an in-context explanation, never on first launch:

```ts
// src/features/notifications/register.ts
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

export async function registerForPushNotifications(): Promise<"registered" | "denied" | "unavailable"> {
  if (!Device.isDevice) return "unavailable";

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "Activity",
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }

  const existing = await Notifications.getPermissionsAsync();
  let granted = existing.granted;
  if (!granted && existing.canAskAgain) {
    granted = (await Notifications.requestPermissionsAsync()).granted;
  }
  if (!granted) return "denied";

  const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  const { data: pushToken } = await Notifications.getExpoPushTokenAsync({ projectId });

  const installationId = await getInstallationId(); // random UUID stored once in SecureStore
  await apiFetch(`/v1/me/devices/${installationId}`, {
    method: "PUT",
    body: JSON.stringify({ pushToken, platform: Platform.OS }),
  });
  return "registered";
}
```

Re-run the registration (without prompting) on every launch when permission is already granted, and subscribe to `Notifications.addPushTokenListener` so the server always has the current token.

## 7. Notification taps → Expo Router

```tsx
// app/(app)/_layout.tsx (inside the authenticated area)
import * as Notifications from "expo-notifications";
import { router } from "expo-router";
import { useEffect } from "react";

const ALLOWED_PREFIXES = ["/events/", "/orders/", "/tickets/"];

export function useNotificationNavigation() {
  const response = Notifications.useLastNotificationResponse(); // covers cold and warm start

  useEffect(() => {
    if (!response || response.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) return;
    const url = response.notification.request.content.data?.url;
    if (typeof url === "string" && ALLOWED_PREFIXES.some((p) => url.startsWith(p))) {
      router.push(url);
    }
  }, [response]);
}
```

## 8. Minimum supported version

```ts
// On launch and on foreground: GET /v1/app-config → { minSupportedVersion: { ios, android }, storeUrl: { ios, android } }
// If the installed version is below the minimum, render a blocking "Update required" screen that links to the store.
// The server can also reject a request with code "app_version_unsupported" (from X-App-Version) when a breaking change is unavoidable.
```
