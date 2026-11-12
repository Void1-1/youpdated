using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Navigation;
using Windows.System;
using Youpdated.App.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

public sealed partial class SettingsPage : Page
{
    private bool _filling;

    public SettingsPage()
    {
        InitializeComponent();
    }

    public SettingsViewModel Settings { get; private set; } = null!;

    private FeedViewModel? _feed;

    private AppViewModel? _app;

    public static string AppVersion(Version version) => Text.Format("SettingsVersion", version.ToString(3));

    public static string UpdateStatus(bool watching) =>
        Text.Get(watching ? "SettingsUpdatesWatching" : "SettingsUpdatesOff");

    public static string UserAgent(string? fixedAgent) => fixedAgent switch
    {
        null => Text.Get("UserAgentRotate"),
        "youpdated" => Text.Get("UserAgentYoupdated"),
        _ => fixedAgent,
    };

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        var app = (AppViewModel)e.Parameter;
        Settings = app.Settings;
        Settings.PropertyChanged += OnSettingsChanged;
        Settings.AskToConfirmUserAgent = () =>
            XamlRoot is { } root ? Dialogs.ConfirmUserAgentAsync(root) : Task.FromResult(false);
        _app = app;
        _feed = app.Feed;
        Bindings.Update();
        _filling = true;
        IntervalBox.Items.Clear();
        foreach (var hours in SettingsViewModel.IntervalChoices)
        {
            IntervalBox.Items.Add(new ComboBoxItem { Content = Text.Interval(hours), Tag = hours });
        }
        IntervalBox.SelectedIndex = SettingsViewModel.IntervalChoices.ToList().IndexOf(Settings.CheckIntervalHours);
        DigestPicker.SelectedTime = Settings.DigestTime.ToTimeSpan();
        FillView(app.Feed.View);
        DiagnosticLogSwitch.IsOn = AppLog.Enabled;
        ShowLogCard.Description = AppLog.File;
        ShowLogCard.IsEnabled = AppLog.Exists;
        ProxyBox.Items.Clear();
        foreach (var (choice, key) in new[]
        {
            (ProxyChoice.None, "ProxyNone"), (ProxyChoice.Tor, "ProxyTor"),
            (ProxyChoice.TorBrowser, "ProxyTorBrowser"), (ProxyChoice.Custom, "ProxyCustom"),
        })
        {
            ProxyBox.Items.Add(new ComboBoxItem { Content = Text.Get(key), Tag = choice });
        }
        _filling = false;
        SyncPrivacy();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        Settings.PropertyChanged -= OnSettingsChanged;
        Settings.AskToConfirmUserAgent = null;
        base.OnNavigatedFrom(e);
    }

    private void OnSettingsChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(SettingsViewModel.ProxyChoice) or nameof(SettingsViewModel.FastMode) or nameof(SettingsViewModel.Privacy))
        {
            SyncPrivacy();
        }
    }

    /// <summary>Proxy and fast mode follow the config</summary>
    private void SyncPrivacy()
    {
        _filling = true;
        var index = ProxyBox.Items.Cast<ComboBoxItem>().ToList()
            .FindIndex(item => item.Tag is ProxyChoice choice && choice == Settings.ProxyChoice);
        if (ProxyBox.SelectedIndex != index)
        {
            ProxyBox.SelectedIndex = index;
        }
        if (FastModeSwitch.IsOn != Settings.FastMode)
        {
            FastModeSwitch.IsOn = Settings.FastMode;
        }
        _filling = false;
    }

    private async void OnProxyChoice(object sender, SelectionChangedEventArgs e)
    {
        if (_filling || ProxyBox.SelectedItem is not ComboBoxItem { Tag: ProxyChoice choice } || choice == Settings.ProxyChoice)
        {
            return;
        }
        await Settings.ChooseProxyAsync(choice);
        if (choice == ProxyChoice.Custom)
        {
            ProxyAddressBox.Focus(FocusState.Programmatic);
        }
    }

    private async void OnUseProxy(object sender, RoutedEventArgs e) => await Settings.UseProxyAddressAsync();

    private async void OnProxyAddressKey(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter)
        {
            e.Handled = true;
            await Settings.UseProxyAddressAsync();
        }
    }

    private async void OnFastModeToggled(object sender, RoutedEventArgs e)
    {
        if (_filling)
        {
            return;
        }
        var on = FastModeSwitch.IsOn;
        if (on && (XamlRoot is not { } root || !await Dialogs.ConfirmFastModeAsync(root)))
        {
            SyncPrivacy();
            return;
        }
        await Settings.SetFastModeAsync(on);
        SyncPrivacy();
    }

    private void OnShowConfig(object sender, RoutedEventArgs e)
    {
        if (_app?.Status?.ConfigPath is { } path)
        {
            new Shell().Reveal(path);
        }
    }

    private void FillView(ViewOptions view)
    {
        Fill(DensityBox, view.Density, [(Density.Comfortable, "DensityComfortable"), (Density.Compact, "DensityCompact")]);
        Fill(GroupByBox, view.GroupBy,
            [(GroupBy.Day, "GroupByDay"), (GroupBy.Priority, "GroupByPriority"), (GroupBy.Source, "GroupBySource"), (GroupBy.None, "GroupByNone")]);
        Fill(LowPriorityBox, view.ShowLowPriority, [(true, "LowPriorityShow"), (false, "LowPriorityHide")]);
        Fill(ReadUpdatesBox, view.ReadUpdates,
            [(ReadUpdates.Last7Days, "ReadLast7Days"), (ReadUpdates.Last30Days, "ReadLast30Days"), (ReadUpdates.All, "ReadAll"), (ReadUpdates.None, "ReadNone")]);
        Fill(TimesBox, view.Times, [(TimeStyle.Relative, "TimesRelative"), (TimeStyle.Absolute, "TimesAbsolute")]);
        PreviewsSwitch.IsOn = view.Previews;
        GroupReadSwitch.IsOn = view.GroupReadUpdates;
        GroupReadCard.IsEnabled = view.GroupBy != GroupBy.Day;
    }

    private static void Fill<T>(ComboBox box, T current, (T Value, string Key)[] choices)
    {
        box.Items.Clear();
        foreach (var (value, key) in choices)
        {
            box.Items.Add(new ComboBoxItem { Content = Text.Get(key), Tag = value });
        }
        box.SelectedIndex = Array.FindIndex(choices, choice => EqualityComparer<T>.Default.Equals(choice.Value, current));
    }

    private void OnViewChoice(object sender, SelectionChangedEventArgs e)
    {
        if (_filling || _feed is not { } feed || sender is not ComboBox { SelectedItem: ComboBoxItem { Tag: { } value } })
        {
            return;
        }
        feed.View = value switch
        {
            Density density => feed.View with { Density = density },
            GroupBy group => feed.View with { GroupBy = group },
            bool show => feed.View with { ShowLowPriority = show },
            ReadUpdates read => feed.View with { ReadUpdates = read },
            TimeStyle times => feed.View with { Times = times },
            _ => feed.View,
        };
        GroupReadCard.IsEnabled = feed.View.GroupBy != GroupBy.Day;
    }

    private void OnGroupReadToggled(object sender, RoutedEventArgs e)
    {
        if (!_filling && _feed is { } feed)
        {
            feed.View = feed.View with { GroupReadUpdates = GroupReadSwitch.IsOn };
        }
    }

    private void OnPreviewsToggled(object sender, RoutedEventArgs e)
    {
        if (!_filling && _feed is { } feed)
        {
            feed.View = feed.View with { Previews = PreviewsSwitch.IsOn };
        }
    }

    private void OnIntervalChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_filling && IntervalBox.SelectedItem is ComboBoxItem { Tag: int hours })
        {
            Settings.CheckIntervalHours = hours;
        }
    }

    private void OnDigestTimeChanged(TimePicker sender, TimePickerSelectedValueChangedEventArgs args)
    {
        if (!_filling && args.NewTime is { } time)
        {
            Settings.DigestTime = TimeOnly.FromTimeSpan(time);
        }
    }

    private void OnDiagnosticLogToggled(object sender, RoutedEventArgs e)
    {
        if (_filling)
        {
            return;
        }
        AppLog.SetEnabled(DiagnosticLogSwitch.IsOn);
        if (DiagnosticLogSwitch.IsOn)
        {
            AppLog.Write($"Diagnostic log on, Youpdated {Settings.AppVersion.ToString(3)}, {Settings.EngineVersion}, Windows {Environment.OSVersion.Version}");
        }
        ShowLogCard.IsEnabled = AppLog.Exists;
    }

    private void OnShowLog(object sender, RoutedEventArgs e) => new Shell().Reveal(AppLog.File);

    private void OnWindowsNotifications(object sender, RoutedEventArgs e) => Shell.OpenSettings("ms-settings:notifications");

    private void OnStartupApps(object sender, RoutedEventArgs e) => Shell.OpenSettings("ms-settings:startupapps");

    private void OnSourceCode(object sender, RoutedEventArgs e) =>
        new Shell().OpenUrl("https://github.com/" + AppUpdate.Repository);

    private async void OnWatchSelf(object sender, RoutedEventArgs e) => await Settings.WatchSelfAsync();

    /// <summary>Clears the Run key, youpdated://, notifications, and the passphrase; optionally data too</summary>
    private async void OnRemoveFromPc(object sender, RoutedEventArgs e)
    {
        if (XamlRoot is not { } root || AppController.Current is not { } controller
            || await Dialogs.RemoveFromThisPcAsync(root) is not { } alsoData)
        {
            return;
        }
        var removed = await controller.RemoveFromThisPcAsync(alsoData);
        await Dialogs.RemovedAsync(root, AppContext.BaseDirectory, alsoData && removed);
        await controller.QuitAsync();
    }
}
