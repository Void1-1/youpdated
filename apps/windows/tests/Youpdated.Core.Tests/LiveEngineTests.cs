using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.Tests;

public class LiveEngineTests
{
    private const string Variable = "YOUPDATED_LIVE_ENGINE";

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task FirstLaunchAgainstTheRealEngine()
    {
        var engine = Environment.GetEnvironmentVariable(Variable);
        Assert.SkipWhen(string.IsNullOrEmpty(engine), $"{Variable} isn't set");

        var folder = Directory.CreateTempSubdirectory("youpdated-live-").FullName;
        try
        {
            var config = Path.Combine(folder, "config.yaml");
            var variables = new Dictionary<string, string>
            {
                [EngineCommand.EngineVariable] = engine!,
                [EngineCommand.ConfigVariable] = config,
                [EngineCommand.StateVariable] = Path.Combine(folder, "state.sqlite3"),
            };
            var command = EngineCommand.Resolve(AppContext.BaseDirectory, name => variables.GetValueOrDefault(name));
            var connection = new EngineConnection(ProcessTransport.Start(command));
            await using (connection)
            {
                var client = new EngineClient(connection);

                var hello = await client.HelloAsync(new ClientInfo("Youpdated", "1.0.0", "windows"), Token);
                Assert.Equal(EngineConnection.ContractVersion, hello.Contract);
                Assert.Empty(ContractTests.ReplyTypes.Keys.Except(hello.Ops));

                var first = await client.StatusAsync(Token);
                Assert.False(first.ConfigExists);
                Assert.False(first.Locked);

                var created = await client.ConfigInitAsync(selfWatch: true, Token);
                Assert.True(created.Created);
                Assert.True(created.SelfWatchAdded);
                Assert.Equal(config, created.ConfigPath);

                var status = await client.StatusAsync(Token);
                Assert.Equal(SelfWatch.Watching, status.SelfWatch);
                Assert.Equal(Settings.Defaults, (await client.SettingsGetAsync(Token)).Settings);
                var changed = await client.SettingsSetAsync(new SettingsPatch().Digest(new TimeOnly(18, 0)), Token);
                Assert.Equal("18:00", changed.Settings.DigestTime);
                var off = await client.SettingsSetAsync(new SettingsPatch().Digest(null), Token);
                Assert.Null(off.Settings.DigestTime);

                var targets = (await client.TargetsListAsync(Token)).Targets;
                var self = Assert.Single(targets);
                Assert.True(self.Starred);
                var priority = await client.PrioritySetAsync(self.AsKey(), Priority.High, Token);
                Assert.Equal(Priority.High, priority.Priority);

                var page = await client.InboxListAsync(new InboxQuery(), Token);
                Assert.Empty(page.Items);
                Assert.Null(page.Cursor);
                await client.ProblemsListAsync(Token);

                // Windows line endings, as a file saved in Notepad would have
                var preview = await client.ImportPreviewAsync(
                    new ImportSource.Content("httpx>=0.27\r\nrich\r\n", "requirements.txt"), Token);
                Assert.Equal("pypi", preview.Add[0].Source);

                var error = await Assert.ThrowsAsync<EngineException>(
                    () => client.TargetsPreviewAsync("hello there", cancellationToken: Token));
                Assert.Equal(ErrorCodes.Unrecognized, error.Code);

                await client.ShutdownAsync(Token);
                var exit = await connection.Closed.WaitAsync(TimeSpan.FromSeconds(10), Token);
                Assert.True(exit.ExitCode == 0, exit.StderrTail);
            }
        }
        finally
        {
            Directory.Delete(folder, recursive: true);
        }
    }
}
