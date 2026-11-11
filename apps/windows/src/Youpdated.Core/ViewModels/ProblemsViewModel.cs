using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.ViewModels;

/// <summary>Problems: source errors, failed webhooks or commands, engine crashes</summary>
public sealed partial class ProblemsViewModel(EngineClient engine) : ObservableObject
{
    private IReadOnlyList<Problem> _fromCheck = [];
    private readonly List<Problem> _engine = [];

    [ObservableProperty]
    public partial IReadOnlyList<Problem> Problems { get; private set; } = [];

    [ObservableProperty]
    public partial DateTimeOffset? CheckedAt { get; private set; }

    public bool Any => Problems.Count > 0;

    /// <summary>Copy Details</summary>
    public string Details(Func<Problem, string>? describe = null)
    {
        describe ??= problem => problem.Target is "-" or ""
            ? $"{problem.Source}: {problem.Message}"
            : $"{problem.Source} {problem.Target}: {problem.Message}";
        var lines = Problems.Select(describe).ToList();
        if (CheckedAt is { } at)
        {
            lines.Insert(0, $"Youpdated, check at {Contract.TimestampConverter.ToWire(at)}");
        }
        return string.Join(Environment.NewLine, lines);
    }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            var reply = await engine.ProblemsListAsync(cancellationToken);
            _fromCheck = reply.Problems;
            CheckedAt = reply.CheckedAt;
            Update();
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            // Shown by whatever else failed
        }
    }

    /// <summary>The engine crashed or couldn't start</summary>
    public void AddEngineProblem(Problem problem)
    {
        _engine.Add(problem);
        // Only the latest
        if (_engine.Count > 3)
        {
            _engine.RemoveAt(0);
        }
        Update();
    }

    /// <summary>A check succeeded after a restart</summary>
    public void ClearEngineProblems()
    {
        if (_engine.Count > 0)
        {
            _engine.Clear();
            Update();
        }
    }

    private void Update()
    {
        Problems = [.. _engine, .. _fromCheck];
        OnPropertyChanged(nameof(Any));
    }
}

/// <summary>The passphrase prompt, with "Remember on this computer" off by default</summary>
public sealed partial class UnlockViewModel(Func<string, bool, Task<string?>> unlock) : ObservableObject
{
    [ObservableProperty]
    public partial string Passphrase { get; set; } = "";

    [ObservableProperty]
    public partial bool Remember { get; set; }

    [ObservableProperty]
    public partial string? Error { get; private set; }

    [ObservableProperty]
    public partial bool IsBusy { get; private set; }

    /// <summary>True once unlocked; otherwise <see cref="Error"/> for why</summary>
    public async Task<bool> UnlockAsync()
    {
        if (Passphrase.Length == 0)
        {
            return false;
        }
        IsBusy = true;
        try
        {
            Error = await unlock(Passphrase, Remember);
            if (Error is null)
            {
                Passphrase = "";
                return true;
            }
            return false;
        }
        finally
        {
            IsBusy = false;
        }
    }
}
