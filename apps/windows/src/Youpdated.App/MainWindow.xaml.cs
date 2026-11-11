using System.ComponentModel;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.System;
using Youpdated.App.Views;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

public sealed partial class MainWindow : Window, IMainWindow
{
    // Window default and min sizes, in DIPs
    private const int DefaultWidth = 560;
    private const int DefaultHeight = 720;
    private const int MinimumWidth = 500;
    private const int MinimumHeight = 480;

    private static readonly (string Label, string Value)[] Examples =
    [
        ("ExampleGitHub", "https://github.com/godotengine/godot"),
        ("ExampleSteam", "https://store.steampowered.com/app/440/"),
        ("ExampleNpm", "https://www.npmjs.com/package/react"),
        ("ExampleYouTube", "https://www.youtube.com/@NASA"),
    ];

    private readonly AppViewModel _app;
    private readonly ProblemsFlyout _problems;
    private bool _settingsOpen;
    private bool _checkingShown;
    private double _titleWidth = 64;
    private double _tabsWidth;

    public MainWindow(AppViewModel app)
    {
        _app = app;
        Open = this;
        InitializeComponent();

        SystemBackdrop = new MicaBackdrop();
        CustomTitleBar.Extend(this, AppTitleBar);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "Youpdated.ico"));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = MinimumWidth;
            presenter.PreferredMinimumHeight = MinimumHeight;
        }

        _problems = new ProblemsFlyout(app) { Placement = Microsoft.UI.Xaml.Controls.Primitives.FlyoutPlacementMode.BottomEdgeAlignedRight };
        ProblemsButton.Flyout = _problems;
        Feed.Attach(app);
        Following.Attach(app);
        FlyoutAdd.Attach(app);
        WelcomeAdd.Attach(app);
        WelcomeAdd.UseLargeField();
        FlyoutAdd.Done += () => AddFlyout.Hide();
        FlyoutAdd.ImportRequested += () =>
        {
            AddFlyout.Hide();
            _ = PickAndImportAsync();
        };
        WelcomeAdd.ImportRequested += () => _ = PickAndImportAsync();
        foreach (var (label, value) in Examples)
        {
            var chip = new Button { Content = Text.Get(label), CornerRadius = new CornerRadius(14) };
            chip.Click += (_, _) => WelcomeAdd.Fill(value);
            ExampleChips.Children.Add(chip);
        }
        AddKeyboardAccelerators();
        CheckIcon.SizeChanged += (_, _) => Motion.CenterPivot(CheckIcon);

        _app.PropertyChanged += OnAppChanged;
        _app.Problems.PropertyChanged += OnAppChanged;
        Root.Loaded += (_, _) =>
        {
            var scale = Root.XamlRoot.RasterizationScale;
            AppWindow.Resize(new SizeInt32((int)(DefaultWidth * scale), (int)(DefaultHeight * scale)));
            UpdateTitleBarRegions();
            CustomTitleBar.MatchTheme(this, Root.ActualTheme);
        };
        Root.ActualThemeChanged += (_, _) => CustomTitleBar.MatchTheme(this, Root.ActualTheme);
        // The tabs move when anything beside them changes size
        foreach (var part in new FrameworkElement[] { AppTitleBar, TitleStart, ViewSwitcher, TitleActions })
        {
            part.SizeChanged += (_, _) => UpdateTitleBarRegions();
        }
        UpdateAll();

        Closed += (_, _) =>
        {
            if (Open == this)
            {
                Open = null;
            }
            _app.PropertyChanged -= OnAppChanged;
            _app.Problems.PropertyChanged -= OnAppChanged;
            Dismissed?.Invoke();
        };
    }

    /// <summary>The open window</summary>
    public static MainWindow? Open { get; private set; }

    public event Action? Dismissed;

    event Action? IMainWindow.Closed
    {
        add => Dismissed += value;
        remove => Dismissed -= value;
    }

    public void ShowPage(AppPage page)
    {
        switch (page)
        {
            case AppPage.Settings:
                OpenSettings();
                break;
            case AppPage.Problems:
                CloseSettings();
                if (_app.Problems.Any)
                {
                    _problems.ShowAt(ProblemsButton);
                }
                break;
            default:
                CloseSettings();
                ViewSwitcher.SelectedItem = page == AppPage.Following ? FollowingItem : UpdatesItem;
                UpdateContent();
                break;
        }
        AppWindow.Show();
        Activate();
    }

    /// <summary>Locked</summary>
    public void ShowUnlock()
    {
        UpdateContent();
        if (_app.IsLocked)
        {
            PassphraseBox.Focus(FocusState.Programmatic);
        }
    }

    public void ShowNotice(Notice notice) =>
        (notice == Notice.SelfWatch ? SelfWatchBar : StartMenuBar).IsOpen = true;

    /// <summary>+, Ctrl+N, paste, dropped link, or youpdated://add</summary>
    public void OpenAdd()
    {
        CloseSettings();
        if (_app.WindowState == WindowState.Welcome)
        {
            WelcomeAdd.FocusField();
            return;
        }
        ViewSwitcher.SelectedItem = UpdatesItem;
        UpdateContent();
        if (!AddFlyout.IsOpen)
        {
            AddFlyout.ShowAt(AddButton);
        }
    }

    void IMainWindow.Close() => Close();

    /// <summary>Preview an import, confirm, apply</summary>
    internal async Task ImportAsync(string path)
    {
        var import = _app.Sources.Import;
        await import.PreviewFileAsync(path);
        if (Content.XamlRoot is { } root && await Dialogs.ImportAsync(root, import))
        {
            await import.ApplyAsync();
        }
        import.Reset();
    }

    private void OnAppChanged(object? sender, PropertyChangedEventArgs args) =>
        DispatcherQueue.TryEnqueue(() =>
        {
            switch (args.PropertyName)
            {
                case nameof(AppViewModel.WindowState):
                    UpdateContent();
                    break;
                case nameof(AppViewModel.IsChecking):
                case nameof(AppViewModel.ProgressDone):
                case nameof(AppViewModel.ProgressOf):
                    UpdateChecking();
                    break;
                case nameof(AppViewModel.ShowCliNotice):
                    UpdateBars();
                    break;
                case nameof(ProblemsViewModel.Problems):
                    UpdateProblems();
                    break;
            }
        });

    private void UpdateAll()
    {
        UpdateContent();
        UpdateChecking();
        UpdateBars();
        UpdateProblems();
    }

    private void UpdateContent()
    {
        var state = _app.WindowState;
        var following = ViewSwitcher.SelectedItem == FollowingItem;
        StartingRing.IsActive = state == WindowState.Starting;
        StartingRing.Visibility = Bind.Show(state == WindowState.Starting && !_settingsOpen);
        LockedPanel.Visibility = Bind.Show(state == WindowState.Locked && !_settingsOpen);
        WelcomePanel.Visibility = Bind.Show(state == WindowState.Welcome && !following && !_settingsOpen);
        Feed.Visibility = Bind.Show(state == WindowState.Feed && !following && !_settingsOpen);
        Following.Visibility = Bind.Show(state is WindowState.Feed or WindowState.Welcome && following && !_settingsOpen);
        SettingsFrame.Visibility = Bind.Show(_settingsOpen);

        // Until unlocked there's nothing to switch between
        var usable = state is WindowState.Feed or WindowState.Welcome;
        ViewSwitcher.Visibility = Bind.Show(usable && !_settingsOpen);
        TitleActions.Visibility = Bind.Show(usable && !_settingsOpen);
        BackButton.Visibility = Bind.Show(_settingsOpen);
        TitleText.Text = _settingsOpen ? Text.Get("SettingsTitleBar") : "Youpdated";
        UpdateTitleBarRegions();
    }

    private void UpdateChecking()
    {
        CheckProgress.Visibility = Bind.Show(_app.IsChecking);
        CheckProgress.IsIndeterminate = _app.ProgressOf == 0;
        CheckProgress.Value = Bind.Progress(_app.ProgressDone, _app.ProgressOf);
        if (_checkingShown != _app.IsChecking)
        {
            _checkingShown = _app.IsChecking;
            Motion.Spin(CheckIcon, _app.IsChecking);
        }
    }

    private void UpdateBars()
    {
        CliBar.IsOpen = _app.ShowCliNotice;
    }

    private void UpdateProblems()
    {
        var count = _app.Problems.Problems.Count;
        ProblemsButton.Visibility = Bind.Show(count > 0);
        ProblemsBadge.Value = count;
        UpdateTitleBarRegions();
    }

    private void UpdateTitleBarRegions()
    {
        CaptionInset.Width = new GridLength(CustomTitleBar.RightInset(this, Root.XamlRoot?.RasterizationScale ?? 1));
        FitTitle();
        CustomTitleBar.SetInteractive(this, [BackButton, ViewSwitcher, ProblemsButton, CheckButton, AddButton, MoreButton]);
    }

    private void FitTitle()
    {
        const double Edge = 20;
        const double Icon = 30;
        const double Gap = 10;

        var tabs = ViewSwitcher.Visibility == Visibility.Visible;
        if (tabs && TitleText.Visibility == Visibility.Visible && TitleText.ActualWidth > 0)
        {
            _titleWidth = TitleText.ActualWidth;
        }
        // The widest seen: a column that's too narrow squeezes them
        _tabsWidth = Math.Max(_tabsWidth, ViewSwitcher.ActualWidth);

        var room = AppTitleBar.ActualWidth - CaptionInset.Width.Value - TitleActions.ActualWidth - _tabsWidth - Edge;
        TitleIcon.Visibility = Bind.Show(!_settingsOpen && (!tabs || room >= Icon));
        TitleText.Visibility = Bind.Show(!tabs || room >= Icon + Gap + _titleWidth);
    }

    // Views, Settings

    private void OnViewChanged(SelectorBar sender, SelectorBarSelectionChangedEventArgs args) => UpdateContent();

    private void OpenSettings()
    {
        if (!_settingsOpen)
        {
            _settingsOpen = true;
            NavigationTransitionInfo transition = Motion.Enabled ? new DrillInNavigationTransitionInfo() : new SuppressNavigationTransitionInfo();
            SettingsFrame.Navigate(typeof(SettingsPage), _app, transition);
        }
        UpdateContent();
    }

    private void CloseSettings()
    {
        if (_settingsOpen)
        {
            _settingsOpen = false;
            UpdateContent();
        }
    }

    private void OnBack(object sender, RoutedEventArgs e) => CloseSettings();

    private void OnCheck(object sender, RoutedEventArgs e) => _app.CheckNowCommand.Execute(null);

    private void OnAddOpening(object? sender, object e)
    {
        // A finished or failed add restarts
        if (_app.Sources.Add.Stage is AddStage.Added or AddStage.Failed)
        {
            _app.Sources.Add.Reset();
        }
    }

    private void OnAddOpened(object? sender, object e) => FlyoutAdd.FocusField();

    /// <summary>Quick View options (Group by, Filter by source, Density), Mark all read, Import a file…, Settings</summary>
    private void OnMoreOpening(object? sender, object e)
    {
        var feed = _app.Feed;
        MoreMenu.Items.Clear();

        MoreMenu.Items.Add(Choices("MoreGroupBy", "\uE8CB", feed.View.GroupBy,
            [(GroupBy.Day, "GroupByDay"), (GroupBy.Priority, "GroupByPriority"), (GroupBy.Source, "GroupBySource"), (GroupBy.None, "GroupByNone")],
            choice => feed.View = feed.View with { GroupBy = choice }));

        var filter = new MenuFlyoutSubItem { Text = Text.Get("MoreFilter"), Icon = new FontIcon { Glyph = "\uE71C" } };
        var all = new RadioMenuFlyoutItem { Text = Text.Get("AllSources"), GroupName = "Filter", IsChecked = feed.SourceFilter is null };
        all.Click += (_, _) => feed.SourceFilter = null;
        filter.Items.Add(all);
        foreach (var label in feed.Labels)
        {
            var item = new RadioMenuFlyoutItem { Text = label, GroupName = "Filter", IsChecked = feed.SourceFilter == label };
            item.Click += (_, _) => feed.SourceFilter = label;
            filter.Items.Add(item);
        }
        MoreMenu.Items.Add(filter);

        MoreMenu.Items.Add(Choices("MoreDensity", "\uE8FD", feed.View.Density,
            [(Density.Comfortable, "DensityComfortable"), (Density.Compact, "DensityCompact")],
            choice => feed.View = feed.View with { Density = choice }));
        MoreMenu.Items.Add(new MenuFlyoutSeparator());

        var markAll = new MenuFlyoutItem { Text = Text.Get("MoreMarkAllRead"), Icon = new FontIcon { Glyph = "\uE8FB" } };
        markAll.KeyboardAcceleratorTextOverride = "Ctrl+Alt+U";
        markAll.Click += async (_, _) => await feed.MarkAllReadAsync();
        MoreMenu.Items.Add(markAll);
        var import = new MenuFlyoutItem { Text = Text.Get("MoreImport"), Icon = new FontIcon { Glyph = "\uE8B5" } };
        import.Click += async (_, _) => await PickAndImportAsync();
        MoreMenu.Items.Add(import);
        MoreMenu.Items.Add(new MenuFlyoutSeparator());
        var settings = new MenuFlyoutItem { Text = Text.Get("MoreSettings"), Icon = new FontIcon { Glyph = "\uE713" } };
        settings.Click += (_, _) => OpenSettings();
        MoreMenu.Items.Add(settings);
    }

    private static MenuFlyoutSubItem Choices<T>(string title, string glyph, T current, (T Value, string Key)[] choices, Action<T> pick)
        where T : struct, Enum
    {
        var menu = new MenuFlyoutSubItem { Text = Text.Get(title), Icon = new FontIcon { Glyph = glyph } };
        foreach (var (value, key) in choices)
        {
            var item = new RadioMenuFlyoutItem { Text = Text.Get(key), GroupName = title, IsChecked = EqualityComparer<T>.Default.Equals(current, value) };
            item.Click += (_, _) => pick(value);
            menu.Items.Add(item);
        }
        return menu;
    }

    /// <summary>package.json, requirements.txt, or Brewfile</summary>
    private async Task PickAndImportAsync()
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.DocumentsLibrary };
        picker.FileTypeFilter.Add("*");
        // An unpackaged app's picker needs the window it belongs to
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        if (await picker.PickSingleFileAsync() is { } file)
        {
            await ImportAsync(file.Path);
        }
    }

    // Unlocking

    private async void OnUnlock(object sender, RoutedEventArgs e)
    {
        UnlockButton.IsEnabled = false;
        _app.Unlock.Passphrase = PassphraseBox.Password;
        _app.Unlock.Remember = RememberBox.IsChecked == true;
        var unlocked = await _app.Unlock.UnlockAsync();
        UnlockButton.IsEnabled = true;
        UnlockError.Message = _app.Unlock.Error ?? "";
        UnlockError.IsOpen = !unlocked && _app.Unlock.Error is not null;
        PassphraseBox.Password = "";
        if (unlocked)
        {
            RememberBox.IsChecked = false;
        }
        else
        {
            PassphraseBox.Focus(FocusState.Programmatic);
        }
    }

    private void OnPassphraseKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter)
        {
            e.Handled = true;
            OnUnlock(sender, e);
        }
    }

    private async void OnCliBarClosed(InfoBar sender, InfoBarClosedEventArgs args) => await _app.DismissCliNoticeAsync();

    private async void OnSelfWatchAdd(object sender, RoutedEventArgs e)
    {
        SelfWatchBar.IsOpen = false;
        await _app.AnswerSelfWatchOfferAsync(watch: true);
    }

    /// <summary>Remember closing to not reshow</summary>
    private async void OnSelfWatchClosed(InfoBar sender, InfoBarClosedEventArgs args)
    {
        if (args.Reason == InfoBarCloseReason.CloseButton)
        {
            await _app.AnswerSelfWatchOfferAsync(watch: false);
        }
    }

    private void OnStartMenuAdd(object sender, RoutedEventArgs e)
    {
        StartMenuBar.IsOpen = false;
        AppController.Current?.AddToStartMenu();
    }

    private void OnStartMenuClosed(InfoBar sender, InfoBarClosedEventArgs args)
    {
        if (args.Reason == InfoBarCloseReason.CloseButton)
        {
            _app.StartMenuOffered();
        }
    }

    // Keyboard

    private void AddKeyboardAccelerators()
    {
        void Add(VirtualKey key, VirtualKeyModifiers modifiers, Action action)
        {
            var accelerator = new KeyboardAccelerator { Key = key, Modifiers = modifiers };
            accelerator.Invoked += (_, args) =>
            {
                args.Handled = true;
                action();
            };
            Root.KeyboardAccelerators.Add(accelerator);
        }

        // Or the whole window shows the first one, "Ctrl+R", as its tooltip
        Root.KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;

        Add(VirtualKey.R, VirtualKeyModifiers.Control, () => _app.CheckNowCommand.Execute(null));
        Add(VirtualKey.N, VirtualKeyModifiers.Control, OpenAdd);
        Add(VirtualKey.Number1, VirtualKeyModifiers.Control, () =>
        {
            ShowPage(AppPage.Updates);
            Feed.FocusFirstCard();
        });
        Add(VirtualKey.Number2, VirtualKeyModifiers.Control, () => ShowPage(AppPage.Following));
        Add(VirtualKey.F, VirtualKeyModifiers.Control, () =>
        {
            ShowPage(AppPage.Following);
            Following.FocusFilter();
        });
        Add(VirtualKey.U, VirtualKeyModifiers.Control | VirtualKeyModifiers.Menu, () => _ = _app.Feed.MarkAllReadAsync());
        Add(VirtualKey.Escape, VirtualKeyModifiers.None, () =>
        {
            if (_settingsOpen)
            {
                CloseSettings();
            }
            else
            {
                _app.Feed.Collapse();
            }
        });

        // Ctrl+V with a link opens Add with paste
        var paste = new KeyboardAccelerator { Key = VirtualKey.V, Modifiers = VirtualKeyModifiers.Control };
        paste.Invoked += async (_, args) =>
        {
            if (FocusManager.GetFocusedElement(Root.XamlRoot) is TextBox or PasswordBox or RichEditBox or AutoSuggestBox)
            {
                return;
            }
            args.Handled = true;
            var content = Clipboard.GetContent();
            string? text = null;
            if (content.Contains(StandardDataFormats.WebLink))
            {
                text = (await content.GetWebLinkAsync()).AbsoluteUri;
            }
            else if (content.Contains(StandardDataFormats.Text))
            {
                text = await content.GetTextAsync();
            }
            await _app.PasteAsync(text);
        };
        Root.KeyboardAccelerators.Add(paste);
    }

    // Drag to add: a link or text adds, file imports

    private void OnDragOver(object sender, DragEventArgs e)
    {
        if (e.DataView.Contains(StandardDataFormats.StorageItems))
        {
            e.AcceptedOperation = DataPackageOperation.Copy;
            e.DragUIOverride.Caption = Text.Get("DropImport");
        }
        else if (e.DataView.Contains(StandardDataFormats.WebLink) || e.DataView.Contains(StandardDataFormats.Text))
        {
            e.AcceptedOperation = DataPackageOperation.Link;
            e.DragUIOverride.Caption = Text.Get("DropAdd");
        }
    }

    private async void OnDrop(object sender, DragEventArgs e)
    {
        var deferral = e.GetDeferral();
        try
        {
            if (e.DataView.Contains(StandardDataFormats.StorageItems))
            {
                var items = await e.DataView.GetStorageItemsAsync();
                if (items.OfType<StorageFile>().FirstOrDefault() is { } file)
                {
                    await ImportAsync(file.Path);
                }
                return;
            }
            string? value = null;
            if (e.DataView.Contains(StandardDataFormats.WebLink))
            {
                value = (await e.DataView.GetWebLinkAsync()).AbsoluteUri;
            }
            else if (e.DataView.Contains(StandardDataFormats.Text))
            {
                value = await e.DataView.GetTextAsync();
            }
            if (!string.IsNullOrWhiteSpace(value))
            {
                await _app.AddAsync(value.Trim());
            }
        }
        finally
        {
            deferral.Complete();
        }
    }
}
