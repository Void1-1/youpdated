using System.Text.Json;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public class NotesTests
{
    [Fact]
    public void ParsesWhatReleaseNotesUse()
    {
        var blocks = Notes.Parse("""
            ## What's new

            Security fixes for **ssl** and `email`,
            see [the changelog](https://docs.python.org/changelog).

            - gh-12345: fix a crash
            * gh-12346: https://github.com/python/cpython/issues/12346.

            ```
            pip install -U thing
            ```
            """);

        Assert.Equal(
            [NoteBlockKind.Heading, NoteBlockKind.Paragraph, NoteBlockKind.Bullet, NoteBlockKind.Bullet, NoteBlockKind.Code],
            blocks.Select(b => b.Kind));
        var paragraph = blocks[1].Spans;
        Assert.Contains(new NoteSpan(NoteSpanKind.Bold, "ssl"), paragraph);
        Assert.Contains(new NoteSpan(NoteSpanKind.Code, "email"), paragraph);
        Assert.Contains(new NoteSpan(NoteSpanKind.Link, "the changelog", "https://docs.python.org/changelog"), paragraph);
        Assert.Contains(new NoteSpan(NoteSpanKind.Link, "https://github.com/python/cpython/issues/12346", "https://github.com/python/cpython/issues/12346"), blocks[3].Spans);
        Assert.Equal("pip install -U thing", blocks[4].Spans[0].Text);
    }

    [Fact]
    public void OnlyWebLinksAreLinks()
    {
        var spans = Notes.Inline("[run me](javascript:alert(1)) and [file](file:///etc/passwd)");
        Assert.DoesNotContain(spans, span => span.Kind == NoteSpanKind.Link);
    }

    [Fact]
    public void LongNotesAreClipped()
    {
        var markdown = string.Join("\n", Enumerable.Range(1, 10).Select(i => $"- change {i}"));
        var preview = Notes.Preview(markdown);
        Assert.True(preview.Clipped);
        Assert.Equal(Notes.PreviewBlocks, preview.Blocks.Count);
        Assert.False(Notes.Preview("one line").Clipped);
    }

    [Theory]
    [InlineData("## Highlights\nFaster", "Highlights")]
    [InlineData("\n\n- **Fixed** a crash", "Fixed a crash")]
    [InlineData("```\ncode\n```\nThen words", "Then words")]
    [InlineData("", null)]
    [InlineData(null, null)]
    public void FirstLine(string? markdown, string? expected) => Assert.Equal(expected, Notes.FirstLine(markdown));
}

public class MonogramTests
{
    [Theory]
    [InlineData("Team Fortress 2", "TF")]
    [InlineData("Hades II", "HI")]
    [InlineData("python/cpython", "C")]
    [InlineData("@SomeChannel", "S")]
    [InlineData("@vitejs/plugin-react", "P")]
    [InlineData("react", "R")]
    [InlineData("440", "4")]
    [InlineData("", "?")]
    public void Initials(string display, string initials) => Assert.Equal(initials, Monogram.InitialsOf(display));

    // Shared across app platforms
    [Fact]
    public void TheColorIsTheTargets()
    {
        // FNV-1a, fixed
        Assert.Equal(0xE40C292Cu, Monogram.Hash("a"));
        var cpython = Monogram.For(new TargetKey("git", "github.com/python/cpython"), "python/cpython");
        Assert.Equal(cpython.ColorIndex, Monogram.For(new TargetKey("git", "github.com/python/cpython"), "CPython").ColorIndex);
        Assert.NotEqual(cpython.ColorIndex, Monogram.For(new TargetKey("git", "gitlab.com/inkscape/inkscape"), "inkscape/inkscape").ColorIndex);
    }

    [Fact]
    public void TheFillIsLighterAtTheTop()
    {
        var red = new Monogram("R", 0);
        Assert.Equal((0xD9, 0x53, 0x4F), ((int)red.Color.R, (int)red.Color.G, (int)red.Color.B));
        Assert.True(red.Lighter.R > red.Color.R && red.Lighter.G > red.Color.G && red.Lighter.B > red.Color.B);
    }
}

public class FollowingTests
{
    private static Target T(string source, string key, string? display = null, bool starred = false,
        DateTimeOffset? checkedAt = null, string? error = null, TargetLatest? latest = null) =>
        new(source, key, display ?? key, Priority.Normal, starred, checkedAt ?? Make.Now, error, 0, latest);

    [Fact]
    public void GroupedByLabelWithForgesApart()
    {
        var layout = FollowingBuilder.Build(
        [
            T("git", "github.com/Void1-1/youpdated", "Void1-1/youpdated", starred: true),
            T("git", "github.com/python/cpython", "python/cpython"),
            T("git", "github.com/godotengine/godot", "godotengine/godot"),
            T("git", "codeberg.org/forgejo/forgejo", "forgejo/forgejo"),
            T("steam", "440", "Team Fortress 2"),
            T("npm", "react"),
        ], null, Make.App);

        Assert.Equal("1.0.0", layout.Starred!.LatestVersion);
        Assert.False(layout.SelfMissing);
        Assert.Equal(["Codeberg", "GitHub", "npm", "Steam"], layout.Groups.Select(g => g.Label));
        Assert.Equal(["godotengine/godot", "python/cpython"], layout.Groups[1].Select(r => r.Display));
    }

    [Fact]
    public void LatestVersionOrTheDateOfTheLatestPost()
    {
        var posted = new DateTimeOffset(2026, 10, 29, 19, 30, 0, TimeSpan.Zero);
        var layout = FollowingBuilder.Build(
        [
            T("git", "github.com/python/cpython", latest: new TargetLatest("v3.14.7", "Python 3.14.7", Make.Ago(5))),
            T("steam", "440", latest: new TargetLatest(null, "CLTF2 Halloween Cup", posted)),
            T("npm", "react"),
        ], null, Make.App);

        var rows = layout.Groups.SelectMany(g => g).ToDictionary(r => r.Target.Key);
        Assert.Equal(("3.14.7", (DateTimeOffset?)null), (rows["github.com/python/cpython"].LatestVersion, rows["github.com/python/cpython"].LatestDate));
        Assert.Equal((null, (DateTimeOffset?)posted), (rows["440"].LatestVersion, rows["440"].LatestDate));
        Assert.Equal((null, (DateTimeOffset?)null), (rows["react"].LatestVersion, rows["react"].LatestDate));
    }

    [Fact]
    public void LatestComesFromTargetsList()
    {
        var targets = Fixtures.Get("targets.list/ok").Reply.ToElement().Deserialize(ContractJson.Default.TargetsListReply)!.Targets;
        var rows = FollowingBuilder.Build(targets, null, Make.App).Groups.SelectMany(g => g).ToDictionary(r => r.Target.Key);

        Assert.Equal("3.14.7", rows["github.com/python/cpython"].LatestVersion);
        Assert.Equal(new DateTimeOffset(2026, 10, 28, 19, 30, 0, TimeSpan.Zero), rows["440"].LatestDate);
        // Before first fetch
        Assert.Null(rows["UC123"].LatestVersion);
        Assert.Null(rows["UC123"].LatestDate);
    }

    [Fact]
    public void StatusAndTheMissingSelfEntry()
    {
        var layout = FollowingBuilder.Build(
        [
            new Target("youtube", "UC123", "@SomeChannel", Priority.Normal, false, null, "HTTP 404", 0, null),
            new Target("npm", "vite", "vite", Priority.Normal, false, null, null, 0, null),
        ], null, Make.App);

        Assert.True(layout.SelfMissing);
        Assert.Null(layout.Starred);
        var rows = layout.Groups.SelectMany(g => g).ToDictionary(r => r.Target.Key);
        Assert.True(rows["UC123"].HasError);
        Assert.False(rows["UC123"].NotCheckedYet);
        Assert.True(rows["vite"].NotCheckedYet);
    }

    [Fact]
    public void TheFilterMatchesNameKeyOrLabel()
    {
        var targets = new[] { T("git", "github.com/python/cpython", "python/cpython"), T("steam", "440", "Team Fortress 2"), T("npm", "react") };
        IEnumerable<string> Keys(string filter) => FollowingBuilder.Build(targets, filter, Make.App).Groups.SelectMany(g => g).Select(r => r.Target.Key);
        Assert.Equal(["440"], Keys("fortress"));
        Assert.Equal(["react"], Keys("NPM"));
        Assert.Equal(["github.com/python/cpython"], Keys("github.com/py"));
        Assert.Equal(["github.com/python/cpython"], Keys("GitHub"));
    }

    [Fact]
    public async Task TheViewModelFollowsSourcesAndTheFilter()
    {
        await using var test = new TestApp();
        await test.StartAsync();
        var following = test.App.Following;

        Assert.True(following.Layout.Starred!.Target.Starred);
        Assert.False(following.NothingFollowed);
        following.Filter = "react";
        Assert.Equal(["react"], following.Layout.Groups.SelectMany(g => g).Select(r => r.Display));
    }
}

public class IconTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task OnlyWhenTurnedOn()
    {
        var engine = new FakeEngine();
        await using var connection = new EngineConnection(engine);
        var cache = new IconCache(new EngineClient(connection));

        cache.Configure(showIcons: false);
        Assert.Null(await cache.GetAsync(new TargetKey("steam", "440")));
        Assert.Empty(engine.RequestsFor("icon.get"));

        cache.Configure(showIcons: true);
        var bytes = await cache.GetAsync(new TargetKey("steam", "440"));
        Assert.Equal(new byte[] { 0x89, (byte)'P', (byte)'N', (byte)'G' }, bytes![..4]);
        // Asked once per target
        await cache.GetAsync(new TargetKey("steam", "440"));
        Assert.Single(engine.RequestsFor("icon.get"));
    }

    [Fact]
    public async Task TheIconEventFetchesItAgain()
    {
        var engine = new FakeEngine().Next("icon.get", "none");
        await using var connection = new EngineConnection(engine);
        var client = new EngineClient(connection);
        var cache = new IconCache(client);
        cache.Configure(true);
        var arrived = new List<TargetKey>();
        cache.TargetChanged += arrived.Add;
        client.IconArrived += cache.Arrived;
        Assert.Null(await cache.GetAsync(new TargetKey("steam", "440")));

        var both = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        client.IconArrived += _ =>
        {
            if (arrived.Count == 2)
            {
                both.TrySetResult();
            }
        };
        engine.Write("{\"event\": \"icon\", \"target\": {\"source\": \"steam\", \"key\": \"440\"}}");
        engine.Write("{\"event\": \"icon\", \"target\": {\"source\": \"npm\", \"key\": \"react\"}}");
        await both.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);

        Assert.Equal([new TargetKey("steam", "440"), new TargetKey("npm", "react")], arrived);
        Assert.NotNull(await cache.GetAsync(new TargetKey("steam", "440")));
    }

    [Fact]
    public async Task NoneMeansTheMonogram()
    {
        var engine = new FakeEngine().Next("icon.get", "none");
        await using var connection = new EngineConnection(engine);
        var cache = new IconCache(new EngineClient(connection));
        cache.Configure(true);

        Assert.Null(await cache.GetAsync(new TargetKey("npm", "react")));
    }

    [Fact]
    public async Task TurningIconsOnSavesTheSetting()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        var settings = Fixtures.Get("settings.get/ok").Reply["settings"]!.DeepClone();
        settings["show_icons"] = true;
        engine.Then("settings.set", _ => FakeEngine.Reply(new System.Text.Json.Nodes.JsonObject { ["settings"] = settings }));
        test.App.Settings.ShowIcons = true;
        await test.App.Settings.PendingSave;

        Assert.True(engine.RequestsFor("settings.set").Single()["settings"]!["show_icons"]!.GetValue<bool>());
        Assert.True(test.App.Icons.Enabled);
    }
}

public class ProblemsRetryTests
{
    [Fact]
    public async Task RetryChecksExactlyThatTargetByKey()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var youtube = test.App.Problems.Problems.First(p => p.Kind == Problem.SourceKind);
        Assert.Equal("UC123", youtube.Key);

        Assert.True(AppViewModel.CanRetry(youtube));
        await test.App.RetryAsync(youtube);

        var check = engine.RequestsFor("check").Single();
        Assert.Equal("youtube", check["targets"]![0]!["source"]!.GetValue<string>());
        Assert.Equal("UC123", check["targets"]![0]!["key"]!.GetValue<string>());
    }

    [Fact]
    public async Task AConfigProblemRetriesItsSource()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        // A config problem doesn't have a target -> no key
        var youtube = new Problem(Problem.ConfigKind, "youtube", "-", "bad entry", null);

        await test.App.RetryAsync(youtube);

        var check = engine.RequestsFor("check").Single();
        Assert.Null(check["targets"]);
        Assert.Equal("youtube", check["sources"]![0]!.GetValue<string>());
    }

    [Fact]
    public async Task AFailedWebhookIsNotRetried()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var hook = test.App.Problems.Problems.First(p => p.Kind == Problem.NotifyKind);

        Assert.False(AppViewModel.CanRetry(hook));
        Assert.False(await test.App.RetryAsync(hook));
        Assert.Empty(engine.RequestsFor("check"));
    }

    [Fact]
    public async Task CopyDetailsHasEveryProblem()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        var details = test.App.Problems.Details();

        Assert.StartsWith("Youpdated, check at 2026-10-29T08:00:06Z", details);
        Assert.Contains("youtube @SomeChannel: FetchError", details);
        Assert.Contains("notify https://ntfy.sh/…: HTTP 502", details);
    }
}

public class WindowStateTests
{
    [Fact]
    public async Task LockedThenTheFeed()
    {
        var engine = new FakeEngine().Next("status", "locked");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        Assert.Equal(WindowState.Locked, test.App.WindowState);

        test.App.Unlock.Passphrase = "correct horse";
        await test.App.Unlock.UnlockAsync();
        Assert.Equal(WindowState.Feed, test.App.WindowState);
    }

    [Fact]
    public async Task NothingFollowedButYoupdatedIsTheWelcome()
    {
        var engine = new FakeEngine()
            .Next("status", "first_launch")
            .Then("targets.list", _ => FakeEngine.Reply(new System.Text.Json.Nodes.JsonObject
            {
                ["targets"] = new System.Text.Json.Nodes.JsonArray(
                    Fixtures.Get("targets.list/ok").Reply["targets"]![0]!.DeepClone()),
            }))
            .Then("inbox.list", _ => FakeEngine.Reply(new System.Text.Json.Nodes.JsonObject
            {
                ["items"] = new System.Text.Json.Nodes.JsonArray(), ["cursor"] = null, ["total"] = 0, ["unread"] = 0,
            }));
        await using var test = new TestApp(engine);
        await test.StartAsync();

        Assert.True(test.App.Following.NothingFollowed);
        Assert.Equal(WindowState.Welcome, test.App.WindowState);
    }

    [Fact]
    public async Task PausingShowsTheSlimBar()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        test.App.PauseForHour();
        Assert.Equal(test.Time.GetUtcNow() + TimeSpan.FromHours(1), test.App.PausedUntil);
        test.App.Resume();
        Assert.Null(test.App.PausedUntil);
    }

    [Fact]
    public async Task TheCliBarIsDismissedForGood()
    {
        var engine = new FakeEngine().Next("status", "cli_ran");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        Assert.True(test.App.ShowCliNotice);

        await test.App.DismissCliNoticeAsync();

        Assert.False(test.App.ShowCliNotice);
        Assert.True(engine.RequestsFor("settings.set").Single()["settings"]!["cli_notice_shown"]!.GetValue<bool>());
    }
}
