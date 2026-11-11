using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Youpdated.Core.Engine;

namespace Youpdated.Core.Tests;

/// <summary>An engine that answers from the contract fixtures. Each op gets its <c>ok</c> case unless a test queued another with <see cref="Next"/></summary>
public sealed class FakeEngine : IEngineTransport
{
    private readonly Channel<string> _lines = Channel.CreateUnbounded<string>();
    private readonly TaskCompletionSource<EngineExit> _exited = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly ConcurrentDictionary<string, ConcurrentQueue<Func<JsonObject, IEnumerable<JsonObject>>>> _scripts = new();
    private readonly ConcurrentQueue<JsonObject> _requests = new();
    private readonly ConcurrentDictionary<string, TaskCompletionSource> _holds = new();

    public ChannelReader<string> Lines => _lines.Reader;

    public Task<EngineExit> Exited => _exited.Task;

    /// <summary>Requests received in order</summary>
    public IReadOnlyList<JsonObject> Requests => [.. _requests];

    public IEnumerable<JsonObject> RequestsFor(string op) =>
        Requests.Where(request => request["op"]!.GetValue<string>() == op);

    /// <summary>Answer the next <paramref name="op"/> with the fixture case <paramref name="fixtureCase"/></summary>
    public FakeEngine Next(string op, string fixtureCase)
    {
        var fixture = Fixtures.Get($"{op}/{fixtureCase}");
        return Then(op, _ => Answer(fixture));
    }

    /// <summary>Answer the next <paramref name="op"/> with what <paramref name="answer"/> writes</summary>
    public FakeEngine Then(string op, Func<JsonObject, IEnumerable<JsonObject>> answer)
    {
        _scripts.GetOrAdd(op, _ => new ConcurrentQueue<Func<JsonObject, IEnumerable<JsonObject>>>()).Enqueue(answer);
        return this;
    }

    /// <summary>Hold replies to <paramref name="op"/> until <see cref="Release"/></summary>
    public void Hold(string op) => _holds[op] = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);

    public void Release(string op)
    {
        if (_holds.TryRemove(op, out var hold))
        {
            hold.TrySetResult();
        }
    }

    /// <summary>Write a line as if the engine had</summary>
    public void Write(string line) => _lines.Writer.TryWrite(line);

    /// <summary>The engine dies</summary>
    public void Crash(int exitCode = 1, string stderr = "Traceback (most recent call last):\n  boom")
    {
        _lines.Writer.TryComplete();
        _exited.TrySetResult(new EngineExit(exitCode, stderr));
    }

    public Task WriteLineAsync(string line, CancellationToken cancellationToken = default)
    {
        if (_exited.Task.IsCompleted)
        {
            throw new EngineExitedException(_exited.Task.Result);
        }
        var request = JsonNode.Parse(line)!.AsObject();
        _requests.Enqueue(request);
        var op = request["op"]!.GetValue<string>();
        _ = RespondAsync(op, request);
        return Task.CompletedTask;
    }

    public void CloseInput() => Crash(0, "");

    public void Kill() => Crash(-1, "");

    public ValueTask DisposeAsync()
    {
        Crash(0, "");
        return ValueTask.CompletedTask;
    }

    private async Task RespondAsync(string op, JsonObject request)
    {
        if (_holds.TryGetValue(op, out var hold))
        {
            await hold.Task;
        }
        var answer = _scripts.TryGetValue(op, out var queue) && queue.TryDequeue(out var scripted)
            ? scripted
            : Default(op);
        foreach (var line in answer(request))
        {
            if (line["event"] is null)
            {
                line["id"] = request["id"]!.GetValue<long>();
            }
            _lines.Writer.TryWrite(line.ToJsonString());
        }
        if (op == "shutdown")
        {
            Crash(0, "");
        }
    }

    private static Func<JsonObject, IEnumerable<JsonObject>> Default(string op)
    {
        var fixture = Fixtures.All.FirstOrDefault(f => f.Op == op && f.Case == "ok" && f.Name.StartsWith(op + "/", StringComparison.Ordinal))
            ?? Fixtures.All.FirstOrDefault(f => f.Op == op && f.Ok && f.Name.StartsWith(op + "/", StringComparison.Ordinal))
            ?? Fixtures.Get("_errors/unknown_op");
        return _ => Answer(fixture);
    }

    public static IEnumerable<JsonObject> Answer(Fixture fixture) =>
    [
        .. fixture.Events.Select(e => (JsonObject)e.DeepClone()),
        (JsonObject)fixture.Reply.DeepClone(),
        .. fixture.After.Select(e => (JsonObject)e.DeepClone()),
    ];

    /// <summary>A reply built in the test</summary>
    public static IEnumerable<JsonObject> Reply(JsonObject fields)
    {
        var reply = new JsonObject { ["ok"] = true };
        foreach (var (name, value) in fields)
        {
            reply[name] = value?.DeepClone();
        }
        return [reply];
    }

    public static IEnumerable<JsonObject> Error(string code, string message) =>
        [new JsonObject { ["ok"] = false, ["error"] = new JsonObject { ["code"] = code, ["message"] = message } }];
}
