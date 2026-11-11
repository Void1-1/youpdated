using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI.ViewManagement;

namespace Youpdated.App;

/// <summary>Theme brushes for elements built in code, where {ThemeResource} can't follow the theme for them</summary>
internal static class Themed
{
    private static readonly Dictionary<(string Theme, string Key), Brush?> Found = [];
    private static readonly AccessibilitySettings? Accessibility = TryAccessibility();

    /// <summary>Give <paramref name="element"/> the brush for its theme, now and whenever the theme changes</summary>
    public static void Brush<T>(T element, string key, Action<T, Brush> set)
        where T : FrameworkElement
    {
        void Apply()
        {
            if (Find(element, key) is { } brush)
            {
                set(element, brush);
            }
        }

        Apply();
        // Its theme is only certain once it's in the window
        element.Loaded += (_, _) => Apply();
        element.ActualThemeChanged += (_, _) => Apply();
    }

    /// <summary>The brush under <paramref name="key"/> for the element's theme as it is now</summary>
    public static Brush? Find(FrameworkElement element, string key)
    {
        var theme = HighContrast ? "HighContrast" : element.ActualTheme == ElementTheme.Dark ? "Dark" : "Light";
        if (!Found.TryGetValue((theme, key), out var brush))
        {
            brush = Search(Application.Current.Resources, theme, key) as Brush;
            Found[(theme, key)] = brush;
        }
        // Not in a theme dictionary: what the app's own theme gives
        return brush ?? Application.Current.Resources[key] as Brush;
    }

    private static bool HighContrast
    {
        get
        {
            try
            {
                return Accessibility?.HighContrast == true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    private static AccessibilitySettings? TryAccessibility()
    {
        try
        {
            return new AccessibilitySettings();
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static object? Search(ResourceDictionary resources, string theme, string key)
    {
        foreach (var name in theme == "Dark" ? new[] { "Dark", "Default" } : [theme])
        {
            if (resources.ThemeDictionaries.TryGetValue(name, out var themed)
                && themed is ResourceDictionary dictionary
                && dictionary.TryGetValue(key, out var found))
            {
                return found;
            }
        }
        for (var i = resources.MergedDictionaries.Count - 1; i >= 0; i--)
        {
            if (Search(resources.MergedDictionaries[i], theme, key) is { } found)
            {
                return found;
            }
        }
        return null;
    }
}
