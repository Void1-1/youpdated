using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
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

    public static string AppVersion(Version version) => Text.Format("SettingsVersion", version.ToString(3));

    public static string UpdateStatus(bool watching) =>
        Text.Get(watching ? "SettingsUpdatesWatching" : "SettingsUpdatesOff");

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        var app = (AppViewModel)e.Parameter;
        Settings = app.Settings;
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
        _filling = false;
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
