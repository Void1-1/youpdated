using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.UI.Dispatching;
using Microsoft.Win32;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;

namespace Youpdated.App.Platform;

/// <summary>Passphrase in Credential Manager, as a generic credential for only this user</summary>
internal sealed class CredentialStore : ICredentialStore
{
    private const string Target = "Youpdated/passphrase";

    public string? Read()
    {
        if (!Native.CredRead(Target, Native.CredTypeGeneric, 0, out var pointer))
        {
            return null;
        }
        try
        {
            var credential = Marshal.PtrToStructure<Native.Credential>(pointer);
            return credential.CredentialBlobSize == 0
                ? null
                : Marshal.PtrToStringUni(credential.CredentialBlob, (int)credential.CredentialBlobSize / 2);
        }
        finally
        {
            Native.CredFree(pointer);
        }
    }

    public void Write(string passphrase)
    {
        var blob = Encoding.Unicode.GetBytes(passphrase);
        var buffer = Marshal.AllocCoTaskMem(blob.Length);
        try
        {
            Marshal.Copy(blob, 0, buffer, blob.Length);
            var credential = new Native.Credential
            {
                Type = Native.CredTypeGeneric,
                TargetName = Target,
                UserName = Environment.UserName,
                Comment = "Unlocks Youpdated's encrypted config and history",
                CredentialBlob = buffer,
                CredentialBlobSize = (uint)blob.Length,
                Persist = Native.CredPersistLocalMachine,
            };
            if (!Native.CredWrite(ref credential, 0))
            {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            Marshal.Copy(new byte[blob.Length], 0, buffer, blob.Length);
            Marshal.FreeCoTaskMem(buffer);
            Array.Clear(blob);
        }
    }

    public void Delete()
    {
        if (!Native.CredDelete(Target, Native.CredTypeGeneric, 0) && Marshal.GetLastWin32Error() != Native.ErrorNotFound)
        {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}

/// <summary>Start at login through the Run key</summary>
internal sealed class StartupRegistration(string exe) : IStartupRegistration
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ApprovedKey = @"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";
    private const string Name = "Youpdated";

    /// <summary>If started at login: tray only</summary>
    public const string BackgroundArgument = "--background";

    public bool IsEnabled
    {
        get
        {
            using var run = Registry.CurrentUser.OpenSubKey(RunKey);
            return run?.GetValue(Name) is string;
        }
    }

    public bool IsDisabledByUser
    {
        get
        {
            if (!IsEnabled)
            {
                return false;
            }
            // Task Manager writes 12 bytes; an odd first byte is turned off there
            using var approved = Registry.CurrentUser.OpenSubKey(ApprovedKey);
            return approved?.GetValue(Name) is byte[] { Length: > 0 } state && (state[0] & 1) == 1;
        }
    }

    /// <summary>Also run on every launch while on</summary>
    public void Enable()
    {
        using var run = Registry.CurrentUser.CreateSubKey(RunKey);
        run.SetValue(Name, $"\"{exe}\" {BackgroundArgument}");
    }

    public void Disable()
    {
        using var run = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
        run?.DeleteValue(Name, throwOnMissingValue: false);
    }
}

/// <summary><c>youpdated://</c> under HKCU\Software\Classes, pointing at this copy of the app</summary>
internal sealed class UrlSchemeRegistration(string exe) : IUrlSchemeRegistration
{
    private const string Key = @"Software\Classes\youpdated";

    public void Register()
    {
        using var scheme = Registry.CurrentUser.CreateSubKey(Key);
        scheme.SetValue("", "URL:Youpdated link");
        scheme.SetValue("URL Protocol", "");
        using (var icon = scheme.CreateSubKey("DefaultIcon"))
        {
            icon.SetValue("", $"\"{exe}\",0");
        }
        using var command = scheme.CreateSubKey(@"shell\open\command");
        command.SetValue("", $"\"{exe}\" \"%1\"");
    }

    public void Unregister() => Registry.CurrentUser.DeleteSubKeyTree(Key, throwOnMissingSubKey: false);
}

internal sealed class Shell : IShell
{
    public string DownloadsFolder
    {
        get
        {
            if (Native.SHGetKnownFolderPath(Native.DownloadsFolder, 0, IntPtr.Zero, out var path) == 0)
            {
                try
                {
                    return Marshal.PtrToStringUni(path)!;
                }
                finally
                {
                    Marshal.FreeCoTaskMem(path);
                }
            }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        }
    }

    public void OpenUrl(string url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
    }

    public void Reveal(string path) =>
        Process.Start(new ProcessStartInfo("explorer.exe") { ArgumentList = { $"/select,{path}" }, UseShellExecute = false });

    /// <summary>A Windows Settings page</summary>
    public static void OpenSettings(string page) =>
        Process.Start(new ProcessStartInfo(page) { UseShellExecute = true });
}

internal sealed class DispatcherQueueUi(DispatcherQueue queue) : IUiDispatcher
{
    public void Post(Action action)
    {
        if (queue.HasThreadAccess)
        {
            action();
        }
        else
        {
            queue.TryEnqueue(() => action());
        }
    }
}

/// <summary>Runs the engine's <c>uninstall</c> to delete the config and history</summary>
internal sealed class EngineDataRemover(string appDirectory) : IDataRemover
{
    public async Task<bool> RemoveDataAsync(CancellationToken cancellationToken = default)
    {
        var command = EngineCommand.Resolve(appDirectory, Environment.GetEnvironmentVariable, ["uninstall", "--keep-package", "--yes"]);
        var info = new ProcessStartInfo(command.FileName)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = command.WorkingDirectory,
        };
        foreach (var argument in command.Arguments)
        {
            info.ArgumentList.Add(argument);
        }
        try
        {
            using var process = Process.Start(info);
            if (process is null)
            {
                return false;
            }
            await process.WaitForExitAsync(cancellationToken);
            return process.ExitCode == 0;
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return false;
        }
    }
}
