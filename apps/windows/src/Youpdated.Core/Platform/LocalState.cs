using System.Text.Json;
using System.Text.Json.Serialization;

namespace Youpdated.Core.Platform;

/// <summary>App storage next to config and history. Kept in %LOCALAPPDATA%\youpdated\app\state.json</summary>
public sealed record LocalState
{
    public DateTimeOffset? LastDigestAt { get; init; }

    /// <summary>Checks are paused until then; <see cref="DateTimeOffset.MaxValue"/> is until resumed</summary>
    public DateTimeOffset? PausedUntil { get; init; }

    /// <summary>First-launch setup ran: Start at login turned on, the Start menu offered</summary>
    public bool SetUp { get; init; }

    public bool StartMenuOffered { get; init; }

    /// <summary>Settings → View</summary>
    public ViewModels.ViewOptions? View { get; init; }

    /// <summary>Settings → Advanced: write errors to the app's log file. Off unless turned on</summary>
    public bool DiagnosticLog { get; init; }

    /// <summary>Settings → Advanced: no offers to watch Youpdated. Off unless turned on</summary>
    public bool HideSelfWatchSuggestions { get; init; }
}

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower, WriteIndented = true, UseStringEnumConverter = true)]
[JsonSerializable(typeof(LocalState))]
internal sealed partial class LocalStateJson : JsonSerializerContext;

public interface ILocalStateStore
{
    LocalState Load();

    void Save(LocalState state);

    void Delete();
}

/// <summary><see cref="LocalState"/> as a JSON file</summary>
public sealed class LocalStateFile(string path) : ILocalStateStore
{
    public string Path => path;

    public LocalState Load()
    {
        try
        {
            using var stream = File.OpenRead(path);
            return JsonSerializer.Deserialize(stream, LocalStateJson.Default.LocalState) ?? new LocalState();
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException or JsonException)
        {
            // Missing or damaged: start over
            return new LocalState();
        }
    }

    public void Save(LocalState state)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
        var temporary = path + ".tmp";
        File.WriteAllBytes(temporary, JsonSerializer.SerializeToUtf8Bytes(state, LocalStateJson.Default.LocalState));
        File.Move(temporary, path, overwrite: true);
    }

    public void Delete()
    {
        File.Delete(path);
    }
}

/// <summary>Kept in memory for tests</summary>
public sealed class MemoryLocalStateStore : ILocalStateStore
{
    public LocalState State { get; private set; } = new();

    public LocalState Load() => State;

    public void Save(LocalState state) => State = state;

    public void Delete() => State = new LocalState();
}
