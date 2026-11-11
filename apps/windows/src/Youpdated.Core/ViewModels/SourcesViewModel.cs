using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.ViewModels;

/// <summary>Sources: everything watched, with add, remove, import</summary>
public sealed partial class SourcesViewModel : ObservableObject
{
    private readonly EngineClient _engine;

    public SourcesViewModel(EngineClient engine, TimeProvider time, Platform.IUiDispatcher ui)
    {
        _engine = engine;
        Add = new AddSourceViewModel(engine, time, ui);
        Import = new ImportViewModel(engine);
        Add.Added += targets => Added?.Invoke(targets);
        Import.Added += targets => Added?.Invoke(targets);
    }

    /// <summary>Targets added; the app checks them immeditately</summary>
    public event Action<IReadOnlyList<TargetRef>>? Added;

    /// <summary>Something was removed; the inbox may have lost items</summary>
    public event Action? Removed;

    public AddSourceViewModel Add { get; }

    public ImportViewModel Import { get; }

    /// <summary>Youpdated's entry, then by name</summary>
    [ObservableProperty]
    public partial IReadOnlyList<Target> Targets { get; private set; } = [];

    /// <summary>If Youpdated's entry is watched</summary>
    public bool WatchingSelf => Targets.Any(target => target.Starred);

    [ObservableProperty]
    public partial string? Error { get; private set; }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            var reply = await _engine.TargetsListAsync(cancellationToken);
            Targets = Order(reply.Targets);
            Error = null;
            OnPropertyChanged(nameof(WatchingSelf));
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
    }

    /// <summary>Stop watching; <paramref name="wipe"/> is "Also forget its history"</summary>
    public async Task<bool> RemoveAsync(Target target, bool wipe)
    {
        try
        {
            await _engine.TargetsRemoveAsync(target.AsKey(), wipe);
            Targets = [.. Targets.Where(t => t.AsKey() != target.AsKey())];
            OnPropertyChanged(nameof(WatchingSelf));
            Removed?.Invoke();
            return true;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            return false;
        }
    }

    public async Task SetPriorityAsync(Target target, Priority priority)
    {
        try
        {
            var reply = await _engine.PrioritySetAsync(target.AsKey(), priority);
            Targets = [.. Targets.Select(t => t.AsKey() == target.AsKey() ? t with { Priority = reply.Priority } : t)];
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
    }

    [RelayCommand]
    private Task Refresh() => RefreshAsync();

    internal static IReadOnlyList<Target> Order(IEnumerable<Target> targets) =>
        [.. targets.OrderByDescending(t => t.Starred).ThenBy(t => t.Display, StringComparer.CurrentCultureIgnoreCase)];
}

public enum AddStage
{
    /// <summary>Waiting for a link or name</summary>
    Idle,
    Previewing,
    /// <summary>More than one source could apply; <see cref="AddSourceViewModel.Choices"/> lists them</summary>
    Choosing,
    /// <summary>The preview is shown</summary>
    Ready,
    Adding,
    Added,
    Failed,
}

public sealed partial class AddSourceViewModel(EngineClient engine, TimeProvider time, Platform.IUiDispatcher ui) : ObservableObject
{
    /// <summary>How long typing has to pause before the preview runs</summary>
    public static readonly TimeSpan PreviewDelay = TimeSpan.FromMilliseconds(350);

    private ITimer? _debounce;
    // Each preview's number; an older one is dropped
    private int _generation;
    private bool _starting;

    public event Action<IReadOnlyList<TargetRef>>? Added;

    [ObservableProperty]
    public partial string Value { get; set; } = "";

    [ObservableProperty]
    public partial AddStage Stage { get; private set; }

    [ObservableProperty]
    public partial IReadOnlyList<string> Choices { get; private set; } = [];

    /// <summary>The source picked from <see cref="Choices"/></summary>
    [ObservableProperty]
    public partial string? Chosen { get; private set; }

    [ObservableProperty]
    public partial PreviewReply? Preview { get; private set; }

    [ObservableProperty]
    public partial string? Error { get; private set; }

    /// <summary>Set on the new target once it's followed</summary>
    [ObservableProperty]
    public partial Priority Priority { get; set; } = Priority.Normal;

    /// <summary>Follow: a preview that isn't already watched</summary>
    public bool CanAdd => Stage == AddStage.Ready && Preview is { AlreadyWatching: false };

    /// <summary>Already watching: "Show in Following"</summary>
    public bool AlreadyWatching => Stage == AddStage.Ready && Preview is { AlreadyWatching: true };

    /// <summary>Look up the value now, without waiting for pauses</summary>
    [RelayCommand]
    public async Task PreviewAsync(string? source = null)
    {
        _debounce?.Dispose();
        var value = Value.Trim();
        if (value.Length == 0)
        {
            return;
        }
        var generation = ++_generation;
        Stage = AddStage.Previewing;
        Error = null;
        Preview = null;
        Chosen = source;
        try
        {
            var preview = await engine.TargetsPreviewAsync(value, source);
            if (generation != _generation)
            {
                return;
            }
            Preview = preview;
            if (source is null)
            {
                Choices = [];
            }
            Stage = AddStage.Ready;
        }
        catch (EngineException exc) when (exc.Is(ErrorCodes.Ambiguous))
        {
            if (generation != _generation)
            {
                return;
            }
            Choices = exc.Choices;
            Stage = AddStage.Choosing;
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            if (generation != _generation)
            {
                return;
            }
            Error = exc.Message;
            Stage = AddStage.Failed;
        }
        Changed();
    }

    /// <summary>A segment of the ambiguous choice: preview again with that source</summary>
    [RelayCommand]
    public Task ChooseAsync(string source) => PreviewAsync(source);

    /// <summary>Follow: add, and set its priority when not Normal</summary>
    [RelayCommand]
    public async Task AddAsync()
    {
        if (!CanAdd || Preview is not { } preview)
        {
            return;
        }
        Stage = AddStage.Adding;
        try
        {
            var reply = await engine.TargetsAddAsync(preview.Source, [preview.Entry]);
            if (Priority != Priority.Normal)
            {
                foreach (var target in reply.Targets)
                {
                    await engine.PrioritySetAsync(target.AsKey(), Priority);
                }
            }
            Stage = AddStage.Added;
            Added?.Invoke(reply.Targets);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
            Stage = AddStage.Failed;
        }
        Changed();
    }

    /// <summary>Start over with a value: paste, dropped link, <c>youpdated://add</c></summary>
    public Task StartAsync(string value)
    {
        _starting = true;
        Value = value;
        _starting = false;
        return PreviewAsync();
    }

    [RelayCommand]
    public void Reset()
    {
        _debounce?.Dispose();
        _generation++;
        _starting = true;
        Value = "";
        _starting = false;
        Stage = AddStage.Idle;
        Preview = null;
        Choices = [];
        Chosen = null;
        Error = null;
        Priority = Priority.Normal;
        Changed();
    }

    partial void OnValueChanged(string value)
    {
        _generation++;
        _debounce?.Dispose();
        if (Stage != AddStage.Idle)
        {
            Stage = AddStage.Idle;
            Preview = null;
            Choices = [];
            Chosen = null;
            Error = null;
            Changed();
        }
        if (!_starting && value.Trim().Length > 0)
        {
            // As you type or paste, once typing pauses
            _debounce = time.CreateTimer(_ => ui.Post(() => _ = PreviewAsync()), null, PreviewDelay, Timeout.InfiniteTimeSpan);
        }
    }

    private void Changed()
    {
        OnPropertyChanged(nameof(CanAdd));
        OnPropertyChanged(nameof(AlreadyWatching));
    }
}

/// <summary>Import a package.json, requirements.txt, or Brewfile: preview what's added and skipped, then confirm</summary>
public sealed partial class ImportViewModel(EngineClient engine) : ObservableObject
{
    public event Action<IReadOnlyList<TargetRef>>? Added;

    [ObservableProperty]
    public partial ImportSource? Source { get; private set; }

    [ObservableProperty]
    public partial ImportPreviewReply? Preview { get; private set; }

    [ObservableProperty]
    public partial ImportApplyReply? Result { get; private set; }

    [ObservableProperty]
    public partial string? Error { get; private set; }

    [ObservableProperty]
    public partial bool IsBusy { get; private set; }

    public int ToAdd => Preview?.Add.Sum(group => group.Entries.Count) ?? 0;

    public Task PreviewFileAsync(string path) => PreviewAsync(new ImportSource.File(path));

    public Task PreviewContentAsync(string text, string format) => PreviewAsync(new ImportSource.Content(text, format));

    public async Task PreviewAsync(ImportSource source)
    {
        Source = source;
        Preview = null;
        Result = null;
        Error = null;
        IsBusy = true;
        try
        {
            Preview = await engine.ImportPreviewAsync(source);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
        finally
        {
            IsBusy = false;
            OnPropertyChanged(nameof(ToAdd));
        }
    }

    [RelayCommand]
    public async Task ApplyAsync()
    {
        if (Source is not { } source)
        {
            return;
        }
        IsBusy = true;
        try
        {
            Result = await engine.ImportApplyAsync(source);
            Added?.Invoke(Result.Targets);
        }
        catch (Exception exc) when (exc is EngineException or EngineUnavailableException or EngineExitedException or EngineProtocolException)
        {
            Error = exc.Message;
        }
        finally
        {
            IsBusy = false;
        }
    }

    public void Reset()
    {
        Source = null;
        Preview = null;
        Result = null;
        Error = null;
        OnPropertyChanged(nameof(ToAdd));
    }
}
