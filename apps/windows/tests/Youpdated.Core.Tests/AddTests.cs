using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public class AddPreviewTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static async Task<TestApp> StartAsync(FakeEngine engine)
    {
        var test = new TestApp(engine);
        await test.StartAsync();
        return test;
    }

    [Fact]
    public async Task ThePreviewWaitsForTypingToPause()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var add = test.App.Sources.Add;

        add.Value = "https://github.com/p";
        test.Time.Advance(TimeSpan.FromMilliseconds(200));
        add.Value = "https://github.com/python/cpython";
        test.Time.Advance(AddSourceViewModel.PreviewDelay - TimeSpan.FromMilliseconds(1));
        Assert.Empty(engine.RequestsFor("targets.preview"));

        test.Time.Advance(TimeSpan.FromMilliseconds(1));
        await WaitFor(() => add.Stage == AddStage.Ready);

        var preview = Assert.Single(engine.RequestsFor("targets.preview"));
        Assert.Equal("https://github.com/python/cpython", preview["value"]!.GetValue<string>());
        Assert.True(add.CanAdd);
    }

    [Fact]
    public async Task AStaleReplyIsDropped()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var add = test.App.Sources.Add;

        engine.Hold("targets.preview");
        add.Value = "express";
        var first = add.PreviewAsync();
        add.Value = "https://github.com/python/cpython";
        engine.Release("targets.preview");
        await first;

        Assert.Equal(AddStage.Idle, add.Stage);
        Assert.Null(add.Preview);
        Assert.Empty(add.Choices);
    }

    [Fact]
    public async Task AnAmbiguousNameAsksWhichSource()
    {
        var engine = new FakeEngine().Next("targets.preview", "ambiguous").Next("targets.preview", "picked");
        await using var test = await StartAsync(engine);
        var add = test.App.Sources.Add;

        await add.StartAsync("express");
        Assert.Equal(AddStage.Choosing, add.Stage);
        Assert.Equal(["homebrew", "npm", "pypi"], add.Choices);

        await add.ChooseAsync("npm");
        // The choice stays up, with npm picked, above the card
        Assert.Equal(["homebrew", "npm", "pypi"], add.Choices);
        Assert.Equal("npm", add.Chosen);
        Assert.Equal(AddStage.Ready, add.Stage);
        // Already watching: Follow becomes Show in Following
        Assert.False(add.CanAdd);
        Assert.True(add.AlreadyWatching);
    }

    [Fact]
    public async Task AFailedFetchCanStillBeFollowed()
    {
        var engine = new FakeEngine().Next("targets.preview", "fetch_failed");
        await using var test = await StartAsync(engine);
        var add = test.App.Sources.Add;

        await add.StartAsync("https://store.steampowered.com/app/440/");

        Assert.Contains("timed out", add.Preview!.FetchError);
        Assert.True(add.CanAdd);
    }

    [Fact]
    public async Task FollowSetsThePriorityPicked()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var add = test.App.Sources.Add;
        await add.StartAsync("https://github.com/python/cpython");
        add.Priority = Priority.High;

        await add.AddAsync();

        Assert.Equal(AddStage.Added, add.Stage);
        var priority = Assert.Single(engine.RequestsFor("priority.set"));
        Assert.Equal("github.com/python/cpython", priority["key"]!.GetValue<string>());
        Assert.Equal("high", priority["priority"]!.GetValue<string>());
    }

    [Fact]
    public async Task NormalPriorityNeedsNoRequest()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        await test.App.Sources.Add.StartAsync("https://github.com/python/cpython");

        await test.App.Sources.Add.AddAsync();

        Assert.Empty(engine.RequestsFor("priority.set"));
    }

    [Fact]
    public async Task PastingALinkOpensAddFilledIn()
    {
        var engine = new FakeEngine();
        await using var test = await StartAsync(engine);
        var asked = new List<string?>();
        test.App.AddRequested += asked.Add;

        Assert.True(await test.App.PasteAsync("  https://github.com/python/cpython\n"));
        Assert.Equal(["https://github.com/python/cpython"], asked);
        Assert.Equal(AddStage.Ready, test.App.Sources.Add.Stage);

        Assert.False(await test.App.PasteAsync("just some words"));
        Assert.Single(asked);
    }

    private static async Task WaitFor(Func<bool> condition)
    {
        for (var i = 0; i < 200 && !condition(); i++)
        {
            await Task.Delay(5, Token);
        }
        Assert.True(condition());
    }
}

public class PasteToAddTests
{
    [Theory]
    [InlineData("https://github.com/godotengine/godot", "https://github.com/godotengine/godot")]
    [InlineData("  http://store.steampowered.com/app/440/ \n", "http://store.steampowered.com/app/440/")]
    [InlineData("github.com/godotengine/godot", "github.com/godotengine/godot")]
    [InlineData("www.youtube.com/@SomeChannel", "www.youtube.com/@SomeChannel")]
    [InlineData("youpdated://add?url=https%3A%2F%2Fgithub.com%2Fa%2Fb", "https://github.com/a/b")]
    public void AcceptsALink(string clipboard, string expected) => Assert.Equal(expected, PasteToAdd.Accept(clipboard));

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("express")]
    [InlineData("two words")]
    [InlineData("https://a.com/x\nhttps://b.com/y")]
    [InlineData("file:///etc/passwd")]
    [InlineData("/Users/me/project/package.json")]
    [InlineData(@"C:\Users\me\package.json")]
    [InlineData("mailto:someone@example.com")]
    [InlineData("javascript:alert(1)")]
    [InlineData("example.com")]
    public void RefusesAnythingElse(string? clipboard) => Assert.Null(PasteToAdd.Accept(clipboard));
}
