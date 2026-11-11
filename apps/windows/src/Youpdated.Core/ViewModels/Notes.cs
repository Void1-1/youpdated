using System.Text.RegularExpressions;

namespace Youpdated.Core.ViewModels;

public enum NoteBlockKind
{
    Paragraph,
    Heading,
    Bullet,
    Code,
}

public enum NoteSpanKind
{
    Text,
    Bold,
    Italic,
    Code,
    Link,
}

public sealed record NoteSpan(NoteSpanKind Kind, string Text, string? Url = null);

public sealed record NoteBlock(NoteBlockKind Kind, IReadOnlyList<NoteSpan> Spans);

/// <summary>Release notes clipped for a card</summary>
public sealed record NotesPreview(IReadOnlyList<NoteBlock> Blocks, bool Clipped);

/// <summary>A card's expanded release notes</summary>
public static partial class Notes
{
    /// <summary>Blocks shown before "Show full notes"</summary>
    public const int PreviewBlocks = 6;

    public static IReadOnlyList<NoteBlock> Parse(string? markdown)
    {
        var blocks = new List<NoteBlock>();
        if (string.IsNullOrWhiteSpace(markdown))
        {
            return blocks;
        }
        var paragraph = new List<string>();
        var code = (List<string>?)null;

        void EndParagraph()
        {
            if (paragraph.Count > 0)
            {
                blocks.Add(new NoteBlock(NoteBlockKind.Paragraph, Inline(string.Join(' ', paragraph))));
                paragraph.Clear();
            }
        }

        foreach (var raw in markdown.Replace("\r\n", "\n").Split('\n'))
        {
            var line = raw.TrimEnd();
            if (line.TrimStart().StartsWith("```", StringComparison.Ordinal))
            {
                if (code is null)
                {
                    EndParagraph();
                    code = [];
                }
                else
                {
                    blocks.Add(new NoteBlock(NoteBlockKind.Code, [new NoteSpan(NoteSpanKind.Code, string.Join('\n', code))]));
                    code = null;
                }
                continue;
            }
            if (code is not null)
            {
                code.Add(raw);
                continue;
            }
            var trimmed = line.Trim();
            if (trimmed.Length == 0)
            {
                EndParagraph();
                continue;
            }
            if (HeadingPattern().Match(trimmed) is { Success: true } heading)
            {
                EndParagraph();
                blocks.Add(new NoteBlock(NoteBlockKind.Heading, Inline(heading.Groups[1].Value)));
                continue;
            }
            if (BulletPattern().Match(trimmed) is { Success: true } bullet)
            {
                EndParagraph();
                blocks.Add(new NoteBlock(NoteBlockKind.Bullet, Inline(bullet.Groups[1].Value)));
                continue;
            }
            paragraph.Add(trimmed);
        }
        EndParagraph();
        if (code is { Count: > 0 })
        {
            blocks.Add(new NoteBlock(NoteBlockKind.Code, [new NoteSpan(NoteSpanKind.Code, string.Join('\n', code))]));
        }
        return blocks;
    }

    /// <summary>The first blocks of the notes</summary>
    public static NotesPreview Preview(string? markdown, int blocks = PreviewBlocks)
    {
        var all = Parse(markdown);
        return all.Count <= blocks ? new NotesPreview(all, false) : new NotesPreview([.. all.Take(blocks)], true);
    }

    /// <summary>The first line of the notes as plain text, for a card's second line</summary>
    public static string? FirstLine(string? markdown)
    {
        var first = Parse(markdown).FirstOrDefault(block => block.Kind != NoteBlockKind.Code);
        return first is null ? null : string.Concat(first.Spans.Select(span => span.Text)).Trim() is { Length: > 0 } text ? text : null;
    }

    /// <summary>Links, code, bold, italics; anything else is text</summary>
    public static IReadOnlyList<NoteSpan> Inline(string text)
    {
        var spans = new List<NoteSpan>();
        var at = 0;
        foreach (Match match in InlinePattern().Matches(text))
        {
            if (match.Index > at)
            {
                spans.Add(new NoteSpan(NoteSpanKind.Text, text[at..match.Index]));
            }
            if (match.Groups["code"].Success)
            {
                spans.Add(new NoteSpan(NoteSpanKind.Code, match.Groups["code"].Value));
            }
            else if (match.Groups["label"].Success)
            {
                var url = match.Groups["href"].Value;
                spans.Add(IsWeb(url)
                    ? new NoteSpan(NoteSpanKind.Link, match.Groups["label"].Value, url)
                    : new NoteSpan(NoteSpanKind.Text, match.Groups["label"].Value));
            }
            else if (match.Groups["bare"].Success)
            {
                var url = match.Groups["bare"].Value.TrimEnd('.', ',', ';', ':', ')');
                spans.Add(new NoteSpan(NoteSpanKind.Link, url, url));
                var rest = match.Groups["bare"].Value[url.Length..];
                if (rest.Length > 0)
                {
                    spans.Add(new NoteSpan(NoteSpanKind.Text, rest));
                }
            }
            else if (match.Groups["bold"].Success)
            {
                spans.Add(new NoteSpan(NoteSpanKind.Bold, match.Groups["bold"].Value));
            }
            else if (match.Groups["italic"].Success)
            {
                spans.Add(new NoteSpan(NoteSpanKind.Italic, match.Groups["italic"].Value));
            }
            at = match.Index + match.Length;
        }
        if (at < text.Length)
        {
            spans.Add(new NoteSpan(NoteSpanKind.Text, text[at..]));
        }
        return spans;
    }

    public static bool IsWeb(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https";

    [GeneratedRegex(@"^#{1,6}\s+(.+?)\s*#*$")]
    private static partial Regex HeadingPattern();

    [GeneratedRegex(@"^(?:[-*+•]|\d+[.)])\s+(.+)$")]
    private static partial Regex BulletPattern();

    [GeneratedRegex(@"`(?<code>[^`]+)`|\[(?<label>[^\]]+)\]\((?<href>[^)\s]+)\)|(?<bare>https?://[^\s<>()]+[^\s<>]*)|\*\*(?<bold>[^*]+)\*\*|__(?<bold>[^_]+)__|(?<![\w*])\*(?<italic>[^*\s][^*]*)\*(?![\w*])|(?<![\w_])_(?<italic>[^_\s][^_]*)_(?![\w_])")]
    private static partial Regex InlinePattern();
}
