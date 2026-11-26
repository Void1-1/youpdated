namespace Youpdated.Core.ViewModels;

/// <summary>The running app's version plus any pre-release label on a beta</summary>
/// <remarks>
/// <see cref="Version"/> holds numbers only, and the assembly version drops a beta's label.
/// </remarks>
public sealed record InstalledVersion
{
    public InstalledVersion(Version number, string? prerelease = null)
    {
        Number = new Version(number.Major, number.Minor, Math.Max(number.Build, 0));
        Prerelease = string.IsNullOrWhiteSpace(prerelease) ? null : prerelease.Trim();
    }

    public Version Number { get; }

    public string? Prerelease { get; }

    public bool IsPrerelease => Prerelease is not null;

    /// <summary><c>0.11.0</c>, or <c>0.11.0-beta.1</c></summary>
    public string Display => IsPrerelease ? $"{Number.ToString(3)}-{Prerelease}" : Number.ToString(3);

    /// <summary>If <paramref name="release"/> is newer. A release is newer than its own betas</summary>
    public bool IsOlderThan(Version release)
    {
        var other = new Version(release.Major, release.Minor, Math.Max(release.Build, 0));
        return IsPrerelease ? other >= Number : other > Number;
    }

    /// <summary>
    /// From the assembly's informational version (<c>0.11.0-beta.1+commit</c>), falling back to <paramref name="assembly"/>
    /// </summary>
    public static InstalledVersion FromAssembly(string? informational, Version? assembly)
    {
        var text = informational?.Split('+', 2)[0].Trim() ?? "";
        var parts = text.Split('-', 2);
        if (Version.TryParse(parts[0], out var number) && number.Build >= 0 && number.Revision < 0)
        {
            return new InstalledVersion(number, parts.Length == 2 ? parts[1] : null);
        }
        return new InstalledVersion(assembly ?? new Version(0, 0, 0));
    }

    public static implicit operator InstalledVersion(Version number) => new(number);

    public override string ToString() => Display;
}
