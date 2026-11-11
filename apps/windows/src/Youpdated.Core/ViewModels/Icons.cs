using System.Text;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.ViewModels;

/// <summary>A monogram: initials on one of 12 colors, hashed from the target (apps/shared/presentation-cases.json)</summary>
public sealed record Monogram(string Initials, int ColorIndex)
{
    public static IReadOnlyList<(string Name, byte R, byte G, byte B)> Palette { get; } =
    [
        ("Red", 0xD9, 0x53, 0x4F),
        ("Orange", 0xCC, 0x6B, 0x27),
        ("Amber", 0xB9, 0x83, 0x18),
        ("Olive", 0x7F, 0x9A, 0x2E),
        ("Green", 0x3E, 0x9A, 0x5E),
        ("Teal", 0x2D, 0x94, 0x90),
        ("Cyan", 0x2B, 0x8F, 0xC4),
        ("Blue", 0x3A, 0x72, 0xD8),
        ("Indigo", 0x5A, 0x5B, 0xCF),
        ("Purple", 0x86, 0x55, 0xC8),
        ("Pink", 0xC9, 0x4F, 0x86),
        ("Graphite", 0x66, 0x70, 0x7A),
    ];

    public (byte R, byte G, byte B) Color => (Palette[ColorIndex].R, Palette[ColorIndex].G, Palette[ColorIndex].B);

    public (byte R, byte G, byte B) Lighter
    {
        get
        {
            static byte Up(byte c) => (byte)Math.Min(255, Math.Round(c + (255 - c) * 0.08));
            var (r, g, b) = Color;
            return (Up(r), Up(g), Up(b));
        }
    }

    public static Monogram For(TargetKey target, string display) =>
        new(InitialsOf(display), ColorIndexOf(target));

    /// <summary>32-bit FNV-1a over <c>source:key</c>, mod 12</summary>
    public static int ColorIndexOf(TargetKey target) => (int)(Hash($"{target.Source}:{target.Key}") % (uint)Palette.Count);

    public static string InitialsOf(string display)
    {
        var name = display.Trim();
        var slash = name.LastIndexOf('/');
        if (slash >= 0 && slash < name.Length - 1)
        {
            name = name[(slash + 1)..];
        }
        name = name.TrimStart('@');
        var words = name.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        var initials = words.Take(words.Length >= 2 ? 2 : 1).Select(word => char.ToUpperInvariant(word[0])).ToArray();
        return initials.Length == 0 ? "?" : new string(initials);
    }

    /// <summary>32-bit FNV-1a over the UTF-8 bytes</summary>
    public static uint Hash(string text)
    {
        var hash = 2166136261u;
        foreach (var b in Encoding.UTF8.GetBytes(text))
        {
            hash = (hash ^ b) * 16777619u;
        }
        return hash;
    }
}

/// <summary>Icons from engine's <c>icon.get</c> while enabled, kept for the session; null for the monogram</summary>
public sealed class IconCache(EngineClient engine)
{
    private readonly Dictionary<TargetKey, Task<byte[]?>> _cache = [];

    public bool Enabled { get; private set; }

    public event Action? Changed;

    /// <summary>Follow the Show icons setting</summary>
    public void Configure(bool showIcons)
    {
        if (showIcons == Enabled)
        {
            return;
        }
        Enabled = showIcons;
        _cache.Clear();
        Changed?.Invoke();
    }

    public Task<byte[]?> GetAsync(TargetKey target)
    {
        if (!Enabled)
        {
            return Task.FromResult<byte[]?>(null);
        }
        if (!_cache.TryGetValue(target, out var pending))
        {
            pending = _cache[target] = FetchAsync(target);
        }
        return pending;
    }

    /// <summary>A target's icon changed: views refetch it</summary>
    public event Action<TargetKey>? TargetChanged;

    /// <summary>The engine's <c>icon</c> event</summary>
    public void Arrived(TargetKey target)
    {
        _cache.Remove(target);
        if (Enabled)
        {
            TargetChanged?.Invoke(target);
        }
    }

    private async Task<byte[]?> FetchAsync(TargetKey target)
    {
        try
        {
            var reply = await engine.IconGetAsync(target).ConfigureAwait(false);
            return reply.Icon?.Bytes();
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException or FormatException)
        {
            return null;
        }
    }
}
