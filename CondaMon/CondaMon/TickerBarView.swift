import SwiftUI
import AppKit

struct TickerBarView: View {
    @ObservedObject var watcher: EventTickerWatcher
    @ObservedObject var panelController: PanelController

    private var worstSeverity: Color {
        if watcher.events.contains(where: { $0.severity == "critical" }) { return .red }
        if watcher.events.contains(where: { $0.severity == "warning" }) { return .orange }
        return .cyan.opacity(0.5)
    }

    var body: some View {
        ZStack {
            Color.black

            VStack(spacing: 0) {
                Spacer(minLength: 0)
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [worstSeverity.opacity(0.6), worstSeverity.opacity(0.0)],
                            startPoint: .bottom, endPoint: .top
                        )
                    )
                    .frame(height: 2)
            }

            HStack(spacing: 0) {
                brandBadge
                    .zIndex(1)

                ZStack {
                    if watcher.events.isEmpty {
                        idleView
                    } else {
                        MarqueeStrip(events: Array(watcher.events.suffix(25)))
                    }

                    fadeEdges
                }
            }
        }
        .frame(height: 28)
    }

    private var brandBadge: some View {
        Button(action: { panelController.toggle() }) {
            HStack(spacing: 5) {
                Circle()
                    .fill(watcher.events.isEmpty ? .green : worstSeverity)
                    .frame(width: 6, height: 6)
                    .overlay(
                        Circle()
                            .fill(watcher.events.isEmpty ? .green : worstSeverity)
                            .frame(width: 10, height: 10)
                            .opacity(0.3)
                    )

                Text("COTTONMOUTH")
                    .font(.system(size: 9, weight: .heavy, design: .rounded))
                    .foregroundColor(.white.opacity(0.9))
                    .tracking(0.8)

                if watcher.recentCount > 0 {
                    Text("\(watcher.recentCount)")
                        .font(.system(size: 8, weight: .bold, design: .monospaced))
                        .foregroundColor(.white.opacity(0.5))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(.white.opacity(0.1)))
                }

                Image(systemName: panelController.isOpen ? "chevron.up" : "chevron.down")
                    .font(.system(size: 7, weight: .bold))
                    .foregroundColor(.white.opacity(0.3))
            }
            .padding(.horizontal, 12)
            .frame(height: 28)
            .background(
                ZStack {
                    Color.black
                    LinearGradient(
                        colors: [worstSeverity.opacity(0.15), .clear],
                        startPoint: .leading, endPoint: .trailing
                    )
                }
            )
            .overlay(
                Rectangle()
                    .fill(.white.opacity(0.12))
                    .frame(width: 1),
                alignment: .trailing
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var idleView: some View {
        Text("All systems nominal")
            .font(.system(size: 11, weight: .medium, design: .monospaced))
            .foregroundColor(.white.opacity(0.4))
    }

    private var fadeEdges: some View {
        HStack {
            LinearGradient(colors: [.black, .black.opacity(0)], startPoint: .leading, endPoint: .trailing)
                .frame(width: 30)
            Spacer()
            LinearGradient(colors: [.black.opacity(0), .black], startPoint: .leading, endPoint: .trailing)
                .frame(width: 30)
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Event List Panel (separate window)

struct EventListPanel: View {
    @ObservedObject var events: EventTickerWatcher
    let onClose: () -> Void

    @State private var filterSource: String? = nil
    @State private var selectedEvent: TickerEvent? = nil

    private var allEvents: [TickerEvent] { events.events }

    private var filteredEvents: [TickerEvent] {
        let reversed = allEvents.reversed()
        guard let src = filterSource else { return Array(reversed) }
        return reversed.filter { sourceGroupKey($0.source) == src }
    }

    private var sourceCounts: [(key: String, label: String, icon: String, color: Color, count: Int)] {
        var groups: [String: Int] = [:]
        for event in allEvents {
            let key = sourceGroupKey(event.source)
            groups[key, default: 0] += 1
        }
        return groups.sorted { $0.value > $1.value }.map { (key, count) in
            let sample = allEvents.first { sourceGroupKey($0.source) == key }!
            return (key: key, label: sample.sourceLabel, icon: sample.sourceIcon, color: sample.sourceColor, count: count)
        }
    }

    private func sourceGroupKey(_ source: String) -> String {
        if source.hasPrefix("slack") { return "slack" }
        if source.hasPrefix("github") { return "github" }
        return source
    }

    var body: some View {
        ZStack {
            listView
                .opacity(selectedEvent == nil ? 1 : 0)

            if let event = selectedEvent {
                EventDetailView(event: event, onBack: {
                    withAnimation(.easeInOut(duration: 0.15)) {
                        selectedEvent = nil
                    }
                })
                .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .background(Color(white: 0.08))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.white.opacity(0.08), lineWidth: 1))
    }

    private var listView: some View {
        VStack(spacing: 0) {
            panelHeader
            Divider().background(Color.white.opacity(0.1))
            sourceFilters
            Divider().background(Color.white.opacity(0.1))
            eventScrollList
        }
    }

    private var panelHeader: some View {
        HStack {
            Text("Events")
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .foregroundColor(.white.opacity(0.9))
            Spacer()
            Text("\(filteredEvents.count) events")
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.white.opacity(0.35))
            Button(action: { events.clearAll() }) {
                HStack(spacing: 3) {
                    Image(systemName: "trash")
                        .font(.system(size: 8, weight: .bold))
                    Text("Clear")
                        .font(.system(size: 9, weight: .medium, design: .rounded))
                }
                .foregroundColor(.white.opacity(0.4))
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(RoundedRectangle(cornerRadius: 4).fill(.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundColor(.white.opacity(0.4))
                    .frame(width: 20, height: 20)
                    .background(Circle().fill(.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var sourceFilters: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 5) {
                filterPill(label: "All", icon: "bolt.fill", color: .white, isActive: filterSource == nil) {
                    filterSource = nil
                }
                ForEach(sourceCounts, id: \.key) { item in
                    filterPill(label: item.label, icon: item.icon, color: item.color, count: item.count, isActive: filterSource == item.key) {
                        filterSource = filterSource == item.key ? nil : item.key
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
        }
    }

    private func filterPill(label: String, icon: String, color: Color, count: Int = 0, isActive: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.system(size: 8, weight: .semibold))
                Text(label)
                    .font(.system(size: 10, weight: isActive ? .bold : .medium, design: .rounded))
                if count > 0 {
                    Text("\(count)")
                        .font(.system(size: 8, weight: .bold, design: .monospaced))
                        .foregroundColor(isActive ? color : .white.opacity(0.35))
                }
            }
            .foregroundColor(isActive ? color : .white.opacity(0.5))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: 5)
                    .fill(isActive ? color.opacity(0.15) : .white.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 5)
                    .stroke(isActive ? color.opacity(0.3) : .clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private var eventScrollList: some View {
        ScrollView {
            LazyVStack(spacing: 1) {
                ForEach(filteredEvents) { event in
                    EventListRow(event: event, onSelect: {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            selectedEvent = event
                        }
                    })
                }
            }
            .padding(.vertical, 4)
        }
    }
}

struct EventListRow: View {
    let event: TickerEvent
    let onSelect: () -> Void
    @State private var isHovering = false

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 1)
                    .fill(event.severityColor)
                    .frame(width: 3, height: 32)

                ZStack {
                    RoundedRectangle(cornerRadius: 5)
                        .fill(event.sourceColor.opacity(0.12))
                        .frame(width: 26, height: 26)
                    Image(systemName: event.sourceIcon)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(event.sourceColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text(event.sourceLabel)
                            .font(.system(size: 9, weight: .bold, design: .rounded))
                            .foregroundColor(event.sourceColor)

                        Text(event.title)
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundColor(.white.opacity(0.9))
                            .lineLimit(1)
                    }
                    Text(event.message)
                        .font(.system(size: 10, design: .rounded))
                        .foregroundColor(.white.opacity(0.5))
                        .lineLimit(1)
                }

                Spacer()

                HStack(spacing: 6) {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(timeAgo)
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.white.opacity(0.25))
                    }
                    Image(systemName: "chevron.right")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundColor(isHovering ? event.sourceColor : .white.opacity(0.12))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(isHovering ? Color.white.opacity(0.04) : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { h in isHovering = h }
    }

    private var timeAgo: String {
        let interval = Date().timeIntervalSince(event.date)
        if interval < 60 { return "now" }
        if interval < 3600 { return "\(Int(interval / 60))m" }
        if interval < 86400 { return "\(Int(interval / 3600))h" }
        return "\(Int(interval / 86400))d"
    }
}

// MARK: - Marquee

struct MarqueeStrip: NSViewRepresentable {
    let events: [TickerEvent]

    func makeNSView(context: Context) -> MarqueeScrollView {
        MarqueeScrollView()
    }

    func updateNSView(_ nsView: MarqueeScrollView, context: Context) {
        nsView.updateEvents(events)
    }
}

class MarqueeScrollView: NSView {
    private var hostA: NSHostingView<AnyView>?
    private var hostB: NSHostingView<AnyView>?
    private var currentEvents: [TickerEvent] = []
    private var displayLink: CVDisplayLink?
    private var offset: CGFloat = 0
    private var stripWidth: CGFloat = 0
    private var isPaused = false
    private var trackingArea: NSTrackingArea?
    private let gap: CGFloat = 12
    private let pixelsPerSecond: CGFloat = 48.0

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = true
    }

    required init?(coder: NSCoder) { fatalError() }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window != nil { startDisplayLink() } else { stopDisplayLink() }
    }

    override func layout() {
        super.layout()
        if offset == 0 { offset = bounds.width }
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let ta = trackingArea { removeTrackingArea(ta) }
        let ta = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways], owner: self)
        addTrackingArea(ta)
        trackingArea = ta
    }

    override func mouseEntered(with event: NSEvent) { isPaused = true }
    override func mouseExited(with event: NSEvent) { isPaused = false }

    func updateEvents(_ events: [TickerEvent]) {
        guard events != currentEvents else { return }
        currentEvents = events
        rebuildHosts()
    }

    private func rebuildHosts() {
        hostA?.removeFromSuperview()
        hostB?.removeFromSuperview()

        let itemGap = gap
        let content = AnyView(
            HStack(spacing: 0) {
                ForEach(Array(currentEvents.enumerated()), id: \.element.id) { index, event in
                    if index > 0 {
                        TickerDivider().padding(.horizontal, itemGap)
                    }
                    TickerItemView(event: event)
                }
            }
            .fixedSize()
        )

        let a = NSHostingView(rootView: content)
        a.frame.size = a.fittingSize
        a.frame.origin.y = 0
        stripWidth = a.frame.width
        addSubview(a)
        hostA = a

        let b = NSHostingView(rootView: content)
        b.frame.size = b.fittingSize
        b.frame.origin.y = 0
        addSubview(b)
        hostB = b

        offset = bounds.width
        positionHosts()
    }

    private func positionHosts() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        hostA?.layer?.transform = CATransform3DMakeTranslation(offset, 0, 0)
        hostB?.layer?.transform = CATransform3DMakeTranslation(offset + stripWidth + gap, 0, 0)
        CATransaction.commit()
    }

    private func tick() {
        guard !isPaused, stripWidth > 0 else { return }
        let step = pixelsPerSecond / 60.0
        offset -= step
        if offset <= -(stripWidth + gap) {
            offset += stripWidth + gap
        }
        positionHosts()
    }

    private func startDisplayLink() {
        guard displayLink == nil else { return }
        var link: CVDisplayLink?
        CVDisplayLinkCreateWithActiveCGDisplays(&link)
        guard let dl = link else { return }

        let opaquePtr = Unmanaged.passUnretained(self).toOpaque()
        CVDisplayLinkSetOutputCallback(dl, { (_, _, _, _, _, userInfo) -> CVReturn in
            let view = Unmanaged<MarqueeScrollView>.fromOpaque(userInfo!).takeUnretainedValue()
            DispatchQueue.main.async { view.tick() }
            return kCVReturnSuccess
        }, opaquePtr)

        CVDisplayLinkStart(dl)
        displayLink = dl
    }

    private func stopDisplayLink() {
        guard let dl = displayLink else { return }
        CVDisplayLinkStop(dl)
        displayLink = nil
    }

    deinit {
        stopDisplayLink()
    }
}

struct TickerDivider: View {
    var body: some View {
        HStack(spacing: 3) {
            Circle().fill(.white.opacity(0.12)).frame(width: 2, height: 2)
            Circle().fill(.white.opacity(0.2)).frame(width: 3, height: 3)
            Circle().fill(.white.opacity(0.12)).frame(width: 2, height: 2)
        }
    }
}

struct TickerItemView: View {
    let event: TickerEvent
    @State private var isHovering = false

    var body: some View {
        Button(action: openLink) {
            HStack(spacing: 5) {
                RoundedRectangle(cornerRadius: 1)
                    .fill(event.severityColor)
                    .frame(width: 3, height: 14)

                Image(systemName: event.sourceIcon)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(event.sourceColor)
                    .frame(width: 14)

                Text(event.title)
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundColor(.white.opacity(0.95))
                    .lineLimit(1)

                Text(event.message)
                    .font(.system(size: 11, design: .rounded))
                    .foregroundColor(.white.opacity(0.55))
                    .lineLimit(1)
                    .frame(maxWidth: 320, alignment: .leading)

                Text(timeAgo)
                    .font(.system(size: 9, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.25))

                if !event.action_url.isEmpty {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 7, weight: .bold))
                        .foregroundColor(isHovering ? event.severityColor : .white.opacity(0.2))
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: 5)
                    .fill(isHovering ? Color.white.opacity(0.08) : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { h in isHovering = h }
    }

    private func openLink() {
        guard !event.action_url.isEmpty,
              let url = URL(string: event.action_url) else { return }
        NSWorkspace.shared.open(url)
    }

    private var timeAgo: String {
        let interval = Date().timeIntervalSince(event.date)
        if interval < 60 { return "now" }
        if interval < 3600 { return "\(Int(interval / 60))m" }
        if interval < 86400 { return "\(Int(interval / 3600))h" }
        return "\(Int(interval / 86400))d"
    }
}
