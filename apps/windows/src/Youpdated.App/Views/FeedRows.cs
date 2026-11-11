using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>A section's heading in the feed</summary>
public sealed record FeedHeader(string Title);

/// <summary>A source's heading in Following</summary>
public sealed record FollowingHeader(string Title);

/// <summary>The feed's sections in one list of headings and cards</summary>
internal static class Rows
{
    public static string SectionTitle(FeedSectionKey key) => key.Kind switch
    {
        FeedSectionKind.Today => Text.Get("SectionToday"),
        FeedSectionKind.Yesterday => Text.Get("SectionYesterday"),
        FeedSectionKind.ThisWeek => Text.Get("SectionThisWeek"),
        FeedSectionKind.Earlier => Text.Get("SectionEarlier"),
        FeedSectionKind.Priority => Text.Priority(Enum.Parse<Priority>(key.Value!)),
        // Already a label: GitHub, Steam, ...
        FeedSectionKind.Source => key.Value!,
        // Group by none: no heading
        _ => "",
    };

    public static List<object> Flatten(IEnumerable<FeedSection> sections, Dictionary<string, FeedHeader> headers)
    {
        var rows = new List<object>();
        foreach (var section in sections)
        {
            var title = SectionTitle(section.Key).ToUpper(System.Globalization.CultureInfo.CurrentCulture);
            if (title.Length > 0)
            {
                if (!headers.TryGetValue(title, out var header))
                {
                    header = headers[title] = new FeedHeader(title);
                }
                rows.Add(header);
            }
            rows.AddRange(section);
        }
        return rows;
    }

    /// <summary>Make <paramref name="rows"/> equal <paramref name="wanted"/> by moving, inserting, removing</summary>
    public static void Sync(ObservableCollection<object> rows, IReadOnlyList<object> wanted)
    {
        for (var i = 0; i < wanted.Count; i++)
        {
            if (i < rows.Count && ReferenceEquals(rows[i], wanted[i]))
            {
                continue;
            }
            var at = -1;
            for (var j = i + 1; j < rows.Count; j++)
            {
                if (ReferenceEquals(rows[j], wanted[i]))
                {
                    at = j;
                    break;
                }
            }
            if (at >= 0)
            {
                rows.Move(at, i);
            }
            else
            {
                rows.Insert(i, wanted[i]);
            }
        }
        while (rows.Count > wanted.Count)
        {
            rows.RemoveAt(rows.Count - 1);
        }
    }
}

/// <summary>Headings and cards (or Following rows) get templates</summary>
public sealed partial class RowTemplateSelector : DataTemplateSelector
{
    public DataTemplate? Header { get; set; }

    public DataTemplate? Card { get; set; }

    public DataTemplate? Row { get; set; }

    protected override DataTemplate? SelectTemplateCore(object item) => item switch
    {
        FeedHeader or FollowingHeader => Header,
        FeedCard => Card,
        FollowingRow => Row,
        _ => null,
    };

    protected override DataTemplate? SelectTemplateCore(object item, DependencyObject container) => SelectTemplateCore(item);
}
