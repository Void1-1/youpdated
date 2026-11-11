using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;

namespace Youpdated.Core.ViewModels;

/// <summary>Settings. Start at login and Remember passphrase are OS-level (here)</summary>
public sealed partial class SettingsViewModel : ObservableObject
{
    /// <summary>Suggested when the digest is on</summary>
    public static readonly TimeOnly SuggestedDigest = new(18, 0);

    private readonly EngineClient _engine;
    private readonly IStartupRegistration _startup;
    private readonly ICredentialStore _credentials;
    private readonly Func<string?> _passphrase;
    private bool _applying;

    public SettingsViewModel(
        EngineClient engine, IStartupRegistration startup, ICredentialStore credentials,
        Func<string?> passphrase, Version appVersion)
    {
        _engine = engine;
        _startup = startup;
        _credentials = credentials;
        _passphrase = passphrase;
        AppVersion = appVersion;
        _applying = true;
        StartAtLogin = startup.IsEnabled;
        StartAtLoginDisabledByUser = startup.IsDisabledByUser;
        RememberPassphrase = credentials.Read() is not null;
        _applying = false;
    }

    /// <summary>The engine settings changed</summary>
    public event Action<Settings>? Changed;

    public static IReadOnlyList<int> IntervalChoices => Settings.IntervalChoices;

    public Version AppVersion { get; }

    public Settings Current { get; private set; } = Settings.Defaults;

    [ObservableProperty]
    public partial int CheckIntervalHours { get; set; } = 6;

    [ObservableProperty]
    public partial bool DigestEnabled { get; set; }

    [ObservableProperty]
    public partial TimeOnly DigestTime { get; set; } = SuggestedDigest;

    [ObservableProperty]
    public partial bool OsNotifications { get; set; } = true;

    /// <summary>View → Show icons: real icons fetched by the engine</summary>
    [ObservableProperty]
    public partial bool ShowIcons { get; set; }

    [ObservableProperty]
    public partial bool StartAtLogin { get; set; }

    /// <summary>Turned off in Task Manager → Startup apps</summary>
    [ObservableProperty]
    public partial bool StartAtLoginDisabledByUser { get; private set; }

    [ObservableProperty]
    public partial bool RememberPassphrase { get; set; }

    /// <summary>Only an encrypted setup has a passphrase</summary>
    [ObservableProperty]
    public partial bool IsEncrypted { get; private set; }

    [ObservableProperty]
    public partial string? EngineVersion { get; private set; }

    /// <summary>If the config watches Youpdated</summary>
    [ObservableProperty]
    public partial bool WatchingSelf { get; private set; } = true;

    [ObservableProperty]
    public partial string? Error { get; private set; }

    public void SetEngine(HelloReply hello)
    {
        EngineVersion = hello.Commit is { } commit ? $"{hello.Engine} ({commit})" : hello.Engine;
    }

    public void SetStatus(StatusReply status)
    {
        IsEncrypted = status.Encrypted.Any;
        if (status.SelfWatch is { } selfWatch)
        {
            WatchingSelf = selfWatch == SelfWatch.Watching;
        }
    }

    public async Task LoadAsync(CancellationToken cancellationToken = default)
    {
        var reply = await _engine.SettingsGetAsync(cancellationToken);
        Apply(reply.Settings);
    }

    public void Apply(Settings settings)
    {
        _applying = true;
        try
        {
            Current = settings;
            CheckIntervalHours = settings.CheckIntervalHours;
            DigestEnabled = settings.Digest is not null;
            DigestTime = settings.Digest ?? SuggestedDigest;
            OsNotifications = settings.OsNotifications;
            ShowIcons = settings.ShowIcons;
        }
        finally
        {
            _applying = false;
        }
        Changed?.Invoke(settings);
    }

    /// <summary>"Watch Youpdated" in About: readd the self-watch entry</summary>
    public async Task WatchSelfAsync()
    {
        try
        {
            await _engine.ConfigInitAsync(selfWatch: true);
            WatchingSelf = true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
    }

    partial void OnCheckIntervalHoursChanged(int value) =>
        Save(new SettingsPatch().CheckIntervalHours(value));

    partial void OnDigestEnabledChanged(bool value) =>
        Save(new SettingsPatch().Digest(value ? DigestTime : null));

    partial void OnDigestTimeChanged(TimeOnly value)
    {
        if (DigestEnabled)
        {
            Save(new SettingsPatch().Digest(value));
        }
    }

    partial void OnOsNotificationsChanged(bool value) =>
        Save(new SettingsPatch().OsNotifications(value));

    partial void OnShowIconsChanged(bool value) =>
        Save(new SettingsPatch().ShowIcons(value));

    partial void OnStartAtLoginChanged(bool value)
    {
        if (_applying)
        {
            return;
        }
        if (value)
        {
            _startup.Enable();
        }
        else
        {
            _startup.Disable();
        }
        StartAtLoginDisabledByUser = _startup.IsDisabledByUser;
    }

    partial void OnRememberPassphraseChanged(bool value)
    {
        if (_applying)
        {
            return;
        }
        if (!value)
        {
            _credentials.Delete();
        }
        else if (_passphrase() is { } passphrase)
        {
            _credentials.Write(passphrase);
        }
        else
        {
            // Nothing unlocked yet to remember
            _applying = true;
            RememberPassphrase = false;
            _applying = false;
        }
    }

    /// <summary>The last change being saved</summary>
    internal Task PendingSave { get; private set; } = Task.CompletedTask;

    /// <summary>The passphrase was entered with "Remember" checked</summary>
    public void Remembered()
    {
        _applying = true;
        RememberPassphrase = true;
        _applying = false;
    }

    private void Save(SettingsPatch patch)
    {
        if (!_applying)
        {
            PendingSave = SaveAsync(patch);
        }
    }

    private async Task SaveAsync(SettingsPatch patch)
    {
        try
        {
            var reply = await _engine.SettingsSetAsync(patch);
            Error = null;
            Apply(reply.Settings);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            Apply(Current);
        }
    }
}
