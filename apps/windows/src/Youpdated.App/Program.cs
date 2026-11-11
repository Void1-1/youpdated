using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;
using Youpdated.App.Platform;

namespace Youpdated.App;

public static class Program
{
    /// <summary>Shared by taskbar, Jump List, Start menu shortcut, toasts</summary>
    public const string AppUserModelId = "Void1-1.Youpdated";

    private const string InstanceKey = "Youpdated";

    [STAThread]
    private static int Main(string[] args)
    {
        WinRT.ComWrappersSupport.InitializeComWrappers();
        Native.SetCurrentProcessExplicitAppUserModelID(AppUserModelId);
        Native.FollowSystemThemeInMenus();

        if (RedirectedToRunningInstance())
        {
            return 0;
        }

        Application.Start(started =>
        {
            var context = new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread());
            SynchronizationContext.SetSynchronizationContext(context);
            _ = new App();
        });
        return 0;
    }

    private static bool RedirectedToRunningInstance()
    {
        var activation = AppInstance.GetCurrent().GetActivatedEventArgs();
        var main = AppInstance.FindOrRegisterForKey(InstanceKey);
        if (main.IsCurrent)
        {
            return false;
        }

        Native.AllowSetForegroundWindow(main.ProcessId);

        // RedirectActivationToAsync must not block the STA thread
        var done = Native.CreateEventW(IntPtr.Zero, true, false, null);
        Task.Run(() =>
        {
            main.RedirectActivationToAsync(activation).AsTask().Wait();
            Native.SetEvent(done);
        });
        const uint Infinite = 0xFFFFFFFF;
        _ = Native.CoWaitForMultipleObjects(0, Infinite, 1, [done], out _);
        Native.CloseHandle(done);
        return true;
    }
}
