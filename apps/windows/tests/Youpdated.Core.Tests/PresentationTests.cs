using System.Globalization;
using System.Text.Json.Nodes;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

/// <summary>apps/shared/presentation-cases.json</summary>
public class PresentationCasesTests
{
    private static readonly JsonObject Cases = JsonNode.Parse(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "shared", "presentation-cases.json")))!.AsObject();

    public static TheoryData<string> Targets()
    {
        var data = new TheoryData<string>();
        foreach (var target in Cases["targets"]!.AsArray())
        {
            data.Add($"{target!["source"]}:{target["key"]}");
        }
        return data;
    }

    public static TheoryData<string, string, string> Since()
    {
        var data = new TheoryData<string, string, string>();
        foreach (var since in Cases["since"]!.AsArray())
        {
            data.Add(since!["now"]!.GetValue<string>(), since["found"]!.GetValue<string>(), since["says"]!.GetValue<string>());
        }
        return data;
    }

    private static JsonNode Case(string id) =>
        Cases["targets"]!.AsArray().Single(t => $"{t!["source"]}:{t["key"]}" == id)!;

    [Fact]
    public void ThePaletteIsInTheSameOrder()
    {
        Assert.Equal(
            Cases["palette"]!.AsArray().Select(name => name!.GetValue<string>()),
            Monogram.Palette.Select(color => color.Name));
    }

    [Theory]
    [MemberData(nameof(Targets))]
    public void Color(string id)
    {
        var target = Case(id);
        var key = new TargetKey(target["source"]!.GetValue<string>(), target["key"]!.GetValue<string>());
        Assert.Equal(target["hash"]!.GetValue<uint>(), Monogram.Hash($"{key.Source}:{key.Key}"));
        var monogram = Monogram.For(key, target["display"]!.GetValue<string>());
        Assert.Equal(target["color"]!.GetValue<int>(), monogram.ColorIndex);
        Assert.Equal(target["color_name"]!.GetValue<string>(), Monogram.Palette[monogram.ColorIndex].Name);
    }

    [Theory]
    [MemberData(nameof(Targets))]
    public void Initials(string id)
    {
        var target = Case(id);
        Assert.Equal(target["initials"]!.GetValue<string>(), Monogram.InitialsOf(target["display"]!.GetValue<string>()));
    }

    [Theory]
    [MemberData(nameof(Targets))]
    public void Label(string id)
    {
        var target = Case(id);
        Assert.Equal(
            target["label"]!.GetValue<string>(),
            SourceLabels.Label(target["source"]!.GetValue<string>(), target["key"]!.GetValue<string>()));
    }

    [Theory]
    [MemberData(nameof(Since))]
    public void SinceWording(string now, string found, string says)
    {
        // Local times, en-US: read them as the clock in a zone with no offset
        static DateTimeOffset Local(string text) =>
            new(DateTime.Parse(text, CultureInfo.InvariantCulture), TimeSpan.Zero);
        var at = Local(found);
        var layout = FeedBuilder.Build([Make.Item("a", at)], new FeedOptions(), Local(now), TimeZoneInfo.Utc, Make.App);

        var heading = FeedHeading.For(layout, null, Local(now), TimeZoneInfo.Utc);

        Assert.Equal(says, heading.SinceText(new FeedText()));
    }
}

public class ViewOptionsTests
{
    private static FeedLayout Build(ViewOptions view, IEnumerable<InboxItem> items, TargetKey? target = null) =>
        FeedBuilder.Build(items, new FeedOptions(view, null, target), Make.Now, TimeZoneInfo.Utc, Make.App);

    [Fact]
    public void CompactDensityMakesEveryCardARow()
    {
        var items = new[] { Make.Item("high", Make.Ago(1), priority: Priority.High), Make.Item("normal", Make.Ago(1)) };
        Assert.All(Build(new ViewOptions(Density: Density.Compact), items).Cards, card => Assert.True(card.IsCompact));
        Assert.All(Build(new ViewOptions(), items).Cards, card => Assert.False(card.IsCompact));
    }

    [Fact]
    public void HiddenLowPriorityStillShowsForItsTarget()
    {
        var items = new[] { Make.Item("react", Make.Ago(1), priority: Priority.Low, source: "npm"), Make.Item("cpython", Make.Ago(2)) };
        var hidden = Build(new ViewOptions(ShowLowPriority: false), items);
        Assert.Equal(["cpython"], hidden.Cards.Select(c => c.Display));
        Assert.Equal(1, hidden.Unread);

        var filtered = Build(new ViewOptions(ShowLowPriority: false), items, new TargetKey("npm", "react"));
        Assert.Equal(["react"], filtered.Cards.Select(c => c.Display));
    }

    [Fact]
    public void ReadUpdatesReachBackAsFarAsChosen()
    {
        var items = new[]
        {
            Make.Item("new", Make.Ago(1)),
            Make.Item("week", Make.Ago(24 * 5), read: true),
            Make.Item("month", Make.Ago(24 * 20), read: true),
            Make.Item("old", Make.Ago(24 * 90), read: true),
        };
        string[] Read(ReadUpdates read) => [.. Build(new ViewOptions(ReadUpdates: read), items).Cards.Where(c => !c.Unread).Select(c => c.Display)];

        Assert.Equal(["week"], Read(ReadUpdates.Last7Days));
        Assert.Equal(["week", "month"], Read(ReadUpdates.Last30Days));
        Assert.Equal(["week", "month", "old"], Read(ReadUpdates.All));
        Assert.Empty(Read(ReadUpdates.None));
        // Last 30 days default
        Assert.Equal(ReadUpdates.Last30Days, ViewOptions.Defaults.ReadUpdates);
    }

    [Fact]
    public void PreviewsOffLeavesTheNotesOut()
    {
        var items = new[]
        {
            Make.Item("godot", Make.Ago(1), version: "4.5.1", title: "4.5.1", body: "Faster 2D batching"),
            Make.Item("cpython", Make.Ago(2), version: "v3.14.7", title: "Python 3.14.7", body: "Security fixes"),
        };
        var cards = Build(new ViewOptions(Previews: false), items).Cards.ToDictionary(c => c.Display);
        Assert.Null(cards["godot"].Line);
        Assert.Equal("Python 3.14.7", cards["cpython"].Line);
    }

    [Fact]
    public async Task OptionsAreKeptInTheAppsSettingsFile()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        test.App.Feed.View = test.App.Feed.View with { GroupBy = GroupBy.Source, Times = TimeStyle.Absolute };

        Assert.Equal(GroupBy.Source, test.LocalState.State.View!.GroupBy);
        Assert.Equal([FeedSectionKind.Source, FeedSectionKind.Source, FeedSectionKind.Source, FeedSectionKind.Source],
            test.App.Feed.Sections.Select(s => s.Key.Kind));
    }

    [Fact]
    public void TheSettingsFileRoundTrips()
    {
        var path = Path.Combine(Path.GetTempPath(), $"youpdated-view-{Guid.NewGuid():N}.json");
        try
        {
            var file = new Platform.LocalStateFile(path);
            file.Save(new Platform.LocalState { View = new ViewOptions(Density.Compact, GroupBy.None, false, ReadUpdates.All, false, TimeStyle.Absolute) });

            Assert.Contains("\"group_by\": \"None\"", File.ReadAllText(path));
            Assert.Equal(new ViewOptions(Density.Compact, GroupBy.None, false, ReadUpdates.All, false, TimeStyle.Absolute), file.Load().View);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task TheHeadingCountsTheCheck()
    {
        var engine = new FakeEngine();
        engine.Hold("check");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        var check = test.App.CheckAsync();
        engine.Write("{\"event\": \"progress\", \"done\": 4, \"of\": 15}");
        for (var i = 0; i < 100 && !test.App.Feed.HeadingLineText.Contains("4 of 15"); i++)
        {
            await Task.Delay(10, TestContext.Current.CancellationToken);
        }
        Assert.Contains("Checking… 4 of 15", test.App.Feed.HeadingLineText);

        engine.Release("check");
        await check;
        Assert.DoesNotContain("Checking", test.App.Feed.HeadingLineText);

        test.App.PauseForHour();
        Assert.Equal(HeadingPartKind.Paused, test.App.Feed.HeadingLine[^1].Kind);
    }
}
