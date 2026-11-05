import Foundation
import Testing
@testable import YoupdatedKit

@Suite struct ShareQueueTests {
    @Test func appendThenDrain() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "youpdated-share-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        let queue = ShareQueue(container: folder, appGroup: "TEAMID.io.github.void1-1.Youpdated")
        #expect(queue.notificationName == "TEAMID.io.github.void1-1.Youpdated.share")
        #expect(queue.drain().isEmpty)
        try queue.append(["https://github.com/python/cpython"])
        try queue.append(["express"])
        #expect(queue.drain() == ["https://github.com/python/cpython", "express"])
        #expect(queue.drain().isEmpty)
    }

    @Test func noTeamMeansNoAppGroup() {
        #expect(ShareQueue.appGroup(".io.github.void1-1.Youpdated") == nil)
        #expect(ShareQueue.appGroup("$(YOUPDATED_APP_GROUP)") == nil)
        #expect(ShareQueue.appGroup("ABCDE12345.io.github.void1-1.Youpdated") == "ABCDE12345.io.github.void1-1.Youpdated")
    }
}
