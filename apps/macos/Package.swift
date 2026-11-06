// swift-tools-version: 6.0
import PackageDescription

// app logic in YoupdatedKit; window views in YoupdatedUI
let package = Package(
    name: "Youpdated",
    defaultLocalization: "en",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "YoupdatedKit", targets: ["YoupdatedKit"]),
        .library(name: "YoupdatedUI", targets: ["YoupdatedUI"]),
        .executable(name: "YoupdatedDev", targets: ["YoupdatedDev"]),
        .executable(name: "YoupdatedScreens", targets: ["YoupdatedScreens"]),
    ],
    targets: [
        .target(name: "YoupdatedKit"),
        .target(name: "YoupdatedUI", dependencies: ["YoupdatedKit"]),
        .executableTarget(name: "YoupdatedDev", dependencies: ["YoupdatedKit", "YoupdatedUI"], path: "Sources/Youpdated"),
        .executableTarget(name: "YoupdatedScreens", dependencies: ["YoupdatedKit", "YoupdatedUI"], path: "Tools/YoupdatedScreens"),
        .testTarget(name: "YoupdatedKitTests", dependencies: ["YoupdatedKit"]),
    ],
    swiftLanguageModes: [.v6]
)
