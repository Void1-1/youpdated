namespace Youpdated.Core.ViewModels;

public static class SourceLabels
{
    private static readonly Dictionary<string, string> Forges = new(StringComparer.OrdinalIgnoreCase)
    {
        ["github.com"] = "GitHub",
        ["gitlab.com"] = "GitLab",
        ["codeberg.org"] = "Codeberg",
    };

    private static readonly Dictionary<string, string> Sources = new()
    {
        ["git"] = "Git",
        ["steam"] = "Steam",
        ["itch"] = "itch.io",
        ["npm"] = "npm",
        ["pypi"] = "PyPI",
        ["homebrew"] = "Homebrew",
        ["youtube"] = "YouTube",
        ["feed"] = "Feed",
    };

    /// <summary>The label for a target: forge or host for <c>git</c>, host for <c>feed</c>, else the source's</summary>
    public static string Label(string source, string? key, string browsers = "Browsers")
    {
        switch (source)
        {
            case "git" when Host(key) is { } host:
                return Forges.GetValueOrDefault(host, host);
            case "feed" when Host(key) is { } host:
                return host;
            case "browser":
                return browsers;
        }
        return Sources.GetValueOrDefault(source, source);
    }

    public static string? Host(string? key)
    {
        if (string.IsNullOrWhiteSpace(key))
        {
            return null;
        }
        if (Uri.TryCreate(key, UriKind.Absolute, out var uri) && !string.IsNullOrEmpty(uri.Host))
        {
            return uri.Host.ToLowerInvariant();
        }
        var slash = key.IndexOf('/');
        var first = slash > 0 ? key[..slash] : key;
        return first.Contains('.') ? first.ToLowerInvariant() : null;
    }
}
