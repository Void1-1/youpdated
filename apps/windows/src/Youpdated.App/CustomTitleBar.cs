using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Foundation;
using Windows.Graphics;

namespace Youpdated.App;

/// <summary>The window draws its own title bar on Mica</summary>
internal static class CustomTitleBar
{
    public static void Extend(Window window, UIElement dragArea)
    {
        window.ExtendsContentIntoTitleBar = true;
        window.SetTitleBar(dragArea);
        window.AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
    }

    /// <summary>Minimize, maximize, and close, drawn for <paramref name="theme"/></summary>
    public static void MatchTheme(Window window, ElementTheme theme) =>
        window.AppWindow.TitleBar.PreferredTheme = theme == ElementTheme.Dark ? TitleBarTheme.Dark : TitleBarTheme.Light;

    public static double RightInset(Window window, double scale) =>
        window.AppWindow.TitleBar.RightInset / scale;

    /// <summary>Let clicks through to <paramref name="controls"/>; call after layout changes</summary>
    public static void SetInteractive(Window window, IEnumerable<FrameworkElement> controls)
    {
        if (window.Content?.XamlRoot is not { } root)
        {
            return;
        }
        var scale = root.RasterizationScale;
        var rects = new List<RectInt32>();
        foreach (var control in controls)
        {
            if (control.Visibility != Visibility.Visible || control.ActualWidth == 0)
            {
                continue;
            }
            var bounds = control.TransformToVisual(null).TransformBounds(new Rect(0, 0, control.ActualWidth, control.ActualHeight));
            rects.Add(new RectInt32(
                (int)Math.Round(bounds.X * scale),
                (int)Math.Round(bounds.Y * scale),
                (int)Math.Round(bounds.Width * scale),
                (int)Math.Round(bounds.Height * scale)));
        }
        InputNonClientPointerSource.GetForWindowId(window.AppWindow.Id)
            .SetRegionRects(NonClientRegionKind.Passthrough, [.. rects]);
    }
}
