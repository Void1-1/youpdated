using System.Text.Json.Serialization;

namespace Youpdated.Core.Contract;

// Contract v1 types (apps/contract/v1/contract.schema.json)

[JsonConverter(typeof(JsonStringEnumConverter<Priority>))]
public enum Priority
{
    [JsonStringEnumMemberName("high")] High,
    [JsonStringEnumMemberName("normal")] Normal,
    [JsonStringEnumMemberName("low")] Low,
}

public sealed record TargetKey(string Source, string Key);

public sealed record TargetRef(string Source, string Key, string Display)
{
    public TargetKey AsKey() => new(Source, Key);
}

public sealed record Target(
    string Source,
    string Key,
    string Display,
    Priority Priority,
    bool Starred,
    DateTimeOffset? LastChecked,
    string? LastError,
    int Unread,
    // Null until target's first item
    TargetLatest? Latest)
{
    public TargetKey AsKey() => new(Source, Key);
}

public sealed record InboxItem(
    string Id,
    string Source,
    string Target,
    string Display,
    string Title,
    string Url,
    string? Version,
    DateTimeOffset? Published,
    IReadOnlyList<string> Tags,
    string? Body,
    DateTimeOffset FoundAt,
    bool Read,
    bool Baseline,
    Priority Priority,
    bool Starred,
    DateTimeOffset? SnoozedUntil)
{
    public TargetKey TargetKey => new(Source, Target);

    /// <summary>When it came out, or when it was found if no date</summary>
    public DateTimeOffset When => Published ?? FoundAt;
}

/// <summary>The newest item the engine has seen for a target</summary>
public sealed record TargetLatest(string? Version, string Title, DateTimeOffset? Published);

public sealed record PreviewItem(string Title, string Url, string? Version, DateTimeOffset? Published);

public sealed record Problem(
    string Kind,
    string Source,
    string Target,
    string Message,
    // The failing target's key
    string? Key)
{
    public const string SourceKind = "source";
    public const string NotifyKind = "notify";
    public const string ConfigKind = "config";

    /// <summary>App-side: the engine crashed or couldn't start</summary>
    public const string EngineKind = "engine";
}

[JsonConverter(typeof(JsonStringEnumConverter<AlertPriority>))]
public enum AlertPriority
{
    [JsonStringEnumMemberName("high")] High,
    [JsonStringEnumMemberName("normal")] Normal,
}

public sealed record Alert(AlertPriority Priority, TargetRef? Target, IReadOnlyList<string> ItemIds);

public sealed record Settings(
    int CheckIntervalHours,
    string? DigestTime,
    bool OsNotifications,
    bool SelfWatchDeclined,
    bool CliNoticeShown,
    bool ShowIcons)
{
    public static IReadOnlyList<int> IntervalChoices { get; } = [1, 3, 6, 12, 24];

    public static Settings Defaults { get; } = new(6, null, true, false, false, false);

    /// <summary>The digest time as local time</summary>
    public TimeOnly? Digest =>
        DigestTime is { } text && TimeOnly.TryParseExact(text, "HH:mm", out var time) ? time : null;
}

/// <summary>The config's <c>settings.privacy</c></summary>
public sealed record Privacy(
    string? Proxy,
    bool FastMode,
    string UserAgent,
    IReadOnlyList<double> Jitter,
    int Concurrency,
    double Timeout);

public sealed record Encrypted(bool Config, bool History)
{
    public bool Any => Config || History;
}

public sealed record Counts(int Targets, int Unread, int Problems);

public sealed record SourceEntries(string Source, IReadOnlyList<string> Entries);

public sealed record Skipped(string What, string Why);

public sealed record Wiped(int Seen, int Validators, int Inbox);

/// <summary>A target's icon, from <c>icon.get</c></summary>
public sealed record Icon(string Mime, string Data)
{
    public byte[] Bytes() => Convert.FromBase64String(Data);
}

public sealed record EngineError(string Code, string Message, IReadOnlyList<string>? Choices = null);

public static class ErrorCodes
{
    public const string BadRequest = "bad_request";
    public const string UnknownOp = "unknown_op";
    public const string Locked = "locked";
    public const string WrongPassphrase = "wrong_passphrase";
    public const string EncryptionUnavailable = "encryption_unavailable";
    public const string NoConfig = "no_config";
    public const string ConfigError = "config_error";
    public const string HistoryBusy = "history_busy";
    public const string Busy = "busy";
    public const string Ambiguous = "ambiguous";
    public const string Unrecognized = "unrecognized";
    public const string BadEntry = "bad_entry";
    public const string NotFound = "not_found";
    public const string IoError = "io_error";
    public const string ProxyUnreachable = "proxy_unreachable";
    public const string UserAgentUnconfirmed = "user_agent_unconfirmed";
    public const string Internal = "internal";
}

/// <summary><c>status.named_user_agent</c>: <c>user_agent: youpdated</c> on an encrypted, proxied setup</summary>
public static class NamedUserAgent
{
    public const string Unconfirmed = "unconfirmed";
    public const string Confirmed = "confirmed";
}

public static class SelfWatch
{
    public const string Watching = "watching";
    public const string Declined = "declined";
    public const string Absent = "absent";
}

public static class RunBy
{
    public const string App = "app";
    public const string Cli = "cli";
}
