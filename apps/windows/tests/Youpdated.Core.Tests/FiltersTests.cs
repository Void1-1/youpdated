using System.Text.Json.Nodes;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public class FiltersTests
{
    private static async Task<(TestApp Test, FiltersViewModel Filters)> OpenAsync(FakeEngine engine)
    {
        var test = new TestApp(engine);
        await test.StartAsync();
        var target = test.App.Sources.Targets.First(t => t.Starred);
        return (test, test.App.Sources.FiltersFor(target));
    }

    [Fact]
    public async Task TheEngineSaysWhetherItFilters()
    {
        var (test, _) = await OpenAsync(new FakeEngine());
        await using var _1 = test;
        Assert.True(test.App.Sources.CanFilter);
    }

    [Fact]
    public async Task LoadingShowsTheRulesAndWhatTheyKeep()
    {
        var engine = new FakeEngine();
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;

        await filters.LoadAsync();

        Assert.False(filters.Loading);
        Assert.True(filters.IsEditable);
        // the saved pattern is one of those offered
        Assert.True(filters.IsTicked("app-macos-v*"));
        Assert.Equal("", filters.Custom);
        Assert.Equal(["app-macos-v*"], filters.Match);
        Assert.True(filters.SkipPrereleases);
        Assert.Equal(2, filters.Preview!.Kept);
        Assert.False(filters.HasChanges);
        Assert.False(filters.CanSave);
        var asked = engine.RequestsFor("filters.preview").Last();
        Assert.Equal("app-macos-v*", asked["match"]![0]!.GetValue<string>());
        Assert.True(asked["skip_prereleases"]!.GetValue<bool>());
        Assert.Null(asked["refresh"]);
        Assert.Null(asked["min"]);
        Assert.Null(asked["bump"]);
    }

    [Fact]
    public async Task TickedPatternsComeInTheOrderOfferedThenTypedOnes()
    {
        var (test, filters) = await OpenAsync(new FakeEngine());
        await using var _ = test;
        await filters.LoadAsync();

        filters.SetTicked("v*", true);
        filters.Custom = " *-lts ,, v*, nightly ";
        Assert.Equal(["v*", "app-macos-v*", "*-lts", "nightly"], filters.Match);

        filters.LimitsVersions = false;
        Assert.Empty(filters.Match);
        Assert.True(filters.HasChanges);
    }

    [Fact]
    public async Task AChangedDraftIsPreviewedAndSaved()
    {
        var engine = new FakeEngine();
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;
        await filters.LoadAsync();
        var saved = 0;
        test.App.Sources.FiltersSaved += _ => saved++;

        filters.SetTicked("v*", true);
        filters.Min = " 1.0 ";
        filters.Bump = Bump.Minor;
        await filters.PreviewAsync(refresh: true);
        var asked = engine.RequestsFor("filters.preview").Last();
        Assert.Equal(2, asked["match"]!.AsArray().Count);
        Assert.Equal("1.0", asked["min"]!.GetValue<string>());
        Assert.Equal("minor", asked["bump"]!.GetValue<string>());
        Assert.True(asked["refresh"]!.GetValue<bool>());
        Assert.True(filters.CanSave);

        Assert.True(await filters.SaveAsync());
        var sent = engine.RequestsFor("filters.set").Single();
        Assert.Equal("git", sent["source"]!.GetValue<string>());
        Assert.Equal("github.com/Void1-1/youpdated", sent["key"]!.GetValue<string>());
        Assert.Equal(["v*", "app-macos-v*"], sent["match"]!.AsArray().Select(glob => glob!.GetValue<string>()));
        Assert.Equal("minor", sent["bump"]!.GetValue<string>());
        Assert.Equal("1.0", sent["min"]!.GetValue<string>());
        Assert.True(sent["skip_prereleases"]!.GetValue<bool>());
        // the app checks the target
        Assert.Equal(1, saved);
        Assert.Contains(engine.RequestsFor("check"), check => check["targets"] is not null);
    }

    [Fact]
    public async Task PatternsSavedInAnotherOrderAreNotAChange()
    {
        var engine = new FakeEngine().Then("filters.get", _ =>
        {
            var reply = (JsonObject)Fixtures.Get("filters.get/ok").Reply.DeepClone();
            reply["match"] = new JsonArray("app-macos-v*", "v*");
            return [reply];
        });
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;
        await filters.LoadAsync();

        Assert.Equal(["v*", "app-macos-v*"], filters.Match);
        Assert.False(filters.HasChanges);
        Assert.False(filters.CanSave);
        filters.SetTicked("v*", false);
        Assert.True(filters.HasChanges);
    }

    [Fact]
    public async Task ARefusedSaveSaysWhyAndStaysOpen()
    {
        var engine = new FakeEngine();
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;
        await filters.LoadAsync();
        engine.Next("filters.set", "bad_min");
        filters.Min = "latest";

        Assert.False(await filters.SaveAsync());
        Assert.Contains("must be a version number", filters.Problem);
        Assert.False(filters.Saving);
        filters.Min = "2";
        Assert.Null(filters.Problem);
    }

    [Fact]
    public async Task AnItemListedTwiceIsReadOnly()
    {
        var engine = new FakeEngine().Next("filters.get", "listed_twice");
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;
        await filters.LoadAsync();

        filters.Bump = Bump.Major;
        Assert.False(filters.IsEditable);
        Assert.False(filters.CanSave);
        Assert.Contains("multiple times", filters.Saved!.Reason);
    }

    [Fact]
    public async Task WithNothingOfferedAPatternStaysTyped()
    {
        var engine = new FakeEngine().Next("filters.preview", "fetch_failed");
        var (test, filters) = await OpenAsync(engine);
        await using var _ = test;
        await filters.LoadAsync();

        Assert.Empty(filters.Offered);
        Assert.Equal("app-macos-v*", filters.Custom);
        Assert.NotNull(filters.Preview!.FetchError);
        filters.Custom = "";
        Assert.True(filters.LimitsNothing);
    }
}
