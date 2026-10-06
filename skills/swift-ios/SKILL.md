---
name: swift-ios
description: Opinionated rules for production native iOS apps in Swift and SwiftUI, covering the Observation framework, Swift concurrency (async/await, actors, Sendable), URLSession networking against a JSON API, Keychain credential storage, StoreKit 2 purchases with server-side entitlements, testing, and App Store / TestFlight release workflows. Use when building or reviewing SwiftUI views, view models, networking, authentication, in-app purchases, concurrency code, or preparing an App Store submission.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# Swift / SwiftUI iOS

Build native iOS apps with SwiftUI, Observation, and Swift concurrency. The app consumes the same API as the web and Expo apps. Targets Swift 6 language mode and iOS 17+ (Observation). If the deployment target is lower, adapt the Observation rules to `ObservableObject`.

Choose native Swift over Expo when the product needs deep platform integration (widgets and Live Activities as core features, watchOS, heavy media or AR, advanced background modes) or an existing native codebase. The decision belongs in the architecture doc (see `fullstack-architect`).

## When to use

- Writing SwiftUI views, navigation, or `@Observable` models.
- Networking with URLSession, auth flows, or token storage.
- Concurrency work: async/await, tasks, actors, Sendable diagnostics.
- In-app purchases and subscriptions with StoreKit 2.
- Tests, TestFlight, App Store submission, review rejections.

## Core rules

1. **Views are functions of state.** Logic, loading, and side effects live in `@MainActor @Observable` models and services, never in `body` or a view's `init`. Views get recreated constantly.
2. **Swift concurrency only.** Use async/await, structured tasks, and actors for new code. Wrap legacy callback APIs once with `withCheckedThrowingContinuation`. Don't use `DispatchQueue` in new code except for interop.
3. **Swift 6 strict concurrency.** Fix Sendable and isolation diagnostics properly. `@unchecked Sendable` and `nonisolated(unsafe)` need a comment that proves safety, or they don't merge.
4. **UI state is MainActor-isolated.** Shared mutable non-UI state (API client, token store, caches) lives in actors.
5. **Credentials live only in the Keychain.** Never in `UserDefaults`, files, or `@AppStorage`. No secrets compiled into the binary: anything in the IPA can be extracted.
6. **The server is the source of truth** for authorization and entitlements. The client caches them for UX.
7. **Every screen handles loading, empty, error-with-retry, and success. Cancellation is not an error:** don't show alerts for `CancellationError` or `URLError.cancelled`.

## Architecture

```
App/
├── AcmeApp.swift              # @main, composition root: builds services, injects via .environment
├── Core/                      # move to a local Swift package as the app grows (faster builds, enforced boundaries)
│   ├── Networking/            # APIClient (actor), Endpoint, APIError, JSON coding
│   ├── Auth/                  # TokenStore (actor, Keychain), SessionModel (@Observable)
│   ├── Purchases/             # StoreKit 2 wrapper, entitlement sync with the API
│   └── Persistence/           # SwiftData/GRDB cache, if offline is needed (a cache, not the source of truth)
├── Features/
│   └── Events/
│       ├── EventsView.swift
│       ├── EventsModel.swift  # @MainActor @Observable
│       └── EventRow.swift
├── DesignSystem/              # reusable components, typography, colors
└── Resources/                 # Assets, Localizable.xcstrings, PrivacyInfo.xcprivacy
```

- **Dependency injection:** build services in the composition root and pass them through initializers or `.environment(_:)`. No global singletons (`APIClient.shared`) reached from deep inside features. They make tests and previews hard.
- **Navigation:** `NavigationStack(path:)` with value-based `navigationDestination(for:)`, and a router model that owns the path. Deep links and notification taps append to the path.

## Preferred patterns

```swift
@MainActor @Observable
final class EventsModel {
    enum State { case loading, empty, loaded([Event]), failed(String) }

    private(set) var state: State = .loading
    private var nextCursor: String?
    private let api: APIClient

    init(api: APIClient) { self.api = api }

    func load() async {
        state = .loading
        do {
            let page = try await api.send(.upcomingEvents(cursor: nil))
            nextCursor = page.nextCursor
            state = page.items.isEmpty ? .empty : .loaded(page.items)
        } catch is CancellationError {
            // the view went away; keep the current state
        } catch {
            state = .failed(error.userMessage)
        }
    }
}

struct EventsView: View {
    @State private var model: EventsModel

    init(api: APIClient) { _model = State(initialValue: EventsModel(api: api)) }

    var body: some View {
        content
            .task { await model.load() }          // starts on appear, cancels on disappear
            .refreshable { await model.load() }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .loading: ProgressView()
        case .empty: ContentUnavailableView("No upcoming events", systemImage: "calendar")
        case .loaded(let events): List(events) { EventRow(event: $0) }
        case .failed(let message):
            ContentUnavailableView {
                Label("Couldn't load events", systemImage: "wifi.exclamationmark")
            } description: { Text(message) } actions: {
                Button("Try again") { Task { await model.load() } }
            }
        }
    }
}
```

- **Observation:** own models with `@State`, pass them down as plain properties, and use `@Bindable` when a child needs bindings. Use `@Environment` for app-wide services. Don't use `ObservableObject`/`@Published`/`@StateObject` in new iOS 17+ code.
- **Async work in views:** use `.task { }`, or `.task(id: query)` to restart when an input changes. Don't use `onAppear { Task { ... } }`, which never cancels.
- **Networking:** one `APIClient` actor with a configured `URLSession` (timeouts, `waitsForConnectivity` where appropriate) and one `JSONDecoder`. It attaches the bearer token, maps Problem Details errors to a typed `APIError` using the API's `code`, and on 401 runs a **single-flight token refresh**, then retries once.
- **Dates:** the API returns ISO 8601 UTC with fractional seconds (Pydantic emits microseconds). `JSONDecoder.DateDecodingStrategy.iso8601` rejects fractional seconds, so use a custom strategy and test it with a fixture captured from the real API.
- **Keychain:** generic password items with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` (use `WhenUnlockedThisDeviceOnly` if nothing refreshes in the background). Keychain items survive app deletion, so wipe them on the first launch after a fresh install.
- **Sign in:** use Sign in with Apple (`SignInWithAppleButton`) or Google via `ASWebAuthenticationSession`/SwiftUI's `webAuthenticationSession`, with PKCE. Send the identity token to the API, which verifies it and issues its own tokens.

Networking and Keychain implementations are in [references/networking-and-keychain.md](references/networking-and-keychain.md). StoreKit 2 with server-side entitlements is in [references/storekit.md](references/storekit.md).

## Patterns to avoid

| Avoid | Do instead |
|---|---|
| Network calls or heavy work in `body` or `init` | `.task` calling an `@Observable` model |
| `@ObservedObject` for an object the view creates | `@State` with an `@Observable` model |
| `onAppear { Task { ... } }` | `.task { }` (cancels automatically) |
| `DispatchQueue.main.async` to silence threading warnings | Correct `@MainActor` isolation |
| `Task.detached` by default | Structured tasks. Detach only with a stated reason |
| `try!`, `as!`, force unwraps on server data | Typed decoding with explicit errors |
| Tokens in `UserDefaults` or `@AppStorage` | Keychain |
| `APIClient.shared` used everywhere | Injected dependencies |
| `.iso8601` decoding strategy for API dates | A custom strategy that accepts fractional seconds |
| `AnyView` in lists and hot paths | `@ViewBuilder` and concrete types |
| Unlocking premium content based only on a client flag | Server-verified entitlements |
| `verifyReceipt` (deprecated) | StoreKit 2 + App Store Server API / Server Notifications V2 |

## Debugging

- **Concurrency:** enable Thread Sanitizer and the Main Thread Checker in the scheme, and read Swift 6 diagnostics as design feedback. A data race warning means shared mutable state without an owner.
- **Unexpected view updates:** call `let _ = Self._printChanges()` in `body` (debug builds only). Use the SwiftUI instrument to find expensive or frequent body evaluations.
- **Decoding failures:** print `DecodingError` cases in full (key path + debug description). The usual causes are date formats, nulls where non-optional, and unknown enum values (decode unknown cases to an `.unknown` fallback).
- **Network:** log the API's `X-Request-ID` response header with every failure, so backend logs can be correlated. Inspect traffic with Proxyman or Charles in debug builds.
- **Memory:** Instruments Leaks/Allocations. Closures stored on models need `[weak self]`. A closure passed to an `async` call usually doesn't.
- **Crashes:** upload dSYMs from CI (Xcode Cloud and fastlane can do this as part of the build) and read symbolicated reports in Xcode Organizer or your crash reporter.

## Performance

- Use `List` or `LazyVStack` with stable identifiers for long content. Paginate with the API cursor.
- Request thumbnail sizes from the server. `AsyncImage` has no tunable cache, so use a caching loader for image-heavy lists.
- Keep `body` cheap: precompute display strings in the model, and use `FormatStyle` instead of creating formatters in `body`.
- Observation tracks properties per access. Small subviews that read only what they show re-render less.
- Measure launch with the App Launch instrument and defer non-critical setup until after first frame.

## Security

- Keychain with `ThisDeviceOnly` accessibility. Clear the Keychain, `URLCache`, and local stores on sign-out.
- App Transport Security stays on. No arbitrary loads.
- Use `os.Logger`: interpolated values are private by default. Mark only non-sensitive values `privacy: .public`.
- Write sensitive files with `.completeFileProtection`.
- Validate universal link and notification payloads before navigating or acting.
- Jailbreak detection is not a control. Server-side authorization is.

## Testing

- **Swift Testing** (`@Test`, `#expect`, `#require`, parameterized tests) for models, decoding, and the API client. Use XCTest for UI tests (XCUITest) and performance tests.
- **API client:** stub responses with a `URLProtocol` subclass. Test request building, decoding of **real captured fixtures**, Problem Details error mapping, and 401 → single refresh → retry → sign-out.
- **Models:** inject a fake API (protocol or closure-based) and assert state transitions: loading → loaded, empty, failed, and cancellation ignored.
- **StoreKit:** a StoreKit configuration file plus `SKTestSession` for purchase, renewal, refund, and Ask to Buy flows.
- **Manual matrix:** the oldest supported iOS, the smallest device, the largest Dynamic Type size, VoiceOver on core flows, dark mode, and offline.

## App Store workflow

- **Versions:** `CFBundleShortVersionString` is the marketing version (bump it per release). `CFBundleVersion` is the build number (monotonic, set by CI).
- **CI:** Xcode Cloud, or fastlane on your CI with managed signing (`match`), produces signed builds and uploads them to TestFlight.
- **TestFlight:** internal testers first. External groups need beta app review.
- **Release:** use phased release for automatic updates. Pause it if crash rates rise.
- **Privacy:** `PrivacyInfo.xcprivacy` declares required-reason APIs (e.g. `UserDefaults`, file timestamps) and any tracking domains. Third-party SDKs must ship their own manifests. App Privacy labels in App Store Connect must match real data collection.
- **Common rejection causes:** no in-app account deletion when accounts can be created; third-party login without an equivalent privacy-focused option such as Sign in with Apple; digital goods sold outside in-app purchase where the storefront rules don't allow it (these rules differ by region and have changed recently, so check current guideline 3.1); user-generated content without reporting, blocking, and filtering; vague permission purpose strings; no demo account for review.
- Apple raises the minimum Xcode/SDK for uploads periodically. Keep CI on a current Xcode.

## Production-readiness checklist

- [ ] Swift 6 language mode. No unexplained `@unchecked Sendable` or `nonisolated(unsafe)`.
- [ ] Tokens are in the Keychain (`ThisDeviceOnly`), wiped on sign-out and on first launch after reinstall.
- [ ] Every screen handles loading, empty, error-with-retry, and offline. Cancellations are silent.
- [ ] The API client decodes captured fixtures, including dates with fractional seconds and unknown enum values.
- [ ] 401 → single refresh → one retry → sign-out, covered by tests.
- [ ] StoreKit: `Transaction.updates` listener started at launch, transactions finished after granting, entitlements verified server-side, a Restore Purchases button exists.
- [ ] Privacy manifest, App Privacy labels, and purpose strings are accurate. Account deletion exists.
- [ ] dSYMs are uploaded and crash reporting is verified with a test crash.
- [ ] Accessibility pass (Dynamic Type, VoiceOver labels). Tested on the oldest supported iOS.
- [ ] CI-managed build numbers, a TestFlight pass before every release, phased release enabled.
