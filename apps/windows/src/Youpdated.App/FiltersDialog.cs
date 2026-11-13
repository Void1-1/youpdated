using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.App.Views;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

/// <summary>Which of a followed item's versions are reported: <c>filters.get</c>, <c>.preview</c>, and <c>.set</c></summary>
internal static class FiltersDialog
{
    private static readonly (Bump Value, string Key)[] Bumps =
    [
        (Bump.Any, "BumpAny"), (Bump.Patch, "BumpPatch"), (Bump.Minor, "BumpMinor"), (Bump.Major, "BumpMajor"),
    ];

    public static async Task ShowAsync(XamlRoot root, FiltersViewModel filters)
    {
        var busy = new ProgressBar { IsIndeterminate = true };
        var notice = new InfoBar { IsClosable = false };
        var show = new RadioButtons { Header = Text.Get("FiltersShow") };
        show.Items.Add(Text.Get("FiltersEvery"));
        show.Items.Add(Text.Get("FiltersOnly"));
        var patterns = new StackPanel { Spacing = 2 };
        var custom = new TextBox
        {
            Header = Text.Get("FiltersCustom"),
            PlaceholderText = "v3.12.*",
            IsSpellCheckEnabled = false,
        };
        ToolTipService.SetToolTip(custom, Text.Get("FiltersCustomHelp"));
        var only = new StackPanel { Spacing = 8, Margin = new Thickness(28, 0, 0, 0), Children = { patterns, custom } };
        var skip = new CheckBox { Content = Text.Get("FiltersSkip") };
        var bump = new ComboBox { Header = Text.Get("FiltersReport"), HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var (value, key) in Bumps)
        {
            bump.Items.Add(new ComboBoxItem { Content = Text.Get(key), Tag = value });
        }
        var min = new TextBox { Header = Text.Get("FiltersMin"), PlaceholderText = Text.Get("FiltersMinAny") };
        var form = new StackPanel { Spacing = 12, Children = { show, only, skip, bump, min } };
        var fits = new TextBlock { TextWrapping = TextWrapping.Wrap };
        Themed.Brush(fits, "TextFillColorSecondaryBrush", (text, brush) => text.Foreground = brush);
        var again = new HyperlinkButton { Content = Text.Get("FiltersListAgain"), Padding = new Thickness(0) };
        var problem = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = false };

        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Format("FiltersTitle", filters.Target.Starred ? "Youpdated" : filters.Target.Display),
            Content = new ScrollViewer
            {
                MaxHeight = 520,
                Content = new StackPanel
                {
                    Spacing = 12,
                    Width = 400,
                    Children = { busy, notice, form, fits, again, problem },
                },
            },
            PrimaryButtonText = Text.Get("FiltersSave"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Primary,
            IsPrimaryButtonEnabled = false,
        };

        var offered = "";

        void Sync()
        {
            var ready = !filters.Loading && filters.LoadError is null;
            var editable = ready && filters.IsEditable && !filters.Saving;
            busy.Visibility = Bind.Show(filters.Loading || filters.Saving);
            notice.Message = filters.LoadError ?? filters.Saved?.Reason ?? "";
            notice.Severity = filters.LoadError is null ? InfoBarSeverity.Informational : InfoBarSeverity.Error;
            notice.IsOpen = notice.Message.Length > 0;
            form.Visibility = Bind.Show(ready);

            show.SelectedIndex = filters.LimitsVersions ? 1 : 0;
            only.Visibility = Bind.Show(filters.LimitsVersions);
            if (custom.Text != filters.Custom)
            {
                custom.Text = filters.Custom;
            }
            skip.IsChecked = filters.SkipPrereleases;
            var inherited = filters.Saved?.SkipPrereleasesInherited == true;
            ToolTipService.SetToolTip(skip, Text.Get(inherited ? "FiltersSkipInherited" : "FiltersSkipHelp"));
            bump.SelectedIndex = Array.FindIndex(Bumps, choice => choice.Value == filters.Bump);
            if (min.Text != filters.Min)
            {
                min.Text = filters.Min;
            }

            var listed = string.Join("\n", filters.Offered.Select(pattern => $"{pattern.Pattern}\t{pattern.Count}"));
            if (listed != offered)
            {
                offered = listed;
                patterns.Children.Clear();
                foreach (var pattern in filters.Offered)
                {
                    var box = new CheckBox
                    {
                        Content = $"{pattern.Label}   {Text.Format("FiltersRecent", pattern.Count)}",
                        Tag = pattern.Pattern,
                        MinHeight = 28,
                    };
                    box.Click += (_, _) =>
                    {
                        filters.SetTicked(pattern.Pattern, box.IsChecked == true);
                        _ = filters.PreviewAsync();
                    };
                    patterns.Children.Add(box);
                }
            }
            foreach (var box in patterns.Children.OfType<CheckBox>())
            {
                box.IsChecked = box.Tag is string pattern && filters.IsTicked(pattern);
                box.IsEnabled = editable;
            }

            show.IsEnabled = custom.IsEnabled = bump.IsEnabled = min.IsEnabled = editable;
            skip.IsEnabled = editable && !inherited;
            fits.Text = Text.Fits(filters);
            again.Visibility = Bind.Show(filters.Preview is not null);
            problem.Message = filters.Problem ?? "";
            problem.IsOpen = filters.Problem is not null;
            dialog.IsPrimaryButtonEnabled = filters.CanSave;
        }

        show.SelectionChanged += (_, _) =>
        {
            if (show.SelectedIndex >= 0 && filters.LimitsVersions != (show.SelectedIndex == 1))
            {
                filters.LimitsVersions = show.SelectedIndex == 1;
                _ = filters.PreviewAsync();
            }
        };
        custom.TextChanged += (_, _) =>
        {
            if (custom.Text != filters.Custom)
            {
                filters.Custom = custom.Text;
                _ = filters.PreviewAsync();
            }
        };
        skip.Click += (_, _) =>
        {
            filters.SkipPrereleases = skip.IsChecked == true;
            _ = filters.PreviewAsync();
        };
        bump.SelectionChanged += (_, _) =>
        {
            if (bump.SelectedItem is ComboBoxItem { Tag: Bump value } && value != filters.Bump)
            {
                filters.Bump = value;
                _ = filters.PreviewAsync();
            }
        };
        min.TextChanged += (_, _) =>
        {
            if (min.Text != filters.Min)
            {
                filters.Min = min.Text;
                _ = filters.PreviewAsync();
            }
        };
        again.Click += async (_, _) =>
        {
            again.IsEnabled = false;
            await filters.PreviewAsync(refresh: true);
            again.IsEnabled = true;
        };
        // Save refusals keep the dialog open
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try
            {
                args.Cancel = !await filters.SaveAsync();
            }
            finally
            {
                deferral.Complete();
            }
        };

        void OnChanged(object? sender, PropertyChangedEventArgs args) => dialog.DispatcherQueue.TryEnqueue(Sync);

        filters.PropertyChanged += OnChanged;
        Sync();
        _ = filters.LoadAsync();
        try
        {
            await dialog.ShowAsync();
        }
        finally
        {
            filters.PropertyChanged -= OnChanged;
        }
    }
}
