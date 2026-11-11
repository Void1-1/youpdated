using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.ViewModels;

/// <summary>A Following row: target, latest version, or date of latest post from <c>targets.list</c></summary>
public sealed record FollowingRow(Target Target, string? AppVersion = null)
{
    public string Display => Target.Display;

    /// <summary>The newest version for the app update</summary>
    public string? LatestVersion =>
        AppVersion ?? (Target.Latest?.Version is { Length: > 0 } version ? FeedText.DisplayVersion(version) : null);

    /// <summary>When the latest post came out</summary>
    public DateTimeOffset? LatestDate => LatestVersion is null ? Target.Latest?.Published : null;

    public bool NotCheckedYet => Target.LastChecked is null && Target.LastError is null;

    public bool HasError => Target.LastError is not null;
}

/// <summary>A label's group</summary>
public sealed class FollowingGroup(string label, IList<FollowingRow> rows) : ReadOnlyCollection<FollowingRow>(rows)
{
    /// <summary>Section label, from <see cref="SourceLabels"/></summary>
    public string Label => label;
}

public sealed record FollowingLayout(FollowingRow? Starred, bool SelfMissing, IReadOnlyList<FollowingGroup> Groups);

public static class FollowingBuilder
{
    public static FollowingLayout Build(
        IEnumerable<Target> targets, string? filter, Version appVersion, string browsers = "Browsers")
    {
        var all = targets.ToList();
        var starred = all.FirstOrDefault(target => target.Starred);
        var groups = all
            .Where(target => !target.Starred && Matches(target, filter, browsers))
            .GroupBy(target => SourceLabels.Label(target.Source, target.Key, browsers))
            .OrderBy(group => group.Key, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new FollowingGroup(group.Key,
            [
                .. group.OrderBy(target => target.Display, StringComparer.CurrentCultureIgnoreCase)
                    .Select(target => new FollowingRow(target)),
            ]))
            .ToList();

        // The starred row shows the version that's running
        var starredRow = starred is not null && Matches(starred, filter, browsers)
            ? new FollowingRow(starred, appVersion.ToString(3))
            : null;
        return new FollowingLayout(starredRow, starred is null, groups);
    }

    /// <summary>The filter field: name, key, label</summary>
    public static bool Matches(Target target, string? filter, string browsers = "Browsers")
    {
        if (string.IsNullOrWhiteSpace(filter))
        {
            return true;
        }
        var text = filter.Trim();
        return target.Display.Contains(text, StringComparison.CurrentCultureIgnoreCase)
            || target.Key.Contains(text, StringComparison.OrdinalIgnoreCase)
            || target.Source.Contains(text, StringComparison.OrdinalIgnoreCase)
            || SourceLabels.Label(target.Source, target.Key, browsers).Contains(text, StringComparison.CurrentCultureIgnoreCase);
    }
}

/// <summary>Following: filter field, groups, removing, priority</summary>
public sealed partial class FollowingViewModel : ObservableObject
{
    private readonly SourcesViewModel _sources;
    private readonly Version _appVersion;

    public FollowingViewModel(SourcesViewModel sources, Version appVersion)
    {
        _sources = sources;
        _appVersion = appVersion;
        _sources.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(SourcesViewModel.Targets))
            {
                Rebuild();
            }
        };
    }

    public string BrowsersLabel { get; set; } = "Browsers";

    public event Action<Target>? ShowInUpdates;

    [ObservableProperty]
    public partial string Filter { get; set; } = "";

    [ObservableProperty]
    public partial FollowingLayout Layout { get; private set; } = new(null, false, []);

    public bool NothingFollowed => _sources.Targets.All(target => target.Starred);

    public string? Error => _sources.Error;

    public Task<bool> RemoveAsync(Target target, bool wipe) => _sources.RemoveAsync(target, wipe);

    public Task SetPriorityAsync(Target target, Priority priority) => _sources.SetPriorityAsync(target, priority);

    public void Select(Target target) => ShowInUpdates?.Invoke(target);

    partial void OnFilterChanged(string value) => Rebuild();

    private void Rebuild()
    {
        Layout = FollowingBuilder.Build(_sources.Targets, Filter, _appVersion, BrowsersLabel);
        OnPropertyChanged(nameof(NothingFollowed));
    }
}
