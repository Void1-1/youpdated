using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.ViewModels;

/// <summary>A followed item's Filters dialog: load rules, preview, save</summary>
public sealed partial class FiltersViewModel(EngineClient engine, Target target) : ObservableObject
{
    private readonly HashSet<string> _ticked = [];
    // Each preview's number
    private int _generation;

    /// <summary>The rules were saved</summary>
    public event Action? SavedRules;

    public Target Target => target;

    [ObservableProperty]
    public partial bool Loading { get; private set; } = true;

    /// <summary>The rules couldn't be loaded</summary>
    [ObservableProperty]
    public partial string? LoadError { get; private set; }

    /// <summary>The rules as last loaded or saved</summary>
    [ObservableProperty]
    public partial Filters? Saved { get; private set; }

    [ObservableProperty]
    public partial FiltersPreview? Preview { get; private set; }

    [ObservableProperty]
    public partial bool Saving { get; private set; }

    /// <summary>Why a save was refused</summary>
    [ObservableProperty]
    public partial string? Problem { get; private set; }

    /// <summary>"Only versions like", rather than every version</summary>
    [ObservableProperty]
    public partial bool LimitsVersions { get; set; }

    /// <summary>Typed patterns, separated by commas</summary>
    [ObservableProperty]
    public partial string Custom { get; set; } = "";

    [ObservableProperty]
    public partial bool SkipPrereleases { get; set; }

    [ObservableProperty]
    public partial Bump Bump { get; set; } = Bump.Any;

    [ObservableProperty]
    public partial string Min { get; set; } = "";

    public IReadOnlyList<FilterPattern> Offered => Preview?.Patterns ?? [];

    public bool IsTicked(string pattern) => _ticked.Contains(pattern);

    public void SetTicked(string pattern, bool on)
    {
        if (on ? _ticked.Add(pattern) : _ticked.Remove(pattern))
        {
            Problem = null;
            OnPropertyChanged(nameof(Match));
        }
    }

    private IEnumerable<string> Typed =>
        Custom.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

    /// <summary><c>match</c> to send: selected patterns, then typed ones</summary>
    public IReadOnlyList<string> Match => LimitsVersions
        ? [.. Offered.Select(pattern => pattern.Pattern).Where(_ticked.Contains).Concat(Typed).Distinct()]
        : [];

    public string? Minimum => string.IsNullOrWhiteSpace(Min) ? null : Min.Trim();

    public bool IsEditable => Saved?.Editable == true;

    // A version fits any pattern, so their order isn't a change
    public bool HasChanges => Saved is { } saved
        && (!Match.ToHashSet().SetEquals(saved.Match) || SkipPrereleases != saved.SkipPrereleases
            || Bump != saved.Bump || Minimum != saved.Min);

    public bool CanSave => !Loading && !Saving && IsEditable && HasChanges;

    /// <summary>"Only versions like" with nothing ticked or typed would keep all</summary>
    public bool LimitsNothing => LimitsVersions && Match.Count == 0;

    public async Task LoadAsync()
    {
        Loading = true;
        try
        {
            var saved = await engine.FiltersGetAsync(target.AsKey());
            Saved = saved;
            _ticked.Clear();
            LimitsVersions = saved.Match.Count > 0;
            Custom = string.Join(", ", saved.Match);
            SkipPrereleases = saved.SkipPrereleases;
            Bump = saved.Bump;
            Min = saved.Min ?? "";
            LoadError = null;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            LoadError = exc.Message;
            return;
        }
        finally
        {
            Loading = false;
        }
        await PreviewAsync();
    }

    /// <summary>What the draft would keep</summary>
    public async Task PreviewAsync(bool refresh = false)
    {
        var generation = ++_generation;
        try
        {
            var reply = await engine.FiltersPreviewAsync(target.AsKey(), Match, SkipPrereleases, Minimum, refresh);
            if (generation != _generation)
            {
                return;
            }
            var first = Preview is null;
            Preview = reply;
            if (first)
            {
                SortTyped();
            }
            OnPropertyChanged(nameof(Offered));
            OnPropertyChanged(nameof(Match));
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // a version it can't read yet
        }
    }

    /// <summary>Typed patterns matching offered ones become ticks</summary>
    private void SortTyped()
    {
        var known = Offered.Select(pattern => pattern.Pattern).ToHashSet();
        var typed = Typed.ToList();
        _ticked.UnionWith(typed.Where(known.Contains));
        Custom = string.Join(", ", typed.Where(glob => !known.Contains(glob)));
    }

    /// <summary>True when saved, and the dialog can close</summary>
    public async Task<bool> SaveAsync()
    {
        if (!CanSave)
        {
            return false;
        }
        Saving = true;
        Problem = null;
        try
        {
            Saved = await engine.FiltersSetAsync(target.AsKey(), Match, SkipPrereleases, Bump, Minimum);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Problem = exc.Message;
            return false;
        }
        finally
        {
            Saving = false;
        }
        SavedRules?.Invoke();
        return true;
    }

    partial void OnLimitsVersionsChanged(bool value) => Problem = null;

    partial void OnCustomChanged(string value) => Problem = null;

    partial void OnMinChanged(string value) => Problem = null;
}
