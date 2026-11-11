using System.Text.RegularExpressions;

namespace Youpdated.Core.ViewModels;

/// <summary>
/// Ctrl+V in the window with a link opens Add</summary>
public static partial class PasteToAdd
{
    private const int MaxLength = 2048;

    /// <summary>The value to preview</summary>
    public static string? Accept(string? clipboard)
    {
        var text = clipboard?.Trim();
        if (string.IsNullOrEmpty(text) || text.Length > MaxLength || text.Any(char.IsWhiteSpace))
        {
            return null;
        }
        if (YoupdatedLink.Parse(text) is YoupdatedLink.Add { Value: { } inner })
        {
            return Accept(inner);
        }
        if (Uri.TryCreate(text, UriKind.Absolute, out var uri) && uri.IsAbsoluteUri && !uri.IsFile)
        {
            return uri.Scheme is "http" or "https" && !string.IsNullOrEmpty(uri.Host) ? text : null;
        }
        return HostAndPath().IsMatch(text) ? text : null;
    }

    [GeneratedRegex(@"^(?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}/\S+$", RegexOptions.IgnoreCase)]
    private static partial Regex HostAndPath();
}
