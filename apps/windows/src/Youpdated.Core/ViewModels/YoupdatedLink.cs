namespace Youpdated.Core.ViewModels;

/// <summary>
/// <c>youpdated://add?url=…</c>, <c>youpdated://open</c>, and <c>youpdated://check</c>: from links, Jump List, a second launch
/// </summary>
public abstract record YoupdatedLink
{
    public const string Scheme = "youpdated";

    public sealed record Add(string? Value) : YoupdatedLink;

    public sealed record Open : YoupdatedLink;

    public sealed record Check : YoupdatedLink;

    public static YoupdatedLink? Parse(string text)
    {
        if (!Uri.TryCreate(text.Trim(), UriKind.Absolute, out var uri)
            || !uri.Scheme.Equals(Scheme, StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }
        // youpdated://add?... in the host; youpdated:add?... in the path
        var verb = (string.IsNullOrEmpty(uri.Host) ? uri.AbsolutePath : uri.Host).Trim('/').ToLowerInvariant();
        return verb switch
        {
            "add" => new Add(UrlParameter(uri)),
            "open" or "" => new Open(),
            "check" => new Check(),
            _ => null,
        };
    }

    /// <summary>The first link among command-line arguments</summary>
    public static YoupdatedLink? FromArguments(IEnumerable<string> arguments) =>
        arguments.Select(Parse).OfType<YoupdatedLink>().FirstOrDefault();

    /// <summary>
    /// The <c>url</c> param: everything after it, so a link that wasn't encoded keeps its own <c>?</c>, <c>&amp;</c>, and <c>#</c>
    /// </summary>
    private static string? UrlParameter(Uri uri)
    {
        var query = uri.Query.TrimStart('?');
        var at = query.StartsWith("url=", StringComparison.OrdinalIgnoreCase)
            ? 0
            : query.IndexOf("&url=", StringComparison.OrdinalIgnoreCase) is var found and >= 0 ? found + 1 : -1;
        if (at < 0)
        {
            return null;
        }
        var decoded = Uri.UnescapeDataString(query[(at + "url=".Length)..] + uri.Fragment);
        return decoded.Length > 0 ? decoded : null;
    }
}
