using System.Text.Json.Nodes;
using System.Text.Json.Serialization.Metadata;
using Youpdated.Core.Contract;

namespace Youpdated.Core.Engine;

public enum EngineState
{
    Stopped,
    Starting,
    Running,
    /// <summary>Crashed or failed to start</summary>
    Restarting,
    /// <summary>Uses another major contract</summary>
    Incompatible,
}

/// <summary>Owns the engine process</summary>
public sealed class EngineHost : IEngineChannel, IAsyncDisposable
{
    private static readonly TimeSpan[] Backoff =
    [
        TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(5),
        TimeSpan.FromSeconds(15), TimeSpan.FromSeconds(30), TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(5),
    ];

    // A process that lived this long counts as healthy
    private static readonly TimeSpan Healthy = TimeSpan.FromMinutes(2);

    private static readonly TimeSpan HelloTimeout = TimeSpan.FromSeconds(30);

    private readonly Func<IEngineTransport> _launch;
    private readonly ClientInfo _client;
    private readonly TimeProvider _time;
    private readonly Action<string>? _log;
    private readonly object _gate = new();
    private EngineConnection? _connection;
    private CancellationTokenSource? _restart;
    private int _failures;
    private bool _stopping;

    public EngineHost(Func<IEngineTransport> launch, ClientInfo client, TimeProvider time, Action<string>? log = null)
    {
        _launch = launch;
        _client = client;
        _time = time;
        _log = log;
    }

    public event Action<ProgressEvent>? Progress;

    public event Action<TargetKey>? IconArrived;

    /// <summary>A process answered <c>hello</c>; the app re-syncs (status, unlock)</summary>
    public event Action<HelloReply>? Started;

    /// <summary>The engine crashed or couldn't start</summary>
    public event Action<Problem>? Crashed;

    public event Action<EngineState>? StateChanged;

    public EngineState State { get; private set; }

    public HelloReply? Hello { get; private set; }

    /// <summary>When the next restart is due, while <see cref="State"/> is Restarting</summary>
    public DateTimeOffset? RestartAt { get; private set; }

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        lock (_gate)
        {
            if (State is EngineState.Starting or EngineState.Running || _stopping)
            {
                return;
            }
            State = EngineState.Starting;
        }
        StateChanged?.Invoke(EngineState.Starting);

        EngineConnection? connection = null;
        try
        {
            connection = new EngineConnection(_launch(), _log);
            connection.Progress += OnProgress;
            connection.IconArrived += OnIconArrived;
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(HelloTimeout);
            var client = new EngineClient(connection);
            var hello = await client.HelloAsync(_client, timeout.Token).ConfigureAwait(false);
            if (hello.Contract != EngineConnection.ContractVersion)
            {
                throw new EngineIncompatibleException(hello.Contract);
            }

            var startedAt = _time.GetUtcNow();
            lock (_gate)
            {
                _connection = connection;
                Hello = hello;
            }
            _ = WatchAsync(connection, startedAt);
            SetState(EngineState.Running);
            Started?.Invoke(hello);
        }
        catch (EngineIncompatibleException exc)
        {
            if (connection is not null)
            {
                await ShutdownQuietlyAsync(connection).ConfigureAwait(false);
            }
            SetState(EngineState.Incompatible);
            Crashed?.Invoke(new Problem(Problem.EngineKind, "engine", "-", exc.Message, null));
        }
        catch (Exception exc) when (exc is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            if (connection is not null)
            {
                connection.Transport.Kill();
                await connection.DisposeAsync().ConfigureAwait(false);
            }
            Failed(exc is OperationCanceledException ? "The Youpdated engine didn't answer." : exc.Message, null);
        }
    }

    /// <summary>Send <c>shutdown</c>, then stop; quitting the app</summary>
    public async Task StopAsync()
    {
        EngineConnection? connection;
        lock (_gate)
        {
            _stopping = true;
            _restart?.Cancel();
            connection = _connection;
            _connection = null;
        }
        if (connection is not null)
        {
            await ShutdownQuietlyAsync(connection).ConfigureAwait(false);
        }
        SetState(EngineState.Stopped);
    }

    public Task<T> SendAsync<T>(
        string op, JsonObject? parameters, JsonTypeInfo<T> reply, CancellationToken cancellationToken = default)
    {
        EngineConnection? connection;
        lock (_gate)
        {
            connection = _connection;
        }
        if (connection is null)
        {
            return Task.FromException<T>(new EngineUnavailableException(State switch
            {
                EngineState.Incompatible => "This Youpdated engine can't be used with this app.",
                EngineState.Restarting => "The Youpdated engine is restarting.",
                _ => "The Youpdated engine isn't running.",
            }));
        }
        return connection.SendAsync(op, parameters, reply, cancellationToken);
    }

    public async ValueTask DisposeAsync() => await StopAsync().ConfigureAwait(false);

    private void OnProgress(ProgressEvent progress) => Progress?.Invoke(progress);

    private void OnIconArrived(TargetKey target) => IconArrived?.Invoke(target);

    private async Task WatchAsync(EngineConnection connection, DateTimeOffset startedAt)
    {
        var exit = await connection.Closed.ConfigureAwait(false);
        connection.Progress -= OnProgress;
        connection.IconArrived -= OnIconArrived;
        lock (_gate)
        {
            if (_connection != connection)
            {
                return;
            }
            _connection = null;
            if (_stopping)
            {
                return;
            }
            if (_time.GetUtcNow() - startedAt >= Healthy)
            {
                _failures = 0;
            }
        }
        await connection.DisposeAsync().ConfigureAwait(false);
        var message = exit.ExitCode is { } code
            ? $"The Youpdated engine stopped unexpectedly (exit code {code})."
            : "The Youpdated engine stopped unexpectedly.";
        Failed(message, exit.StderrTail);
    }

    private void Failed(string message, string? stderr)
    {
        TimeSpan delay;
        CancellationTokenSource restart;
        lock (_gate)
        {
            if (_stopping)
            {
                return;
            }
            delay = Backoff[Math.Min(_failures, Backoff.Length - 1)];
            _failures++;
            _restart?.Dispose();
            restart = _restart = new CancellationTokenSource();
            RestartAt = _time.GetUtcNow() + delay;
        }
        var detail = string.IsNullOrWhiteSpace(stderr) ? message : $"{message}\n{LastLines(stderr, 5)}";
        _log?.Invoke($"{message} Restarting in {delay.TotalSeconds:0}s.");
        SetState(EngineState.Restarting);
        Crashed?.Invoke(new Problem(Problem.EngineKind, "engine", "-", detail, null));
        _ = RestartAfterAsync(delay, restart.Token);
    }

    private async Task RestartAfterAsync(TimeSpan delay, CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(delay, _time, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }
        lock (_gate)
        {
            RestartAt = null;
            if (_stopping)
            {
                return;
            }
            State = EngineState.Stopped;
        }
        await StartAsync(CancellationToken.None).ConfigureAwait(false);
    }

    private static async Task ShutdownQuietlyAsync(EngineConnection connection)
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
            await new EngineClient(connection).ShutdownAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // Gone already, or not answering: disposing closes stdin, then kills
        }
        await connection.DisposeAsync().ConfigureAwait(false);
    }

    private void SetState(EngineState state)
    {
        lock (_gate)
        {
            if (State == state)
            {
                return;
            }
            State = state;
        }
        StateChanged?.Invoke(state);
    }

    private static string LastLines(string text, int count)
    {
        var lines = text.Split('\n', StringSplitOptions.RemoveEmptyEntries);
        return string.Join('\n', lines.TakeLast(count));
    }
}
