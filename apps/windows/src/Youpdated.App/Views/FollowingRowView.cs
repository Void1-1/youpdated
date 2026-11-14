using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.System;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>A Following row: name, latest version (or latest post date), priorities, status dot</summary>
public sealed class FollowingRowView : UserControl
{
    public static readonly DependencyProperty RowProperty = DependencyProperty.Register(
        nameof(Row), typeof(FollowingRow), typeof(FollowingRowView), new PropertyMetadata(null, (d, _) => ((FollowingRowView)d).Apply()));

    private readonly IconView _icon = new() { Size = 24 };
    private readonly FontIcon _star = new() { Glyph = "\uE735", FontSize = 12, Visibility = Visibility.Collapsed };
    private readonly TextBlock _name = new() { TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock _latest = new() { VerticalAlignment = VerticalAlignment.Center, MinWidth = 72 };
    private readonly PriorityMenu _priority = new();
    private readonly Ellipse _ok = new() { Width = 8, Height = 8, VerticalAlignment = VerticalAlignment.Center };
    private readonly FontIcon _warning = new() { Glyph = "\uE7BA", FontSize = 14, Visibility = Visibility.Collapsed };
    private readonly TextBlock _notYet = new() { VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed };
    private readonly Grid _surface;

    public FollowingRowView()
    {
        IsTabStop = true;
        UseSystemFocusVisuals = true;
        Themed.Brush(_star, "AccentTextFillColorPrimaryBrush", (icon, brush) => icon.Foreground = brush);
        Themed.Brush(_latest, "TextFillColorSecondaryBrush", (text, brush) => text.Foreground = brush);
        Themed.Brush(_ok, "SystemFillColorSuccessBrush", (dot, brush) => dot.Fill = brush);
        Themed.Brush(_warning, "SystemFillColorCautionBrush", (icon, brush) => icon.Foreground = brush);
        Themed.Brush(_notYet, "TextFillColorTertiaryBrush", (text, brush) => text.Foreground = brush);
        _notYet.Style = (Style)Application.Current.Resources["CaptionTextBlockStyle"];
        _notYet.Text = Text.Get("SourceNotYet");
        _priority.Changed += async level =>
        {
            if (Row is { } row && App is { } app)
            {
                await app.Following.SetPriorityAsync(row.Target, level);
            }
        };

        var name = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { _star, _name } };
        var status = new Grid { Width = 96, Children = { _ok, _warning, _notYet } };
        _ok.HorizontalAlignment = _warning.HorizontalAlignment = _notYet.HorizontalAlignment = HorizontalAlignment.Right;
        _surface = new Grid
        {
            Padding = new Thickness(12, 6, 12, 6),
            ColumnSpacing = 10,
            CornerRadius = (CornerRadius)Application.Current.Resources["ControlCornerRadius"],
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
            ColumnDefinitions =
            {
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition(),
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition { Width = GridLength.Auto },
            },
        };
        Place(_icon, 0);
        Place(name, 1);
        Place(_latest, 2);
        Place(_priority, 3);
        Place(status, 4);
        Content = _surface;

        ContextFlyout = new MenuFlyout();
        ((MenuFlyout)ContextFlyout).Opening += OnMenuOpening;
        Tapped += OnTapped;
        KeyDown += OnKeyDown;
        PointerEntered += (_, _) => _surface.Background = Themed.Find(this, "SubtleFillColorSecondaryBrush");
        PointerExited += (_, _) => _surface.Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
    }

    public FollowingRow? Row
    {
        get => (FollowingRow?)GetValue(RowProperty);
        set => SetValue(RowProperty, value);
    }

    private static AppViewModel? App => AppController.Current?.App;

    private void Place(FrameworkElement element, int column)
    {
        Grid.SetColumn(element, column);
        _surface.Children.Add(element);
    }

    private void Apply()
    {
        if (Row is not { } row)
        {
            return;
        }
        var target = row.Target;
        _icon.Target = target.AsKey();
        _icon.Display = target.Display;
        _name.Text = target.Display;
        _star.Visibility = Bind.Show(target.Starred);
        // The latest version, or the date of the latest post for sources without versions
        _latest.Text = row.LatestVersion ?? (row.LatestDate is { } posted ? Text.ShortDate(posted) : "");
        _priority.Priority = target.Priority;
        _warning.Visibility = Bind.Show(row.HasError);
        _notYet.Visibility = Bind.Show(row.NotCheckedYet);
        _ok.Visibility = Bind.Show(!row.HasError && !row.NotCheckedYet);
        ToolTipService.SetToolTip(_warning, target.LastError);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(this, target.Display);
    }

    /// <summary>Selecting a row filters Updates to that target</summary>
    private void OnTapped(object sender, TappedRoutedEventArgs e)
    {
        if (Row is { } row && App is { } app && !InButton(e.OriginalSource as DependencyObject))
        {
            app.Following.Select(row.Target);
        }
    }

    private bool InButton(DependencyObject? element)
    {
        for (var node = element; node is not null && node != this; node = VisualTreeHelper.GetParent(node))
        {
            if (node is Microsoft.UI.Xaml.Controls.Primitives.ButtonBase)
            {
                return true;
            }
        }
        return false;
    }

    private async void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (Row is not { } row || App is not { } app)
        {
            return;
        }
        switch (e.Key)
        {
            case VirtualKey.Enter:
            case VirtualKey.Space:
                e.Handled = true;
                app.Following.Select(row.Target);
                break;
            case VirtualKey.Delete when !row.Target.Starred:
                e.Handled = true;
                await StopFollowingAsync(row.Target);
                break;
        }
    }

    private void OnMenuOpening(object? sender, object e)
    {
        if (sender is not MenuFlyout menu || Row is not { } row || App is not { } app)
        {
            return;
        }
        menu.Items.Clear();
        var show = new MenuFlyoutItem { Text = Text.Get("MenuShowInUpdates"), Icon = new FontIcon { Glyph = "\uE8F1" } };
        show.Click += (_, _) => app.Following.Select(row.Target);
        menu.Items.Add(show);
        if (app.Sources.CanFilter)
        {
            var filters = new MenuFlyoutItem { Text = Text.Get("MenuFilters"), Icon = new FontIcon { Glyph = "\uE71C" } };
            filters.Click += async (_, _) =>
            {
                if (XamlRoot is { } root)
                {
                    await FiltersDialog.ShowAsync(root, app.Sources.FiltersFor(row.Target));
                }
            };
            menu.Items.Add(filters);
        }
        var stop = new MenuFlyoutItem { Text = Text.Get("MenuStopFollowing"), Icon = new FontIcon { Glyph = "\uE74D" } };
        stop.Click += async (_, _) => await StopFollowingAsync(row.Target);
        menu.Items.Add(new MenuFlyoutSeparator());
        menu.Items.Add(stop);
    }

    /// <summary>Ask first, with "Also forget its history" (<c>rm --wipe</c>)</summary>
    private async Task StopFollowingAsync(Target target)
    {
        if (App is { } app && XamlRoot is { } root && await Dialogs.RemoveTargetAsync(root, target) is { } wipe)
        {
            await app.Following.RemoveAsync(target, wipe);
        }
    }
}
