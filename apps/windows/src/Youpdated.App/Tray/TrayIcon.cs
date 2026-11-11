using System.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using H.NotifyIcon;
using H.NotifyIcon.Core;
using Microsoft.UI.Xaml.Controls;
using Windows.UI.ViewManagement;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Tray;

/// <summary>Notification-area icon and menu</summary>
internal sealed class TrayIcon : IDisposable
{
    private readonly AppViewModel _app;
    private readonly Action<AppPage> _show;
    private readonly Action _quit;
    private readonly TaskbarIcon _icon;
    private readonly UISettings _ui = new();
    private System.Drawing.Icon? _drawn;

    public TrayIcon(AppViewModel app, Action<AppPage> show, Action quit)
    {
        _app = app;
        _show = show;
        _quit = quit;
        _icon = new TaskbarIcon
        {
            ContextMenuMode = ContextMenuMode.PopupMenu,
            NoLeftClickDelay = true,
            LeftClickCommand = new RelayCommand(() => _show(AppPage.Updates)),
        };
        _app.PropertyChanged += OnChanged;
        // Accent and theme changes redraw the icon
        _ui.ColorValuesChanged += (_, _) => _icon.DispatcherQueue.TryEnqueue(() =>
        {
            Platform.Native.FollowSystemThemeInMenus();
            Redraw();
        });
        Redraw();
        RebuildMenu();
        _icon.ForceCreate(enablesEfficiencyMode: false);
    }

    public void Dispose()
    {
        _app.PropertyChanged -= OnChanged;
        _icon.Dispose();
        _drawn?.Dispose();
    }

    private void OnChanged(object? sender, PropertyChangedEventArgs args)
    {
        switch (args.PropertyName)
        {
            case nameof(AppViewModel.TrayState):
            case nameof(AppViewModel.Unread):
            case nameof(AppViewModel.IsLocked):
                Redraw();
                RebuildMenu();
                break;
            case nameof(AppViewModel.RecentUnread):
            case nameof(AppViewModel.AvailableUpdate):
                RebuildMenu();
                break;
        }
    }

    private void Redraw()
    {
        var accent = _ui.GetColorValue(UIColorType.Accent);
        var icon = TrayIconRenderer.Render(
            _app.TrayState, _app.Unread, System.Drawing.Color.FromArgb(accent.A, accent.R, accent.G, accent.B));
        _icon.UpdateIcon(icon);
        _drawn?.Dispose();
        _drawn = icon;
        _icon.ToolTipText = _app.TrayState switch
        {
            TrayState.Checking => Text.Get("TrayChecking"),
            TrayState.Locked => Text.Get("TrayLocked"),
            TrayState.Paused => Text.Get("TrayPaused"),
            TrayState.Problems => Text.Get("TrayProblems"),
            _ when _app.Unread > 0 => Text.NewUpdates(_app.Unread),
            _ => "Youpdated",
        };
    }

    /// <summary>The menu: what's new, Open, Check now, Pause, update info, Settings, Quit</summary>
    private void RebuildMenu()
    {
        var menu = new MenuFlyout();
        if (_app.IsLocked)
        {
            menu.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayLocked"), IsEnabled = false });
            menu.Items.Add(Item(Text.Get("TrayUnlock"), () => _show(AppPage.Updates)));
        }
        else if (_app.Unread > 0)
        {
            menu.Items.Add(new MenuFlyoutItem { Text = Text.NewUpdates(_app.Unread), IsEnabled = false });
            foreach (var item in _app.RecentUnread)
            {
                var entry = new MenuFlyoutItem { Text = $"{item.Display}\t{item.Title}" };
                entry.Click += async (_, _) => await _app.Inbox.OpenItemAsync(item);
                menu.Items.Add(entry);
            }
        }
        else
        {
            menu.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayNothingNew"), IsEnabled = false });
        }
        menu.Items.Add(new MenuFlyoutSeparator());

        menu.Items.Add(Item(Text.Get("TrayOpen"), () => _show(AppPage.Updates)));
        menu.Items.Add(new MenuFlyoutItem
        {
            Text = Text.Get("TrayCheckNow"),
            Command = _app.CheckNowCommand,
            IsEnabled = !_app.IsLocked && !_app.IsChecking,
        });
        if (_app.Scheduler.IsPaused)
        {
            menu.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayResume"), Command = _app.ResumeCommand });
        }
        else
        {
            var pause = new MenuFlyoutSubItem { Text = Text.Get("TrayPause"), IsEnabled = !_app.IsLocked };
            pause.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayPauseHour"), Command = _app.PauseForHourCommand });
            pause.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayPauseTomorrow"), Command = _app.PauseUntilTomorrowCommand });
            pause.Items.Add(new MenuFlyoutItem { Text = Text.Get("TrayPauseResumed"), Command = _app.PauseUntilResumedCommand });
            menu.Items.Add(pause);
        }
        menu.Items.Add(new MenuFlyoutSeparator());

        if (_app.AvailableUpdate is { } update)
        {
            menu.Items.Add(Item(Text.UpdateAvailable(update) + "…", () => _show(AppPage.Updates)));
        }
        menu.Items.Add(Item(Text.Get("TraySettings"), () => _show(AppPage.Settings)));
        menu.Items.Add(Item(Text.Get("TrayQuit"), _quit));
        _icon.ContextFlyout = menu;
    }

    private static MenuFlyoutItem Item(string text, Action click)
    {
        var item = new MenuFlyoutItem { Text = text };
        item.Click += (_, _) => click();
        return item;
    }
}
