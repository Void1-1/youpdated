using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>Paste a link or name; the preview runs as you type, debounced</summary>
public sealed partial class AddPanel : UserControl
{
    private AppViewModel? _app;
    private bool _syncing;

    public AddPanel()
    {
        InitializeComponent();
        PriorityPicker.ShowName = true;
        PriorityPicker.Changed += level =>
        {
            if (Add is { } add)
            {
                add.Priority = level;
            }
        };
    }

    /// <summary>Followed, or Show in Following</summary>
    public event Action? Done;

    /// <summary>Import a file...</summary>
    public event Action? ImportRequested;

    private AddSourceViewModel? Add => _app?.Sources.Add;

    public void Attach(AppViewModel app)
    {
        _app = app;
        app.Sources.Add.PropertyChanged += OnAddChanged;
        Apply();
    }

    /// <summary>The big field of an empty window</summary>
    public void UseLargeField()
    {
        Box.FontSize = 20;
        Box.Padding = new Thickness(12, 10, 12, 10);
        Label.Visibility = Visibility.Collapsed;
    }

    public void FocusField()
    {
        Box.Focus(FocusState.Programmatic);
        Box.SelectionStart = Box.Text.Length;
    }

    /// <summary>An example chip can fill empty field</summary>
    public void Fill(string value)
    {
        Box.Text = value;
        FocusField();
    }

    private void OnAddChanged(object? sender, PropertyChangedEventArgs e) => DispatcherQueue.TryEnqueue(Apply);

    private void Apply()
    {
        if (Add is not { } add)
        {
            return;
        }
        _syncing = true;
        if (Box.Text != add.Value)
        {
            Box.Text = add.Value;
        }
        PriorityPicker.Priority = add.Priority;
        FillChoices(add);
        _syncing = false;

        Spinner.IsActive = add.Stage is AddStage.Previewing or AddStage.Adding;
        var preview = add.Preview;
        PreviewCard.Visibility = Bind.ShowIf(preview);
        if (preview is not null)
        {
            PreviewIcon.Target = new TargetKey(preview.Source, preview.Key);
            PreviewIcon.Display = preview.Display;
            PreviewName.Text = preview.Display;
            PreviewLine.Text = PreviewText(preview);
        }
        var error = add.Error ?? preview?.FetchError;
        ErrorText.Text = error ?? "";
        ErrorText.Visibility = Bind.ShowText(error);

        FollowButton.Content = Text.Get(add.AlreadyWatching ? "AddShowInFollowing" : "AddFollow");
        FollowButton.IsEnabled = add.CanAdd || add.AlreadyWatching;
        if (add.Stage == AddStage.Added)
        {
            Done?.Invoke();
        }
    }

    /// <summary>"4.5.1 · 2 days ago"</summary>
    private static string PreviewText(PreviewReply preview)
    {
        if (preview.Newest is not { } newest)
        {
            return preview.FetchError is null ? Text.Get("PreviewNothingYet") : Text.Label(preview.Source, preview.Key);
        }
        var lead = newest.Version is { Length: > 0 } version ? FeedText.DisplayVersion(version) : newest.Title;
        return newest.Published is { } published ? $"{lead} · {Text.Ago(published)}" : lead;
    }

    private void FillChoices(AddSourceViewModel add)
    {
        var sources = add.Choices;
        Choices.Visibility = Bind.Show(sources.Count > 0);
        if (!sources.SequenceEqual(Choices.Items.Select(item => (string)item.Tag)))
        {
            Choices.Items.Clear();
            foreach (var source in sources)
            {
                Choices.Items.Add(new SelectorBarItem { Text = Text.Source(source), Tag = source });
            }
        }
        Choices.SelectedItem = Choices.Items.FirstOrDefault(item => (string)item.Tag == add.Chosen);
    }

    private void OnTextChanged(object sender, TextChangedEventArgs e)
    {
        if (!_syncing && Add is { } add)
        {
            add.Value = Box.Text;
        }
    }

    private async void OnBoxKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key != VirtualKey.Enter || Add is not { } add)
        {
            return;
        }
        e.Handled = true;
        if (add.CanAdd)
        {
            await add.AddAsync();
        }
        else if (add.Stage is AddStage.Idle or AddStage.Failed)
        {
            await add.PreviewAsync();
        }
    }

    private async void OnChoiceChanged(SelectorBar sender, SelectorBarSelectionChangedEventArgs args)
    {
        if (!_syncing && sender.SelectedItem is { Tag: string source } && Add is { } add && add.Chosen != source)
        {
            await add.ChooseAsync(source);
        }
    }

    private async void OnFollow(object sender, RoutedEventArgs e)
    {
        if (Add is not { } add || _app is not { } app)
        {
            return;
        }
        if (add.AlreadyWatching && add.Preview is { } preview)
        {
            app.ShowInFollowing(new TargetKey(preview.Source, preview.Key));
            Done?.Invoke();
            return;
        }
        await add.AddAsync();
    }

    private void OnImport(object sender, RoutedEventArgs e) => ImportRequested?.Invoke();
}
