using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization.Metadata;
using Youpdated.Core.Contract;

namespace Youpdated.Core.Engine;

public interface IEngineChannel
{
    /// <summary>Progress of a running check</summary>
    event Action<ProgressEvent>? Progress;

    /// <summary>A target's icon arrived after a check</summary>
    event Action<TargetKey>? IconArrived;

    /// <summary>Send <paramref name="op"/> and await reply; error replies throw <see cref="EngineException"/></summary>
    Task<T> SendAsync<T>(string op, JsonObject? parameters, JsonTypeInfo<T> reply, CancellationToken cancellationToken = default);
}

/// <summary>An engine process's JSON: numbers requests, matches replies to them in order returned, and raises events</summary>
public sealed class EngineConnection : IEngineChannel, IAsyncDisposable
{
    public const int ContractVersion = 1;

    private readonly IEngineTransport _transport;
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly Action<string>? _log;
    private long _nextId;

    public EngineConnection(IEngineTransport transport, Action<string>? log = null)
    {
        _transport = transport;
        _log = log;
        Closed = ReadAsync();
    }

    public event Action<ProgressEvent>? Progress;

    public event Action<TargetKey>? IconArrived;

    /// <summary>When the engine's output ends</summary>
    public Task<EngineExit> Closed { get; }

    public IEngineTransport Transport => _transport;

    public async Task<T> SendAsync<T>(
        string op, JsonObject? parameters, JsonTypeInfo<T> reply, CancellationToken cancellationToken = default)
    {
        var id = Interlocked.Increment(ref _nextId);
        var request = new JsonObject { ["id"] = id, ["op"] = op };
        if (parameters is not null)
        {
            foreach (var (name, value) in parameters)
            {
                request[name] = value?.DeepClone();
            }
        }

        var answer = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = answer;
        if (Closed.IsCompleted)
        {
            _pending.TryRemove(id, out _);
            throw new EngineExitedException(Closed.Result);
        }
        using var cancel = cancellationToken.Register(() =>
        {
            if (_pending.TryRemove(id, out var waiting))
            {
                waiting.TrySetCanceled(cancellationToken);
            }
        });

        try
        {
            await _transport.WriteLineAsync(request.ToJsonString(), cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            _pending.TryRemove(id, out _);
            throw;
        }

        var element = await answer.Task.ConfigureAwait(false);
        try
        {
            return element.Deserialize(reply)
                ?? throw new EngineProtocolException($"`{op}` replied null");
        }
        catch (JsonException exc)
        {
            throw new EngineProtocolException($"`{op}` reply doesn't match the contract: {exc.Message}", exc);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _transport.DisposeAsync().ConfigureAwait(false);
    }

    private async Task<EngineExit> ReadAsync()
    {
        try
        {
            await foreach (var line in _transport.Lines.ReadAllAsync().ConfigureAwait(false))
            {
                Dispatch(line);
            }
        }
        catch (Exception exc)
        {
            _log?.Invoke($"engine output: {exc.Message}");
        }

        var exit = await _transport.Exited.ConfigureAwait(false);
        foreach (var id in _pending.Keys)
        {
            if (_pending.TryRemove(id, out var waiting))
            {
                waiting.TrySetException(new EngineExitedException(exit));
            }
        }
        return exit;
    }

    private void Dispatch(string line)
    {
        if (string.IsNullOrWhiteSpace(line))
        {
            return;
        }
        JsonElement root;
        try
        {
            using var document = JsonDocument.Parse(line);
            root = document.RootElement.Clone();
        }
        catch (JsonException)
        {
            // stdout only has the protocol
            _log?.Invoke($"engine wrote a line that isn't JSON: {Truncate(line)}");
            return;
        }
        if (root.ValueKind != JsonValueKind.Object)
        {
            _log?.Invoke($"engine wrote a line that isn't an object: {Truncate(line)}");
            return;
        }

        if (root.TryGetProperty("event", out var kind))
        {
            OnEvent(kind.GetString(), root);
            return;
        }

        var ok = root.TryGetProperty("ok", out var okValue) && okValue.ValueKind == JsonValueKind.True;
        if (!root.TryGetProperty("id", out var idValue) || idValue.ValueKind != JsonValueKind.Number)
        {
            // An error about a line it couldn't read at all
            _log?.Invoke($"engine reply without an id: {Truncate(line)}");
            return;
        }
        if (!_pending.TryRemove(idValue.GetInt64(), out var waiting))
        {
            // Canceled during
            return;
        }
        if (ok)
        {
            waiting.TrySetResult(root);
            return;
        }
        waiting.TrySetException(ReadError(root));
    }

    private static Exception ReadError(JsonElement root)
    {
        if (!root.TryGetProperty("error", out var error))
        {
            return new EngineProtocolException("an error reply without `error`");
        }
        try
        {
            var parsed = error.Deserialize(ContractJson.Default.EngineError);
            return parsed is null
                ? new EngineProtocolException("an error reply with a null `error`")
                : new EngineException(parsed);
        }
        catch (JsonException exc)
        {
            return new EngineProtocolException($"an error reply that doesn't match the contract: {exc.Message}", exc);
        }
    }

    private void OnEvent(string? kind, JsonElement root)
    {
        try
        {
            switch (kind)
            {
                case ProgressEvent.Name when root.Deserialize(ContractJson.Default.ProgressEvent) is { } progress:
                    Progress?.Invoke(progress);
                    break;
                case IconEvent.Name when root.Deserialize(ContractJson.Default.IconEvent) is { } icon:
                    IconArrived?.Invoke(icon.Target);
                    break;
            }
        }
        catch (JsonException exc)
        {
            _log?.Invoke($"engine {kind} event doesn't match the contract: {exc.Message}");
        }
    }

    private static string Truncate(string line) => line.Length <= 200 ? line : line[..200] + "…";
}
