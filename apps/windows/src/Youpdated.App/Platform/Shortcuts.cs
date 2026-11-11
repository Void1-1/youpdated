using Microsoft.Windows.System.Power;

namespace Youpdated.App.Platform;

/// <summary>Taskbar Jump List: Check now, Add..., Open</summary>
internal static class JumpList
{
    public static void Update(string exe, string appUserModelId)
    {
        var list = (Native.ICustomDestinationList)Native.Create(Native.DestinationListClass);
        list.SetAppID(appUserModelId);
        list.BeginList(out _, Native.ObjectArrayInterface, out _);
        try
        {
            var tasks = (Native.IObjectCollection)Native.Create(Native.EnumerableObjectCollectionClass);
            tasks.AddObject(Native.ShellLink(exe, "youpdated://check", Text.Get("JumpCheckNow")));
            tasks.AddObject(Native.ShellLink(exe, "youpdated://add", Text.Get("JumpAddSource")));
            tasks.AddObject(Native.ShellLink(exe, "youpdated://open", Text.Get("JumpOpen")));
            list.AddUserTasks((Native.IObjectArray)tasks);
            list.CommitList();
        }
        catch
        {
            list.AbortList();
            throw;
        }
    }

    public static void Remove(string appUserModelId)
    {
        var list = (Native.ICustomDestinationList)Native.Create(Native.DestinationListClass);
        list.DeleteList(appUserModelId);
    }
}

/// <summary>"Add to Start menu"</summary>
internal static class StartMenuShortcut
{
    public static string Path => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Youpdated.lnk");

    public static bool Exists => File.Exists(Path);

    public static void Create(string exe, string appUserModelId)
    {
        var link = Native.ShellLink(exe, "", "Youpdated", appUserModelId);
        Native.Save(link, Path);
    }

    public static void Remove() => File.Delete(Path);
}

internal sealed class PowerEvents : IDisposable
{
    private readonly Action _resumed;

    public PowerEvents(Action resumed)
    {
        _resumed = resumed;
        PowerManager.SystemSuspendStatusChanged += OnChanged;
    }

    public void Dispose() => PowerManager.SystemSuspendStatusChanged -= OnChanged;

    private void OnChanged(object? sender, object args)
    {
        if (PowerManager.SystemSuspendStatus is SystemSuspendStatus.AutoResume or SystemSuspendStatus.ManualResume)
        {
            _resumed();
        }
    }
}
