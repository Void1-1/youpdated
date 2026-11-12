using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;
using Youpdated.Core.Scheduling;

namespace Youpdated.Core.ViewModels;

public enum TrayState
{
    Idle,
    Checking,
    Locked,
    Paused,
    Problems,
}

public enum AppPage
{
    Updates,
    Following,
    Problems,
    Settings,
}

public enum WindowState
{
    Starting,
    Locked,
    Welcome,
    Feed,
}

/// <summary>What view models need</summary>
public sealed record AppServices(
    IUiDispatcher Ui,
    TimeProvider Time,
    ICredentialStore Credentials,
    IStartupRegistration Startup,
    IUrlSchemeRegistration UrlScheme,
    INotificationPresenter Notifications,
    IShell Shell,
    ILocalStateStore LocalState,
    IDataRemover DataRemover,
    HttpClient Http,
    Version AppVersion);

public sealed partial class AppViewModel : ObservableObject, IAsyncDisposable
{
    /// <summary>Snooze on a notification</summary>
    public const int SnoozeMinutes = 60;

    /// <summary>Updates listed in the tray menu</summary>
    public const int TrayItems = 3;

    private readonly EngineHost _host;
    private readonly AppServices _services;
    private readonly EngineClient _engine;
    private readonly Dictionary<string, ITimer> _snoozes = [];
    private readonly TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private FileSystemWatcher? _configWatcher;
    private ITimer? _configSettle;
    private string? _passphrase;
    private bool _syncing;

    public AppViewModel(EngineHost host, AppServices services)
    {
        _host = host;
        _services = services;
        _engine = new EngineClient(host);
        Scheduler = new CheckScheduler(services.Time, services.Ui, services.LocalState, ScheduledCheckAsync, DigestAsync);
        Inbox = new InboxViewModel(_engine, services.Shell, services.Notifications);
        Sources = new SourcesViewModel(_engine, services.Time, services.Ui);
        Feed = new FeedViewModel(Inbox, services.Shell, services.Time, services.Ui, services.AppVersion);
        Following = new FollowingViewModel(Sources, services.AppVersion);
        Icons = new IconCache(_engine);
        Problems = new ProblemsViewModel(_engine);
        Settings = new SettingsViewModel(
            _engine, services.Startup, services.Credentials, () => _passphrase, services.AppVersion, services.LocalState);
        Unlock = new UnlockViewModel(UnlockAsync);
        Downloader = new UpdateDownloader(services.Http);

        _host.Started += hello => _services.Ui.Post(() => _ = OnEngineStartedAsync(hello));
        _host.Crashed += problem => _services.Ui.Post(() => OnEngineCrashed(problem));
        _host.Progress += progress => _services.Ui.Post(() => OnProgress(progress));
        _host.IconArrived += target => _services.Ui.Post(() => Icons.Arrived(target));

        Feed.View = services.LocalState.Load().View ?? ViewOptions.Defaults;
        Feed.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(FeedViewModel.View))
            {
                _services.LocalState.Save(_services.LocalState.Load() with { View = Feed.View });
            }
        };
        Inbox.UnreadChanged += _ => UpdateTray();
        Inbox.PriorityChanged += () => _ = Sources.RefreshAsync();
        Inbox.ShowRequested += () => ShowRequested?.Invoke(AppPage.Updates);
        Inbox.ItemsChanged += _ => UpdateWindowState();
        Sources.Added += targets => _ = AddedAsync(targets);
        Sources.Removed += () => _ = Inbox.RefreshAsync();
        Following.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(FollowingViewModel.NothingFollowed))
            {
                UpdateWindowState();
            }
        };
        Following.ShowInUpdates += target =>
        {
            Feed.TargetFilter = new TargetRef(target.Source, target.Key, target.Display);
            ShowRequested?.Invoke(AppPage.Updates);
        };
        Settings.Changed += settings =>
        {
            Scheduler.Configure(TimeSpan.FromHours(settings.CheckIntervalHours), settings.Digest);
            Icons.Configure(settings.ShowIcons);
        };
        Scheduler.Changed += () => _services.Ui.Post(() =>
        {
            Feed.LastCheck = Scheduler.LastCheck;
            UpdateTray();
        });
        Problems.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(ProblemsViewModel.Problems))
            {
                UpdateTray();
            }
        };
    }

    /// <summary>The passphrase is needed -> show the unlock prompt</summary>
    public event Action? UnlockNeeded;

    /// <summary>An existing config without Youpdated's entry</summary>
    public event Action? SelfWatchOfferNeeded;

    /// <summary>A scheduled CLI check is also running</summary>
    public event Action? CliNoticeNeeded;

    /// <summary>First launch: offer "Add to Start menu"</summary>
    public event Action? StartMenuOfferNeeded;

    /// <summary>Bring the window up on a page</summary>
    public event Action<AppPage>? ShowRequested;

    /// <summary>Open Add flyout, filled in with a value when there is one: paste, dropped link, <c>youpdated://add</c>, Jump List</summary>
    public event Action<string?>? AddRequested;

    public InboxViewModel Inbox { get; }

    /// <summary>Updates view</summary>
    public FeedViewModel Feed { get; }

    public FollowingViewModel Following { get; }

    public IconCache Icons { get; }

    public SourcesViewModel Sources { get; }

    public ProblemsViewModel Problems { get; }

    public SettingsViewModel Settings { get; }

    public UnlockViewModel Unlock { get; }

    public CheckScheduler Scheduler { get; }

    public UpdateDownloader Downloader { get; }

    public EngineHost Host => _host;

    public Task Ready => _ready.Task;

    [ObservableProperty]
    public partial TrayState TrayState { get; private set; } = TrayState.Idle;

    [ObservableProperty]
    public partial int Unread { get; private set; }

    [ObservableProperty]
    public partial bool IsLocked { get; private set; }

    /// <summary>Unlocked, synced: pages have something to show</summary>
    [ObservableProperty]
    public partial bool IsReady { get; private set; }

    [ObservableProperty]
    public partial bool IsChecking { get; private set; }

    [ObservableProperty]
    public partial int ProgressDone { get; private set; }

    [ObservableProperty]
    public partial int ProgressOf { get; private set; }

    /// <summary>The newest unread updates</summary>
    [ObservableProperty]
    public partial IReadOnlyList<InboxItem> RecentUnread { get; private set; } = [];

    /// <summary>A newer Youpdated app version</summary>
    [ObservableProperty]
    public partial AppUpdate? AvailableUpdate { get; private set; }

    [ObservableProperty]
    public partial StatusReply? Status { get; private set; }

    [ObservableProperty]
    public partial WindowState WindowState { get; private set; } = WindowState.Starting;

    /// <summary>Checks are paused until then (MaxValue: until resumed)</summary>
    [ObservableProperty]
    public partial DateTimeOffset? PausedUntil { get; private set; }

    /// <summary>"Checks are also running from the command line" notice</summary>
    [ObservableProperty]
    public partial bool ShowCliNotice { get; private set; }

    /// <summary>Launch: register the link handler and start the engine</summary>
    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        _services.UrlScheme.Register();
        await _host.StartAsync(cancellationToken);
    }

    /// <summary>"Check now" in tray and window</summary>
    [RelayCommand]
    private Task CheckNow() => CheckAsync();

    /// <summary>Check everything, or only <paramref name="targets"/> after adding them</summary>
    public async Task<bool> CheckAsync(IReadOnlyList<TargetKey>? targets = null, IReadOnlyList<string>? sources = null)
    {
        if (IsChecking && targets is null && sources is null)
        {
            return true;
        }
        IsChecking = true;
        ProgressDone = 0;
        ProgressOf = 0;
        Feed.Checking = new CheckProgress(0, 0);
        UpdateTray();
        try
        {
            var reply = await _engine.CheckAsync(targets, sources: sources);
            if (targets is null && sources is null)
            {
                Scheduler.Checked(reply.CheckedAt);
            }
            Problems.ClearEngineProblems();
            PostAlerts(reply.Alerts, reply.Items);
            await RefreshAsync();
            return true;
        }
        catch (EngineException exc) when (exc.Is(ErrorCodes.Busy))
        {
            // Already running; result will show
            return true;
        }
        catch (EngineException exc) when (exc.Is(ErrorCodes.Locked))
        {
            Locked();
            return false;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            return false;
        }
        finally
        {
            IsChecking = false;
            Feed.Checking = null;
            UpdateTray();
        }
    }

    [RelayCommand]
    public void PauseForHour() => Scheduler.PauseFor(TimeSpan.FromHours(1));

    [RelayCommand]
    public void PauseUntilTomorrow() => Scheduler.PauseUntilTomorrow();

    [RelayCommand]
    public void PauseUntilResumed() => Scheduler.PauseUntilResumed();

    [RelayCommand]
    public void Resume() => Scheduler.Resume();

    public void SystemResumed() => Scheduler.SystemResumed();

    /// <summary>A notification or its buttons</summary>
    public async Task HandleNotificationAsync(NotificationAction action, IReadOnlyList<string> ids)
    {
        switch (action)
        {
            case NotificationAction.Open:
                if (ids.Count == 0)
                {
                    ShowRequested?.Invoke(AppPage.Updates);
                    return;
                }
                await Inbox.OpenItemsAsync(ids);
                break;
            case NotificationAction.MarkRead:
                await Inbox.MarkAsync(ids, read: true);
                break;
            case NotificationAction.Snooze:
                await SnoozeAsync(ids);
                break;
            case NotificationAction.Download:
                var update = Inbox.Items.Where(item => ids.Contains(item.Id))
                    .Select(item => AppUpdate.From(item, _services.AppVersion))
                    .OfType<AppUpdate>()
                    .MaxBy(found => found.Version);
                if (update is not null)
                {
                    await DownloadUpdateAsync(update);
                }
                break;
        }
        UpdateTray();
    }

    /// <summary><c>youpdated://</c> links, Jump List, second launch</summary>
    public async Task HandleLinkAsync(YoupdatedLink link)
    {
        switch (link)
        {
            case YoupdatedLink.Add add:
                await AddAsync(add.Value);
                break;
            case YoupdatedLink.Open:
                ShowRequested?.Invoke(AppPage.Updates);
                break;
            case YoupdatedLink.Check:
                await CheckAsync();
                break;
        }
    }

    /// <summary>Open Add, previewing <paramref name="value"/></summary>
    public async Task AddAsync(string? value = null)
    {
        ShowRequested?.Invoke(AppPage.Updates);
        AddRequested?.Invoke(value);
        if (!string.IsNullOrWhiteSpace(value))
        {
            await Sources.Add.StartAsync(value.Trim());
        }
        else
        {
            Sources.Add.Reset();
        }
    }

    public async Task<bool> PasteAsync(string? clipboard)
    {
        if (PasteToAdd.Accept(clipboard) is not { } value)
        {
            return false;
        }
        await AddAsync(value);
        return true;
    }

    public static bool CanRetry(Problem problem) => problem.Kind != Problem.NotifyKind;

    /// <summary>Retry: check that target, by the problem key</summary>
    public async Task<bool> RetryAsync(Problem problem)
    {
        switch (problem.Kind)
        {
            case Problem.EngineKind:
                await _host.StartAsync();
                return _host.State == EngineState.Running;
            case Problem.NotifyKind:
                return false;
        }
        return problem.Key is { } key
            ? await CheckAsync([new TargetKey(problem.Source, key)])
            : await CheckAsync(sources: [problem.Source]);
    }

    public void ShowInFollowing(TargetKey target)
    {
        Following.Filter = Sources.Targets.FirstOrDefault(t => t.AsKey() == target)?.Display ?? target.Key;
        ShowRequested?.Invoke(AppPage.Following);
    }

    /// <summary>CLI bar dismissed</summary>
    public async Task DismissCliNoticeAsync()
    {
        ShowCliNotice = false;
        await CliNoticeShownAsync();
    }

    /// <summary>"Watch Youpdated for app updates?" -> Add or Not now</summary>
    public async Task AnswerSelfWatchOfferAsync(bool watch)
    {
        try
        {
            if (watch)
            {
                await _engine.ConfigInitAsync(selfWatch: true);
                await Sources.RefreshAsync();
            }
            else
            {
                var reply = await _engine.SettingsSetAsync(new SettingsPatch().SelfWatchDeclined(true));
                Settings.Apply(reply.Settings);
            }
            Status = await _engine.StatusAsync();
            Settings.SetStatus(Status);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // Ask again next launch
        }
    }

    /// <summary>The CLI notice was shown</summary>
    public async Task CliNoticeShownAsync()
    {
        try
        {
            var reply = await _engine.SettingsSetAsync(new SettingsPatch().CliNoticeShown(true));
            Settings.Apply(reply.Settings);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // Shown again next launch
        }
    }

    public void StartMenuOffered()
    {
        _services.LocalState.Save(_services.LocalState.Load() with { StartMenuOffered = true });
    }

    /// <summary>Download btn: fetch the .zip into Downloads and show in Explorer</summary>
    public async Task<string> DownloadUpdateAsync(AppUpdate update, IProgress<double>? progress = null, CancellationToken cancellationToken = default)
    {
        var path = await Downloader.DownloadAsync(update, _services.Shell.DownloadsFolder, progress, cancellationToken);
        _services.Shell.Reveal(path);
        await Inbox.MarkAsync([update.ItemId], read: true);
        return path;
    }

    /// <summary>Run key, <c>youpdated://</c>, notification registration, stored passphrase, and optionally the config and history</summary>
    public async Task<bool> RemoveFromThisPcAsync(bool alsoData)
    {
        Scheduler.Stop();
        _services.Startup.Disable();
        _services.UrlScheme.Unregister();
        _services.Notifications.Unregister();
        _services.Credentials.Delete();
        _services.LocalState.Delete();
        await _host.StopAsync();
        return !alsoData || await _services.DataRemover.RemoveDataAsync();
    }

    /// <summary>The config changed on disk, engine re-reads on every op: the pages need reloading</summary>
    public async Task ConfigChangedAsync()
    {
        if (!IsReady)
        {
            return;
        }
        try
        {
            Status = await _engine.StatusAsync();
            Settings.SetStatus(Status);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            return;
        }
        await Settings.LoadPrivacyAsync();
        await RefreshAsync();
    }

    /// <summary>Quit: checks stop while closed</summary>
    public async ValueTask DisposeAsync()
    {
        _configWatcher?.Dispose();
        _configSettle?.Dispose();
        Scheduler.Dispose();
        foreach (var timer in _snoozes.Values)
        {
            timer.Dispose();
        }
        _snoozes.Clear();
        await _host.StopAsync();
    }

    /// <summary>Reload the pages</summary>
    public async Task RefreshAsync()
    {
        await Inbox.RefreshAsync();
        await Sources.RefreshAsync();
        await Problems.RefreshAsync();
        ScheduleSnoozes();
        UpdateTray();
    }

    private async Task OnEngineStartedAsync(HelloReply hello)
    {
        Settings.SetEngine(hello);
        await SyncAsync();
    }

    private async Task SyncAsync()
    {
        if (_syncing)
        {
            return;
        }
        _syncing = true;
        try
        {
            var status = await _engine.StatusAsync();
            if (!status.ConfigExists)
            {
                await _engine.ConfigInitAsync(selfWatch: true);
                status = await _engine.StatusAsync();
            }
            Status = status;
            Settings.SetStatus(status);

            if (!status.Locked)
            {
                await UnlockedAsync(status);
            }
            else if (await TryStoredPassphraseAsync())
            {
                await UnlockedAsync();
            }
            else
            {
                Locked();
            }
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // A crash restarts the engine, which syncs again
        }
        finally
        {
            _syncing = false;
        }
    }

    private async Task<bool> TryStoredPassphraseAsync()
    {
        foreach (var (passphrase, stored) in new[] { (_passphrase, false), (_services.Credentials.Read(), true) })
        {
            if (passphrase is null)
            {
                continue;
            }
            try
            {
                await _engine.UnlockAsync(passphrase);
                _passphrase = passphrase;
                return true;
            }
            catch (EngineException exc) when (exc.Is(ErrorCodes.WrongPassphrase))
            {
                if (stored)
                {
                    // Changed from the command line; ask again
                    _services.Credentials.Delete();
                }
            }
        }
        return false;
    }

    /// <summary>The unlock prompt: null when unlocked, otherwise why not</summary>
    private async Task<string?> UnlockAsync(string passphrase, bool remember)
    {
        try
        {
            await _engine.UnlockAsync(passphrase);
        }
        catch (EngineException exc)
        {
            return exc.Message;
        }
        catch (Exception exc) when (exc is EngineUnavailableException or EngineExitedException)
        {
            return exc.Message;
        }
        _passphrase = passphrase;
        if (remember)
        {
            _services.Credentials.Write(passphrase);
            Settings.Remembered();
        }
        await UnlockedAsync();
        return null;
    }

    /// <param name="status">The status already read, when it was read unlocked</param>
    private async Task UnlockedAsync(StatusReply? status = null)
    {
        IsLocked = false;
        status ??= await _engine.StatusAsync();
        Status = status;
        Settings.SetStatus(status);
        WatchConfig(status.ConfigPath);
        await Settings.LoadAsync();
        await Settings.LoadPrivacyAsync();
        Scheduler.LastRunWas(status.LastRun);

        var local = _services.LocalState.Load();
        if (!local.SetUp)
        {
            // Start at login defaults on
            _services.Startup.Enable();
            Settings.StartAtLogin = _services.Startup.IsEnabled;
            _services.LocalState.Save(local with { SetUp = true });
        }
        if (!local.StartMenuOffered)
        {
            StartMenuOfferNeeded?.Invoke();
        }
        if (status.SelfWatch == SelfWatch.Absent && !Settings.Current.SelfWatchDeclined && !Settings.HideSelfWatchSuggestions)
        {
            SelfWatchOfferNeeded?.Invoke();
        }
        if (status.LastRunBy == RunBy.Cli && !Settings.Current.CliNoticeShown)
        {
            ShowCliNotice = true;
            CliNoticeNeeded?.Invoke();
        }

        await RefreshAsync();
        IsReady = true;
        UpdateWindowState();
        Scheduler.Start();
        // Last, so anything waiting on Ready sees the finished window
        _ready.TrySetResult();
    }

    private void WatchConfig(string? path)
    {
        if (path is null || _configWatcher?.Filter == Path.GetFileName(path) && _configWatcher.Path == Path.GetDirectoryName(path))
        {
            return;
        }
        _configWatcher?.Dispose();
        _configWatcher = null;
        if (Path.GetDirectoryName(path) is not { } folder || !Directory.Exists(folder))
        {
            return;
        }
        var watcher = new FileSystemWatcher(folder, Path.GetFileName(path))
        {
            NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size,
        };
        watcher.Changed += OnConfigFileChanged;
        watcher.Created += OnConfigFileChanged;
        watcher.Renamed += OnConfigFileChanged;
        watcher.EnableRaisingEvents = true;
        _configWatcher = watcher;
    }

    /// <summary>Edits are several events in a row, wait</summary>
    private void OnConfigFileChanged(object sender, FileSystemEventArgs args)
    {
        _configSettle?.Dispose();
        _configSettle = _services.Time.CreateTimer(
            _ => _services.Ui.Post(() => _ = ConfigChangedAsync()), null, TimeSpan.FromMilliseconds(500), Timeout.InfiniteTimeSpan);
    }

    private void Locked()
    {
        IsLocked = true;
        IsReady = false;
        Scheduler.Stop();
        UpdateTray();
        UpdateWindowState();
        UnlockNeeded?.Invoke();
    }

    private void OnEngineCrashed(Problem problem)
    {
        Scheduler.Stop();
        Problems.AddEngineProblem(problem);
        UpdateTray();
    }

    private void OnProgress(ProgressEvent progress)
    {
        ProgressDone = progress.Done;
        ProgressOf = progress.Of;
        if (IsChecking)
        {
            Feed.Checking = new CheckProgress(progress.Done, progress.Of);
        }
    }

    private async Task AddedAsync(IReadOnlyList<TargetRef> targets)
    {
        await Sources.RefreshAsync();
        if (targets.Count > 0)
        {
            // A new target's first check records a baseline: its newest item shows, already read
            await CheckAsync([.. targets.Select(target => target.AsKey())]);
        }
    }

    private Task<bool> ScheduledCheckAsync() => CheckAsync();

    private async Task<bool> DigestAsync()
    {
        try
        {
            var reply = await _engine.DigestSendAsync();
            PostAlerts(reply.Alerts, reply.Items);
            await Problems.RefreshAsync();
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            return false;
        }
    }

    private void PostAlerts(IReadOnlyList<Alert> alerts, IReadOnlyList<InboxItem> items)
    {
        var known = items.ToDictionary(item => item.Id);
        foreach (var alert in alerts)
        {
            if (NotificationRequest.FromAlert(alert, known) is { } request)
            {
                _services.Notifications.Show(request);
            }
        }
    }

    private async Task SnoozeAsync(IReadOnlyList<string> ids)
    {
        try
        {
            await _engine.InboxSnoozeAsync(ids, SnoozeMinutes);
            await Inbox.RefreshAsync();
            ScheduleSnoozes();
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // The notification is dismissed; the item unread in inbox
        }
    }

    /// <summary>Show snoozed items after timer ends</summary>
    private void ScheduleSnoozes()
    {
        var now = _services.Time.GetUtcNow();
        var due = Inbox.Items
            .Where(item => !item.Read && item.SnoozedUntil is { } until && until > now)
            .GroupBy(item => item.SnoozedUntil!.Value);
        foreach (var group in due)
        {
            var key = $"{group.Key:O}|{string.Join(',', group.Select(item => item.Id).Order(StringComparer.Ordinal))}";
            if (_snoozes.ContainsKey(key))
            {
                continue;
            }
            var ids = group.Select(item => item.Id).ToHashSet();
            _snoozes[key] = _services.Time.CreateTimer(
                _ => _services.Ui.Post(() => SnoozeEnded(key, ids)), null, group.Key - now, Timeout.InfiniteTimeSpan);
        }
    }

    private void SnoozeEnded(string key, HashSet<string> ids)
    {
        if (_snoozes.Remove(key, out var timer))
        {
            timer.Dispose();
        }
        var items = Inbox.Items.Where(item => ids.Contains(item.Id) && !item.Read).ToList();
        if (items.Count == 0 || !Settings.OsNotifications)
        {
            return;
        }
        var target = items.Select(item => item.TargetKey).Distinct().Count() == 1
            ? new TargetRef(items[0].Source, items[0].Target, items[0].Display)
            : null;
        _services.Notifications.Show(new NotificationRequest(
            target is null ? AlertPriority.Normal : AlertPriority.High, target, items, NotificationRequest.GroupOf(items)));
    }

    private void UpdateWindowState()
    {
        WindowState = IsLocked ? WindowState.Locked
            : !IsReady ? WindowState.Starting
            : Following.NothingFollowed && Feed.SelfUpdate is null ? WindowState.Welcome
            : WindowState.Feed;
    }

    private void UpdateTray()
    {
        PausedUntil = Scheduler.PausedUntil;
        Feed.PausedUntil = PausedUntil;
        Unread = Inbox.Unread;
        RecentUnread = Inbox.NewestUnread(TrayItems);
        AvailableUpdate = AppUpdate.Newest(Inbox.Items, _services.AppVersion);
        TrayState = IsLocked ? TrayState.Locked
            : IsChecking ? TrayState.Checking
            : Scheduler.IsPaused ? TrayState.Paused
            : Problems.Any ? TrayState.Problems
            : TrayState.Idle;
    }
}
