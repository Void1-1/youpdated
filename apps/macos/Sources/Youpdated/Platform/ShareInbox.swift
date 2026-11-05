import Foundation
import YoupdatedKit

/// Picks up links the Share extension queued
@MainActor
final class ShareInbox {
    private let queue: ShareQueue?
    private let handle: (String) -> Void

    init(handle: @escaping (String) -> Void) {
        self.handle = handle
        queue = ShareQueue.shared()
        guard let queue else { return }
        let observer = Unmanaged.passUnretained(self).toOpaque()
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(), observer,
            { _, observer, _, _, _ in
                guard let observer else { return }
                let inbox = Unmanaged<ShareInbox>.fromOpaque(observer).takeUnretainedValue()
                DispatchQueue.main.async {
                    MainActor.assumeIsolated { inbox.drain() }
                }
            },
            queue.notificationName as CFString, nil, .deliverImmediately)
        drain()
    }

    private func drain() {
        // one sheet at a time: the last shared link wins
        if let value = queue?.drain().last {
            handle(value)
        }
    }
}
