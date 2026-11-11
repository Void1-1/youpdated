using System.Globalization;
using Youpdated.Core.Contract;

namespace Youpdated.Core.ViewModels;

public sealed record AppUpdate(Version Version, string ItemId, string ReleaseUrl)
{
    public const string Repository = "Void1-1/youpdated";

    /// <summary>The self-watch target's key</summary>
    public const string SelfKey = "github.com/" + Repository;

    public string DisplayVersion => Version.Build > 0
        ? Version.ToString(3)
        : Version.ToString(2);

    public string FileName => $"Youpdated-{Version.ToString(3)}-Windows-x64.zip";

    public Uri DownloadUrl => new(
        $"https://github.com/{Repository}/releases/download/{TagPrefix}{Version.ToString(3)}/{FileName}");

    public static AppUpdate? From(InboxItem item, Version current)
    {
        if (!item.Starred || ParseTag(item.Version ?? item.Title) is not { } version || version <= Normalize(current))
        {
            return null;
        }
        return new AppUpdate(version, item.Id, item.Url);
    }

    /// <summary>The newest update in <paramref name="items"/>, if any is newer than <paramref name="current"/></summary>
    public static AppUpdate? Newest(IEnumerable<InboxItem> items, Version current) =>
        items.Select(item => From(item, current)).OfType<AppUpdate>().MaxBy(update => update.Version);


    public const string TagPrefix = "app-windows-v";


    public static Version? ParseTag(string tag)
    {
        var text = tag.Trim();
        if (!text.StartsWith(TagPrefix, StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }
        text = text[TagPrefix.Length..];
        var parts = text.Split('.');
        if (parts.Length is < 2 or > 3 || !parts.All(part => part.Length > 0 && part.All(char.IsAsciiDigit)))
        {
            return null;
        }
        var numbers = parts.Select(part => int.Parse(part, CultureInfo.InvariantCulture)).ToArray();
        return new Version(numbers[0], numbers[1], numbers.Length == 3 ? numbers[2] : 0);
    }

    private static Version Normalize(Version version) =>
        new(version.Major, version.Minor, Math.Max(version.Build, 0));
}

/// <summary>Fetches an update's .zip into a folder</summary>
public sealed class UpdateDownloader(HttpClient http)
{
    public async Task<string> DownloadAsync(
        AppUpdate update, string folder, IProgress<double>? progress = null, CancellationToken cancellationToken = default)
    {
        Directory.CreateDirectory(folder);
        var path = Path.Combine(folder, update.FileName);
        var partial = path + ".download";
        using (var response = await http.GetAsync(update.DownloadUrl, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false))
        {
            response.EnsureSuccessStatusCode();
            var total = response.Content.Headers.ContentLength;
            await using var source = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            await using var target = File.Create(partial);
            var buffer = new byte[81920];
            long copied = 0;
            int read;
            while ((read = await source.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
            {
                await target.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
                copied += read;
                if (total is > 0)
                {
                    progress?.Report((double)copied / total.Value);
                }
            }
        }
        File.Move(partial, path, overwrite: true);
        progress?.Report(1);
        return path;
    }
}
