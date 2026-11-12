using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;

namespace Youpdated.Core.ViewModels;

/// <summary>Settings → Privacy: proxy options</summary>
public enum ProxyChoice
{
    None,
    Tor,
    TorBrowser,
    Custom,
}

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

    /// <summary>Tor and Tor Browser's SOCKS ports</summary>
    public const string TorProxy = "socks5://127.0.0.1:9050";
    public const string TorBrowserProxy = "socks5://127.0.0.1:9150";

    /// <summary>If the engine can edit config privacy</summary>
    [ObservableProperty]
    public partial bool CanEditPrivacy { get; private set; }

    /// <summary>The config's privacy options</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasPrivacy), nameof(FastMode), nameof(UserAgentText), nameof(GapText), nameof(ConcurrencyText), nameof(TimeoutText))]
    public partial Privacy? Privacy { get; private set; }

    public bool HasPrivacy => Privacy is not null;

    public bool FastMode => Privacy?.FastMode ?? false;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ProxyIsCustom))]
    public partial ProxyChoice ProxyChoice { get; private set; }

    public bool ProxyIsCustom => ProxyChoice == ProxyChoice.Custom;

    [ObservableProperty]
    public partial string ProxyAddress { get; set; } = "";

    [ObservableProperty]
    public partial bool PrivacySaving { get; private set; }

    [ObservableProperty]
    public partial string? PrivacyError { get; private set; }

    /// <summary>null for "rotate"</summary>
    public string? UserAgentText => Privacy is { UserAgent: not "rotate" } privacy ? privacy.UserAgent : null;

    /// <summary>"0.5-3 s"</summary>
    public string GapText => Privacy is { } privacy ? Seconds(privacy.Jitter) : "";

    public string ConcurrencyText => Privacy?.Concurrency.ToString(System.Globalization.CultureInfo.CurrentCulture) ?? "";

    public string TimeoutText => Privacy is { } privacy ? Seconds([privacy.Timeout]) : "";

    public static string Seconds(IReadOnlyList<double> values)
    {
        var shown = values.Count == 2 && values[0] == values[1] ? values.Take(1) : values;
        return string.Join("-", shown.Select(v => v.ToString("0.#", System.Globalization.CultureInfo.CurrentCulture))) + " s";
    }

    public static ProxyChoice ChoiceFor(string? proxy) => proxy switch
    {
        null => ProxyChoice.None,
        TorProxy => ProxyChoice.Tor,
        TorBrowserProxy => ProxyChoice.TorBrowser,
        _ => ProxyChoice.Custom,
    };

    public void SetEngine(HelloReply hello)
    {
        EngineVersion = hello.Commit is { } commit ? $"{hello.Engine} ({commit})" : hello.Engine;
        CanEditPrivacy = hello.Ops.Contains("privacy.set");
    }

    /// <summary>Reads config privacy options</summary>
    public async Task LoadPrivacyAsync(CancellationToken cancellationToken = default)
    {
        if (!CanEditPrivacy)
        {
            return;
        }
        try
        {
            ApplyPrivacy((await _engine.PrivacyGetAsync(cancellationToken)).Privacy);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Privacy = null;
        }
    }

    /// <summary>None and the Tor ports save immediately; Custom waits for the address</summary>
    public Task ChooseProxyAsync(ProxyChoice choice)
    {
        PrivacyError = null;
        ProxyChoice = choice;
        switch (choice)
        {
            case ProxyChoice.None:
                return SavePrivacyAsync(new PrivacyPatch().Proxy(null));
            case ProxyChoice.Tor:
                return SavePrivacyAsync(new PrivacyPatch().Proxy(TorProxy));
            case ProxyChoice.TorBrowser:
                return SavePrivacyAsync(new PrivacyPatch().Proxy(TorBrowserProxy));
            default:
                if (ChoiceFor(ProxyAddress) != ProxyChoice.Custom)
                {
                    ProxyAddress = "";
                }
                return Task.CompletedTask;
        }
    }

    public bool CanUseAddress => !PrivacySaving && ProxyAddress.Trim() is { Length: > 0 } typed && typed != Privacy?.Proxy;

    public Task UseProxyAddressAsync() =>
        CanUseAddress ? SavePrivacyAsync(new PrivacyPatch().Proxy(ProxyAddress.Trim())) : Task.CompletedTask;

    public Task SetFastModeAsync(bool on) =>
        on == FastMode ? Task.CompletedTask : SavePrivacyAsync(new PrivacyPatch().FastMode(on));

    private async Task SavePrivacyAsync(PrivacyPatch patch)
    {
        PrivacyError = null;
        PrivacySaving = true;
        try
        {
            ApplyPrivacy((await _engine.PrivacySetAsync(patch)).Privacy);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            PrivacyError = exc.Message;
            // a custom address that failed stays
            if (ProxyChoice != ProxyChoice.Custom && Privacy is { } privacy)
            {
                ApplyPrivacy(privacy);
            }
            else
            {
                OnPropertyChanged(nameof(FastMode));
            }
        }
        finally
        {
            PrivacySaving = false;
            OnPropertyChanged(nameof(CanUseAddress));
        }
    }

    private void ApplyPrivacy(Privacy privacy)
    {
        Privacy = privacy;
        ProxyChoice = ChoiceFor(privacy.Proxy);
        if (ProxyChoice == ProxyChoice.Custom)
        {
            ProxyAddress = privacy.Proxy!;
        }
        OnPropertyChanged(nameof(CanUseAddress));
    }

    partial void OnProxyAddressChanged(string value) => OnPropertyChanged(nameof(CanUseAddress));

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
