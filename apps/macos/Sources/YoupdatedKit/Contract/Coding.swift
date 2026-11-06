import Foundation

/// JSON settings shared by everything using the contract
public enum ContractCoding {
    public static let major = 1

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            guard let date = parseTimestamp(raw) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "not a contract timestamp: \(raw)")
            }
            return date
        }
        return decoder
    }

    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(formatTimestamp(date))
        }
        return encoder
    }

    public static func parseTimestamp(_ raw: String) -> Date? {
        if let date = try? Date(raw, strategy: .iso8601) {
            return date
        }
        let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        return try? fractional.parse(raw)
    }

    public static func formatTimestamp(_ date: Date) -> String {
        date.formatted(.iso8601)
    }
}
