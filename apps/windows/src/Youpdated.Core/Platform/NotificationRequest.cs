using Youpdated.Core.Contract;

namespace Youpdated.Core.Platform;

/// <summary>An OS notification, from an engine <see cref="Alert"/> or after a snooze</summary>
public sealed record NotificationRequest(
    AlertPriority Priority,
    TargetRef? Target,
    IReadOnlyList<InboxItem> Items,
    string Group)
{
    /// <summary>The group of a notification whose items span sources</summary>
    public const string MixedGroup = "updates";

    public IReadOnlyList<string> ItemIds => [.. Items.Select(item => item.Id)];

    /// <summary>About Youpdated: "Youpdated 1.1 is available"</summary>
    public bool IsAppUpdate => Items.Count > 0 && Items.All(item => item.Starred);

    /// <summary>The notification for <paramref name="alert"/>, with its items looked up in<paramref name="known"/></summary>
    public static NotificationRequest? FromAlert(Alert alert, IReadOnlyDictionary<string, InboxItem> known)
    {
        var items = alert.ItemIds.Select(id => known.GetValueOrDefault(id)).OfType<InboxItem>().ToList();
        if (items.Count == 0)
        {
            return null;
        }
        return new NotificationRequest(alert.Priority, alert.Target, items, GroupOf(items));
    }

    /// <summary>Notifications grouped by source; a mix of sources gets a shared group</summary>
    public static string GroupOf(IReadOnlyList<InboxItem> items)
    {
        var sources = items.Select(item => item.Source).Distinct().ToList();
        return sources.Count == 1 ? sources[0] : MixedGroup;
    }
}

/// <summary>A button on a notification, or the notification itself being clicked</summary>
public enum NotificationAction
{
    Open,
    MarkRead,
    Snooze,
    /// <summary>On "Youpdated 1.1 is available": fetch this platform's file</summary>
    Download,
}
