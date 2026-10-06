# StoreKit 2 with server-side entitlements

In-app purchases and subscriptions where **the backend decides what a user is entitled to**, so that web, Android, and iOS agree. The device's StoreKit state is used for immediate UX and offline fallback.

## Architecture

```
App ──purchase──▶ App Store
 │                    │
 │ signed transaction │ App Store Server Notifications V2 (renewals, refunds, revocations, expirations)
 ▼                    ▼
POST /v1/purchases/apple ──▶ API ◀── POST /v1/webhooks/app-store
                             │
                             ▼
                       entitlements table ──▶ GET /v1/me/entitlements (all clients)
```

- At purchase time, the app sends the transaction's JWS (`verification.jwsRepresentation`) to the API for immediate granting.
- The API verifies the JWS signature chain, then upserts the entitlement. Apple's official `app-store-server-library` (available for Python) does the verification and talks to the App Store Server API.
- Server Notifications V2 keep the entitlement current through renewals, grace periods, billing retry, refunds, and revocations, even when the app is never opened again.
- Set `appAccountToken` to the backend user's UUID on purchase. Transactions and notifications then map to a user without guesswork.

## Entitlements table (API side)

```sql
CREATE TABLE entitlements (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id text NOT NULL,
    store text NOT NULL CHECK (store IN ('app_store', 'play_store', 'web')),
    original_transaction_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'grace_period', 'billing_retry', 'expired', 'revoked')),
    expires_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_entitlements_store_original_tx UNIQUE (store, original_transaction_id)
);
CREATE INDEX ix_entitlements_user_id ON entitlements (user_id);
```

Process notifications idempotently: dedupe on `notificationUUID` and apply only state that is newer than the stored state.

## App side

```swift
import StoreKit

@MainActor @Observable
final class PurchaseManager {
    private(set) var products: [Product] = []
    private(set) var activeProductIDs: Set<String> = []
    private var updatesTask: Task<Void, Never>?
    private let api: APIClient

    init(api: APIClient) { self.api = api }

    /// Call at launch. Transactions arrive here for renewals, Ask to Buy approvals,
    /// purchases made on other devices, and refunds. Starting late means missing them.
    func start() {
        guard updatesTask == nil else { return }
        updatesTask = Task { [weak self] in
            for await verification in Transaction.updates {
                await self?.handle(verification)
            }
        }
    }

    func loadProducts() async throws {
        products = try await Product.products(for: ["com.example.pro.monthly", "com.example.pro.yearly"])
            .sorted { $0.price < $1.price }
    }

    func handlePurchaseResult(_ result: Product.PurchaseResult) async {
        switch result {
        case .success(let verification):
            await handle(verification)
        case .pending:
            break // Ask to Buy or Strong Customer Authentication: the result arrives via Transaction.updates
        case .userCancelled:
            break
        @unknown default:
            break
        }
    }

    private func handle(_ verification: VerificationResult<Transaction>) async {
        guard case .verified(let transaction) = verification else {
            return // never grant on unverified transactions
        }
        do {
            // Server-side grant first; the API verifies the JWS independently.
            _ = try await api.send(.registerApplePurchase(jws: verification.jwsRepresentation))
            await transaction.finish() // finish only after the grant is recorded, or it's redelivered next launch
        } catch {
            // Leave the transaction unfinished. StoreKit redelivers it, and the server notification is the backstop.
        }
        await refreshEntitlements()
    }

    /// Local view of entitlements for instant UI and offline use. The API remains authoritative.
    func refreshEntitlements() async {
        var active: Set<String> = []
        for await verification in Transaction.currentEntitlements {
            if case .verified(let transaction) = verification, transaction.revocationDate == nil {
                active.insert(transaction.productID)
            }
        }
        activeProductIDs = active
    }

    /// Only behind an explicit "Restore Purchases" button: it may prompt for the Apple Account password.
    func restore() async throws {
        try await AppStore.sync()
        await refreshEntitlements()
    }
}
```

Purchasing from SwiftUI:

```swift
struct PaywallView: View {
    @Environment(\.purchase) private var purchase
    @Environment(PurchaseManager.self) private var purchases
    @Environment(SessionModel.self) private var session
    let product: Product

    var body: some View {
        Button("Subscribe for \(product.displayPrice)") {
            Task {
                let result = try await purchase(product, options: [.appAccountToken(session.userID)])
                await purchases.handlePurchaseResult(result)
            }
        }
    }
}
```

For subscriptions, `SubscriptionStoreView(groupID:)` renders a compliant paywall with less code. Use it unless the design truly needs a custom one.

## Rules

- Start the `Transaction.updates` listener at launch, before any UI that depends on entitlements.
- Grant only on `.verified`. Finish the transaction only after the grant is persisted.
- Server-delivered premium content (API features, downloads) checks the **server** entitlement. Client-only gating is for content that's already on the device.
- Handle `revocationDate` (refunds), expirations, grace periods, Family Sharing (`ownershipType`), and offer codes.
- Provide a visible Restore Purchases action. Reviewers check for it.
- Show prices from `product.displayPrice`. Never hard-code prices or currencies.
- Selling across platforms: one `entitlements` table fed by App Store, Play Billing, and web payments. A third-party service such as RevenueCat can replace the per-store server work. Decide this in the architecture doc.

## Testing

- **Local:** add a StoreKit configuration file to the scheme, and use Xcode's Transaction Manager to approve, refund, expire, and fail renewals.
- **Unit and UI tests:** `SKTestSession` drives purchases, renewals (with an accelerated time rate), Ask to Buy, and interrupted purchases deterministically.
- **Sandbox:** use Sandbox Apple Accounts against a staging API with a staging notification URL configured in App Store Connect.
- **Server:** replay captured, signed notification payloads in tests. Test idempotency (the same notification twice) and out-of-order delivery.
