using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace Youpdated.App;

public partial class App : Application
{
    private readonly AppController _controller = new(app => new MainWindow(app));

    public App()
    {
        InitializeComponent();
        AppLog.Load();
        UnhandledException += (_, args) => AppLog.Write($"Unhandled: {args.Message}{Environment.NewLine}{args.Exception}");
        AppDomain.CurrentDomain.UnhandledException += (_, args) => AppLog.Write($"Unhandled: {args.ExceptionObject}");
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args) =>
        _ = _controller.LaunchAsync(AppInstance.GetCurrent().GetActivatedEventArgs());
}
