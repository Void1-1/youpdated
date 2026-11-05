import AppKit
import Observation
import SwiftUI
import YoupdatedKit
import YoupdatedUI

/// Opens the window from AppKit code: menu bar item, links, Services, notifications
@MainActor
final class Router {
    let window = WindowState()
    private var openWindow: OpenWindowAction?
    private var openSettings: OpenSettingsAction?
    private var waiting = false

    func show(_ mode: WindowState.Mode = .updates) {
        window.mode = mode
        open()
    }

    /// The ＋ popover, filled in with `value`
    func add(_ value: String?, model: AppModel) {
        open()
        window.add(value, model: model)
    }

    func showProblems() {
        window.mode = .updates
        open()
        window.showsProblems = true
    }

    func showSettings() {
        NSApp.activate()
        openSettings?()
    }

    func open() {
        guard let openWindow else {
            waiting = true
            return
        }
        openWindow(id: WindowID.main)
        NSApp.activate()
    }

    fileprivate func connect(open: OpenWindowAction, settings: OpenSettingsAction) {
        openWindow = open
        openSettings = settings
        if waiting {
            waiting = false
            self.open()
        }
    }
}

enum WindowID {
    static let main = "main"
}

/// Zero-size view that captures SwiftUI's window actions for `Router`
struct SceneBridge: View {
    let router: Router
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .onAppear { router.connect(open: openWindow, settings: openSettings) }
    }
}
