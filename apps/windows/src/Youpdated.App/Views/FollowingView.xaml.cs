using System.Collections.ObjectModel;
using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>Following: watched targets by source, with filters</summary>
public sealed partial class FollowingView : UserControl
{
    private readonly ObservableCollection<object> _rows = [];
    private readonly Dictionary<string, FollowingHeader> _headers = [];
    private AppViewModel? _app;
    private bool _syncing;

    public FollowingView()
    {
        InitializeComponent();
        Repeater.ItemsSource = _rows;
    }

    public void Attach(AppViewModel app)
    {
        _app = app;
        app.Following.BrowsersLabel = Text.Get("SourceBrowsers");
        app.Following.PropertyChanged += OnFollowingChanged;
        Refresh();
    }

    /// <summary>Ctrl+F</summary>
    public void FocusFilter()
    {
        FilterBox.Focus(FocusState.Keyboard);
        FilterBox.SelectAll();
    }

    private void OnFollowingChanged(object? sender, PropertyChangedEventArgs args)
    {
        if (args.PropertyName is nameof(FollowingViewModel.Layout) or nameof(FollowingViewModel.Filter))
        {
            DispatcherQueue.TryEnqueue(Refresh);
        }
    }

    private void Refresh()
    {
        if (_app?.Following is not { } following)
        {
            return;
        }
        if (FilterBox.Text != following.Filter)
        {
            _syncing = true;
            FilterBox.Text = following.Filter;
            _syncing = false;
        }
        var layout = following.Layout;
        StarredRow.Row = layout.Starred;
        StarredRow.Visibility = Bind.ShowIf(layout.Starred);
        SelfMissingRow.Visibility = Bind.Show(layout.SelfMissing && string.IsNullOrWhiteSpace(following.Filter));

        var wanted = new List<object>();
        foreach (var group in layout.Groups)
        {
            var title = group.Label.ToUpper(System.Globalization.CultureInfo.CurrentCulture);
            if (!_headers.TryGetValue(title, out var header))
            {
                header = _headers[title] = new FollowingHeader(title);
            }
            wanted.Add(header);
            wanted.AddRange(group);
        }
        Rows.Sync(_rows, wanted);
        NothingText.Visibility = Bind.Show(wanted.Count == 0 && layout.Starred is null);
    }

    private void OnFilterChanged(object sender, TextChangedEventArgs e)
    {
        if (!_syncing && _app?.Following is { } following)
        {
            following.Filter = FilterBox.Text;
        }
    }

    private async void OnWatchSelf(object sender, RoutedEventArgs e)
    {
        if (_app is { } app)
        {
            await app.AnswerSelfWatchOfferAsync(watch: true);
        }
    }
}
