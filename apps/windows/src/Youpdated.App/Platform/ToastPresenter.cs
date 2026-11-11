using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Xml.Linq;
using Microsoft.Windows.AppNotifications;
using Youpdated.Core.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Platform;

/// <summary>Toasts through the Windows App SDK</summary>
internal sealed class ToastPresenter : INotificationPresenter
{
    // Toast tags and groups limited to 64 chars
    private const int TagLength = 16;

    private readonly Version _appVersion;
    private readonly ConcurrentDictionary<string, (string Tag, string Group)> _shown = new();
    private bool _registered;

    public ToastPresenter(Version appVersion)
    {
        _appVersion = appVersion;
    }

    /// <summary>Handle clicks, then register</summary>
    public void Register(Action<NotificationAction, IReadOnlyList<string>> invoked)
    {
        _registered = Try("register", () =>
        {
            AppNotificationManager.Default.NotificationInvoked += (_, args) =>
            {
                if (Parse(args.Argument) is { } parsed)
                {
                    invoked(parsed.Action, parsed.Ids);
                }
            };
            AppNotificationManager.Default.Register();
        });
        AppLog.Write($"Notifications: registered {_registered}, supported {AppNotificationManager.IsSupported()}, as administrator {Environment.IsPrivilegedProcess}");
    }

    public void Show(NotificationRequest request)
    {
        if (!_registered || !AppNotificationManager.IsSupported())
        {
            AppLog.Write($"Notifications: not shown, registered {_registered}, supported {AppNotificationManager.IsSupported()}");
            return;
        }
        var ids = request.ItemIds;
        var tag = Tag(ids);
        var notification = new AppNotification(Payload(request))
        {
            Tag = tag,
            Group = request.Group,
        };
        if (!Try("show", () => AppNotificationManager.Default.Show(notification)))
        {
            return;
        }
        // Windows drops them without an error for an app running as administrator, as it does with UAC off
        AppLog.Write(Environment.IsPrivilegedProcess
            ? $"Notifications: sent for {ids.Count} item(s), but Windows doesn't show them to an app running as administrator"
            : $"Notifications: shown for {ids.Count} item(s)");
        foreach (var id in ids)
        {
            _shown[id] = (tag, request.Group);
        }
    }

    public void Remove(IReadOnlyCollection<string> itemIds)
    {
        if (!_registered)
        {
            return;
        }
        var toasts = itemIds
            .Select(id => _shown.TryRemove(id, out var toast) ? toast : default((string, string)?))
            .OfType<(string Tag, string Group)>()
            .Distinct();
        foreach (var (tag, group) in toasts)
        {
            Try("remove", () => _ = AppNotificationManager.Default.RemoveByTagAndGroupAsync(tag, group));
        }
    }

    public void Unregister()
    {
        if (_registered)
        {
            Try("unregister", () => AppNotificationManager.Default.UnregisterAll());
            _registered = false;
        }
    }

    private static bool Try(string what, Action action)
    {
        try
        {
            action();
            return true;
        }
        catch (Exception exc)
        {
            AppLog.Write($"Notifications, {what}: {exc.GetType().Name} 0x{exc.HResult:X8} {exc.Message}");
            return false;
        }
    }

    /// <summary>A click or button's arguments: <c>action=open&amp;ids=a,b</c></summary>
    public static (NotificationAction Action, IReadOnlyList<string> Ids)? Parse(string argument)
    {
        NotificationAction? action = null;
        IReadOnlyList<string> ids = [];
        foreach (var pair in argument.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var at = pair.IndexOf('=');
            if (at < 0)
            {
                continue;
            }
            var value = Uri.UnescapeDataString(pair[(at + 1)..]);
            switch (pair[..at])
            {
                case "action":
                    action = value switch
                    {
                        "open" => NotificationAction.Open,
                        "markread" => NotificationAction.MarkRead,
                        "snooze" => NotificationAction.Snooze,
                        "download" => NotificationAction.Download,
                        _ => null,
                    };
                    break;
                case "ids":
                    ids = value.Split(',', StringSplitOptions.RemoveEmptyEntries);
                    break;
            }
        }
        return action is { } found ? (found, ids) : null;
    }

    private string Payload(NotificationRequest request)
    {
        var ids = Uri.EscapeDataString(string.Join(',', request.ItemIds));
        string Arguments(string action) => $"action={action}&ids={ids}";

        var (title, lines) = Describe(request);
        var update = request.IsAppUpdate ? AppUpdate.Newest(request.Items, _appVersion) : null;
        var newest = request.Items.Max(item => item.When);

        var binding = new XElement("binding", new XAttribute("template", "ToastGeneric"),
            new XElement("text", title));
        foreach (var line in lines)
        {
            binding.Add(new XElement("text", line));
        }

        var actions = new XElement("actions");
        if (update is not null)
        {
            actions.Add(Button(Text.Get("ToastDownload"), Arguments("download"), foreground: false));
        }
        else
        {
            actions.Add(Button(Text.Get("ToastOpen"), Arguments("open"), foreground: true));
        }
        actions.Add(Button(Text.Get("ToastMarkRead"), Arguments("markread"), foreground: false));
        actions.Add(Button(Text.Get("ToastSnooze"), Arguments("snooze"), foreground: false));

        var toast = new XElement("toast",
            new XAttribute("launch", Arguments("open")),
            new XAttribute("displayTimestamp", newest.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ssZ")),
            new XElement("header",
                new XAttribute("id", request.Group),
                new XAttribute("title", request.Group == NotificationRequest.MixedGroup
                    ? Text.Source(request.Group)
                    : Text.Label(request.Items[0].Source, request.Items[0].Target)),
                new XAttribute("arguments", "action=open")),
            new XElement("visual", binding),
            actions);
        return toast.ToString(SaveOptions.DisableFormatting);
    }

    private (string Title, IReadOnlyList<string> Lines) Describe(NotificationRequest request)
    {
        var items = request.Items.OrderByDescending(item => item.When).ToList();
        if (request.IsAppUpdate && AppUpdate.Newest(items, _appVersion) is { } update)
        {
            return (Text.UpdateAvailable(update), []);
        }
        if (request.Target is { } target)
        {
            var body = items.Count == 1 ? items[0].Title : Text.Format("ToastAndMore", items[0].Title, items.Count - 1);
            return (target.Display, [body]);
        }
        // Combined: up to two lines, and how many more
        var lines = items.Take(2).Select(item => Text.Format("ToastLine", item.Display, item.Title)).ToList();
        if (items.Count > 2)
        {
            lines[^1] += " " + Text.Format("ToastMore", items.Count - 2);
        }
        return (Text.NewUpdates(items.Count), lines);
    }

    private static XElement Button(string content, string arguments, bool foreground) =>
        new("action",
            new XAttribute("content", content),
            new XAttribute("arguments", arguments),
            new XAttribute("activationType", foreground ? "foreground" : "background"));

    private static string Tag(IEnumerable<string> ids)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(',', ids.Order(StringComparer.Ordinal))));
        return Convert.ToHexString(hash)[..TagLength];
    }
}
