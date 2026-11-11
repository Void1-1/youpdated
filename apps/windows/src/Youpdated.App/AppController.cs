using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;
using Microsoft.Windows.AppNotifications;
using Youpdated.App.Platform;
using Youpdated.App.Tray;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

public interface IMainWindow
{
    event Action? Closed;

    /// <summary>Show and bring forward, on <paramref name="page"/></summary>
    void ShowPage(AppPage page);

    /// <summary>Locked: focus passphrase field</summary>
    void ShowUnlock();

    void ShowNotice(Notice notice);

    /// <summary>Open + flyout</summary>
    void OpenAdd();

    void Close();
}

public enum Notice
{
    /// <summary>"Watch Youpdated for app updates?"</summary>
    SelfWatch,

    /// <summary>"Add Youpdated to the Start menu?"</summary>
    StartMenu,
}

internal sealed class AppController
{
    private readonly Func<AppViewModel, IMainWindow> _createWindow;
    private readonly HashSet<Notice> _pending = [];
    private AppViewModel? _app;
    private TrayIcon? _tray;
    private ToastPresenter? _toasts;
    private PowerEvents? _power;
    private IMainWindow? _window;
    private DispatcherQueue? _queue;
    private bool _quitting;

    public AppController(Func<AppViewModel, IMainWindow> createWindow)
    {
        _createWindow = createWindow;
    }

    public static AppController? Current { get; private set; }

    public AppViewModel App => _app ?? throw new InvalidOperationException("not launched");

    public string Exe { get; } = Environment.ProcessPath ?? Path.Combine(AppContext.BaseDirectory, "Youpdated.exe");

    public static Version AppVersion =>
        typeof(AppController).Assembly.GetName().Version is { } version
            ? new Version(version.Major, version.Minor, Math.Max(version.Build, 0))
            : new Version(0, 9, 0);

    public async Task LaunchAsync(AppActivationArguments activation)
    {
        Current = this;
        // Closing the window keeps the app running
        Application.Current.DispatcherShutdownMode = DispatcherShutdownMode.OnExplicitShutdown;
        _queue = DispatcherQueue.GetForCurrentThread();
        var ui = new DispatcherQueueUi(_queue);
        var directory = AppContext.BaseDirectory;
        _toasts = new ToastPresenter(AppVersion);
        var startup = new StartupRegistration(Exe);
        var services = new AppServices(
            ui, TimeProvider.System, new CredentialStore(), startup, new UrlSchemeRegistration(Exe), _toasts,
            new Shell(), AppLog.State, new EngineDataRemover(directory), new HttpClient(), AppVersion);
        var host = new EngineHost(
            () => ProcessTransport.Start(EngineCommand.Resolve(directory, Environment.GetEnvironmentVariable), Log),
            new ClientInfo("Youpdated", AppVersion.ToString(3), "windows"),
            TimeProvider.System,
            Log);
        var app = _app = new AppViewModel(host, services);

        app.ShowRequested += Show;
        app.UnlockNeeded += () => _window?.ShowUnlock();
        app.SelfWatchOfferNeeded += () => Notify(Notice.SelfWatch);
        app.AddRequested += _ =>
        {
            Show(AppPage.Updates);
            _window?.OpenAdd();
        };
        app.StartMenuOfferNeeded += () =>
        {
            if (!StartMenuShortcut.Exists)
            {
                Notify(Notice.StartMenu);
            }
        };

        // Clicks first, then the registration for delivery
        _toasts.Register((action, ids) => ui.Post(() => _ = OnNotificationAsync(action, ids)));
        AppInstance.GetCurrent().Activated += (_, redirected) => ui.Post(() => _ = OnActivatedAsync(redirected, launch: false));

        if (startup.IsEnabled)
        {
            startup.Enable();
        }
        TryPlatform(() => JumpList.Update(Exe, Program.AppUserModelId));

        _tray = new TrayIcon(app, Show, () => _ = QuitAsync());
        _power = new PowerEvents(() => ui.Post(app.SystemResumed));

        var background = Environment.GetCommandLineArgs().Contains(StartupRegistration.BackgroundArgument);
        if (!background && activation.Kind != ExtendedActivationKind.AppNotification)
        {
            Show(AppPage.Updates);
        }

        await app.StartAsync();
        await OnActivatedAsync(activation, launch: true);
    }

    public void Show(AppPage page)
    {
        if (_quitting || _app is null)
        {
            return;
        }
        if (_window is null)
        {
            var window = _window = _createWindow(_app);
            window.Closed += () =>
            {
                if (_window == window)
                {
                    _window = null;
                }
            };
        }
        _window.ShowPage(page);
        foreach (var notice in _pending)
        {
            _window.ShowNotice(notice);
        }
        _pending.Clear();
        if (_app.IsLocked)
        {
            _window.ShowUnlock();
        }
    }

    public void AddToStartMenu()
    {
        TryPlatform(() => StartMenuShortcut.Create(Exe, Program.AppUserModelId));
        App.StartMenuOffered();
    }

    /// <summary>Settings → Remove Youpdated from this PC</summary>
    public async Task<bool> RemoveFromThisPcAsync(bool alsoData)
    {
        var removed = await App.RemoveFromThisPcAsync(alsoData);
        TryPlatform(() => JumpList.Remove(Program.AppUserModelId));
        TryPlatform(StartMenuShortcut.Remove);
        AppLog.Delete();
        return removed;
    }

    /// <summary>Quit: checks stop while closed</summary>
    public async Task QuitAsync()
    {
        if (_quitting)
        {
            return;
        }
        _quitting = true;
        _power?.Dispose();
        _tray?.Dispose();
        _window?.Close();
        if (_app is not null)
        {
            await _app.DisposeAsync();
        }
        Application.Current.Exit();
    }

    private void Notify(Notice notice)
    {
        if (_window is not null)
        {
            _window.ShowNotice(notice);
        }
        else
        {
            _pending.Add(notice);
        }
    }

    private async Task OnActivatedAsync(AppActivationArguments activation, bool launch)
    {
        switch (activation.Kind)
        {
            case ExtendedActivationKind.AppNotification when activation.Data is AppNotificationActivatedEventArgs clicked:
                if (ToastPresenter.Parse(clicked.Argument) is { } parsed)
                {
                    await OnNotificationAsync(parsed.Action, parsed.Ids);
                }
                break;
            case ExtendedActivationKind.Protocol when activation.Data is Windows.ApplicationModel.Activation.IProtocolActivatedEventArgs protocol:
                await OnLinkAsync(YoupdatedLink.Parse(protocol.Uri.AbsoluteUri));
                break;
            case ExtendedActivationKind.Launch when activation.Data is Windows.ApplicationModel.Activation.ILaunchActivatedEventArgs started:
                var link = YoupdatedLink.FromArguments(EngineCommand.SplitCommandLine(started.Arguments ?? ""));
                if (link is not null)
                {
                    await OnLinkAsync(link);
                }
                else if (!launch)
                {
                    Show(AppPage.Updates);
                }
                break;
        }
    }

    private async Task OnLinkAsync(YoupdatedLink? link)
    {
        if (link is null || _app is null)
        {
            return;
        }
        if (link is YoupdatedLink.Open)
        {
            Show(AppPage.Updates);
        }
        await _app.Ready;
        await _app.HandleLinkAsync(link);
    }

    private async Task OnNotificationAsync(NotificationAction action, IReadOnlyList<string> ids)
    {
        if (_app is null)
        {
            return;
        }
        await _app.Ready;
        await _app.HandleNotificationAsync(action, ids);
    }

    private static void TryPlatform(Action action)
    {
        try
        {
            action();
        }
        catch (Exception exc) when (exc is System.Runtime.InteropServices.COMException or UnauthorizedAccessException or IOException)
        {
            Log(exc.Message);
        }
    }

    private static void Log(string message) => AppLog.Write(message);
}
