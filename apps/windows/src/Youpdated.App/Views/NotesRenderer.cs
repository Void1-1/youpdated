using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>Release notes parsed by Core into a RichTextBlock</summary>
internal static class NotesRenderer
{
    public static void Render(RichTextBlock target, NotesPreview notes)
    {
        target.Blocks.Clear();
        foreach (var block in notes.Blocks)
        {
            var paragraph = new Paragraph { Margin = new Thickness(0, 0, 0, 6) };
            switch (block.Kind)
            {
                case NoteBlockKind.Heading:
                    paragraph.FontWeight = FontWeights.SemiBold;
                    paragraph.Margin = new Thickness(0, 6, 0, 4);
                    break;
                case NoteBlockKind.Bullet:
                    paragraph.TextIndent = -14;
                    paragraph.Margin = new Thickness(14, 0, 0, 2);
                    paragraph.Inlines.Add(new Run { Text = "•  " });
                    break;
                case NoteBlockKind.Code:
                    paragraph.FontFamily = new FontFamily("Cascadia Mono, Consolas");
                    break;
            }
            foreach (var span in block.Spans)
            {
                paragraph.Inlines.Add(Inline(span));
            }
            target.Blocks.Add(paragraph);
        }
    }

    private static Inline Inline(NoteSpan span)
    {
        switch (span.Kind)
        {
            case NoteSpanKind.Link when span.Url is { } url && Uri.TryCreate(url, UriKind.Absolute, out var uri):
                var link = new Hyperlink { NavigateUri = uri };
                link.Inlines.Add(new Run { Text = span.Text });
                ToolTipService.SetToolTip(link, url);
                return link;
            case NoteSpanKind.Bold:
                return new Run { Text = span.Text, FontWeight = FontWeights.SemiBold };
            case NoteSpanKind.Italic:
                return new Run { Text = span.Text, FontStyle = Windows.UI.Text.FontStyle.Italic };
            case NoteSpanKind.Code:
                return new Run { Text = span.Text, FontFamily = new FontFamily("Cascadia Mono, Consolas") };
            default:
                return new Run { Text = span.Text };
        }
    }
}
