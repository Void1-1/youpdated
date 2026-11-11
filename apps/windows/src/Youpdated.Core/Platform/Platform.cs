namespace Youpdated.Core.Platform;

// What the view models need from the OS

/// <summary>Runs work on the UI thread</summary>
public interface IUiDispatcher
{
    void Post(Action action);
}

/// <summary>The passphrase in Credential Manager</summary>
public interface ICredentialStore
{
    string? Read();

    void Write(string passphrase);

    void Delete();
}

/// <summary>Start at login</summary>
public interface IStartupRegistration
{
    bool IsEnabled { get; }

    /// <summary>Registered, but turned off in Task Manager</summary>
    bool IsDisabledByUser { get; }

    void Enable();

    void Disable();
}

/// <summary><c>youpdated://</c> link handler</summary>
public interface IUrlSchemeRegistration
{
    void Register();

    void Unregister();
}

/// <summary>OS notifications</summary>
public interface INotificationPresenter
{
    void Show(NotificationRequest request);

    /// <summary>Remove what was shown for these items</summary>
    void Remove(IReadOnlyCollection<string> itemIds);

    /// <summary>Remove the app's notification registration</summary>
    void Unregister();
}

public interface IShell
{
    void OpenUrl(string url);

    /// <summary>Show <paramref name="path"/> selected in Explorer</summary>
    void Reveal(string path);

    string DownloadsFolder { get; }
}

/// <summary>Runs the engine's CLI to delete the config and history</summary>
public interface IDataRemover
{
    Task<bool> RemoveDataAsync(CancellationToken cancellationToken = default);
}
