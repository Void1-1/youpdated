using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Youpdated.Core.Contract;

namespace Youpdated.App.Views;

/// <summary>Priority control for Following, expanded cards, Add</summary>
public sealed class PriorityMenu : Button
{
    /// <summary>Segoe Fluent Icons: Ringer, and RingerSilent for Inbox</summary>
    public const string Bell = "\uEA8F";
    public const string SilentBell = "\uE7ED";

    private readonly FontIcon _icon = new() { FontSize = 14 };
    private readonly TextBlock _name = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly FontIcon _chevron = new() { Glyph = "\uE70D", FontSize = 10, Margin = new Thickness(2, 0, 0, 0) };
    private Priority _priority = Priority.Normal;

    public PriorityMenu()
    {
        Style = (Style)Application.Current.Resources["SubtleButtonStyle"];
        Padding = new Thickness(6, 4, 6, 4);
        Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { _icon, _name, _chevron } };
        var menu = new MenuFlyout { Placement = FlyoutPlacementMode.BottomEdgeAlignedRight };
        menu.Opening += (_, _) =>
        {
            menu.Items.Clear();
            foreach (var item in Items(_priority, level =>
            {
                Priority = level;
                Changed?.Invoke(level);
            }))
            {
                menu.Items.Add(item);
            }
        };
        Flyout = menu;
        ShowName = false;
        _icon.Loaded += (_, _) => ColorIcon();
        _icon.ActualThemeChanged += (_, _) => ColorIcon();
        Apply();
    }

    /// <summary>Picked from the menu</summary>
    public event Action<Priority>? Changed;

    public Priority Priority
    {
        get => _priority;
        set
        {
            _priority = value;
            Apply();
        }
    }

    public bool ShowName
    {
        get => _name.Visibility == Visibility.Visible;
        set
        {
            _name.Visibility = Bind.Show(value);
            _chevron.Visibility = Bind.Show(value);
        }
    }

    /// <summary>The bell for a priority</summary>
    public static FontIcon Icon(Priority priority, double size = 14)
    {
        var icon = new FontIcon { Glyph = Glyph(priority), FontSize = size };
        Themed.Brush(icon, BrushKey(priority), (bell, brush) => bell.Foreground = brush);
        return icon;
    }

    /// <summary>"High: Notify immediately", with bell, for this and context menus</summary>
    public static IEnumerable<MenuFlyoutItemBase> Items(Priority current, Action<Priority> pick)
    {
        foreach (var level in new[] { Priority.High, Priority.Normal, Priority.Low })
        {
            var item = new RadioMenuFlyoutItem
            {
                Text = Text.Format("PriorityChoice", Text.Priority(level), Text.PriorityMeans(level)),
                Icon = Icon(level),
                GroupName = "Priority",
                IsChecked = current == level,
            };
            item.Click += (_, _) => pick(level);
            yield return item;
        }
    }

    private void Apply()
    {
        _icon.Glyph = Glyph(_priority);
        ColorIcon();
        _name.Text = Text.Priority(_priority);
        ToolTipService.SetToolTip(this, Text.PriorityMeans(_priority));
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(this,
            Text.Format("PriorityChoice", Text.Priority(_priority), Text.PriorityMeans(_priority)));
    }

    private static string Glyph(Priority priority) => priority == Priority.Low ? SilentBell : Bell;

    private static string BrushKey(Priority priority) =>
        priority == Priority.High ? "AccentTextFillColorPrimaryBrush" : "TextFillColorSecondaryBrush";

    private void ColorIcon()
    {
        if (Themed.Find(_icon, BrushKey(_priority)) is { } brush)
        {
            _icon.Foreground = brush;
        }
    }
}
