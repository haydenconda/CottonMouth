import Foundation
import SwiftUI

struct TickerEvent: Codable, Identifiable, Equatable {
    let ts: String
    let agent: String
    let severity: String
    let title: String
    let message: String
    let source: String
    let action_url: String

    var id: String { "\(ts)-\(agent)-\(title)" }

    var date: Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: ts) ?? Date()
    }

    var severityColor: Color {
        switch severity {
        case "critical": return .red
        case "warning": return .orange
        case "info": return .cyan
        default: return .gray
        }
    }

    var agentEmoji: String { "⚡" }

    var agentLabel: String { "AF" }

    var sourceIcon: String {
        switch source {
        case "slack-dm", "slack-mention", "slack-thread", "slack-channel":
            return "number"
        case "jira":
            return "ticket"
        case "argocd":
            return "arrow.triangle.2.circlepath"
        case "confluence":
            return "doc.richtext"
        case "cloudwatch":
            return "eye.trianglebadge.exclamationmark"
        case "github-pr", "github-actions", "github-mention", "github-digest":
            return "arrow.triangle.branch"
        case "grafana":
            return "chart.line.uptrend.xyaxis"
        case "cloudtrail":
            return "shield.lefthalf.filled"
        case "cloudflare":
            return "cloud.fill"
        case "daily-report":
            return "doc.text"
        case "agent-trace":
            return "waveform.path.ecg"
        case "agent-error":
            return "exclamationmark.triangle"
        case "agent-anomaly":
            return "chart.line.uptrend.xyaxis.circle"
        default:
            return "bolt.fill"
        }
    }

    var sourceLabel: String {
        switch source {
        case "slack-dm": return "Slack"
        case "slack-mention": return "Slack"
        case "slack-thread": return "Slack"
        case "slack-channel": return "Slack"
        case "jira": return "Jira"
        case "confluence": return "Confluence"
        case "argocd": return "Argo"
        case "cloudwatch": return "CW"
        case "github-pr": return "GitHub"
        case "github-actions": return "Actions"
        case "github-mention": return "GitHub"
        case "github-digest": return "GitHub"
        case "grafana": return "Grafana"
        case "cloudtrail": return "IAM"
        case "cloudflare": return "CF"
        case "daily-report": return "Report"
        case "agent-trace": return "Agent"
        case "agent-error": return "Agent"
        case "agent-anomaly": return "Agent"
        default: return source
        }
    }

    var sourceColor: Color {
        switch source {
        case "slack-dm", "slack-mention", "slack-thread", "slack-channel":
            return Color(red: 0.31, green: 0.75, blue: 0.56)
        case "jira":
            return Color(red: 0.0, green: 0.45, blue: 0.94)
        case "confluence":
            return Color(red: 0.0, green: 0.45, blue: 0.94)
        case "argocd":
            return Color(red: 0.94, green: 0.53, blue: 0.22)
        case "cloudwatch", "cloudtrail":
            return Color(red: 1.0, green: 0.6, blue: 0.0)
        case "github-pr", "github-actions", "github-mention", "github-digest":
            return Color(red: 0.56, green: 0.51, blue: 0.86)
        case "grafana":
            return Color(red: 0.96, green: 0.58, blue: 0.11)
        case "cloudflare":
            return Color(red: 0.96, green: 0.55, blue: 0.14)
        case "daily-report":
            return .cyan
        case "agent-trace":
            return Color(red: 0.0, green: 0.8, blue: 0.6)
        case "agent-error":
            return .red
        case "agent-anomaly":
            return .orange
        default:
            return .gray
        }
    }

    static func == (lhs: TickerEvent, rhs: TickerEvent) -> Bool {
        lhs.id == rhs.id
    }
}
