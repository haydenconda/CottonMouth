import Foundation
import Combine

struct ChatMessage: Identifiable, Equatable {
    let id: String
    let role: ChatRole
    let text: String
    let timestamp: Date

    enum ChatRole: String {
        case user
        case assistant
        case system
    }

    static func == (lhs: ChatMessage, rhs: ChatMessage) -> Bool {
        lhs.id == rhs.id
    }
}

class ChatBridge: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var isLoading = false

    private let basePath: String
    private let queriesPath: String
    private let responsesPath: String
    private var responseOffset: UInt64 = 0
    private var pollTimer: Timer?
    private var timeoutTimer: Timer?
    private var pendingQueryId: String?
    private let sessionId: String

    private static let responseTimeoutSeconds: TimeInterval = 210

    init() {
        let base = EventTickerWatcher.baseDirectory
        basePath = base
        queriesPath = "\(base)/queries.jsonl"
        responsesPath = "\(base)/responses.jsonl"
        sessionId = UUID().uuidString

        let fm = FileManager.default
        if fm.fileExists(atPath: responsesPath),
           let attrs = try? fm.attributesOfItem(atPath: responsesPath),
           let size = attrs[.size] as? UInt64 {
            responseOffset = size
        }
    }

    func send(question: String, eventContext: TickerEvent? = nil) {
        let queryId = UUID().uuidString
        pendingQueryId = queryId

        let userMsg = ChatMessage(
            id: "user-\(queryId)",
            role: .user,
            text: question,
            timestamp: Date()
        )
        messages.append(userMsg)
        isLoading = true

        var queryDict: [String: Any] = [
            "query_id": queryId,
            "session_id": sessionId,
            "question": question,
            "ts": ISO8601DateFormatter().string(from: Date()),
        ]

        if let event = eventContext {
            queryDict["event_context"] = [
                "source": event.source,
                "severity": event.severity,
                "title": event.title,
                "message": event.message,
                "action_url": event.action_url,
                "event_ts": event.ts,
            ]
        }

        guard let data = try? JSONSerialization.data(withJSONObject: queryDict),
              let line = String(data: data, encoding: .utf8) else {
            isLoading = false
            return
        }

        let fm = FileManager.default
        if !fm.fileExists(atPath: queriesPath) {
            fm.createFile(atPath: queriesPath, contents: nil)
        }
        guard let handle = FileHandle(forWritingAtPath: queriesPath) else {
            appendError("Failed to write query — is CottonMouth backend running? Try: make restart")
            isLoading = false
            return
        }
        handle.seekToEndOfFile()
        handle.write((line + "\n").data(using: .utf8)!)
        handle.closeFile()

        startPolling()
        startTimeout()
    }

    func sendWithContext(question: String, event: TickerEvent) {
        send(question: question, eventContext: event)
    }

    private func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.checkForResponse()
        }
    }

    private func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
        timeoutTimer?.invalidate()
        timeoutTimer = nil
    }

    private func startTimeout() {
        timeoutTimer?.invalidate()
        timeoutTimer = Timer.scheduledTimer(withTimeInterval: Self.responseTimeoutSeconds, repeats: false) { [weak self] _ in
            guard let self = self, self.isLoading else { return }
            DispatchQueue.main.async {
                self.appendError(
                    "The agent didn't respond in time. The backend may need restarting — run `make restart` in the cottonmouth directory."
                )
                self.isLoading = false
                self.pendingQueryId = nil
                self.stopPolling()
            }
        }
    }

    private func appendError(_ text: String) {
        let msg = ChatMessage(
            id: "error-\(UUID().uuidString)",
            role: .system,
            text: text,
            timestamp: Date()
        )
        messages.append(msg)
    }

    private func checkForResponse() {
        guard let targetId = pendingQueryId else {
            stopPolling()
            return
        }

        let fm = FileManager.default
        guard fm.fileExists(atPath: responsesPath),
              let attrs = try? fm.attributesOfItem(atPath: responsesPath),
              let currentSize = attrs[.size] as? UInt64,
              currentSize > responseOffset else { return }

        guard let handle = FileHandle(forReadingAtPath: responsesPath) else { return }
        handle.seek(toFileOffset: responseOffset)
        let newData = handle.readDataToEndOfFile()
        handle.closeFile()
        responseOffset = currentSize

        guard let content = String(data: newData, encoding: .utf8) else { return }
        let lines = content.components(separatedBy: "\n").filter { !$0.isEmpty }

        for line in lines {
            guard let data = line.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let qid = json["query_id"] as? String,
                  qid == targetId,
                  let answer = json["answer"] as? String else { continue }

            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                let msg = ChatMessage(
                    id: "assistant-\(qid)",
                    role: .assistant,
                    text: answer,
                    timestamp: Date()
                )
                self.messages.append(msg)
                self.isLoading = false
                self.pendingQueryId = nil
                self.stopPolling()
            }
            return
        }
    }

    func reset() {
        messages.removeAll()
        isLoading = false
        pendingQueryId = nil
        stopPolling()
    }
}
