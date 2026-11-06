import Foundation

public enum Pause: Codable, Hashable, Sendable {
    case none
    case until(Date)
    case indefinitely

    public func isActive(at now: Date) -> Bool {
        switch self {
        case .none: false
        case .until(let date): now < date
        case .indefinitely: true
        }
    }

    /// `.none` after a timed pause runs out
    public func settled(at now: Date) -> Pause {
        isActive(at: now) ? self : .none
    }
}

public enum PauseOption: String, CaseIterable, Sendable {
    case oneHour, untilTomorrow, untilResumed

    public func pause(from now: Date, calendar: Calendar = .current) -> Pause {
        switch self {
        case .oneHour:
            return .until(now.addingTimeInterval(3600))
        case .untilTomorrow:
            let today = calendar.startOfDay(for: now)
            return .until(calendar.date(byAdding: .day, value: 1, to: today) ?? now.addingTimeInterval(86400))
        case .untilResumed:
            return .indefinitely
        }
    }
}

/// When checks run: every interval since the last, at launch and after wake when due
public enum Schedule {
    /// The next check, or nil while paused until resumed
    public static func nextCheck(lastRun: Date?, interval: TimeInterval, pause: Pause, now: Date) -> Date? {
        let due = lastRun.map { $0.addingTimeInterval(interval) } ?? now
        switch pause.settled(at: now) {
        case .none:
            return due
        case .until(let end):
            return max(due, end)
        case .indefinitely:
            return nil
        }
    }

    public static func isDue(lastRun: Date?, interval: TimeInterval, pause: Pause, now: Date) -> Bool {
        guard let next = nextCheck(lastRun: lastRun, interval: interval, pause: pause, now: now) else {
            return false
        }
        return next <= now
    }
}

/// Daily digest, at local "HH:MM"
public enum DigestSchedule {
    public static let suggested = "18:00"

    public static func components(_ time: String) -> DateComponents? {
        let parts = time.split(separator: ":")
        guard parts.count == 2, let hour = Int(parts[0]), let minute = Int(parts[1]),
              (0..<24).contains(hour), (0..<60).contains(minute) else { return nil }
        return DateComponents(hour: hour, minute: minute)
    }

    public static func time(hour: Int, minute: Int) -> String {
        String(format: "%02d:%02d", hour, minute)
    }

    /// The first digest time after `date`
    public static func next(after date: Date, time: String, calendar: Calendar = .current) -> Date? {
        guard let parts = components(time) else { return nil }
        return calendar.nextDate(after: date, matching: parts, matchingPolicy: .nextTime)
    }

    /// The latest digest time at/before `now`
    public static func latest(atOrBefore now: Date, time: String, calendar: Calendar = .current) -> Date? {
        guard let parts = components(time) else { return nil }
        return calendar.nextDate(
            after: now.addingTimeInterval(1), matching: parts, matchingPolicy: .nextTime, direction: .backward)
    }

    /// A digest time passed since the last one went out
    public static func isMissed(lastSent: Date?, now: Date, time: String, calendar: Calendar = .current) -> Bool {
        guard let lastSent, let latest = latest(atOrBefore: now, time: time, calendar: calendar) else {
            return false
        }
        return latest > lastSent
    }
}
