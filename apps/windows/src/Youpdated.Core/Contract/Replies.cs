namespace Youpdated.Core.Contract;

// The fields of each op's ok reply; `id` and `ok` read by EngineConnection

public sealed record HelloReply(string Engine, int Contract, IReadOnlyList<string> Ops, string? Commit, bool Encryption);

public sealed record UnlockReply(Encrypted Encrypted);

public sealed record StatusReply(
    bool Locked,
    Encrypted Encrypted,
    string? ConfigPath,
    bool ConfigExists,
    string? ConfigError,
    string HistoryPath,
    DateTimeOffset? LastRun,
    string? LastRunBy,
    string? SelfWatch,
    Counts? Counts);

public sealed record CheckReply(
    DateTimeOffset CheckedAt,
    int New,
    IReadOnlyList<InboxItem> Items,
    IReadOnlyList<TargetRef> Baselined,
    IReadOnlyList<Problem> Errors,
    IReadOnlyList<string> Warnings,
    IReadOnlyList<Problem> NotifyFailures,
    IReadOnlyList<Alert> Alerts,
    int Held,
    int Unread);

public sealed record InboxListReply(IReadOnlyList<InboxItem> Items, string? Cursor, int Total, int Unread);

public sealed record InboxMarkReply(int Changed, int Unread);

public sealed record InboxSnoozeReply(DateTimeOffset Until);

public sealed record InboxDeleteReply(int Deleted, int Unread);

public sealed record TargetsListReply(IReadOnlyList<Target> Targets);

public sealed record PreviewReply(
    string Source,
    string Entry,
    string Key,
    string Display,
    bool AlreadyWatching,
    PreviewItem? Newest,
    string? FetchError);

public sealed record TargetsAddReply(
    string Section,
    IReadOnlyList<string> Added,
    IReadOnlyList<string> Skipped,
    IReadOnlyList<TargetRef> Targets);

public sealed record TargetsRemoveReply(IReadOnlyList<string> Removed, Wiped? Wiped);

public sealed record ImportPreviewReply(
    IReadOnlyList<SourceEntries> Add,
    IReadOnlyList<SourceEntries> Already,
    IReadOnlyList<Skipped> Skipped,
    IReadOnlyList<string> Warnings);

public sealed record ImportApplyReply(
    IReadOnlyList<SourceEntries> Added,
    IReadOnlyList<Skipped> Skipped,
    IReadOnlyList<string> Warnings,
    IReadOnlyList<TargetRef> Targets);

public sealed record SettingsReply(Settings Settings);

public sealed record PriorityReply(Priority Priority);

public sealed record ProblemsListReply(IReadOnlyList<Problem> Problems, DateTimeOffset? CheckedAt);

public sealed record DigestReply(IReadOnlyList<InboxItem> Items, IReadOnlyList<Alert> Alerts, IReadOnlyList<Problem> NotifyFailures);

public sealed record SnapshotReply(string Path, bool Redacted, int Items);

public sealed record ConfigInitReply(string ConfigPath, bool Created, bool SelfWatchAdded);

public sealed record EmptyReply;

/// <summary><c>icon.get</c></summary>
public sealed record IconReply(Icon? Icon);

public sealed record ProgressEvent(string Event, int Done, int Of, TargetRef? Target = null, bool? Failed = null)
{
    public const string Name = "progress";
}

public sealed record IconEvent(string Event, TargetKey Target)
{
    public const string Name = "icon";
}
