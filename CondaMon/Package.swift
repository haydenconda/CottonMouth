// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CondaMon",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "CondaMon",
            path: "CondaMon"
        ),
    ]
)
