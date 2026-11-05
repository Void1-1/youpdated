import Foundation
import Testing
@testable import YoupdatedKit

@Suite struct PresentationCasesTests {
    struct Cases: Decodable {
        struct TargetCase: Decodable {
            let source: String
            let key: String
            let display: String
            let hash: UInt32
            let color: Int
            let colorName: String
            let initials: String
            let label: String
        }

        struct SinceCase: Decodable {
            let now: String
            let found: String
            let says: String
        }

        let palette: [String]
        let targets: [TargetCase]
        let since: [SinceCase]
    }

    static let cases: Cases = {
        let url = Fixtures.root
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()  // apps
            .appending(path: "shared/presentation-cases.json")
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try! decoder.decode(Cases.self, from: Data(contentsOf: url))
    }()

    @Test func paletteOrder() {
        #expect(Monogram.palette.map(\.name) == Self.cases.palette)
    }

    @Test(arguments: cases.targets.map { $0.key })
    func target(_ key: String) throws {
        let item = try #require(Self.cases.targets.first { $0.key == key })
        #expect(Monogram.hash("\(item.source):\(item.key)") == item.hash)
        let monogram = Monogram(source: item.source, key: item.key, display: item.display)
        #expect(monogram.colorIndex == item.color)
        #expect(monogram.color.name == item.colorName)
        #expect(monogram.initials == item.initials)
        #expect(SourceLabel.label(source: item.source, key: item.key) == item.label)
    }

    /// Times are local
    @Test(arguments: cases.since.map { "\($0.now)|\($0.found)" })
    func since(_ pair: String) throws {
        let item = try #require(Self.cases.since.first { "\($0.now)|\($0.found)" == pair })
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        calendar.locale = Locale(identifier: "en_US")
        let parse = { (text: String) in try #require(ContractCoding.parseTimestamp(text + "Z")) }
        #expect(Feed.since(try parse(item.found), now: try parse(item.now), calendar: calendar) == item.says)
    }
}
