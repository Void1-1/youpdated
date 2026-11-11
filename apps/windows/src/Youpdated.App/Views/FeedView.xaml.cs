using System.Collections.ObjectModel;
using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>Updates: "Since …", the self-update card, then card sections</summary>
public sealed partial class FeedView : UserControl
{
    private const double TopSlack = 48;

    private readonly ObservableCollection<object> _rows = [];
    private readonly Dictionary<string, FeedHeader> _headers = [];
    private AppViewModel? _app;

    public FeedView()
    {
        InitializeComponent();
        Repeater.ItemsSource = _rows;
    }

    public void Attach(AppViewModel app)
    {
        _app = app;
        app.Feed.Text = FeedWords();
        app.Feed.PropertyChanged += OnFeedChanged;
        Refresh();
    }

    /// <summary>The translated words the heading uses</summary>
    public static FeedText FeedWords() => new()
    {
        Culture = System.Globalization.CultureInfo.CurrentCulture,
        NewUpdatesOne = Text.Get("NewUpdatesOne"),
        NewUpdatesMany = Text.Get("NewUpdatesMany"),
        UpToDate = Text.Get("UpToDate"),
        SinceThisMorning = Text.Get("SinceThisMorning"),
        SinceThisAfternoon = Text.Get("SinceThisAfternoon"),
        SinceThisEvening = Text.Get("SinceThisEvening"),
        SinceYesterday = Text.Get("SinceYesterday"),
        SinceDay = Text.Get("SinceDay"),
        Checked = Text.Get("CheckedAt"),
        NotChecked = Text.Get("NotChecked"),
        CheckingStarted = Text.Get("CheckingStarted"),
        CheckingProgress = Text.Get("CheckingProgress"),
        PausedUntil = Text.Get("PausedUntil"),
        Paused = Text.Get("PausedIndefinitely"),
        Separator = " · ",
    };

    /// <summary>Behind the cards, for hover shadows</summary>
    public UIElement ShadowSurface => ShadowReceiver;

    public void FocusFirstCard()
    {
        for (var i = 0; i < _rows.Count; i++)
        {
            if (_rows[i] is FeedCard && Repeater.TryGetElement(i) is Control card)
            {
                card.Focus(FocusState.Keyboard);
                return;
            }
        }
    }

    private void OnFeedChanged(object? sender, PropertyChangedEventArgs args)
    {
        switch (args.PropertyName)
        {
            case nameof(FeedViewModel.Sections):
            case nameof(FeedViewModel.SelfUpdate):
            case nameof(FeedViewModel.HeadingTitle):
            case nameof(FeedViewModel.HeadingLine):
            case nameof(FeedViewModel.View):
            case nameof(FeedViewModel.TargetFilter):
            case nameof(FeedViewModel.SourceFilter):
                DispatcherQueue.TryEnqueue(Refresh);
                break;
        }
    }

    private void Refresh()
    {
        if (_app?.Feed is not { } feed)
        {
            return;
        }
        HeadingTitle.Text = feed.HeadingTitle;
        FillLine(feed.HeadingLine);

        var filter = feed.TargetFilter?.Display ?? feed.SourceFilter;
        FilterChip.Visibility = Bind.ShowText(filter);
        FilterText.Text = filter is null ? "" : Text.Format("FeedShowing", filter);

        UpdateCard.Visibility = Bind.ShowIf(feed.SelfUpdate);
        UpdateText.Text = feed.SelfUpdate is { } update ? Text.UpdateAvailable(update) : "";

        Rows.Sync(_rows, Rows.Flatten(feed.Sections, _headers));
    }

    private void FillLine(IReadOnlyList<HeadingPart> parts)
    {
        HeadingLine.Children.Clear();
        for (var i = 0; i < parts.Count; i++)
        {
            if (i > 0)
            {
                // Margins, not spaces: a TextBlock leaves its trailing space out of its width
                var dot = Secondary("·");
                dot.Margin = new Thickness(5, 0, 5, 0);
                HeadingLine.Children.Add(dot);
            }
            var part = parts[i];
            if (part.Kind == HeadingPartKind.Paused)
            {
                var resume = new HyperlinkButton { Content = part.Text, Padding = new Thickness(0) };
                ToolTipService.SetToolTip(resume, Text.Get("PausedResumeTip"));
                resume.Click += (_, _) => _app?.Resume();
                HeadingLine.Children.Add(resume);
            }
            else
            {
                HeadingLine.Children.Add(Secondary(part.Text));
            }
        }
    }

    private static TextBlock Secondary(string text)
    {
        var block = new TextBlock { Text = text };
        Themed.Brush(block, "TextFillColorSecondaryBrush", (b, brush) => b.Foreground = brush);
        return block;
    }

    private void OnElementPrepared(ItemsRepeater sender, ItemsRepeaterElementPreparedEventArgs args)
    {
        if (_app?.Feed is not { } feed || _rows.ElementAtOrDefault(args.Index) is not FeedCard card)
        {
            return;
        }
        // Once per check, only when not scrolled down
        if (feed.NewCards.Contains(card.Key) && Scroller.VerticalOffset <= TopSlack)
        {
            Motion.SlideIn(args.Element);
        }
    }

    private void OnClearFilter(object sender, RoutedEventArgs e)
    {
        if (_app?.Feed is { } feed)
        {
            feed.TargetFilter = null;
            feed.SourceFilter = null;
        }
    }

    private async void OnDownload(object sender, RoutedEventArgs e)
    {
        if (_app is not { } app || app.Feed.SelfUpdate is not { } update)
        {
            return;
        }
        DownloadButton.IsEnabled = false;
        try
        {
            await app.DownloadUpdateAsync(update);
        }
        catch (HttpRequestException)
        {
            // The release page has the file
            new Platform.Shell().OpenUrl(update.ReleaseUrl);
        }
        finally
        {
            DownloadButton.IsEnabled = true;
        }
    }
}
