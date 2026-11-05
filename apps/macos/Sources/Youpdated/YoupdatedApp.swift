import SwiftUI
import YoupdatedKit
import YoupdatedUI

@main
struct YoupdatedApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Window(String(localized: "Youpdated"), id: WindowID.main) {
            AppWindowView()
                .environment(delegate.model)
                .environment(delegate.router.window)
                .environment(delegate.downloader)
        }
        .defaultSize(width: 560, height: 720)
        .windowToolbarStyle(.unified)
        .defaultLaunchBehavior(.suppressed)
        .commands {
            WindowCommands(model: delegate.model, window: delegate.router.window) { delegate.router.open() }
        }

        Settings {
            SettingsView()
                .environment(delegate.model)
                .environment(delegate.downloader)
                .environment(delegate.loginItem)
        }
    }
}
