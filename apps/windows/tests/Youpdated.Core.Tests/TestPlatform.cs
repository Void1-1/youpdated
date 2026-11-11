using Microsoft.Extensions.Time.Testing;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public sealed class FakeCredentials : ICredentialStore
{
    public string? Stored { get; set; }

    public string? Read() => Stored;

    public void Write(string passphrase) => Stored = passphrase;

    public void Delete() => Stored = null;
}

public sealed class FakeStartup : IStartupRegistration
{
    public bool IsEnabled { get; private set; }

    public bool IsDisabledByUser => false;

    public void Enable() => IsEnabled = true;

    public void Disable() => IsEnabled = false;
}

public sealed class FakeUrlScheme : IUrlSchemeRegistration
{
    public int Registered { get; private set; }

    public bool Unregistered { get; private set; }

    public void Register() => Registered++;

    public void Unregister() => Unregistered = true;
}

public sealed class FakeNotifications : INotificationPresenter
{
    public List<NotificationRequest> Shown { get; } = [];

    public List<string> Removed { get; } = [];

    public bool Unregistered { get; private set; }

    public void Show(NotificationRequest request) => Shown.Add(request);

    public void Remove(IReadOnlyCollection<string> itemIds) => Removed.AddRange(itemIds);

    public void Unregister() => Unregistered = true;
}

public sealed class FakeShell : IShell
{
    public List<string> Opened { get; } = [];

    public List<string> Revealed { get; } = [];

    public string DownloadsFolder { get; } = Path.Combine(Path.GetTempPath(), "youpdated-tests-downloads");

    public void OpenUrl(string url) => Opened.Add(url);

    public void Reveal(string path) => Revealed.Add(path);
}

public sealed class FakeDataRemover : IDataRemover
{
    public bool Removed { get; private set; }

    public Task<bool> RemoveDataAsync(CancellationToken cancellationToken = default)
    {
        Removed = true;
        return Task.FromResult(true);
    }
}

/// <summary>An <see cref="AppViewModel"/> over a <see cref="FakeEngine"/> and fake platform services</summary>
public sealed class TestApp : IAsyncDisposable
{
    public TestApp(FakeEngine? engine = null)
    {
        Engine = engine ?? new FakeEngine();
        Time.SetLocalTimeZone(TimeZoneInfo.Utc);
        var host = new EngineHost(() => Engine, new ClientInfo("Youpdated", "1.0.0", "windows"), Time);
        Services = new AppServices(
            new ImmediateDispatcher(), Time, Credentials, Startup, UrlScheme, Notifications, Shell, LocalState,
            DataRemover, new HttpClient(), new Version(1, 0, 0));
        App = new AppViewModel(host, Services);
        App.UnlockNeeded += () => UnlockNeeded++;
        App.SelfWatchOfferNeeded += () => SelfWatchOffers++;
        App.CliNoticeNeeded += () => CliNotices++;
        App.ShowRequested += page => Shown.Add(page);
    }

    public FakeEngine Engine { get; }

    public FakeTimeProvider Time { get; } = new(new DateTimeOffset(2026, 10, 29, 9, 0, 0, TimeSpan.Zero));

    public FakeCredentials Credentials { get; } = new();

    public FakeStartup Startup { get; } = new();

    public FakeUrlScheme UrlScheme { get; } = new();

    public FakeNotifications Notifications { get; } = new();

    public FakeShell Shell { get; } = new();

    public MemoryLocalStateStore LocalState { get; } = new();

    public FakeDataRemover DataRemover { get; } = new();

    public AppServices Services { get; }

    public AppViewModel App { get; }

    public int UnlockNeeded { get; private set; }

    public int SelfWatchOffers { get; private set; }

    public int CliNotices { get; private set; }

    public List<AppPage> Shown { get; } = [];

    /// <summary>Start, and wait for post-handshake sync</summary>
    public async Task StartAsync()
    {
        var locked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        App.UnlockNeeded += () => locked.TrySetResult();
        await App.StartAsync(TestContext.Current.CancellationToken);
        await Task.WhenAny(App.Ready, locked.Task).WaitAsync(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken);
    }

    public async Task SettleAsync()
    {
        Time.Advance(TimeSpan.Zero);
        for (var i = 0; i < 50 && App.IsChecking; i++)
        {
            await Task.Delay(10, TestContext.Current.CancellationToken);
        }
    }

    public async ValueTask DisposeAsync() => await App.DisposeAsync();
}
