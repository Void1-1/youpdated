using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App;

internal static class Dialogs
{
    /// <summary>Remove, with "Also forget its history" (<c>rm --wipe</c>)</summary>
    public static async Task<bool?> RemoveTargetAsync(XamlRoot root, Target target)
    {
        var wipe = new CheckBox { Content = Text.Get("RemoveWipe") };
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Format("RemoveTitle", target.Display),
            Content = new StackPanel
            {
                Spacing = 12,
                Children =
                {
                    wipe,
                },
            },
            PrimaryButtonText = Text.Get("RemoveButton"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary ? wipe.IsChecked == true : null;
    }

    /// <summary>What an import adds and skips, and why</summary>
    public static async Task<bool> ImportAsync(XamlRoot root, ImportViewModel import)
    {
        var preview = import.Preview;
        var body = new StackPanel { Spacing = 8 };
        if (import.Error is { } failed)
        {
            body.Children.Add(new InfoBar { Severity = InfoBarSeverity.Error, Message = failed, IsOpen = true, IsClosable = false });
        }
        if (preview is not null)
        {
            Section(body, Text.Format("ImportAdd", import.ToAdd), preview.Add.SelectMany(g => g.Entries.Select(e => $"{e}  ·  {Text.Source(g.Source)}")));
            Section(body, Text.Get("ImportAlready"), preview.Already.SelectMany(g => g.Entries));
            Section(body, Text.Get("ImportSkipped"), preview.Skipped.Select(s => $"{s.What}: {s.Why}"));
            foreach (var warning in preview.Warnings)
            {
                body.Children.Add(new InfoBar { Severity = InfoBarSeverity.Warning, Message = warning, IsOpen = true, IsClosable = false });
            }
        }
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get("ImportTitle"),
            Content = new ScrollViewer { Content = body, MaxHeight = 420 },
            PrimaryButtonText = import.ToAdd > 0 ? Text.Get("ImportButton") : "",
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = import.ToAdd > 0 ? ContentDialogButton.Primary : ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>Settings → Fast mode</summary>
    public static async Task<bool> ConfirmFastModeAsync(XamlRoot root)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get("FastModeTitle"),
            Content = new TextBlock { Text = Text.Get("FastModeBody"), TextWrapping = TextWrapping.Wrap, MaxWidth = 420 },
            PrimaryButtonText = Text.Get("FastModeButton"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>Asks before Youpdated identifies itself in requests on an encrypted, proxied setup</summary>
    public static async Task<bool> ConfirmUserAgentAsync(XamlRoot root)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get("UserAgentConfirmTitle"),
            Content = new TextBlock { Text = Text.Get("UserAgentConfirmBody"), TextWrapping = TextWrapping.Wrap, MaxWidth = 420 },
            PrimaryButtonText = Text.Get("UserAgentConfirmButton"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>Settings → Remove Youpdated from this PC</summary>
    public static async Task<bool?> RemoveFromThisPcAsync(XamlRoot root)
    {
        var data = new CheckBox { Content = Text.Get("RemovePcData") };
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get("RemovePcTitle"),
            Content = new StackPanel
            {
                Spacing = 12,
                MaxWidth = 420,
                Children =
                {
                    new TextBlock { Text = Text.Get("RemovePcBody"), TextWrapping = TextWrapping.Wrap },
                    data,
                },
            },
            PrimaryButtonText = Text.Get("RemovePcButton"),
            CloseButtonText = Text.Get("Cancel"),
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary ? data.IsChecked == true : null;
    }

    /// <summary>The folder is left after removal</summary>
    public static async Task RemovedAsync(XamlRoot root, string folder, bool dataRemoved)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = Text.Get("RemovedTitle"),
            Content = new TextBlock
            {
                Text = Text.Format(dataRemoved ? "RemovedBody" : "RemovedBodyDataKept", folder),
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            },
            CloseButtonText = Text.Get("RemovedClose"),
        };
        await dialog.ShowAsync();
    }

    private static void Section(StackPanel body, string header, IEnumerable<string> lines)
    {
        var list = lines.ToList();
        if (list.Count == 0)
        {
            return;
        }
        body.Children.Add(new TextBlock { Text = header, Style = (Style)Application.Current.Resources["BodyStrongTextBlockStyle"] });
        foreach (var line in list)
        {
            var text = new TextBlock { Text = line, TextWrapping = TextWrapping.Wrap };
            Themed.Brush(text, "TextFillColorSecondaryBrush", (block, brush) => block.Foreground = brush);
            body.Children.Add(text);
        }
    }
}
