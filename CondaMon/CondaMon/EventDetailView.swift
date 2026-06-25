import SwiftUI
import AppKit

struct EventDetailView: View {
    let event: TickerEvent
    let onBack: () -> Void

    @StateObject private var chat = ChatBridge()
    @State private var inputText = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            detailHeader
            Divider().background(Color.white.opacity(0.1))
            eventContextCard
            Divider().background(Color.white.opacity(0.08))
            quickActions
            Divider().background(Color.white.opacity(0.08))
            chatArea
            chatInput
        }
        .background(Color(white: 0.08))
        .onAppear { inputFocused = true }
    }

    // MARK: - Header

    private var detailHeader: some View {
        HStack(spacing: 8) {
            Button(action: {
                chat.reset()
                onBack()
            }) {
                HStack(spacing: 4) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 9, weight: .bold))
                    Text("Events")
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                }
                .foregroundColor(.white.opacity(0.5))
            }
            .buttonStyle(.plain)

            Spacer()

            Text("Investigate")
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .foregroundColor(.white.opacity(0.9))

            Spacer()

            if !event.action_url.isEmpty {
                Button(action: {
                    if let url = URL(string: event.action_url) {
                        NSWorkspace.shared.open(url)
                    }
                }) {
                    HStack(spacing: 3) {
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 8, weight: .bold))
                        Text("Open")
                            .font(.system(size: 10, weight: .medium, design: .rounded))
                    }
                    .foregroundColor(event.sourceColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 5)
                            .fill(event.sourceColor.opacity(0.15))
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: - Event Context Card

    private var eventContextCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(event.severityColor)
                    .frame(width: 4, height: 36)

                ZStack {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(event.sourceColor.opacity(0.12))
                        .frame(width: 30, height: 30)
                    Image(systemName: event.sourceIcon)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(event.sourceColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text(event.sourceLabel)
                            .font(.system(size: 9, weight: .bold, design: .rounded))
                            .foregroundColor(event.sourceColor)

                        Text(event.severity.uppercased())
                            .font(.system(size: 8, weight: .heavy, design: .monospaced))
                            .foregroundColor(event.severityColor)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(event.severityColor.opacity(0.15))
                            )
                    }
                    Text(event.title)
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundColor(.white.opacity(0.95))
                        .lineLimit(2)
                }

                Spacer()

                Text(timeAgo)
                    .font(.system(size: 9, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.25))
            }

            Text(event.message)
                .font(.system(size: 11, design: .rounded))
                .foregroundColor(.white.opacity(0.55))
                .lineLimit(3)
                .padding(.leading, 40)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.white.opacity(0.02))
    }

    // MARK: - Quick Actions

    private var quickActions: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(contextActions, id: \.label) { action in
                    quickActionButton(action)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
        }
    }

    private func quickActionButton(_ action: QuickAction) -> some View {
        Button(action: {
            chat.sendWithContext(question: action.prompt, event: event)
        }) {
            HStack(spacing: 4) {
                Image(systemName: action.icon)
                    .font(.system(size: 9, weight: .semibold))
                Text(action.label)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
            }
            .foregroundColor(event.sourceColor)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(event.sourceColor.opacity(0.1))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(event.sourceColor.opacity(0.2), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(chat.isLoading)
        .opacity(chat.isLoading ? 0.5 : 1.0)
    }

    // MARK: - Chat Area

    private var chatArea: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if chat.messages.isEmpty {
                        emptyState
                    } else {
                        ForEach(chat.messages) { msg in
                            ChatBubble(message: msg, accentColor: event.sourceColor)
                                .id(msg.id)
                        }
                        if chat.isLoading {
                            thinkingIndicator
                                .id("thinking")
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
            }
            .onChange(of: chat.messages.count) { _ in
                withAnimation(.easeOut(duration: 0.2)) {
                    if chat.isLoading {
                        proxy.scrollTo("thinking", anchor: .bottom)
                    } else if let last = chat.messages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 18, weight: .light))
                .foregroundColor(.white.opacity(0.15))
            Text("Ask about this event or use a quick action above")
                .font(.system(size: 10, design: .rounded))
                .foregroundColor(.white.opacity(0.25))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    private var thinkingIndicator: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle()
                    .fill(event.sourceColor.opacity(0.5))
                    .frame(width: 5, height: 5)
                    .opacity(0.4)
            }
            Text("Investigating...")
                .font(.system(size: 10, weight: .medium, design: .rounded))
                .foregroundColor(.white.opacity(0.3))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: - Chat Input

    private var chatInput: some View {
        HStack(spacing: 8) {
            TextField("Ask about this event...", text: $inputText)
                .textFieldStyle(.plain)
                .font(.system(size: 12, design: .rounded))
                .foregroundColor(.white.opacity(0.9))
                .focused($inputFocused)
                .onSubmit { sendMessage() }

            Button(action: sendMessage) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 18))
                    .foregroundColor(
                        inputText.isEmpty || chat.isLoading
                        ? .white.opacity(0.15)
                        : event.sourceColor
                    )
            }
            .buttonStyle(.plain)
            .disabled(inputText.isEmpty || chat.isLoading)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.white.opacity(0.04))
        .overlay(
            Rectangle()
                .fill(Color.white.opacity(0.06))
                .frame(height: 1),
            alignment: .top
        )
    }

    private func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !chat.isLoading else { return }
        inputText = ""
        chat.sendWithContext(question: text, event: event)
    }

    // MARK: - Helpers

    private var timeAgo: String {
        let interval = Date().timeIntervalSince(event.date)
        if interval < 60 { return "now" }
        if interval < 3600 { return "\(Int(interval / 60))m ago" }
        if interval < 86400 { return "\(Int(interval / 3600))h ago" }
        return "\(Int(interval / 86400))d ago"
    }

    private var contextActions: [QuickAction] {
        QuickAction.forSource(event.source, event: event)
    }
}

// MARK: - Chat Bubble

struct ChatBubble: View {
    let message: ChatMessage
    let accentColor: Color

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            if message.role == .system {
                systemBubble
            } else if message.role == .assistant {
                assistantBubble
            } else {
                Spacer(minLength: 40)
                userBubble
            }
        }
    }

    private var systemBubble: some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 10))
                .foregroundColor(.orange)
            Text(message.text)
                .font(.system(size: 11, design: .rounded))
                .foregroundColor(.orange.opacity(0.8))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.orange.opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.orange.opacity(0.15), lineWidth: 1)
        )
    }

    private var userBubble: some View {
        Text(message.text)
            .font(.system(size: 11, design: .rounded))
            .foregroundColor(.white.opacity(0.9))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(accentColor.opacity(0.2))
            )
    }

    private var assistantBubble: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Image(systemName: "bolt.fill")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(accentColor)
                Text("CottonMouth")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .foregroundColor(accentColor.opacity(0.7))
            }

            markdownText(message.text)
                .font(.system(size: 11, design: .rounded))
                .foregroundColor(.white.opacity(0.8))
                .textSelection(.enabled)

            Button(action: {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(message.text, forType: .string)
            }) {
                HStack(spacing: 3) {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 7, weight: .medium))
                    Text("Copy")
                        .font(.system(size: 9, weight: .medium, design: .rounded))
                }
                .foregroundColor(.white.opacity(0.25))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.white.opacity(0.04))
        )
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func markdownText(_ raw: String) -> Text {
        if let attributed = try? AttributedString(markdown: raw, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            return Text(attributed)
        }
        return Text(raw)
    }
}

// MARK: - Quick Actions

struct QuickAction {
    let label: String
    let icon: String
    let prompt: String

    static func forSource(_ source: String, event: TickerEvent) -> [QuickAction] {
        switch source {
        case "slack-dm", "slack-mention", "slack-thread", "slack-channel":
            return [
                QuickAction(label: "Full thread", icon: "text.bubble", prompt: "Get the full Slack thread for this message. Show who said what."),
                QuickAction(label: "Context", icon: "clock.arrow.circlepath", prompt: "Show me the last 10 messages from this channel for context around this message."),
                QuickAction(label: "Who is this?", icon: "person", prompt: "Look up the user who sent this message. What team are they on?"),
                QuickAction(label: "Draft reply", icon: "pencil", prompt: "Draft a reply to this Slack message. Keep it concise and helpful."),
            ]

        case "jira":
            return [
                QuickAction(label: "Full ticket", icon: "ticket", prompt: "Get the full details of this Jira ticket — status, assignee, description, and recent comments."),
                QuickAction(label: "Comments", icon: "text.bubble", prompt: "Show me all comments on this Jira ticket."),
                QuickAction(label: "Linked PRs", icon: "arrow.triangle.branch", prompt: "Find any GitHub pull requests linked to this Jira ticket."),
                QuickAction(label: "Sprint status", icon: "chart.bar", prompt: "What sprint is this ticket in? What's the sprint progress?"),
                QuickAction(label: "Add comment", icon: "pencil", prompt: "Draft a Jira comment for this ticket summarizing the current state."),
            ]

        case "github-pr", "github-actions", "github-mention":
            return [
                QuickAction(label: "PR details", icon: "arrow.triangle.branch", prompt: "Get the full PR details — files changed, review status, CI checks."),
                QuickAction(label: "CI logs", icon: "terminal", prompt: "Show me the CI/Actions logs for this. What failed and why?"),
                QuickAction(label: "Review status", icon: "checkmark.circle", prompt: "Who has reviewed this PR? Are there any blocking reviews?"),
                QuickAction(label: "Diff summary", icon: "doc.text.magnifyingglass", prompt: "Summarize the code changes in this PR. What's the intent?"),
            ]

        case "argocd":
            return [
                QuickAction(label: "App status", icon: "arrow.triangle.2.circlepath", prompt: "Get the full ArgoCD application status — sync state, health, and recent events."),
                QuickAction(label: "Pod logs", icon: "terminal", prompt: "Show me the recent pod logs for this application. Look for errors."),
                QuickAction(label: "Resources", icon: "square.stack.3d.up", prompt: "List all Kubernetes resources for this ArgoCD application and their status."),
                QuickAction(label: "Sync", icon: "arrow.clockwise", prompt: "Should this application be synced? What would change?"),
            ]

        case "cloudwatch":
            return [
                QuickAction(label: "Alarm details", icon: "exclamationmark.triangle", prompt: "Get the full CloudWatch alarm details — threshold, metric, and recent data points."),
                QuickAction(label: "Recent logs", icon: "terminal", prompt: "Query CloudWatch Logs for errors related to this alarm in the last hour."),
                QuickAction(label: "Metrics", icon: "chart.line.uptrend.xyaxis", prompt: "Show the metric trend for this alarm over the last 4 hours."),
                QuickAction(label: "Runbook", icon: "doc.text", prompt: "What's the standard response for this type of alarm? Check for any runbooks."),
            ]

        case "grafana":
            return [
                QuickAction(label: "Alert details", icon: "chart.line.uptrend.xyaxis", prompt: "Get the full Grafana alert details — query, threshold, and current values."),
                QuickAction(label: "Dashboard", icon: "rectangle.3.group", prompt: "Find and show me the dashboard related to this alert."),
                QuickAction(label: "Prometheus", icon: "gauge.with.needle", prompt: "Run the PromQL query behind this alert and show me current values."),
                QuickAction(label: "On-call", icon: "person.badge.clock", prompt: "Who is currently on-call for this service?"),
            ]

        case "cloudtrail":
            return [
                QuickAction(label: "Event details", icon: "shield.lefthalf.filled", prompt: "Get the full CloudTrail event details — who did what, from where, and when."),
                QuickAction(label: "User activity", icon: "person.badge.key", prompt: "Show me all recent activity by this IAM user in the last 24 hours."),
                QuickAction(label: "Policy check", icon: "lock.shield", prompt: "Review the IAM policies involved. Are they following least privilege?"),
            ]

        case "cloudflare":
            return [
                QuickAction(label: "Audit details", icon: "cloud.fill", prompt: "Get the full Cloudflare audit log entry — what changed, who did it."),
                QuickAction(label: "Zone status", icon: "globe", prompt: "Show the current status of Cloudflare zones and any active incidents."),
                QuickAction(label: "Analytics", icon: "chart.bar", prompt: "Show recent traffic analytics for this zone."),
            ]

        case "confluence":
            return [
                QuickAction(label: "Page content", icon: "doc.richtext", prompt: "Get the content of this Confluence page. Summarize the key points."),
                QuickAction(label: "Recent changes", icon: "clock.arrow.circlepath", prompt: "What changed in this update? Show a diff summary."),
            ]

        case "agent-trace", "agent-error", "agent-anomaly":
            return [
                QuickAction(label: "Full trace", icon: "waveform.path.ecg", prompt: "Get the full trace for this agent run. Show all spans, their status, duration, and token usage."),
                QuickAction(label: "Why failed?", icon: "exclamationmark.triangle", prompt: "Analyze why this agent run failed. Identify the root cause span and explain the error."),
                QuickAction(label: "Cost breakdown", icon: "dollarsign.circle", prompt: "Break down the cost of this agent run. Which LLM calls consumed the most tokens?"),
                QuickAction(label: "Compare to normal", icon: "chart.bar", prompt: "Compare this agent run to its historical averages. Is the duration, cost, or error rate abnormal?"),
            ]

        default:
            return [
                QuickAction(label: "Investigate", icon: "magnifyingglass", prompt: "Tell me everything you can find about this event."),
                QuickAction(label: "Related", icon: "link", prompt: "Find any related events or issues connected to this."),
            ]
        }
    }
}
