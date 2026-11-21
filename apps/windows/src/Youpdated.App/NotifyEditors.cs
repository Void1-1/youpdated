using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

internal static class NotifyText
{
    public static string Format(string? format) => format switch
    {
        null => Text.Get("WebhookFormatAuto"),
        "json" => Text.Get("WebhookFormatJson"),
        "discord" => "Discord",
        "slack" => "Slack",
        "ntfy" => "ntfy",
        _ => format,
    };

    /// <summary>Lowest priority for an endpoint</summary>
    public static string Sends(Priority priority) => Text.Get(priority switch
    {
        Priority.High => "NotifySendHigh",
        Priority.Low => "NotifySendLow",
        _ => "NotifySendNormal",
    });

    public static string Rules(Priority priority, bool digest) =>
        digest ? Sends(priority) : Text.Format("NotifyIgnoresDigest", Sends(priority));

    public static string Summary(Webhook hook) => $"{Format(hook.Format)} · {Rules(hook.Priority, hook.Digest)}";

    /// <summary>Just the host</summary>
    public static string Host(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri) && uri.Host.Length > 0 ? uri.Host : url;
}

/// <summary>Settings → Notifications</summary>
internal static class NotifyEditors
{
    /// <summary>Saves through <paramref name="settings"/></summary>
    public static async Task EditWebhookAsync(XamlRoot root, SettingsViewModel settings, int? index, Webhook current)
    {
        var isNew = index is null;
        var url = new TextBox
        {
            Header = Text.Get("WebhookUrl"),
            PlaceholderText = "https://ntfy.sh/my-topic",
            Text = current.Url,
            IsSpellCheckEnabled = false,
        };
        var format = new ComboBox { Header = Text.Get("WebhookFormat"), MinWidth = 220 };
        if (isNew || current.Format is null)
        {
            format.Items.Add(new ComboBoxItem { Content = NotifyText.Format(null), Tag = null });
        }
        foreach (var name in WebhookFormats.All)
        {
            format.Items.Add(new ComboBoxItem { Content = NotifyText.Format(name), Tag = name });
        }
        format.SelectedIndex = Math.Max(0, format.Items.Cast<ComboBoxItem>().ToList()
            .FindIndex(item => item.Tag as string == current.Format));
        var priority = PriorityBox(current.Priority);
        var digest = DigestBox(current.Digest);

        var headers = new StackPanel { Spacing = 6 };
        void AddHeader(string name, string value)
        {
            var row = new Grid { ColumnSpacing = 6 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var nameBox = new TextBox { Text = name, PlaceholderText = "Authorization", IsSpellCheckEnabled = false };
            var valueBox = new TextBox { Text = value, PlaceholderText = Text.Get("WebhookHeaderValue"), IsSpellCheckEnabled = false };
            var remove = new Button { Content = new FontIcon { Glyph = "", FontSize = 12 } };
            ToolTipService.SetToolTip(remove, Text.Get("WebhookRemoveHeader"));
            remove.Click += (_, _) => headers.Children.Remove(row);
            Grid.SetColumn(valueBox, 1);
            Grid.SetColumn(remove, 2);
            row.Children.Add(nameBox);
            row.Children.Add(valueBox);
            row.Children.Add(remove);
            headers.Children.Add(row);
        }
        foreach (var header in current.Headers)
        {
            AddHeader(header.Name, header.Value);
        }
        var addHeader = new HyperlinkButton { Content = Text.Get("WebhookAddHeader") };
        addHeader.Click += (_, _) => AddHeader("", "");

        Webhook Read() => new(
            url.Text.Trim(),
            (format.SelectedItem as ComboBoxItem)?.Tag as string,
            [.. headers.Children.OfType<Grid>()
                .Select(row => row.Children.OfType<TextBox>().ToList())
                .Select(boxes => new Header(boxes[0].Text.Trim(), boxes[1].Text))
                .Where(header => header.Name.Length > 0)],
            (Priority)((ComboBoxItem)priority.SelectedItem).Tag,
            digest.IsChecked == true);

        var error = Error();
        var (test, result) = TestRow(() => settings.TestWebhookAsync(Read()), () => Read().Url.Length > 0);
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get(isNew ? "WebhookAddTitle" : "WebhookEditTitle"),
            Content = new ScrollViewer
            {
                MaxHeight = 520,
                Content = new StackPanel
                {
                    Spacing = 12,
                    MinWidth = 420,
                    Children =
                    {
                        url, format, priority, digest,
                        new TextBlock { Text = Text.Get("WebhookHeaders"), Style = (Style)Application.Current.Resources["BodyStrongTextBlockStyle"] },
                        headers, addHeader,
                        Caption(Text.Get("WebhookSecretNote")),
                        new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12, Children = { test, result } },
                        error,
                    },
                },
            },
            PrimaryButtonText = Text.Get("NotifySave"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Primary,
        };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            var hook = Read();
            var problem = hook.Url.Length == 0 ? Text.Get("WebhookNeedsUrl") : await settings.SaveWebhookAsync(index, hook);
            if (problem is not null)
            {
                args.Cancel = true;
                error.Message = problem;
                error.IsOpen = true;
            }
            deferral.Complete();
        };
        await dialog.ShowAsync();
    }

    public static async Task EditCommandAsync(XamlRoot root, SettingsViewModel settings, NotifyCommand? current)
    {
        var run = new TextBox
        {
            Header = Text.Get("CommandRun"),
            PlaceholderText = "msg * \"{label}: {title}\"",
            Text = current?.Run ?? "",
            IsSpellCheckEnabled = false,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas"),
        };
        var priority = PriorityBox(current?.Priority ?? Priority.Normal);
        var digest = DigestBox(current?.Digest ?? true);

        NotifyCommand Read() => new(run.Text.Trim(), (Priority)((ComboBoxItem)priority.SelectedItem).Tag, digest.IsChecked == true);

        var error = Error();
        var (test, result) = TestRow(() => settings.TestCommandAsync(Read()), () => Read().Run.Length > 0);
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get(current is null ? "CommandSetTitle" : "CommandEditTitle"),
            Content = new StackPanel
            {
                Spacing = 12,
                MinWidth = 420,
                Children =
                {
                    run,
                    Caption(Text.Get("CommandPlaceholders")),
                    priority, digest,
                    new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12, Children = { test, result } },
                    error,
                },
            },
            PrimaryButtonText = Text.Get("NotifySave"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Primary,
        };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            var command = Read();
            var problem = command.Run.Length == 0 ? Text.Get("CommandNeedsRun") : await settings.SaveCommandAsync(command);
            if (problem is not null)
            {
                args.Cancel = true;
                error.Message = problem;
                error.IsOpen = true;
            }
            deferral.Complete();
        };
        await dialog.ShowAsync();
    }

    private static ComboBox PriorityBox(Priority current)
    {
        var box = new ComboBox { Header = Text.Get("NotifySend"), MinWidth = 220 };
        foreach (var priority in new[] { Priority.High, Priority.Normal, Priority.Low })
        {
            box.Items.Add(new ComboBoxItem { Content = NotifyText.Sends(priority), Tag = priority });
        }
        box.SelectedIndex = current switch { Priority.High => 0, Priority.Low => 2, _ => 1 };
        return box;
    }

    private static CheckBox DigestBox(bool on)
    {
        var box = new CheckBox { Content = Text.Get("NotifyDigest"), IsChecked = on };
        ToolTipService.SetToolTip(box, Text.Get("NotifyDigestMeans"));
        return box;
    }

    private static InfoBar Error() => new() { Severity = InfoBarSeverity.Error, IsClosable = false, IsOpen = false };

    private static TextBlock Caption(string text)
    {
        var block = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Style = (Style)Application.Current.Resources["CaptionTextBlockStyle"] };
        Themed.Brush(block, "TextFillColorSecondaryBrush", (b, brush) => b.Foreground = brush);
        return block;
    }

    /// <summary>Send test, and what came of it</summary>
    private static (Button Test, TextBlock Result) TestRow(Func<Task<string?>> send, Func<bool> ready)
    {
        var result = new TextBlock { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center, MaxWidth = 300, IsTextSelectionEnabled = true };
        var test = new Button { Content = Text.Get("NotifyTest") };
        test.Click += async (_, _) =>
        {
            if (!ready())
            {
                return;
            }
            test.IsEnabled = false;
            result.Text = Text.Get("NotifyTesting");
            var problem = await send();
            result.Text = problem ?? Text.Get("NotifyTestSent");
            test.IsEnabled = true;
        };
        return (test, result);
    }
}
