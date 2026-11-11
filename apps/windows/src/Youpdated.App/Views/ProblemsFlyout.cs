using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>source errors, failed webhooks and commands, engine crashes</summary>
internal sealed class ProblemsFlyout : Flyout
{
    private readonly AppViewModel _app;
    private readonly StackPanel _list = new() { Spacing = 12 };

    public ProblemsFlyout(AppViewModel app)
    {
        _app = app;
        var copy = new Button { Content = Text.Get("ProblemsCopy") };
        copy.Click += (_, _) =>
        {
            var package = new DataPackage();
            package.SetText(_app.Problems.Details(Describe));
            Clipboard.SetContent(package);
        };
        var header = new Grid
        {
            ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } },
        };
        header.Children.Add(new TextBlock
        {
            Text = Text.Get("ProblemsTitle"),
            Style = (Style)Application.Current.Resources["SubtitleTextBlockStyle"],
            VerticalAlignment = VerticalAlignment.Center,
        });
        Grid.SetColumn(copy, 1);
        header.Children.Add(copy);
        Content = new StackPanel
        {
            Width = 380,
            Spacing = 16,
            Children =
            {
                header,
                new ScrollViewer { Content = _list, MaxHeight = 420, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            },
        };
        Opening += (_, _) => Fill();
        _app.Problems.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(ProblemsViewModel.Problems) && IsOpen)
            {
                Fill();
            }
        };
    }

    private static string Describe(Problem problem) => $"{Bind.ProblemTitle(problem)}: {problem.Message}";

    private void Fill()
    {
        _list.Children.Clear();
        foreach (var problem in _app.Problems.Problems)
        {
            var row = new Grid
            {
                ColumnSpacing = 12,
                ColumnDefinitions =
                {
                    new ColumnDefinition { Width = GridLength.Auto },
                    new ColumnDefinition(),
                    new ColumnDefinition { Width = GridLength.Auto },
                },
            };
            var icon = new FontIcon
            {
                Glyph = Bind.ProblemGlyph(problem.Kind),
                FontSize = 16,
                VerticalAlignment = VerticalAlignment.Top,
                Margin = new Thickness(0, 2, 0, 0),
            };
            Themed.Brush(icon, "SystemFillColorCautionBrush", (glyph, brush) => glyph.Foreground = brush);
            var message = new TextBlock
            {
                Text = problem.Message,
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
                MaxLines = 4,
                TextTrimming = TextTrimming.CharacterEllipsis,
            };
            Themed.Brush(message, "TextFillColorSecondaryBrush", (text, brush) => text.Foreground = brush);
            var words = new StackPanel
            {
                Spacing = 2,
                Children =
                {
                    new TextBlock
                    {
                        Text = Bind.ProblemTitle(problem),
                        Style = (Style)Application.Current.Resources["BodyStrongTextBlockStyle"],
                        TextTrimming = TextTrimming.CharacterEllipsis,
                    },
                    message,
                },
            };
            Grid.SetColumn(words, 1);
            row.Children.Add(icon);
            row.Children.Add(words);
            if (AppViewModel.CanRetry(problem))
            {
                var retry = new Button { Content = Text.Get("ProblemsRetry"), VerticalAlignment = VerticalAlignment.Top };
                retry.Click += async (_, _) =>
                {
                    retry.IsEnabled = false;
                    await _app.RetryAsync(problem);
                    retry.IsEnabled = true;
                };
                Grid.SetColumn(retry, 2);
                row.Children.Add(retry);
            }
            _list.Children.Add(row);
        }
        if (_list.Children.Count == 0)
        {
            var none = new TextBlock { Text = Text.Get("ProblemsNone") };
            Themed.Brush(none, "TextFillColorSecondaryBrush", (text, brush) => text.Foreground = brush);
            _list.Children.Add(none);
        }
    }
}
