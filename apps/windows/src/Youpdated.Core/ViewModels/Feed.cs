using System.Globalization;
using Youpdated.Core.Contract;

namespace Youpdated.Core.ViewModels;

public enum Density
{
    /// <summary>Cards</summary>
    Comfortable,
    /// <summary>Every item a one-line row; cards when expanded</summary>
    Compact,
}

public enum GroupBy
{
    Day,
    Priority,
    Source,
    None,
}

/// <summary>Which read updates the feed keeps under Earlier</summary>
public enum ReadUpdates
{
    Last7Days,
    Last30Days,
    All,
    None,
}

public enum TimeStyle
{
    Relative,
    Absolute,
}

public sealed record ViewOptions(
    Density Density = Density.Comfortable,
    GroupBy GroupBy = GroupBy.Day,
    bool ShowLowPriority = true,
    ReadUpdates ReadUpdates = ReadUpdates.Last30Days,
    bool Previews = true,
    TimeStyle Times = TimeStyle.Relative,
    /// <summary>Read updates join their group, after unread; by day, they stay under Earlier</summary>
    bool GroupReadUpdates = true)
{
    public static ViewOptions Defaults { get; } = new();

    /// <summary>How far back read updates reach; null for all</summary>
    public TimeSpan? ReadWindow => ReadUpdates switch
    {
        ReadUpdates.Last7Days => TimeSpan.FromDays(7),
        ReadUpdates.Last30Days => TimeSpan.FromDays(30),
        ReadUpdates.None => TimeSpan.Zero,
        _ => null,
    };
}

public enum FeedSectionKind
{
    Today,
    Yesterday,
    ThisWeek,
    Earlier,
    Priority,
    Source,
    Unread,
}

/// <summary>A section: its kind, and for Priority and Source which one</summary>
public sealed record FeedSectionKey(FeedSectionKind Kind, string? Value = null);

public sealed record FeedCardData(
    TargetKey Target,
    string Display,
    IReadOnlyList<InboxItem> Items,
    bool Unread,
    ViewOptions? View = null)
{
    private ViewOptions Options => View ?? ViewOptions.Defaults;

    public InboxItem Lead => Items[0];

    public string Source => Target.Source;

    public int More => Items.Count - 1;

    public Priority Priority => Lead.Priority;

    public bool Starred => Lead.Starred;

    /// <summary>When the card's newest item was found: which day section it sits in</summary>
    public DateTimeOffset FoundAt => Items.Max(item => item.FoundAt);

    public bool ShowsUnread => Items.Any(item => !item.Read);

    public bool IsCompact => Options.Density == Density.Compact || Priority == Priority.Low;

    public bool IsDimmed => !Unread;

    public bool ShowsHighPriority => Priority == Priority.High;

    public bool ShowsAccentEdge => Priority == Priority.High && ShowsUnread;

    public bool LeadIsVersion => !string.IsNullOrWhiteSpace(Lead.Version);

    public string LeadText => LeadIsVersion ? FeedText.DisplayVersion(Lead.Version!) : Lead.Title;

    public string? Line =>
        LeadIsVersion && !FeedText.SameVersion(Lead.Title, Lead.Version!) ? Lead.Title
        : Options.Previews ? Notes.FirstLine(Lead.Body)
        : null;

    /// <summary>The target's other updates on the card, for "Also new"</summary>
    public IReadOnlyList<InboxItem> AlsoNew => [.. Items.Skip(1)];

    public string Key => $"{Target.Source}\u0001{Target.Key}\u0001{(Unread ? "unread" : "read")}";
}

public sealed record FeedSectionData(FeedSectionKey Key, IReadOnlyList<FeedCardData> Cards);

/// <summary>What the feed shows: self-update card, then sections</summary>
public sealed record FeedLayout(
    AppUpdate? SelfUpdate,
    IReadOnlyList<FeedSectionData> Sections,
    int Unread,
    DateTimeOffset? OldestUnreadFoundAt)
{
    public IEnumerable<FeedCardData> Cards => Sections.SelectMany(section => section.Cards);
}

public sealed record FeedOptions(ViewOptions? View = null, string? Source = null, TargetKey? Target = null)
{
    public ViewOptions Options => View ?? ViewOptions.Defaults;
}

public static class FeedBuilder
{
    /// <param name="held">Items to place as if unread</param>
    public static FeedLayout Build(
        IEnumerable<InboxItem> items, FeedOptions options, DateTimeOffset now, TimeZoneInfo zone, Version appVersion,
        IReadOnlySet<string>? held = null)
    {
        var all = items.ToList();
        var view = options.Options;
        held ??= new HashSet<string>();
        bool Unread(InboxItem item) => !item.Read || held.Contains(item.Id);

        // The self-update card is pinned while an app update is unread
        var updates = all
            .Where(item => !item.Read && AppUpdate.From(item, appVersion) is not null)
            .ToList();
        var selfUpdate = AppUpdate.Newest(updates, appVersion);
        var updateIds = updates.Select(item => item.Id).ToHashSet();

        var readSince = view.ReadWindow is { } window ? now - window : DateTimeOffset.MinValue;
        var shown = all.Where(item => !updateIds.Contains(item.Id)
            && (options.Source is null || SourceLabels.Label(item.Source, item.Target) == options.Source)
            && (options.Target is null || item.TargetKey == options.Target)
            // Hidden low priority still shows when filtering to its target
            && (view.ShowLowPriority || item.Priority != Priority.Low || options.Target is not null)
            && (Unread(item) || item.FoundAt >= readSince)).ToList();

        var unreadCards = Cards(shown.Where(Unread), unread: true, view);
        var readCards = Cards(shown.Where(item => !Unread(item)), unread: false, view);

        List<FeedSectionData> Group(List<FeedCardData> cards) => view.GroupBy switch
        {
            GroupBy.Priority => Grouped(cards, card => new FeedSectionKey(FeedSectionKind.Priority, card.Priority.ToString()),
                key => Enum.Parse<Priority>(key.Value!), Comparer<Priority>.Default),
            GroupBy.Source => Grouped(cards, card => new FeedSectionKey(FeedSectionKind.Source, SourceLabels.Label(card.Source, card.Target.Key)),
                key => key.Value!, StringComparer.CurrentCultureIgnoreCase),
            GroupBy.None => cards.Count == 0 ? [] : [new FeedSectionData(new FeedSectionKey(FeedSectionKind.Unread), Sorted(cards, ByFound))],
            _ => ByDay(cards, now, zone),
        };
        var sections = Group(unreadCards);

        if (view.GroupReadUpdates && view.GroupBy != GroupBy.Day)
        {
            // each group's unread, then read cards
            var grouped = sections.Concat(Group(readCards))
                .GroupBy(section => section.Key)
                .Select(group => new FeedSectionData(group.Key, [.. group.SelectMany(section => section.Cards)]))
                .ToList();
            grouped.Sort((a, b) => view.GroupBy == GroupBy.Priority
                ? Enum.Parse<Priority>(a.Key.Value!).CompareTo(Enum.Parse<Priority>(b.Key.Value!))
                : string.Compare(a.Key.Value, b.Key.Value, StringComparison.CurrentCultureIgnoreCase));
            sections = grouped;
        }
        else
        {
            var earlierUnread = sections.FirstOrDefault(s => s.Key.Kind == FeedSectionKind.Earlier)?.Cards ?? [];
            sections = [.. sections.Where(s => s.Key.Kind != FeedSectionKind.Earlier)];
            var read = Sorted(readCards, view.GroupBy switch
            {
                GroupBy.Priority => (a, b) => a.Priority != b.Priority ? a.Priority.CompareTo(b.Priority) : ByWhen(a, b),
                GroupBy.Source => (a, b) => string.Compare(
                    SourceLabels.Label(a.Source, a.Target.Key), SourceLabels.Label(b.Source, b.Target.Key),
                    StringComparison.CurrentCultureIgnoreCase) is var by and not 0 ? by : ByWhen(a, b),
                _ => ByWhen,
            });
            var earlier = earlierUnread.Concat(read).ToList();
            if (earlier.Count > 0)
            {
                sections.Add(new FeedSectionData(new FeedSectionKey(FeedSectionKind.Earlier), earlier));
            }
        }

        var unreadFound = shown.Where(item => !item.Read).Concat(updates).Select(item => item.FoundAt).ToList();
        return new FeedLayout(
            selfUpdate,
            sections,
            unreadFound.Count,
            unreadFound.Count > 0 ? unreadFound.Min() : null);
    }

    private static List<FeedCardData> Cards(IEnumerable<InboxItem> items, bool unread, ViewOptions view) =>
    [
        .. items.GroupBy(item => item.TargetKey).Select(group =>
        {
            var ordered = group.OrderByDescending(item => item.When).ThenBy(item => item.Id, StringComparer.Ordinal).ToList();
            return new FeedCardData(group.Key, ordered[0].Display, ordered, unread, view);
        }),
    ];

    private static List<FeedSectionData> ByDay(List<FeedCardData> cards, DateTimeOffset now, TimeZoneInfo zone)
    {
        var today = TimeZoneInfo.ConvertTime(now, zone).Date;
        FeedSectionKind Day(FeedCardData card) => (today - TimeZoneInfo.ConvertTime(card.FoundAt, zone).Date).Days switch
        {
            <= 0 => FeedSectionKind.Today,
            1 => FeedSectionKind.Yesterday,
            < 7 => FeedSectionKind.ThisWeek,
            _ => FeedSectionKind.Earlier,
        };
        return
        [
            .. cards.GroupBy(Day).OrderBy(group => group.Key)
                .Select(group => new FeedSectionData(new FeedSectionKey(group.Key), Sorted(group, ByFound))),
        ];
    }

    private static List<FeedSectionData> Grouped<T>(
        List<FeedCardData> cards, Func<FeedCardData, FeedSectionKey> keyOf, Func<FeedSectionKey, T> orderOf, IComparer<T> order) =>
    [
        .. cards.GroupBy(keyOf).OrderBy(group => orderOf(group.Key), order)
            .Select(group => new FeedSectionData(group.Key, Sorted(group, ByFound))),
    ];

    private static List<FeedCardData> Sorted(IEnumerable<FeedCardData> cards, Comparison<FeedCardData> order)
    {
        var sorted = cards.ToList();
        sorted.Sort(order);
        return sorted;
    }

    /// <summary>Most recently found, newest, by name</summary>
    private static int ByFound(FeedCardData a, FeedCardData b) =>
        b.FoundAt.CompareTo(a.FoundAt) is var by and not 0 ? by : ByWhen(a, b);

    private static int ByWhen(FeedCardData a, FeedCardData b) =>
        b.Lead.When.CompareTo(a.Lead.When) is var by and not 0
            ? by
            : string.Compare(a.Display, b.Display, StringComparison.CurrentCultureIgnoreCase);
}

/// <summary>Which phrase "Since ..." uses</summary>
public enum SinceKind
{
    ThisMorning,
    ThisAfternoon,
    ThisEvening,
    Yesterday,
    Weekday,
    Date,
}

/// <summary>A check in progress</summary>
public sealed record CheckProgress(int Done, int Of);

public enum HeadingPartKind
{
    Since,
    Checked,
    Checking,
    Paused,
}

public sealed record HeadingPart(HeadingPartKind Kind, string Text);

/// <summary>The heading: count, one line of what applies</summary>
public sealed record FeedHeading(
    int Unread,
    SinceKind? Since,
    DateTimeOffset? At,
    DateTimeOffset? CheckedAt,
    TimeZoneInfo Zone,
    DateTimeOffset Now,
    CheckProgress? Checking = null,
    DateTimeOffset? PausedUntil = null)
{
    public bool UpToDate => Unread == 0;

    public static FeedHeading For(
        FeedLayout layout, DateTimeOffset? lastCheck, DateTimeOffset now, TimeZoneInfo zone,
        CheckProgress? checking = null, DateTimeOffset? pausedUntil = null)
    {
        if (layout.Unread == 0 || layout.OldestUnreadFoundAt is not { } oldest)
        {
            return new FeedHeading(0, null, null, lastCheck, zone, now, checking, pausedUntil);
        }
        return new FeedHeading(layout.Unread, SinceOf(oldest, now, zone), oldest, lastCheck, zone, now, checking, pausedUntil);
    }

    /// <summary>The "Since ..." phrase for <paramref name="found"/>, as of <paramref name="now"/></summary>
    public static SinceKind SinceOf(DateTimeOffset found, DateTimeOffset now, TimeZoneInfo zone)
    {
        var local = TimeZoneInfo.ConvertTime(found, zone);
        var days = (TimeZoneInfo.ConvertTime(now, zone).Date - local.Date).Days;
        return days switch
        {
            <= 0 => local.Hour < 12 ? SinceKind.ThisMorning : local.Hour < 18 ? SinceKind.ThisAfternoon : SinceKind.ThisEvening,
            1 => SinceKind.Yesterday,
            < 7 => SinceKind.Weekday,
            _ => SinceKind.Date,
        };
    }

    public string Title(FeedText text) => Unread switch
    {
        0 => text.UpToDate,
        1 => text.NewUpdatesOne,
        _ => string.Format(text.Culture, text.NewUpdatesMany, Unread),
    };

    /// <summary>"Since this morning", or null when up to date</summary>
    public string? SinceText(FeedText text)
    {
        if (UpToDate || Since is not { } since || At is not { } at)
        {
            return null;
        }
        var local = TimeZoneInfo.ConvertTime(at, Zone);
        return since switch
        {
            SinceKind.ThisMorning => text.SinceThisMorning,
            SinceKind.ThisAfternoon => text.SinceThisAfternoon,
            SinceKind.ThisEvening => text.SinceThisEvening,
            SinceKind.Yesterday => text.SinceYesterday,
            SinceKind.Weekday => string.Format(text.Culture, text.SinceDay, text.Culture.DateTimeFormat.GetDayName(local.DayOfWeek)),
            _ => string.Format(text.Culture, text.SinceDay, text.Date(local, TimeZoneInfo.ConvertTime(Now, Zone))),
        };
    }

    public IReadOnlyList<HeadingPart> Line(FeedText text)
    {
        var parts = new List<HeadingPart>();
        if (SinceText(text) is { } since)
        {
            parts.Add(new HeadingPart(HeadingPartKind.Since, since));
        }
        if (Checking is { } checking)
        {
            parts.Add(new HeadingPart(HeadingPartKind.Checking, checking.Of > 0
                ? string.Format(text.Culture, text.CheckingProgress, checking.Done, checking.Of)
                : text.CheckingStarted));
        }
        else if (CheckedAt is { } checkedAt)
        {
            var local = TimeZoneInfo.ConvertTime(checkedAt, Zone);
            var today = TimeZoneInfo.ConvertTime(Now, Zone);
            var when = local.Date == today.Date ? local.ToString("t", text.Culture) : text.Date(local, today);
            parts.Add(new HeadingPart(HeadingPartKind.Checked, string.Format(text.Culture, text.Checked, when)));
        }
        else
        {
            parts.Add(new HeadingPart(HeadingPartKind.Checked, text.NotChecked));
        }
        if (PausedUntil is { } until)
        {
            parts.Add(new HeadingPart(HeadingPartKind.Paused, until == DateTimeOffset.MaxValue
                ? text.Paused
                : string.Format(text.Culture, text.PausedUntil, TimeZoneInfo.ConvertTime(until, Zone).ToString("t", text.Culture))));
        }
        return parts;
    }

    public string LineText(FeedText text) => string.Join(text.Separator, Line(text).Select(part => part.Text));
}

public sealed record FeedText
{
    public CultureInfo Culture { get; init; } = CultureInfo.GetCultureInfo("en-US");

    public string NewUpdatesOne { get; init; } = "1 new update";

    public string NewUpdatesMany { get; init; } = "{0} new updates";

    public string UpToDate { get; init; } = "You're up to date";

    public string SinceThisMorning { get; init; } = "Since this morning";

    public string SinceThisAfternoon { get; init; } = "Since this afternoon";

    public string SinceThisEvening { get; init; } = "Since this evening";

    public string SinceYesterday { get; init; } = "Since yesterday";

    /// <summary>"Since {0}": a weekday or a date</summary>
    public string SinceDay { get; init; } = "Since {0}";

    public string Checked { get; init; } = "Checked {0}";

    public string NotChecked { get; init; } = "Not checked yet";

    public string CheckingStarted { get; init; } = "Checking…";

    public string CheckingProgress { get; init; } = "Checking… {0} of {1}";

    public string PausedUntil { get; init; } = "Paused until {0}";

    public string Paused { get; init; } = "Paused";

    public string Separator { get; init; } = " · ";

    /// <summary>"Oct 12", or the short date in another year</summary>
    public string Date(DateTimeOffset local, DateTimeOffset today)
    {
        var format = Culture.DateTimeFormat.MonthDayPattern.Replace("MMMM", "MMM", StringComparison.Ordinal);
        return local.Year == today.Year
            ? local.ToString(format, Culture)
            : local.ToString("d", Culture);
    }

    public static string DisplayVersion(string version)
    {
        var trimmed = version.Trim();
        return trimmed.Length > 1 && trimmed[0] is 'v' or 'V' && char.IsAsciiDigit(trimmed[1]) ? trimmed[1..] : trimmed;
    }

    /// <summary>A title that only repeats the version</summary>
    public static bool SameVersion(string title, string version) =>
        string.Equals(DisplayVersion(title), DisplayVersion(version), StringComparison.OrdinalIgnoreCase);
}
