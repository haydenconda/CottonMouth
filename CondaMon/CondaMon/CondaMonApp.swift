import SwiftUI
import AppKit

@main
struct CondaMonApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        Settings { EmptyView() }
    }
}

class PanelController: ObservableObject {
    @Published var isOpen = false
    var onToggle: (() -> Void)?

    func toggle() {
        isOpen.toggle()
        onToggle?()
    }

    func close() {
        isOpen = false
        onToggle?()
    }
}

class KeyableWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var tickerWindow: NSWindow?
    var panelWindow: KeyableWindow?
    var statusItem: NSStatusItem?
    let watcher = EventTickerWatcher()
    let panelController = PanelController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        createTickerWindow()
        createPanelWindow()
        watcher.startWatching()

        panelController.onToggle = { [weak self] in
            self?.updatePanelVisibility()
        }
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "eye.trianglebadge.exclamationmark", accessibilityDescription: "CottonMouth")
            button.image?.size = NSSize(width: 16, height: 16)
        }

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Show All Events", action: #selector(showEvents), keyEquivalent: "e"))
        menu.addItem(NSMenuItem(title: "Toggle Ticker", action: #selector(toggleTicker), keyEquivalent: "t"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit CottonMouth", action: #selector(quitApp), keyEquivalent: "q"))
        statusItem?.menu = menu
    }

    private func createTickerWindow() {
        guard let screen = NSScreen.main else { return }

        let menuBarHeight: CGFloat = NSStatusBar.system.thickness
        let tickerHeight: CGFloat = 28
        let frame = NSRect(
            x: 0,
            y: screen.frame.height - menuBarHeight - tickerHeight,
            width: screen.frame.width,
            height: tickerHeight
        )

        let window = NSWindow(
            contentRect: frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.level = .statusBar
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.isMovable = false
        window.ignoresMouseEvents = false

        let hostingView = NSHostingView(rootView: TickerBarView(watcher: watcher, panelController: panelController))
        window.contentView = hostingView
        window.orderFrontRegardless()

        tickerWindow = window
    }

    private func createPanelWindow() {
        guard let screen = NSScreen.main else { return }

        let menuBarHeight: CGFloat = NSStatusBar.system.thickness
        let tickerHeight: CGFloat = 28
        let panelHeight: CGFloat = 560
        let panelWidth: CGFloat = 440

        let frame = NSRect(
            x: 12,
            y: screen.frame.height - menuBarHeight - tickerHeight - panelHeight - 4,
            width: panelWidth,
            height: panelHeight
        )

        let window = KeyableWindow(
            contentRect: frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.level = .statusBar
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.isMovable = false
        window.acceptsMouseMovedEvents = true

        let hostingView = NSHostingView(
            rootView: EventListPanel(events: watcher, onClose: { [weak self] in
                self?.panelController.close()
            })
        )
        window.contentView = hostingView

        panelWindow = window
    }

    private func updatePanelVisibility() {
        if panelController.isOpen {
            NSApp.activate(ignoringOtherApps: true)
            panelWindow?.orderFrontRegardless()
            panelWindow?.makeKeyAndOrderFront(nil)
        } else {
            panelWindow?.orderOut(nil)
            panelWindow?.resignKey()
        }
    }

    @objc func showEvents() {
        if !panelController.isOpen {
            panelController.isOpen = true
        }
        updatePanelVisibility()
    }

    @objc func toggleTicker() {
        if let window = tickerWindow {
            if window.isVisible {
                window.orderOut(nil)
                panelController.close()
            } else {
                window.orderFrontRegardless()
            }
        }
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }
}
