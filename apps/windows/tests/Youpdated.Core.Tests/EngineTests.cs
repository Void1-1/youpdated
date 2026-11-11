using System.Text.Json.Nodes;
using Microsoft.Extensions.Time.Testing;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.Tests;

public class EngineConnectionTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task RepliesMatchTheirRequestsOutOfOrder()
    {
        var engine = new FakeEngine();
        engine.Hold("check");
        await using var connection = new EngineConnection(engine);
        var client = new EngineClient(connection);

        var check = client.CheckAsync(cancellationToken: Token);
        var inbox = await client.InboxListAsync(new InboxQuery(), Token);

        Assert.False(check.IsCompleted);
        Assert.Equal(6, inbox.Items.Count);
        engine.Release("check");
        Assert.Equal(4, (await check).New);
    }

    [Fact]
    public async Task ProgressEventsArrive()
    {
        var engine = new FakeEngine();
        await using var connection = new EngineConnection(engine);
        var seen = new List<ProgressEvent>();
        connection.Progress += seen.Add;

        await new EngineClient(connection).CheckAsync(cancellationToken: Token);

        Assert.Equal(7, seen.Count);
        Assert.Equal((6, 6), (seen[^1].Done, seen[^1].Of));
        Assert.True(seen[^1].Failed);
        Assert.Equal("@SomeChannel", seen[^1].Target!.Display);
    }

    [Fact]
    public async Task ErrorRepliesThrowWithTheirCode()
    {
        var engine = new FakeEngine().Next("targets.preview", "ambiguous");
        await using var connection = new EngineConnection(engine);

        var error = await Assert.ThrowsAsync<EngineException>(
            () => new EngineClient(connection).TargetsPreviewAsync("express", cancellationToken: Token));

        Assert.Equal(ErrorCodes.Ambiguous, error.Code);
        Assert.Equal(["homebrew", "npm", "pypi"], error.Choices);
    }

    [Fact]
    public async Task UnknownEventsAndStrayLinesAreSkipped()
    {
        var engine = new FakeEngine();
        var log = new List<string>();
        await using var connection = new EngineConnection(engine, log.Add);
        engine.Write("""{"event": "added_in_1_1", "x": 1}""");
        engine.Write("not json at all");

        var status = await new EngineClient(connection).StatusAsync(Token);

        Assert.False(status.Locked);
        Assert.Single(log);
    }

    [Fact]
    public async Task AnExitFailsWhatsInFlight()
    {
        var engine = new FakeEngine();
        engine.Hold("check");
        await using var connection = new EngineConnection(engine);

        var check = new EngineClient(connection).CheckAsync(cancellationToken: Token);
        engine.Crash(3, "boom");

        var error = await Assert.ThrowsAsync<EngineExitedException>(() => check);
        Assert.Equal(3, error.Exit!.ExitCode);
        Assert.Equal(3, (await connection.Closed).ExitCode);
    }

    [Fact]
    public async Task CancelingStopsWaiting()
    {
        var engine = new FakeEngine();
        engine.Hold("check");
        await using var connection = new EngineConnection(engine);
        using var cancel = new CancellationTokenSource();

        var check = new EngineClient(connection).CheckAsync(cancellationToken: cancel.Token);
        cancel.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => check);
    }

    [Fact]
    public void CommandLinesSplitLikeWindows()
    {
        Assert.Equal(
            [@"C:\Program Files\Python\python.exe", "-m", "youpdated"],
            EngineCommand.SplitCommandLine(@"""C:\Program Files\Python\python.exe"" -m youpdated"));
        Assert.Equal([@"a\\b", "c d", "e\"f"], EngineCommand.SplitCommandLine(@"a\\b ""c d"" e\""f"));
    }

    [Fact]
    public void TheBundledEngineSitsBesideTheApp()
    {
        var command = EngineCommand.Resolve(@"C:\Apps\Youpdated", _ => null);
        Assert.EndsWith(Path.Combine(@"C:\Apps\Youpdated", @"youpdated-engine\youpdated-engine.exe"), command.FileName);
        Assert.Equal(["api", "serve"], command.Arguments);
    }

    [Fact]
    public void TheDevOverrideRunsPythonWithTestPaths()
    {
        var environment = new Dictionary<string, string>
        {
            [EngineCommand.EngineVariable] = @"C:\src\youpdated\.venv\Scripts\python.exe -m youpdated",
            [EngineCommand.ConfigVariable] = @"C:\tmp\config.yaml",
            [EngineCommand.StateVariable] = @"C:\tmp\state.sqlite3",
        };
        var command = EngineCommand.Resolve(@"C:\Apps\Youpdated", name => environment.GetValueOrDefault(name));

        Assert.Equal(@"C:\src\youpdated\.venv\Scripts\python.exe", command.FileName);
        Assert.Equal(
            ["-m", "youpdated", "api", "serve", "--config", @"C:\tmp\config.yaml", "--state", @"C:\tmp\state.sqlite3"],
            command.Arguments);
    }

    [Fact]
    public void TheFrozenEngineIsAlsoTheCli()
    {
        var command = EngineCommand.Resolve(@"C:\Apps\Youpdated", _ => null, ["uninstall", "--keep-package", "--yes"]);
        Assert.EndsWith("youpdated-engine.exe", command.FileName);
        Assert.Equal(["uninstall", "--keep-package", "--yes"], command.Arguments);
    }
}

public class EngineHostTests
{
    private static readonly ClientInfo Client = new("Youpdated", "1.0.0", "windows");

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task StartsWithAHandshake()
    {
        var engine = new FakeEngine();
        await using var host = new EngineHost(() => engine, Client, new FakeTimeProvider());
        HelloReply? started = null;
        host.Started += hello => started = hello;

        await host.StartAsync(Token);

        Assert.Equal(EngineState.Running, host.State);
        Assert.Equal("1.0.0", started!.Engine);
        var hello = engine.RequestsFor("hello").Single();
        Assert.Equal("windows", hello["client"]!["platform"]!.GetValue<string>());
    }

    [Fact]
    public async Task RefusesAnotherContractMajor()
    {
        var engine = new FakeEngine().Then("hello", _ =>
        {
            var reply = (JsonObject)Fixtures.Get("hello/ok").Reply.DeepClone();
            reply["contract"] = 2;
            return [reply];
        });
        await using var host = new EngineHost(() => engine, Client, new FakeTimeProvider());
        Problem? problem = null;
        host.Crashed += p => problem = p;

        await host.StartAsync(Token);

        Assert.Equal(EngineState.Incompatible, host.State);
        Assert.Contains("contract 2", problem!.Message);
        await Assert.ThrowsAsync<EngineUnavailableException>(() => new EngineClient(host).StatusAsync(Token));
    }

    [Fact]
    public async Task RestartsAfterACrashWithBackoff()
    {
        var time = new FakeTimeProvider();
        var engines = new List<FakeEngine>();
        await using var host = new EngineHost(() =>
        {
            var engine = new FakeEngine();
            engines.Add(engine);
            return engine;
        }, Client, time);
        var problems = new List<Problem>();
        var started = 0;
        var restarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        host.Crashed += problems.Add;
        host.Started += _ =>
        {
            if (++started == 2)
            {
                restarted.TrySetResult();
            }
        };

        await host.StartAsync(Token);
        // Crashed after the state change
        var crashed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        host.Crashed += _ => crashed.TrySetResult();
        engines[0].Crash(1, "Traceback\nValueError: boom");
        await crashed.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);

        Assert.Equal(EngineState.Restarting, host.State);
        var problem = Assert.Single(problems);
        Assert.Equal(Problem.EngineKind, problem.Kind);
        Assert.Contains("exit code 1", problem.Message);
        Assert.Contains("ValueError: boom", problem.Message);

        for (var i = 0; i < 50 && !restarted.Task.IsCompleted; i++)
        {
            time.Advance(TimeSpan.FromSeconds(1));
            await Task.WhenAny(restarted.Task, Task.Delay(100, Token));
        }
        await restarted.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);
        Assert.Equal(2, engines.Count);
        Assert.Equal(EngineState.Running, host.State);
    }

    [Fact]
    public async Task StopSendsShutdownAndDoesNotRestart()
    {
        var engine = new FakeEngine();
        var launches = 0;
        await using var host = new EngineHost(() =>
        {
            launches++;
            return engine;
        }, Client, new FakeTimeProvider());
        await host.StartAsync(Token);

        await host.StopAsync();

        Assert.Single(engine.RequestsFor("shutdown"));
        Assert.Equal(EngineState.Stopped, host.State);
        Assert.Equal(1, launches);
    }

    [Fact]
    public async Task AFailedStartIsRetried()
    {
        var time = new FakeTimeProvider();
        var attempts = 0;
        await using var host = new EngineHost(() =>
        {
            if (++attempts == 1)
            {
                throw new EngineStartException("youpdated-engine\\youpdated-engine.exe: not found");
            }
            return new FakeEngine();
        }, Client, time);
        var running = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        host.Started += _ => running.TrySetResult();

        await host.StartAsync(Token);
        Assert.Equal(EngineState.Restarting, host.State);

        time.Advance(TimeSpan.FromSeconds(1));
        await running.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);
        Assert.Equal(2, attempts);
    }
}
