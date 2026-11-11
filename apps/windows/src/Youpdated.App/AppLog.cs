using System.Diagnostics;
using Youpdated.Core.Platform;

namespace Youpdated.App;

/// <summary>Errors in <c>%LOCALAPPDATA%\youpdated\app\app.log</c> when enabled</summary>
internal static class AppLog
{
    private const long Limit = 256 * 1024;

    private static readonly object Gate = new();

    public static string Folder { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "youpdated", "app");

    public static string File { get; } = Path.Combine(Folder, "app.log");

    /// <summary>The setting is kept in the app's state.json</summary>
    public static LocalStateFile State { get; } = new(Path.Combine(Folder, "state.json"));

    public static bool Enabled { get; private set; }

    /// <summary>Read the setting; call before anything can log</summary>
    public static void Load() => Enabled = State.Load().DiagnosticLog;

    /// <summary>Turn it on or off, and remember preference</summary>
    public static void SetEnabled(bool enabled)
    {
        Enabled = enabled;
        try
        {
            State.Save(State.Load() with { DiagnosticLog = enabled });
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
        {
            Debug.WriteLine($"Youpdated: setting not saved: {exc.Message}");
        }
    }

    public static bool Exists => System.IO.File.Exists(File);

    public static void Write(string message)
    {
        Debug.WriteLine($"Youpdated: {message}");
        if (!Enabled)
        {
            return;
        }
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(Folder);
                // Start over when it's too long
                if (System.IO.File.Exists(File) && new FileInfo(File).Length > Limit)
                {
                    System.IO.File.Delete(File);
                }
                System.IO.File.AppendAllText(File, $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz} {message}{Environment.NewLine}");
            }
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
        {
            Debug.WriteLine($"Youpdated: no log: {exc.Message}");
        }
    }

    /// <summary>Remove from this PC</summary>
    public static void Delete()
    {
        try
        {
            lock (Gate)
            {
                System.IO.File.Delete(File);
            }
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
        {
            Debug.WriteLine($"Youpdated: error log not removed: {exc.Message}");
        }
    }
}
