using Microsoft.UI.Xaml;
using Youpdated.Core.Contract;

namespace Youpdated.App.Views;

/// <summary>Shared small conversions for views and x:Bind expressions</summary>
public static class Bind
{
    public static Visibility Show(bool visible) => visible ? Visibility.Visible : Visibility.Collapsed;

    public static Visibility ShowIf(object? value) => value is null ? Visibility.Collapsed : Visibility.Visible;

    public static Visibility ShowText(string? value) => string.IsNullOrEmpty(value) ? Visibility.Collapsed : Visibility.Visible;

    public static Visibility Hide(bool hidden) => hidden ? Visibility.Collapsed : Visibility.Visible;

    public static bool NotNull(object? value) => value is not null;

    /// <summary>Segoe Fluent Icons glyph for a source, for the icon's badge</summary>
    public static string SourceGlyph(string source) => source switch
    {
        "git" => "\uE943", // Code
        "npm" or "pypi" or "homebrew" => "\uE7B8", // Package
        "steam" or "itch" => "\uE7FC", // Game
        "youtube" => "\uE714", // Video
        "browser" => "\uE774", // Globe
        "feed" => "\uE8A1", // PreviewLink
        _ => "\uE789", // Updates
    };

    public static string ProblemGlyph(string kind) => kind switch
    {
        Problem.NotifyKind => "\uEA8F", // Ringer
        Problem.ConfigKind => "\uE713", // Setting
        Problem.EngineKind => "\uE7BA", // Warning
        _ => "\uE783", // Error
    };

    /// <summary>The target and label</summary>
    public static string ProblemTitle(Problem problem) => problem switch
    {
        { Kind: Problem.EngineKind } => "Youpdated",
        { Kind: Problem.NotifyKind } => problem.Target,
        { Target: "-" or "" } => Text.Label(problem.Source, problem.Key),
        _ => $"{problem.Target}  ·  {Text.Label(problem.Source, problem.Key)}",
    };

    /// <summary>The progress line fills from the engine's progress events</summary>
    public static double Progress(int done, int of) => of == 0 ? 0 : 100.0 * done / of;
}
