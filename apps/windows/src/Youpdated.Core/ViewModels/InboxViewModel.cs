using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;

namespace Youpdated.Core.ViewModels;

/// <summary>Inbox items and actions: reading, marking, priority, snoozing. The feed (<see cref="FeedViewModel"/>) decides how to display</summary>
public sealed partial class InboxViewModel : ObservableObject
{
    private readonly EngineClient _engine;
    private readonly IShell _shell;
    private readonly INotificationPresenter _notifications;
    private List<InboxItem> _items = [];

    public InboxViewModel(EngineClient engine, IShell shell, INotificationPresenter notifications)
    {
        _engine = engine;
        _shell = shell;
        _notifications = notifications;
    }

    public event Action<int>? UnreadChanged;

    public event Action? PriorityChanged;

    public event Action? ShowRequested;

    /// <summary>The items changed: <c>true</c> when reloaded from the engine, <c>false</c> for a local change</summary>
    public event Action<bool>? ItemsChanged;

    public IReadOnlyList<InboxItem> Items => _items;

    /// <summary>Sources with items, for ... → Filter by source</summary>
    [ObservableProperty]
    public partial IReadOnlyList<string> Sources { get; private set; } = [];

    [ObservableProperty]
    public partial int Unread { get; private set; }

    [ObservableProperty]
    public partial bool IsLoading { get; private set; }

    [ObservableProperty]
    public partial string? Error { get; private set; }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        IsLoading = true;
        try
        {
            var (items, unread) = await _engine.InboxListAllAsync(new InboxQuery(), cancellationToken: cancellationToken);
            _items = items;
            Error = null;
            SetUnread(unread);
            Changed(refreshed: true);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task OpenItemAsync(InboxItem item)
    {
        _shell.OpenUrl(item.Url);
        await MarkAsync([item.Id], read: true);
    }

    /// <summary>Open a notification's details</summary>
    public async Task OpenItemsAsync(IReadOnlyList<string> ids)
    {
        var items = _items.Where(item => ids.Contains(item.Id)).ToList();
        if (items.Count == 1)
        {
            await OpenItemAsync(items[0]);
            return;
        }
        ShowRequested?.Invoke();
        if (items.Count > 0)
        {
            await MarkAsync([.. items.Select(item => item.Id)], read: true);
        }
    }

    /// <summary>Mark all read, or everything from <paramref name="source"/></summary>
    public async Task<bool> MarkAllReadAsync(string? source = null)
    {
        try
        {
            var reply = await _engine.InboxMarkAllAsync(read: true, source);
            var changed = _items.Where(item => !item.Read && (source is null || item.Source == source)).ToList();
            _notifications.Remove([.. changed.Select(item => item.Id)]);
            _items = [.. _items.Select(item => source is null || item.Source == source ? item with { Read = true } : item)];
            SetUnread(reply.Unread);
            Changed(refreshed: false);
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    public async Task<bool> MarkAsync(IReadOnlyList<string> ids, bool read)
    {
        if (ids.Count == 0)
        {
            return true;
        }
        try
        {
            var reply = await _engine.InboxMarkAsync(ids, read);
            if (read)
            {
                _notifications.Remove(ids);
            }
            var marked = ids.ToHashSet();
            _items = [.. _items.Select(item => marked.Contains(item.Id) ? item with { Read = read } : item)];
            SetUnread(reply.Unread);
            Changed(refreshed: false);
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    /// <summary>Delete Update from the inbox</summary>
    public async Task<bool> DeleteAsync(IReadOnlyList<string> ids)
    {
        if (ids.Count == 0)
        {
            return true;
        }
        try
        {
            var reply = await _engine.InboxDeleteAsync(ids);
            _notifications.Remove(ids);
            var deleted = ids.ToHashSet();
            _items = [.. _items.Where(item => !deleted.Contains(item.Id))];
            SetUnread(reply.Unread);
            Changed(refreshed: false);
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    public async Task<bool> SetPriorityAsync(TargetKey target, Priority priority)
    {
        try
        {
            var reply = await _engine.PrioritySetAsync(target, priority);
            _items = [.. _items.Select(item => item.TargetKey == target ? item with { Priority = reply.Priority } : item)];
            Changed(refreshed: false);
            PriorityChanged?.Invoke();
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    public async Task<bool> SnoozeAsync(IReadOnlyList<string> ids, int minutes)
    {
        try
        {
            var reply = await _engine.InboxSnoozeAsync(ids, minutes);
            var snoozed = ids.ToHashSet();
            _items = [.. _items.Select(item => snoozed.Contains(item.Id) ? item with { SnoozedUntil = reply.Until } : item)];
            _notifications.Remove(ids);
            Changed(refreshed: false);
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    /// <summary>Newest unread items</summary>
    public IReadOnlyList<InboxItem> NewestUnread(int count) =>
        [.. _items.Where(item => !item.Read).OrderByDescending(item => item.When).Take(count)];

    private void SetUnread(int unread)
    {
        if (Unread != unread)
        {
            Unread = unread;
        }
        UnreadChanged?.Invoke(unread);
    }

    private void Changed(bool refreshed)
    {
        Sources = [.. _items.Select(item => item.Source).Distinct().Order(StringComparer.OrdinalIgnoreCase)];
        ItemsChanged?.Invoke(refreshed);
    }
}
