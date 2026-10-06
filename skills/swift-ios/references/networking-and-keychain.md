# Networking and Keychain

Reference implementations for the API client, JSON coding, token storage, and the session model. Adapt names. Keep the structure.

## JSON coding that matches the API

The API sends camelCase keys, ISO 8601 UTC datetimes with fractional seconds, and string enums.

```swift
import Foundation

extension JSONDecoder {
    static let api: JSONDecoder = {
        let decoder = JSONDecoder()
        let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        let withoutFraction = Date.ISO8601FormatStyle()
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            if let date = try? withFraction.parse(value) { return date }
            if let date = try? withoutFraction.parse(value) { return date }
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath,
                debugDescription: "Expected ISO 8601 date, got \(value)"
            ))
        }
        return decoder
    }()
}

extension JSONEncoder {
    static let api: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}

/// Server enums gain cases over time; old app versions must not fail to decode them.
enum OrderStatus: Decodable, Sendable, Equatable {
    case pending, paid, cancelled, unknown(String)

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "pending": self = .pending
        case "paid": self = .paid
        case "cancelled": self = .cancelled
        default: self = .unknown(raw)
        }
    }
}
```

Cover the decoder with a fixture captured from the real API (`2026-03-01T10:15:30.123456Z`). Don't rely on a hand-typed example.

## Errors

```swift
struct Problem: Decodable, Sendable {
    let code: String
    let title: String
    let status: Int
    let detail: String?
    let requestId: String?
}

enum APIError: Error, Sendable {
    case offline
    case transport(URLError)
    case sessionExpired
    case problem(Problem)
    case unexpectedStatus(Int, requestId: String?)
    case decoding(String)
}

extension Error {
    var isCancellation: Bool {
        self is CancellationError || (self as? URLError)?.code == .cancelled
    }
}
```

## Endpoints

```swift
struct Endpoint<Response: Decodable & Sendable>: Sendable {
    var method = "GET"
    var path: String
    var query: [URLQueryItem] = []
    var body: Data?
}

extension Endpoint where Response == EventPage {
    static func upcomingEvents(cursor: String?) -> Self {
        var query = [URLQueryItem(name: "limit", value: "20")]
        if let cursor { query.append(URLQueryItem(name: "cursor", value: cursor)) }
        return Endpoint(path: "/v1/events", query: query)
    }
}
```

## API client actor with single-flight refresh

```swift
protocol TokenProviding: Sendable {
    func accessToken() async -> String?
    /// Exchanges the refresh token for a new pair. Throws APIError.sessionExpired when the server rejects it.
    func refresh() async throws -> String
}

actor APIClient {
    private let baseURL: URL
    private let session: URLSession
    private let tokens: TokenProviding
    private var refreshTask: Task<String, Error>?

    init(baseURL: URL, tokens: TokenProviding) {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.httpAdditionalHeaders = ["Accept": "application/json"]
        self.baseURL = baseURL
        self.session = URLSession(configuration: config)
        self.tokens = tokens
    }

    func send<Response>(_ endpoint: Endpoint<Response>) async throws -> Response {
        let (data, response) = try await perform(endpoint, token: await tokens.accessToken())
        if response.statusCode == 401 {
            let fresh = try await refreshedToken()
            let (retryData, retryResponse) = try await perform(endpoint, token: fresh)
            return try decode(retryData, retryResponse)
        }
        return try decode(data, response)
    }

    /// Concurrent 401s share one refresh, because refresh tokens rotate and a second refresh would look like reuse.
    private func refreshedToken() async throws -> String {
        if let refreshTask { return try await refreshTask.value }
        let task = Task { try await tokens.refresh() }
        refreshTask = task
        defer { refreshTask = nil }
        return try await task.value
    }

    private func perform<Response>(_ endpoint: Endpoint<Response>, token: String?) async throws -> (Data, HTTPURLResponse) {
        var components = URLComponents(url: baseURL.appending(path: endpoint.path), resolvingAgainstBaseURL: false)!
        if !endpoint.query.isEmpty { components.queryItems = endpoint.query }

        var request = URLRequest(url: components.url!)
        request.httpMethod = endpoint.method
        request.httpBody = endpoint.body
        if endpoint.body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }

        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw APIError.unexpectedStatus(-1, requestId: nil) }
            return (data, http)
        } catch let error as URLError where error.code == .notConnectedToInternet {
            throw APIError.offline
        } catch let error as URLError where error.code != .cancelled {
            throw APIError.transport(error)
        }
    }

    private func decode<Response: Decodable>(_ data: Data, _ response: HTTPURLResponse) throws -> Response {
        let requestId = response.value(forHTTPHeaderField: "X-Request-ID")
        guard (200..<300).contains(response.statusCode) else {
            if response.statusCode == 401 { throw APIError.sessionExpired }
            if let problem = try? JSONDecoder.api.decode(Problem.self, from: data) { throw APIError.problem(problem) }
            throw APIError.unexpectedStatus(response.statusCode, requestId: requestId)
        }
        if Response.self == EmptyResponse.self, let empty = EmptyResponse() as? Response { return empty }
        do {
            return try JSONDecoder.api.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding("\(error) [request \(requestId ?? "-")]")
        }
    }
}

struct EmptyResponse: Decodable, Sendable {}
```

The session layer handles `APIError.sessionExpired` by signing the user out (see below). Every other error goes to the screen's error state.

## Keychain store

```swift
import Security

struct KeychainError: Error { let status: OSStatus }

struct KeychainStore: Sendable {
    let service: String

    func set(_ data: Data, for account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let addStatus = SecItemAdd(query.merging(attributes) { $1 } as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw KeychainError(status: addStatus) }
        } else if status != errSecSuccess {
            throw KeychainError(status: status)
        }
    }

    func data(for account: String) throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess: return result as? Data
        case errSecItemNotFound: return nil
        default: throw KeychainError(status: status)
        }
    }

    func delete(_ account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }
}
```

## Token store

```swift
actor TokenStore: TokenProviding {
    private let keychain: KeychainStore
    private let baseURL: URL
    private var cachedAccessToken: String?           // memory only

    init(keychain: KeychainStore, baseURL: URL) {
        self.keychain = keychain
        self.baseURL = baseURL
    }

    /// Keychain items survive app deletion. Clear them on the first launch of a fresh install.
    static func wipeIfFreshInstall(_ keychain: KeychainStore, defaults: UserDefaults = .standard) {
        let key = "hasLaunchedBefore"
        guard !defaults.bool(forKey: key) else { return }
        try? keychain.delete("refreshToken")
        defaults.set(true, forKey: key)
    }

    func accessToken() async -> String? { cachedAccessToken }

    func store(_ pair: TokenPair) throws {
        try keychain.set(Data(pair.refreshToken.utf8), for: "refreshToken")
        cachedAccessToken = pair.accessToken
    }

    func refresh() async throws -> String {
        guard let refreshToken = try storedRefreshToken() else { throw APIError.sessionExpired }

        let request = try postRequest(path: "/v1/auth/refresh", body: ["refreshToken": refreshToken])
        let (body, response) = try await URLSession.shared.data(for: request) // network errors propagate: don't sign out offline users
        guard let http = response as? HTTPURLResponse else { throw APIError.unexpectedStatus(-1, requestId: nil) }
        if http.statusCode == 401 || http.statusCode == 400 {
            try? keychain.delete("refreshToken")
            cachedAccessToken = nil
            throw APIError.sessionExpired
        }
        guard (200..<300).contains(http.statusCode) else { throw APIError.unexpectedStatus(http.statusCode, requestId: nil) }

        let pair = try JSONDecoder.api.decode(TokenPair.self, from: body)
        try store(pair)                                   // rotation: persist the new refresh token
        return pair.accessToken
    }

    /// Revokes the server session with the refresh token (works even if the access token expired), then wipes local state.
    func revokeAndClear() async {
        if let refreshToken = try? storedRefreshToken(),
           let request = try? postRequest(path: "/v1/auth/logout", body: ["refreshToken": refreshToken]) {
            _ = try? await URLSession.shared.data(for: request)   // best effort: we may be offline
        }
        try? keychain.delete("refreshToken")
        cachedAccessToken = nil
    }

    private func storedRefreshToken() throws -> String? {
        try keychain.data(for: "refreshToken").flatMap { String(data: $0, encoding: .utf8) }
    }

    private func postRequest(path: String, body: [String: String]) throws -> URLRequest {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder.api.encode(body)
        return request
    }
}

struct TokenPair: Codable, Sendable {
    let accessToken: String
    let refreshToken: String
}
```

`UserDefaults` is a required-reason API. Declare it in `PrivacyInfo.xcprivacy` (reason `CA92.1` for app-only defaults).

## Session model

```swift
@MainActor @Observable
final class SessionModel {
    enum Status { case restoring, signedOut, signedIn }
    private(set) var status: Status = .restoring

    private let tokens: TokenStore

    init(tokens: TokenStore) {
        self.tokens = tokens
    }

    func restore() async {
        do {
            _ = try await tokens.refresh()
            status = .signedIn
        } catch APIError.sessionExpired {
            status = .signedOut
        } catch {
            status = .signedIn  // offline: keep the user in, show cached data and an offline banner
        }
    }

    func signOut() async {
        await tokens.revokeAndClear()
        URLCache.shared.removeAllCachedResponses()
        // also clear SwiftData/GRDB caches and in-memory models that hold user data
        status = .signedOut
    }
}
```

Call `SessionModel.restore()` from a `.task` on the root view. While the status is `.restoring`, keep showing the launch UI. When any request fails with `APIError.sessionExpired`, route it to `SessionModel.signOut()`, for example through an `AsyncStream` the `APIClient` publishes, so a revoked session signs out on every screen.
