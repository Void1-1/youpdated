using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

/// <summary>Items built in the test (Thursday 2026-10-29 15:00 UTC)</summary>
internal static class Make
{
    public static readonly DateTimeOffset Now = new(2026, 10, 29, 15, 0, 0, TimeSpan.Zero);

    public static readonly Version App = new(1, 0, 0);

    private static int _next;

    public static InboxItem Item(
        string target, DateTimeOffset found, bool read = false, Priority priority = Priority.Normal,
        string source = "git", string? version = null, string? title = null, string? body = null,
        string? display = null, bool starred = false, DateTimeOffset? published = null) =>
        new(
            $"i_{Interlocked.Increment(ref _next)}", source, target, display ?? target, title ?? version ?? "an update",
            $"https://example.com/{target}", version, published ?? found, [], body, found, read, false, priority, starred, null);

    public static DateTimeOffset Ago(double hours) => Now - TimeSpan.FromHours(hours);

    public static FeedLayout Build(
        IEnumerable<InboxItem> items, GroupBy group = GroupBy.Day, string? source = null, TargetKey? target = null, ViewOptions? view = null) =>
        FeedBuilder.Build(items, new FeedOptions(view ?? new ViewOptions(GroupBy: group), source, target), Now, TimeZoneInfo.Utc, App);
}

public class FeedBuilderTests
{
    [Fact]
    public void UnreadByDayFoundThenReadUnderEarlier()
    {
        var layout = Make.Build(
        [
            Make.Item("a", Make.Ago(1)),
            Make.Item("b", Make.Ago(20)), // yesterday, 19:00
            Make.Item("c", Make.Ago(24 * 4)), // Sunday
            Make.Item("d", Make.Ago(24 * 10)), // older, unread
            Make.Item("e", Make.Ago(2), read: true),
        ]);

        Assert.Equal(
            [FeedSectionKind.Today, FeedSectionKind.Yesterday, FeedSectionKind.ThisWeek, FeedSectionKind.Earlier],
            layout.Sections.Select(s => s.Key.Kind));
        Assert.Equal(["d", "e"], layout.Sections[^1].Cards.Select(c => c.Display));
        Assert.False(layout.Sections[^1].Cards[0].IsDimmed);
        Assert.True(layout.Sections[^1].Cards[1].IsDimmed);
        Assert.Equal(4, layout.Unread);
    }

    [Fact]
    public void ATargetsUpdatesShareOneCardWithTheNewestLeading()
    {
        var layout = Make.Build(
        [
            Make.Item("tf2", Make.Ago(5), source: "steam", title: "Older news", published: Make.Ago(9)),
            Make.Item("tf2", Make.Ago(5), source: "steam", title: "CLTF2 Halloween Cup", published: Make.Ago(5)),
            Make.Item("tf2", Make.Ago(30), source: "steam", title: "Earlier still"),
        ]);

        var card = Assert.Single(Assert.Single(layout.Sections).Cards);
        Assert.Equal("CLTF2 Halloween Cup", card.Lead.Title);
        Assert.Equal(2, card.More);
        Assert.Equal(["Older news", "Earlier still"], card.AlsoNew.Select(i => i.Title));
    }

    [Fact]
    public void LowPriorityCardsAreCompactRows()
    {
        var layout = Make.Build(
        [
            Make.Item("high", Make.Ago(1), priority: Priority.High),
            Make.Item("normal", Make.Ago(1)),
            Make.Item("low", Make.Ago(1), priority: Priority.Low),
            Make.Item("read", Make.Ago(1), read: true, priority: Priority.High),
        ]);

        var cards = layout.Cards.ToDictionary(c => c.Display);
        Assert.False(cards["high"].IsCompact);
        Assert.False(cards["normal"].IsCompact);
        Assert.True(cards["low"].IsCompact);
        Assert.False(cards["read"].IsCompact);
        Assert.True(cards["high"].ShowsHighPriority && cards["high"].ShowsAccentEdge);
        Assert.False(cards["normal"].ShowsHighPriority);
        Assert.True(cards["read"].ShowsHighPriority);
        Assert.False(cards["read"].ShowsAccentEdge);
    }

    [Fact]
    public void TheVersionLeadsWithTheTitleUnderIt()
    {
        var cards = Make.Build(
        [
            Make.Item("cpython", Make.Ago(1), version: "v3.14.7", title: "Python 3.14.7", body: "Security fixes for ssl"),
            Make.Item("godot", Make.Ago(2), version: "4.5.1", title: "4.5.1", body: "## Highlights\nFaster 2D batching"),
            Make.Item("tf2", Make.Ago(3), title: "CLTF2 Halloween Cup", body: "Sign-ups are open"),
        ]).Cards.ToDictionary(c => c.Display);

        Assert.Equal(("3.14.7", "Python 3.14.7"), (cards["cpython"].LeadText, cards["cpython"].Line));
        Assert.Equal(("4.5.1", "Highlights"), (cards["godot"].LeadText, cards["godot"].Line));
        Assert.Equal(("CLTF2 Halloween Cup", "Sign-ups are open"), (cards["tf2"].LeadText, cards["tf2"].Line));
    }

    [Fact]
    public void AnUnreadAppUpdateIsPinnedAboveTheSections()
    {
        var update = Make.Item("github.com/Void1-1/youpdated", Make.Ago(1), version: "app-windows-v1.1.0", starred: true);
        var layout = Make.Build([update, Make.Item("a", Make.Ago(2))]);

        Assert.Equal(new Version(1, 1, 0), layout.SelfUpdate!.Version);
        Assert.DoesNotContain(layout.Cards, card => card.Starred);
        Assert.Equal(2, layout.Unread);

        var read = Make.Build([update with { Read = true }]);
        Assert.Null(read.SelfUpdate);
        Assert.True(Assert.Single(read.Cards).Starred);
    }

    [Fact]
    public void GroupingReplacesTheDaySections()
    {
        IEnumerable<InboxItem> Items() =>
        [
            Make.Item("zeta", Make.Ago(1), source: "npm", priority: Priority.Low),
            Make.Item("alpha", Make.Ago(2), source: "steam", priority: Priority.High),
            Make.Item("beta", Make.Ago(3), source: "npm"),
            Make.Item("@chan", Make.Ago(4), source: "youtube"),
            Make.Item("old", Make.Ago(5), read: true),
        ];

        var byPriority = Make.Build(Items(), view: new ViewOptions(GroupBy: GroupBy.Priority, GroupReadUpdates: false));
        Assert.Equal(["High", "Normal", "Low", null], byPriority.Sections.Select(s => s.Key.Value));
        Assert.Equal(FeedSectionKind.Earlier, byPriority.Sections[^1].Key.Kind);

        // By label
        var bySource = Make.Build(Items(), view: new ViewOptions(GroupBy: GroupBy.Source, GroupReadUpdates: false));
        Assert.Equal(["npm", "Steam", "YouTube", null], bySource.Sections.Select(s => s.Key.Value));
        Assert.Equal(["zeta", "beta"], bySource.Sections[0].Cards.Select(c => c.Display));

        var none = Make.Build(Items(), view: new ViewOptions(GroupBy: GroupBy.None, GroupReadUpdates: false));
        Assert.Equal([FeedSectionKind.Unread, FeedSectionKind.Earlier], none.Sections.Select(s => s.Key.Kind));
        Assert.Equal(["zeta", "alpha", "beta", "@chan"], none.Sections[0].Cards.Select(c => c.Display));
    }

    [Fact]
    public void ReadUpdatesJoinTheirGroup()
    {
        InboxItem[] items =
        [
            Make.Item("alpha", Make.Ago(2), priority: Priority.High),
            Make.Item("beta", Make.Ago(3)),
            Make.Item("old", Make.Ago(1), read: true),
        ];
        var byPriority = Make.Build(items, GroupBy.Priority);
        Assert.Equal(["High", "Normal"], byPriority.Sections.Select(s => s.Key.Value));
        // after the group's unread cards
        Assert.Equal(["beta", "old"], byPriority.Sections[1].Cards.Select(c => c.Display));
        Assert.Equal([FeedSectionKind.Unread], Make.Build(items, GroupBy.None).Sections.Select(s => s.Key.Kind));
        Assert.Equal(FeedSectionKind.Earlier, Make.Build(items).Sections[^1].Key.Kind);
    }

    [Fact]
    public void FiltersByLabelAndTarget()
    {
        IEnumerable<InboxItem> Items() =>
        [
            Make.Item("a", Make.Ago(1), source: "npm"),
            Make.Item("b", Make.Ago(1), source: "steam"),
            Make.Item("github.com/python/cpython", Make.Ago(1)),
            Make.Item("gitlab.com/inkscape/inkscape", Make.Ago(1)),
        ];
        Assert.Equal(["b"], Make.Build(Items(), source: "Steam").Cards.Select(c => c.Target.Key));
        Assert.Equal(["gitlab.com/inkscape/inkscape"], Make.Build(Items(), source: "GitLab").Cards.Select(c => c.Target.Key));
        Assert.Equal(["a"], Make.Build(Items(), target: new TargetKey("npm", "a")).Cards.Select(c => c.Display));
    }

    [Fact]
    public void HeldItemsStayWhereTheyWereWhileExpanded()
    {
        var item = Make.Item("a", Make.Ago(1)) with { Read = true };
        var held = FeedBuilder.Build([item], new FeedOptions(), Make.Now, TimeZoneInfo.Utc, Make.App, new HashSet<string> { item.Id });

        var section = Assert.Single(held.Sections);
        Assert.Equal(FeedSectionKind.Today, section.Key.Kind);
        Assert.False(section.Cards[0].ShowsUnread);
        Assert.Equal(0, held.Unread);
    }
}

public class FeedHeadingTests
{
    private static string Since(DateTimeOffset found)
    {
        var layout = Make.Build([Make.Item("a", found)]);
        return FeedHeading.For(layout, Make.Now, Make.Now, TimeZoneInfo.Utc).SinceText(new FeedText())!;
    }

    [Fact]
    public void NamesWhenTheOldestUnreadWasFound()
    {
        Assert.Equal("Since this morning", Since(new DateTimeOffset(2026, 10, 29, 7, 30, 0, TimeSpan.Zero)));
        Assert.Equal("Since this afternoon", Since(new DateTimeOffset(2026, 10, 29, 12, 0, 0, TimeSpan.Zero)));
        Assert.Equal("Since yesterday", Since(new DateTimeOffset(2026, 10, 28, 22, 0, 0, TimeSpan.Zero)));
        Assert.Equal("Since Tuesday", Since(new DateTimeOffset(2026, 10, 27, 9, 0, 0, TimeSpan.Zero)));
        Assert.Equal("Since Friday", Since(new DateTimeOffset(2026, 10, 23, 9, 0, 0, TimeSpan.Zero)));
        Assert.Equal("Since Oct 12", Since(new DateTimeOffset(2026, 10, 12, 9, 0, 0, TimeSpan.Zero)));
        Assert.Equal("Since 12/30/2025", Since(new DateTimeOffset(2025, 12, 30, 9, 0, 0, TimeSpan.Zero)));
    }

    [Fact]
    public void TheEveningIsFromSix()
    {
        var now = new DateTimeOffset(2026, 10, 29, 23, 0, 0, TimeSpan.Zero);
        var layout = FeedBuilder.Build([Make.Item("a", now.AddHours(-4))], new FeedOptions(), now, TimeZoneInfo.Utc, Make.App);
        Assert.Equal("Since this evening", FeedHeading.For(layout, null, now, TimeZoneInfo.Utc).SinceText(new FeedText()));
    }

    [Fact]
    public void UsesTheLocalDay()
    {
        var zone = TimeZoneInfo.FindSystemTimeZoneById("America/New_York");
        var layout = FeedBuilder.Build([Make.Item("a", new DateTimeOffset(2026, 10, 29, 1, 0, 0, TimeSpan.Zero))],
            new FeedOptions(), Make.Now, zone, Make.App);
        Assert.Equal("Since yesterday", FeedHeading.For(layout, null, Make.Now, zone).SinceText(new FeedText()));
    }

    [Fact]
    public void TheCountLeads()
    {
        var two = Make.Build([Make.Item("a", Make.Ago(1)), Make.Item("b", Make.Ago(2)), Make.Item("c", Make.Ago(3), read: true)]);
        Assert.Equal("2 new updates", FeedHeading.For(two, null, Make.Now, TimeZoneInfo.Utc).Title(new FeedText()));
        var one = Make.Build([Make.Item("a", Make.Ago(1))]);
        Assert.Equal("1 new update", FeedHeading.For(one, null, Make.Now, TimeZoneInfo.Utc).Title(new FeedText()));
    }

    [Fact]
    public void TheLineJoinsWhatApplies()
    {
        var layout = Make.Build([Make.Item("a", Make.Ago(7))]);
        var checkedAt = new DateTimeOffset(2026, 10, 29, 9, 41, 0, TimeSpan.Zero);
        string Line(CheckProgress? checking = null, DateTimeOffset? paused = null) =>
            FeedHeading.For(layout, checkedAt, Make.Now, TimeZoneInfo.Utc, checking, paused).LineText(new FeedText()).Replace('\u202F', ' ');

        Assert.Equal("Since this morning · Checked 9:41 AM", Line());
        Assert.Equal("Since this morning · Checking… 4 of 15", Line(new CheckProgress(4, 15)));
        Assert.Equal("Since this morning · Checking…", Line(new CheckProgress(0, 0)));
        Assert.Equal("Since this morning · Checked 9:41 AM · Paused until 4:00 PM", Line(paused: Make.Now + TimeSpan.FromHours(1)));
        Assert.Equal("Since this morning · Checked 9:41 AM · Paused", Line(paused: DateTimeOffset.MaxValue));

        var parts = FeedHeading.For(layout, checkedAt, Make.Now, TimeZoneInfo.Utc, null, DateTimeOffset.MaxValue).Line(new FeedText());
        Assert.Equal([HeadingPartKind.Since, HeadingPartKind.Checked, HeadingPartKind.Paused], parts.Select(p => p.Kind));
    }

    [Fact]
    public void UpToDateSaysWhenItLastChecked()
    {
        var layout = Make.Build([Make.Item("a", Make.Ago(30), read: true)]);
        var today = FeedHeading.For(layout, new DateTimeOffset(2026, 10, 29, 9, 41, 0, TimeSpan.Zero), Make.Now, TimeZoneInfo.Utc);
        Assert.Equal("You're up to date", today.Title(new FeedText()));
        // ICU puts a narrow no-break space before AM
        Assert.Equal("Checked 9:41 AM", today.LineText(new FeedText()).Replace('\u202F', ' '));

        var earlier = FeedHeading.For(layout, new DateTimeOffset(2026, 10, 27, 9, 41, 0, TimeSpan.Zero), Make.Now, TimeZoneInfo.Utc);
        Assert.Equal("Checked Oct 27", earlier.LineText(new FeedText()));
        Assert.Equal("Not checked yet", FeedHeading.For(layout, null, Make.Now, TimeZoneInfo.Utc).LineText(new FeedText()));
    }

    [Fact]
    public void TheAppsOwnWordsAreUsed()
    {
        var text = new FeedText { SinceThisAfternoon = "Seit heute Nachmittag" };
        var layout = Make.Build([Make.Item("a", Make.Ago(1))]);
        Assert.Equal("Seit heute Nachmittag", FeedHeading.For(layout, null, Make.Now, TimeZoneInfo.Utc).SinceText(text));
    }
}

public class FeedViewModelTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static async Task<TestApp> StartAsync(FakeEngine? engine = null)
    {
        var test = new TestApp(engine);
        await test.StartAsync();
        return test;
    }

    private static FeedCard Card(TestApp test, string target) =>
        test.App.Feed.Cards.First(card => card.Target.Key == target && card.Items.Any(item => !item.Read));

    [Fact]
    public async Task TheFixtureInboxAsAFeed()
    {
        await using var test = await StartAsync();
        var feed = test.App.Feed;

        Assert.Equal(new Version(1, 1, 0), feed.SelfUpdate!.Version);
        Assert.Equal([FeedSectionKind.Today, FeedSectionKind.Earlier], feed.Sections.Select(s => s.Key.Kind));
        Assert.Equal(["Team Fortress 2", "react", "python/cpython"], feed.Sections[0].Select(c => c.Display));
        Assert.Equal(["Bar", "Team Fortress 2"], feed.Sections[1].Select(c => c.Display));
        Assert.Equal("4 new updates", feed.HeadingTitle);
        Assert.StartsWith("Since this morning · Checked 8:00", feed.HeadingLineText);
    }

    [Fact]
    public async Task ExpandingMarksTheCardReadButKeepsItInPlace()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var feed = test.App.Feed;
        var tf2 = Card(test, "440");

        await feed.ExpandAsync(tf2);

        var mark = engine.RequestsFor("inbox.mark").Single();
        Assert.Equal([tf2.Lead.Id], mark["ids"]!.AsArray().Select(id => id!.GetValue<string>()));
        Assert.True(tf2.IsExpanded);
        Assert.Same(tf2, feed.Expanded);
        Assert.Contains(tf2, feed.Sections[0]);
        Assert.True(tf2.ShowsUnread);
        test.Time.Advance(FeedViewModel.DotFadeDelay);
        Assert.False(tf2.ShowsUnread);
        Assert.Contains(tf2, feed.Sections[0]);

        feed.Collapse();
        Assert.Null(feed.Expanded);
        Assert.DoesNotContain(feed.Sections[0], card => card.Target.Key == "440");
        Assert.Contains(feed.Sections[^1], card => card.Target.Key == "440");
    }

    [Fact]
    public async Task OnlyOneCardIsExpandedAtATime()
    {
        await using var test = await StartAsync();
        var feed = test.App.Feed;
        var react = Card(test, "react");
        var cpython = Card(test, "github.com/python/cpython");

        await feed.ToggleAsync(react);
        await feed.ToggleAsync(cpython);

        Assert.False(react.IsExpanded);
        Assert.True(cpython.IsExpanded);
        Assert.Same(cpython, feed.Expanded);
        await feed.ToggleAsync(cpython);
        Assert.Null(feed.Expanded);
    }

    [Fact]
    public async Task ARefreshWhileExpandedKeepsTheCardExpanded()
    {
        await using var test = await StartAsync();
        var feed = test.App.Feed;
        var tf2 = Card(test, "440");
        await feed.ExpandAsync(tf2);

        await test.App.Inbox.RefreshAsync(Token);

        Assert.Same(tf2, feed.Expanded);
        Assert.True(tf2.IsExpanded);
        Assert.Contains(tf2, feed.Sections[0]);
    }

    [Fact]
    public async Task OpeningALinkMarksTheTargetRead()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var cpython = Card(test, "github.com/python/cpython");

        await test.App.Feed.OpenAsync(cpython);

        Assert.Equal(["https://github.com/python/cpython/releases/tag/v3.14.7"], test.Shell.Opened);
        Assert.Single(engine.RequestsFor("inbox.mark"));
        test.Time.Advance(FeedViewModel.DotFadeDelay + FeedViewModel.SettleDelay);
        Assert.DoesNotContain(test.App.Feed.Sections[0], card => card.Target.Key == "github.com/python/cpython");
    }

    [Fact]
    public async Task MarkAllReadFadesTheDotsInACascadeThenSettles()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var feed = test.App.Feed;
        var today = feed.Sections[0].ToList();

        await feed.MarkAllReadAsync();

        Assert.True(engine.RequestsFor("inbox.mark").Single()["all"]!.GetValue<bool>());
        Assert.All(today, card => Assert.False(card.ShowsUnread));
        Assert.Equal([TimeSpan.Zero, FeedViewModel.CascadeStep, FeedViewModel.CascadeStep * 2], today.Select(c => c.FadeDelay));
        // The cards haven't moved yet
        Assert.Equal(FeedSectionKind.Today, feed.Sections[0].Key.Kind);

        test.Time.Advance(FeedViewModel.CascadeStep * 3 + FeedViewModel.SettleDelay);
        Assert.Equal([FeedSectionKind.Earlier], feed.Sections.Select(s => s.Key.Kind));
        Assert.Equal("You're up to date", feed.HeadingTitle);
    }

    [Fact]
    public async Task MarkAllReadFollowsTheLabelFilter()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var feed = test.App.Feed;
        Assert.Equal(["GitHub", "itch.io", "npm", "Steam"], feed.Labels);

        feed.SourceFilter = "Steam";
        await feed.MarkAllReadAsync();

        var mark = engine.RequestsFor("inbox.mark").Single();
        Assert.Null(mark["all"]);
        Assert.Equal(["i_2b9e5d7f0c1a3e44"], mark["ids"]!.AsArray().Select(id => id!.GetValue<string>()));
    }

    [Fact]
    public async Task MarkingUnreadBringsACardBack()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var bar = test.App.Feed.Cards.First(card => card.Display == "Bar");

        await test.App.Feed.MarkUnreadAsync(bar);

        Assert.False(engine.RequestsFor("inbox.mark").Single()["read"]!.GetValue<bool>());
        Assert.Contains(test.App.Feed.Sections[0], card => card.Display == "Bar");
    }

    [Fact]
    public async Task CardsACheckAddsAreMarkedNew()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        Assert.Empty(test.App.Feed.NewCards);

        engine.Then("inbox.list", _ => FakeEngine.Reply(new System.Text.Json.Nodes.JsonObject
        {
            ["items"] = new System.Text.Json.Nodes.JsonArray([.. Fixtures.Get("inbox.list/ok").Reply["items"]!.AsArray()
                .Select(i => i!.DeepClone())
                .Append(FixtureItem("i_new", "npm", "vite"))]),
            ["cursor"] = null,
            ["total"] = 7,
            ["unread"] = 5,
        }));
        await test.App.Inbox.RefreshAsync(Token);

        var added = Assert.Single(test.App.Feed.NewCards);
        Assert.Contains("vite", added);
    }

    [Fact]
    public async Task FilteringToATargetFromFollowing()
    {
        await using var test = await StartAsync();
        var tf2 = test.App.Sources.Targets.Single(t => t.Key == "440");

        test.App.Following.Select(tf2);

        Assert.Equal("440", test.App.Feed.TargetFilter!.Key);
        Assert.All(test.App.Feed.Cards, card => Assert.Equal("440", card.Target.Key));
        Assert.Equal([AppPage.Updates], test.Shown);
    }

    private static System.Text.Json.Nodes.JsonObject FixtureItem(string id, string source, string target) => new()
    {
        ["id"] = id, ["source"] = source, ["target"] = target, ["display"] = target, ["title"] = "7.2.0",
        ["url"] = $"https://www.npmjs.com/package/{target}", ["version"] = "7.2.0", ["published"] = "2026-10-29T08:30:00Z",
        ["tags"] = new System.Text.Json.Nodes.JsonArray(), ["body"] = null, ["found_at"] = "2026-10-29T08:59:00Z",
        ["read"] = false, ["baseline"] = false, ["priority"] = "normal", ["starred"] = false, ["snoozed_until"] = null,
    };
}
