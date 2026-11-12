using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;
using Windows.System;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>
/// A card in the feed.
/// Click, Space, or Enter expands it, marking it read. Ctrl+Enter opens the link, Ctrl+U toggles read.
/// </summary>
public sealed partial class FeedCardView : UserControl
{
    public static readonly DependencyProperty CardProperty = DependencyProperty.Register(
        nameof(Card), typeof(FeedCard), typeof(FeedCardView), new PropertyMetadata(null, OnCardChanged));

    private bool _dotShown = true;
    private bool _hovered;

    public FeedCardView()
    {
        InitializeComponent();
        PriorityPicker.ShowName = true;
        PriorityPicker.Changed += async level =>
        {
            if (Card is { } card && Feed is { } feed)
            {
                await feed.SetPriorityAsync(card, level);
            }
        };
        // Once hovered, the card's color is set here and no longer follows the theme by itself
        ActualThemeChanged += (_, _) => ColorSurface();
        Loaded += (_, _) =>
        {
            Motion.AnimateLayoutMoves(this);
            Motion.FadeOnShowAndHide(Details, Motion.NotesDelay);
            if (Ancestor<FeedView>() is { } feed && Surface.Shadow is null)
            {
                Motion.CastShadow(Surface, feed.ShadowSurface);
            }
        };
        Unloaded += (_, _) =>
        {
            if (Card is { } card)
            {
                card.PropertyChanged -= OnCardPropertyChanged;
            }
        };
    }

    public FeedCard? Card
    {
        get => (FeedCard?)GetValue(CardProperty);
        set => SetValue(CardProperty, value);
    }

    private static FeedViewModel? Feed => AppController.Current?.App.Feed;

    private static void OnCardChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var view = (FeedCardView)sender;
        if (args.OldValue is FeedCard old)
        {
            old.PropertyChanged -= view.OnCardPropertyChanged;
        }
        if (args.NewValue is FeedCard card)
        {
            card.PropertyChanged += view.OnCardPropertyChanged;
        }
        view._dotShown = (args.NewValue as FeedCard)?.ShowsUnread ?? false;
        view.Apply(animateLead: false);
    }

    private void OnCardPropertyChanged(object? sender, PropertyChangedEventArgs args) =>
        DispatcherQueue.TryEnqueue(() =>
        {
            switch (args.PropertyName)
            {
                case nameof(FeedCard.ShowsUnread):
                    UpdateDot();
                    break;
                case nameof(FeedCard.IsExpanded):
                case nameof(FeedCard.ShowFullNotes):
                case nameof(FeedCard.Notes):
                    Apply(animateLead: false);
                    break;
                default:
                    Apply(animateLead: Card?.LeadChanged == true);
                    break;
            }
        });

    private void Apply(bool animateLead)
    {
        if (Card is not { } card)
        {
            return;
        }
        var compact = card.IsCompact && !card.IsExpanded;
        Full.Visibility = Bind.Show(!compact);
        Compact.Visibility = Bind.Show(compact);
        Surface.Opacity = card.IsDimmed && !card.IsExpanded ? 0.65 : 1;
        HighBell.Visibility = Bind.Show(card.ShowsHighPriority);
        ToolTipService.SetToolTip(HighBell, Text.PriorityMeans(card.Priority));
        AutomationName(card);

        CardIcon.Target = card.Target;
        CardIcon.Display = card.Display;
        NameText.Text = card.Display;
        LeadText.Text = card.LeadText;
        LineText.Text = card.Line ?? "";
        LineText.Visibility = Bind.ShowText(card.Line);
        FooterText.Text = Footer(card, card.IsExpanded);

        CompactIcon.Target = card.Target;
        CompactIcon.Display = card.Display;
        CompactName.Text = card.Display;
        CompactLead.Text = card.LeadText;
        CompactFooter.Text = $"{Text.Label(card.Source, card.Target.Key)} · {Text.When(card.When, Times)}";

        Details.Visibility = Bind.Show(card.IsExpanded);
        if (card.IsExpanded)
        {
            FillDetails(card);
        }
        UpdateDot(immediate: true);
        if (animateLead)
        {
            Motion.NumberChanged(LeadText);
        }
    }

    private static TimeStyle Times => Feed?.View.Times ?? TimeStyle.Relative;

    /// <summary>"GitHub · 2 hr ago · +2 more"; expanded, the full date and time</summary>
    private static string Footer(FeedCard card, bool expanded)
    {
        var when = expanded ? Text.DateAndTime(card.When) : Text.When(card.When, Times);
        var footer = $"{Text.Label(card.Source, card.Target.Key)} · {when}";
        return card.More > 0 ? $"{footer} · {Text.Format("CardMore", card.More)}" : footer;
    }

    private void FillDetails(FeedCard card)
    {
        var notes = card.Notes;
        NotesRenderer.Render(NotesText, card.HasNotes
            ? notes
            : new NotesPreview([new NoteBlock(NoteBlockKind.Paragraph, [new NoteSpan(NoteSpanKind.Link, card.Lead.Title, card.Lead.Url)])], false));
        FullNotesButton.Visibility = Bind.Show(notes.Clipped);

        AlsoNewList.Children.Clear();
        foreach (var item in card.AlsoNew)
        {
            var row = new HyperlinkButton
            {
                Content = $"{item.Version ?? item.Title}  ·  {Text.When(item.When, Times)}",
                Padding = new Thickness(4, 2, 4, 2),
            };
            row.Click += async (_, _) =>
            {
                if (Feed is { } feed)
                {
                    await feed.OpenItemAsync(item);
                }
            };
            AlsoNewList.Children.Add(row);
        }
        AlsoNewPanel.Visibility = Bind.Show(card.AlsoNew.Count > 0);

        ReadButton.Content = Text.Get(card.ShowsUnread ? "MenuMarkRead" : "MenuMarkUnread");
        PriorityPicker.Priority = card.Priority;
    }

    private void UpdateDot(bool immediate = false)
    {
        if (Card is not { } card)
        {
            return;
        }
        AccentEdge.Visibility = Bind.Show(card.ShowsAccentEdge);
        if (card.ShowsUnread == _dotShown && !immediate)
        {
            return;
        }
        var fading = _dotShown && !card.ShowsUnread && !immediate;
        _dotShown = card.ShowsUnread;
        foreach (var dot in new UIElement[] { Dot, CompactDot })
        {
            if (fading)
            {
                Motion.FadeTo(dot, 0f, card.FadeDelay);
            }
            else
            {
                Motion.VisualOf(dot).Opacity = card.ShowsUnread ? 1f : 0f;
            }
        }
        if (card.IsExpanded)
        {
            ReadButton.Content = Text.Get(card.ShowsUnread ? "MenuMarkRead" : "MenuMarkUnread");
        }
    }

    private void AutomationName(FeedCard card) =>
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(this,
            $"{card.Display}, {card.LeadText}" + (card.ShowsUnread ? $", {Text.Get("CardUnread")}" : ""));

    private async void OnTapped(object sender, TappedRoutedEventArgs e)
    {
        // Buttons and links inside the details do their own thing
        if (Card is not { } card || Feed is not { } feed || Inside<ButtonBase>(e.OriginalSource as DependencyObject) || Inside<RichTextBlock>(e.OriginalSource as DependencyObject))
        {
            return;
        }
        e.Handled = true;
        Focus(FocusState.Pointer);
        await feed.ToggleAsync(card);
    }

    private async void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (Card is not { } card || Feed is not { } feed || !ReferenceEquals(FocusManager.GetFocusedElement(XamlRoot), this))
        {
            return;
        }
        var control = Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Control)
            .HasFlag(Windows.UI.Core.CoreVirtualKeyStates.Down);
        switch (e.Key)
        {
            case VirtualKey.Enter when control:
                e.Handled = true;
                await feed.OpenAsync(card);
                break;
            case VirtualKey.U when control:
                e.Handled = true;
                await feed.ToggleReadAsync(card);
                break;
            case VirtualKey.Space:
            case VirtualKey.Enter:
                e.Handled = true;
                await feed.ToggleAsync(card);
                break;
            case VirtualKey.Escape when card.IsExpanded:
                e.Handled = true;
                feed.Collapse();
                break;
        }
    }

    private void OnPointerEntered(object sender, PointerRoutedEventArgs e)
    {
        _hovered = true;
        ColorSurface();
        Motion.Lift(Surface, lifted: true);
    }

    private void OnPointerExited(object sender, PointerRoutedEventArgs e)
    {
        _hovered = false;
        ColorSurface();
        Motion.Lift(Surface, lifted: false);
    }

    private void ColorSurface()
    {
        var key = _hovered ? "CardBackgroundFillColorSecondaryBrush" : "CardBackgroundFillColorDefaultBrush";
        if (Themed.Find(this, key) is { } brush)
        {
            Surface.Background = brush;
        }
    }

    private async void OnOpen(object sender, RoutedEventArgs e)
    {
        if (Card is { } card && Feed is { } feed)
        {
            await feed.OpenAsync(card);
        }
    }

    private async void OnToggleRead(object sender, RoutedEventArgs e)
    {
        if (Card is { } card && Feed is { } feed)
        {
            await feed.ToggleReadAsync(card);
        }
    }

    private void OnFullNotes(object sender, RoutedEventArgs e)
    {
        if (Card is { } card)
        {
            card.ShowFullNotes = true;
        }
    }

    /// <summary>Open, Mark read / unread, Priority, Snooze 1 hour, Copy link, Show in Following, Delete, Stop following</summary>
    private void OnMenuOpening(object? sender, object e)
    {
        if (sender is not MenuFlyout menu || Card is not { } card || Feed is not { } feed || AppController.Current?.App is not { } app)
        {
            return;
        }
        menu.Items.Clear();
        menu.Items.Add(Item("MenuOpen", "\uE8A7", async () => await feed.OpenAsync(card)));
        menu.Items.Add(card.ShowsUnread
            ? Item("MenuMarkRead", "\uE8C3", async () => await feed.MarkReadAsync(card))
            : Item("MenuMarkUnread", "\uE715", async () => await feed.MarkUnreadAsync(card)));
        var priority = new MenuFlyoutSubItem { Text = Text.Get("MenuPriority"), Icon = PriorityMenu.Icon(card.Priority) };
        foreach (var item in PriorityMenu.Items(card.Priority, async level => await feed.SetPriorityAsync(card, level)))
        {
            priority.Items.Add(item);
        }
        menu.Items.Add(priority);
        menu.Items.Add(Item("MenuSnooze", "\uE916", async () => await feed.SnoozeAsync(card)));
        menu.Items.Add(Item("MenuCopyLink", "\uE71B", () =>
        {
            var package = new DataPackage();
            package.SetText(card.Lead.Url);
            Clipboard.SetContent(package);
        }));
        menu.Items.Add(new MenuFlyoutSeparator());
        menu.Items.Add(Item("MenuShowInFollowing", "\uE71D", () => app.ShowInFollowing(card.Target)));
        var deleteItem = Item("MenuDelete", "\uE738", async () => await feed.DeleteAsync(card));
        if (card.Items.Count > 1)
        {
            deleteItem.Text = Text.Format("MenuDeleteMany", card.Items.Count);
        }
        menu.Items.Add(deleteItem);
        if (app.Sources.Targets.FirstOrDefault(t => t.AsKey() == card.Target) is { } target)
        {
            menu.Items.Add(Item("MenuStopFollowing", "\uE74D", async () =>
            {
                if (XamlRoot is { } root && await Dialogs.RemoveTargetAsync(root, target) is { } wipe)
                {
                    await app.Sources.RemoveAsync(target, wipe);
                }
            }));
        }
    }

    private static MenuFlyoutItem Item(string key, string glyph, Action click)
    {
        var item = new MenuFlyoutItem { Text = Text.Get(key), Icon = new FontIcon { Glyph = glyph } };
        item.Click += (_, _) => click();
        return item;
    }

    private T? Ancestor<T>() where T : DependencyObject
    {
        for (var node = VisualTreeHelper.GetParent(this); node is not null; node = VisualTreeHelper.GetParent(node))
        {
            if (node is T found)
            {
                return found;
            }
        }
        return null;
    }

    private bool Inside<T>(DependencyObject? element) where T : DependencyObject
    {
        for (var node = element; node is not null && node != this; node = VisualTreeHelper.GetParent(node))
        {
            if (node is T)
            {
                return true;
            }
        }
        return false;
    }
}
