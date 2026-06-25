import Foundation
import Combine

class EventTickerWatcher: ObservableObject {
    @Published var events: [TickerEvent] = []

    private let eventsPath: String
    private let clearedOffsetPath: String
    private var fileOffset: UInt64 = 0
    private var clearedOffset: UInt64 = 0
    private var dispatchSource: DispatchSourceFileSystemObject?
    private var fileDescriptor: Int32 = -1

    var recentCount: Int { events.count }

    static let baseDirectory: String = {
        if let env = ProcessInfo.processInfo.environment["CONDAMON_DIR"] {
            return env
        }
        let execURL = Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
        // CondaMon.app/Contents/MacOS/CondaMon → walk up to condamon/
        let appDir = execURL
            .deletingLastPathComponent()  // MacOS/
            .deletingLastPathComponent()  // Contents/
            .deletingLastPathComponent()  // CondaMon.app/
            .deletingLastPathComponent()  // CondaMon/
            .deletingLastPathComponent()  // condamon/
        return appDir.path
    }()

    init() {
        let base = Self.baseDirectory
        eventsPath = "\(base)/events.jsonl"
        clearedOffsetPath = "\(base)/.cleared_offset"
        if let saved = try? String(contentsOfFile: clearedOffsetPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           let offset = UInt64(saved) {
            clearedOffset = offset
        }
        loadRecent()
    }

    func clearAll() {
        let fm = FileManager.default
        if let attrs = try? fm.attributesOfItem(atPath: eventsPath),
           let size = attrs[.size] as? UInt64 {
            clearedOffset = size
            try? String(size).write(toFile: clearedOffsetPath, atomically: true, encoding: .utf8)
        }
        DispatchQueue.main.async { [weak self] in
            self?.events.removeAll()
        }
    }

    func startWatching() {
        startDispatchSource()
    }

    func stopWatching() {
        dispatchSource?.cancel()
        dispatchSource = nil
        if fileDescriptor >= 0 {
            close(fileDescriptor)
            fileDescriptor = -1
        }
    }

    private func startDispatchSource() {
        stopWatching()

        fileDescriptor = Darwin.open(eventsPath, O_RDONLY | O_EVTONLY)
        guard fileDescriptor >= 0 else { return }

        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fileDescriptor,
            eventMask: [.write, .rename, .delete, .extend],
            queue: .global(qos: .utility)
        )

        source.setEventHandler { [weak self] in
            self?.checkForNew()
        }

        source.setCancelHandler { [weak self] in
            guard let self = self, self.fileDescriptor >= 0 else { return }
            Darwin.close(self.fileDescriptor)
            self.fileDescriptor = -1
        }

        source.resume()
        dispatchSource = source
    }

    private func loadRecent() {
        let fm = FileManager.default
        guard fm.fileExists(atPath: eventsPath),
              let attrs = try? fm.attributesOfItem(atPath: eventsPath),
              let totalSize = attrs[.size] as? UInt64,
              totalSize > clearedOffset else {
            fileOffset = clearedOffset
            events = []
            return
        }

        guard let handle = FileHandle(forReadingAtPath: eventsPath) else {
            fileOffset = clearedOffset
            return
        }
        handle.seek(toFileOffset: clearedOffset)
        let data = handle.readDataToEndOfFile()
        handle.closeFile()
        fileOffset = totalSize

        guard let content = String(data: data, encoding: .utf8) else { return }
        let decoder = JSONDecoder()
        let lines = content.components(separatedBy: "\n").filter { !$0.isEmpty }

        var loaded: [TickerEvent] = []
        for line in lines {
            if let d = line.data(using: .utf8),
               let event = try? decoder.decode(TickerEvent.self, from: d) {
                loaded.append(event)
            }
        }

        events = loaded
    }

    private func checkForNew() {
        let fm = FileManager.default
        guard fm.fileExists(atPath: eventsPath),
              let attrs = try? fm.attributesOfItem(atPath: eventsPath),
              let currentSize = attrs[.size] as? UInt64 else { return }

        if currentSize < fileOffset {
            loadRecent()
            return
        }

        guard currentSize > fileOffset else { return }

        guard let handle = FileHandle(forReadingAtPath: eventsPath) else { return }
        handle.seek(toFileOffset: fileOffset)
        let newData = handle.readDataToEndOfFile()
        handle.closeFile()
        fileOffset = currentSize

        guard let content = String(data: newData, encoding: .utf8) else { return }
        let decoder = JSONDecoder()
        let lines = content.components(separatedBy: "\n").filter { !$0.isEmpty }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            for line in lines {
                if let data = line.data(using: .utf8),
                   let event = try? decoder.decode(TickerEvent.self, from: data) {
                    self.events.append(event)
                }
            }
        }
    }
}
