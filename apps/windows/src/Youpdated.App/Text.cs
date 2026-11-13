using System.Globalization;
using Microsoft.Windows.ApplicationModel.Resources;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

/// <summary>User-facing strings from Strings\en-US\Resources.resw, and how values read</summary>
internal static class Text
{
    private static readonly ResourceLoader Loader = new();

    public static string Get(string key) => Loader.GetString(key) is { Length: > 0 } text ? text : key;

    public static string Format(string key, params object?[] args) =>
        string.Format(CultureInfo.CurrentCulture, Get(key), args);

    /// <summary>"3 new updates", "1 new update"</summary>
    public static string NewUpdates(int count) =>
        count == 1 ? Get("NewUpdatesOne") : Format("NewUpdatesMany", count);

    /// <summary>"Youpdated 1.1 is available"</summary>
    public static string UpdateAvailable(AppUpdate update) => Format("UpdateAvailable", update.DisplayVersion);

    /// <summary>How long ago: "Just now", "5 min ago", "Yesterday", "Oct 12"</summary>
    public static string Ago(DateTimeOffset when, DateTimeOffset now)
    {
        var age = now - when;
        if (age < TimeSpan.FromMinutes(1))
        {
            return Get("JustNow");
        }
        if (age < TimeSpan.FromHours(1))
        {
            return Format("MinutesAgo", (int)age.TotalMinutes);
        }
        if (age < TimeSpan.FromDays(1))
        {
            return Format("HoursAgo", (int)age.TotalHours);
        }
        if (age < TimeSpan.FromDays(2))
        {
            return Get("Yesterday");
        }
        return ShortDate(when);
    }

    public static string Ago(DateTimeOffset when) => Ago(when, DateTimeOffset.UtcNow);

    /// <summary>Card's time, as in View → Times: "2 hr ago", or "Oct 28, 14:02"</summary>
    public static string When(DateTimeOffset when, TimeStyle style) =>
        style == TimeStyle.Absolute ? DateAndTime(when) : Ago(when);

    /// <summary>Full date and time, for expanded cards</summary>
    public static string DateAndTime(DateTimeOffset when) =>
        $"{ShortDate(when)}, {when.ToLocalTime().ToString("t", CultureInfo.CurrentCulture)}";

    /// <summary>"Oct 12"</summary>
    public static string ShortDate(DateTimeOffset when)
    {
        var pattern = CultureInfo.CurrentCulture.DateTimeFormat.MonthDayPattern.Replace("MMMM", "MMM", StringComparison.Ordinal);
        return when.ToLocalTime().ToString(pattern, CultureInfo.CurrentCulture);
    }

    /// <summary>Priority names: High, Normal, Low</summary>
    public static string Priority(Priority priority) => Get(priority switch
    {
        Core.Contract.Priority.High => "PriorityHigh",
        Core.Contract.Priority.Low => "PriorityLow",
        _ => "PriorityNormal",
    });

    /// <summary>What it does to notifications: "Notify immediately", "Notify", "Inbox only"</summary>
    public static string PriorityMeans(Priority priority) => Get(priority switch
    {
        Core.Contract.Priority.High => "PriorityHighMeans",
        Core.Contract.Priority.Low => "PriorityLowMeans",
        _ => "PriorityNormalMeans",
    });

    /// <summary>The name for a source or forge</summary>
    public static string Label(string source, string? key) => SourceLabels.Label(source, key, Get("SourceBrowsers"));

    /// <summary>A source's label for no target</summary>
    public static string Source(string source) =>
        source == Core.Platform.NotificationRequest.MixedGroup ? Get("SourceMixed") : Label(source, null);

    /// <summary>"4 of the last 20 versions fit: 1.2.0, 1.1.0"</summary>
    public static string Fits(FiltersViewModel filters)
    {
        if (filters.Preview is not { } preview)
        {
            return filters.LoadError is null ? Get("FiltersListing") : "";
        }
        if (preview.FetchError is { } error)
        {
            return Format("FiltersFetchFailed", error);
        }
        if (filters.LimitsNothing)
        {
            return Get("FiltersLimitsNothing");
        }
        var total = preview.Versions.Count;
        if (total == 0)
        {
            return Get("FiltersNoVersions");
        }
        if (preview.Kept == 0)
        {
            return Format("FiltersFitNone", total);
        }
        if (preview.Kept == total)
        {
            return Format("FiltersFitAll", total);
        }
        var names = string.Join(", ", preview.Versions.Where(version => version.Kept).Take(3).Select(version => version.Version));
        return Format("FiltersFitSome", preview.Kept, total, names);
    }

    public static string Interval(int hours) => hours == 1 ? Get("EveryHour") : Format("EveryHours", hours);
}
