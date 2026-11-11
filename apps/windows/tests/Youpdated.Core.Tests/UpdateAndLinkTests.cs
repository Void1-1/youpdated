using System.Text.Json;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public class UpdateAndLinkTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static IReadOnlyList<InboxItem> Items =>
        Fixtures.Get("inbox.list/ok").Reply.ToElement().Deserialize(ContractJson.Default.InboxListReply)!.Items;

    [Theory]
    [InlineData("app-windows-v1.1.0", "1.1.0")]
    [InlineData("app-windows-v2.0", "2.0.0")]
    [InlineData("app-macos-v1.1.0", null)]
    [InlineData("app-v1.1.0", null)]
    [InlineData("v1.0.1", null)]
    [InlineData("1.0.1", null)]
    [InlineData("app-windows-v1.1.0rc1", null)]
    [InlineData("v1.5.0-beta", null)]
    [InlineData("nightly", null)]
    public void AppTagsParse(string tag, string? version) =>
        Assert.Equal(version is null ? null : Version.Parse(version), AppUpdate.ParseTag(tag));

    [Fact]
    public void OnlyNewerStarredReleasesAreUpdates()
    {
        var self = Items.Single(item => item.Starred);
        Assert.NotNull(AppUpdate.From(self, new Version(1, 0, 0)));
        Assert.Null(AppUpdate.From(self, new Version(1, 1, 0)));
        Assert.Null(AppUpdate.From(self with { Starred = false }, new Version(1, 0, 0)));
    }

    [Theory]
    [InlineData("youpdated://add?url=https%3A%2F%2Fgithub.com%2Fa%2Fb", "https://github.com/a/b")]
    [InlineData("youpdated://add?url=https://example.com/feed?x=1&y=2", "https://example.com/feed?x=1&y=2")]
    [InlineData("youpdated://add?url=express", "express")]
    [InlineData("youpdated://add", null)]
    public void AddLinks(string link, string? value) =>
        Assert.Equal(new YoupdatedLink.Add(value), YoupdatedLink.Parse(link));

    [Fact]
    public void OtherLinks()
    {
        Assert.IsType<YoupdatedLink.Open>(YoupdatedLink.Parse("youpdated://open"));
        Assert.IsType<YoupdatedLink.Check>(YoupdatedLink.Parse("YOUPDATED://check/"));
        Assert.Null(YoupdatedLink.Parse("https://github.com"));
        Assert.Null(YoupdatedLink.Parse("youpdated://delete-everything"));
        Assert.IsType<YoupdatedLink.Check>(YoupdatedLink.FromArguments(["--background", "youpdated://check"]));
    }
}
