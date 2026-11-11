using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization.Metadata;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;

namespace Youpdated.Core.Tests;

public class ContractTests
{
    internal static readonly Dictionary<string, JsonTypeInfo> ReplyTypes = new()
    {
        ["hello"] = ContractJson.Default.HelloReply,
        ["unlock"] = ContractJson.Default.UnlockReply,
        ["status"] = ContractJson.Default.StatusReply,
        ["check"] = ContractJson.Default.CheckReply,
        ["inbox.list"] = ContractJson.Default.InboxListReply,
        ["inbox.mark"] = ContractJson.Default.InboxMarkReply,
        ["inbox.snooze"] = ContractJson.Default.InboxSnoozeReply,
        ["targets.list"] = ContractJson.Default.TargetsListReply,
        ["targets.preview"] = ContractJson.Default.PreviewReply,
        ["targets.add"] = ContractJson.Default.TargetsAddReply,
        ["targets.remove"] = ContractJson.Default.TargetsRemoveReply,
        ["import.preview"] = ContractJson.Default.ImportPreviewReply,
        ["import.apply"] = ContractJson.Default.ImportApplyReply,
        ["settings.get"] = ContractJson.Default.SettingsReply,
        ["settings.set"] = ContractJson.Default.SettingsReply,
        ["priority.set"] = ContractJson.Default.PriorityReply,
        ["problems.list"] = ContractJson.Default.ProblemsListReply,
        ["digest.send"] = ContractJson.Default.DigestReply,
        ["snapshot.write"] = ContractJson.Default.SnapshotReply,
        ["config.init"] = ContractJson.Default.ConfigInitReply,
        ["shutdown"] = ContractJson.Default.EmptyReply,
        ["icon.get"] = ContractJson.Default.IconReply,
    };

    [Fact]
    public void EveryOpInHelloHasAModel()
    {
        var hello = Fixtures.Get("hello/ok").Reply["ops"]!.AsArray().Select(op => op!.GetValue<string>());
        Assert.Empty(hello.Except(ReplyTypes.Keys));
    }

    [Theory]
    [MemberData(nameof(Fixtures.Names), MemberType = typeof(Fixtures))]
    public void ReplyDecodes(string name)
    {
        var fixture = Fixtures.Get(name);
        var element = fixture.Reply.ToElement();
        if (fixture.Ok)
        {
            var reply = element.Deserialize(ReplyTypes[fixture.Op]);
            Assert.NotNull(reply);
        }
        else
        {
            var error = element.GetProperty("error").Deserialize(ContractJson.Default.EngineError);
            Assert.NotNull(error);
            Assert.False(string.IsNullOrEmpty(error.Code));
            Assert.False(string.IsNullOrEmpty(error.Message));
        }
    }

    [Theory]
    [MemberData(nameof(Fixtures.Names), MemberType = typeof(Fixtures))]
    public void EventsDecode(string name)
    {
        var fixture = Fixtures.Get(name);
        foreach (var raw in fixture.Events.Concat(fixture.After))
        {
            switch (raw["event"]!.GetValue<string>())
            {
                case ProgressEvent.Name:
                    var progress = raw.ToElement().Deserialize(ContractJson.Default.ProgressEvent)!;
                    Assert.InRange(progress.Done, 0, progress.Of);
                    break;
                case IconEvent.Name:
                    var icon = raw.ToElement().Deserialize(ContractJson.Default.IconEvent)!;
                    Assert.False(string.IsNullOrEmpty(icon.Target.Key));
                    break;
                default:
                    Assert.Fail($"an event kind the app doesn't know: {raw["event"]}");
                    break;
            }
        }
    }

    [Fact]
    public void CheckReplyReadsAsTheFixtureSays()
    {
        var reply = Fixtures.Get("check/ok").Reply.ToElement().Deserialize(ContractJson.Default.CheckReply)!;

        Assert.Equal(new DateTimeOffset(2026, 10, 29, 8, 0, 6, TimeSpan.Zero), reply.CheckedAt);
        Assert.Equal(4, reply.New);
        var cpython = reply.Items.Single(item => item.Target == "github.com/python/cpython");
        Assert.Equal(Priority.High, cpython.Priority);
        Assert.Equal("v3.14.7", cpython.Version);
        Assert.Null(cpython.Body);
        Assert.Contains(reply.Items, item => item.Starred);
        Assert.Contains(reply.Items, item => item.Baseline && item.Read);
        Assert.Equal(AlertPriority.High, reply.Alerts[0].Priority);
        Assert.Equal("python/cpython", reply.Alerts[0].Target!.Display);
        Assert.Null(reply.Alerts[1].Target);
        Assert.Equal(Problem.SourceKind, reply.Errors[0].Kind);
        Assert.Equal(Problem.NotifyKind, reply.NotifyFailures[0].Kind);
    }

    [Fact]
    public void AmbiguousErrorCarriesChoices()
    {
        var error = Fixtures.Get("targets.preview/ambiguous").Reply["error"]!.ToElement()
            .Deserialize(ContractJson.Default.EngineError)!;
        Assert.Equal(ErrorCodes.Ambiguous, error.Code);
        Assert.Equal(["homebrew", "npm", "pypi"], error.Choices);
    }

    [Fact]
    public void SettingsDigestParsesAsALocalTime()
    {
        var reply = Fixtures.Get("settings.set/ok").Reply.ToElement().Deserialize(ContractJson.Default.SettingsReply)!;
        Assert.Equal(new TimeOnly(18, 0), reply.Settings.Digest);
        Assert.Null(Settings.Defaults.Digest);
    }

    [Fact]
    public void MissingRequiredFieldIsAProtocolError()
    {
        var reply = Fixtures.Get("inbox.mark/ids").Reply.DeepClone().AsObject();
        reply.Remove("unread");
        Assert.ThrowsAny<JsonException>(() => reply.ToElement().Deserialize(ContractJson.Default.InboxMarkReply));
    }

    [Fact]
    public void UnknownFieldsAreIgnored()
    {
        var reply = Fixtures.Get("inbox.mark/ids").Reply.DeepClone().AsObject();
        reply["added_in_1_1"] = "whatever";
        Assert.NotNull(reply.ToElement().Deserialize(ContractJson.Default.InboxMarkReply));
    }

    [Fact]
    public void TimestampsWriteInTheContractFormat()
    {
        var local = new DateTimeOffset(2026, 10, 29, 10, 0, 6, 500, TimeSpan.FromHours(2));
        Assert.Equal("2026-10-29T08:00:06Z", TimestampConverter.ToWire(local));
    }

    public static TheoryData<string> RequestCases() => [.. RequestsByFixture.Keys];

    private static readonly Dictionary<string, Func<EngineClient, Task>> RequestsByFixture = new()
    {
        ["hello/ok"] = client => client.HelloAsync(new ClientInfo("Youpdated", "1.0.0", "macos")),
        ["unlock/ok"] = client => client.UnlockAsync("correct horse"),
        ["status/ok"] = client => client.StatusAsync(),
        ["check/ok"] = client => client.CheckAsync(),
        ["check/targets"] = client => client.CheckAsync([new TargetKey("itch", "https://foo.itch.io/bar")]),
        ["inbox.list/page"] = client => client.InboxListAsync(new InboxQuery(UnreadOnly: true, Limit: 2)),
        ["inbox.mark/ids"] = client => client.InboxMarkAsync(["i_8f3a1c2e9b7d4a10"], read: true),
        ["inbox.mark/all"] = client => client.InboxMarkAllAsync(read: true),
        ["inbox.snooze/ok"] = client => client.InboxSnoozeAsync(["i_2b9e5d7f0c1a3e44"], 60),
        ["targets.list/ok"] = client => client.TargetsListAsync(),
        ["targets.preview/ok"] = client => client.TargetsPreviewAsync("https://github.com/python/cpython"),
        ["targets.preview/picked"] = client => client.TargetsPreviewAsync("express", "npm"),
        ["targets.add/ok"] = client => client.TargetsAddAsync("git", ["python/cpython"]),
        ["targets.remove/ok"] = client => client.TargetsRemoveAsync(new TargetKey("npm", "react"), wipe: false),
        ["targets.remove/wipe"] = client => client.TargetsRemoveAsync(new TargetKey("steam", "440"), wipe: true),
        ["import.preview/path"] = client => client.ImportPreviewAsync(new ImportSource.File("/Users/me/project/package.json")),
        ["import.preview/content"] = client => client.ImportPreviewAsync(new ImportSource.Content("httpx>=0.27\nrich\n", "requirements.txt")),
        ["import.apply/ok"] = client => client.ImportApplyAsync(new ImportSource.File("/Users/me/project/package.json")),
        ["settings.get/ok"] = client => client.SettingsGetAsync(),
        ["settings.set/ok"] = client => client.SettingsSetAsync(new SettingsPatch().Digest(new TimeOnly(18, 0))),
        ["priority.set/ok"] = client => client.PrioritySetAsync(new TargetKey("steam", "440"), Priority.High),
        ["problems.list/ok"] = client => client.ProblemsListAsync(),
        ["digest.send/ok"] = client => client.DigestSendAsync(),
        ["config.init/created"] = client => client.ConfigInitAsync(),
        ["shutdown/ok"] = client => client.ShutdownAsync(),
        ["icon.get/found"] = client => client.IconGetAsync(new TargetKey("steam", "440")),
    };

    [Theory]
    [MemberData(nameof(RequestCases))]
    public async Task ClientSendsTheFixtureRequest(string name)
    {
        var fixture = Fixtures.Get(name);
        var engine = new FakeEngine().Next(fixture.Op, fixture.Case);
        await using var connection = new EngineConnection(engine);

        await RequestsByFixture[name](new EngineClient(connection));

        var sent = Normalize(engine.Requests.Single());
        var expected = Normalize(fixture.Request);
        Assert.True(JsonNode.DeepEquals(expected, sent), $"sent {sent.ToJsonString()}\nexpected {expected.ToJsonString()}");
    }

    [Fact]
    public async Task SettingsPatchCanClearTheDigest()
    {
        var engine = new FakeEngine();
        await using var connection = new EngineConnection(engine);
        await new EngineClient(connection).SettingsSetAsync(new SettingsPatch().Digest(null), TestContext.Current.CancellationToken);

        var settings = engine.Requests.Single()["settings"]!.AsObject();
        Assert.True(settings.ContainsKey("digest_time"));
        Assert.Null(settings["digest_time"]);
    }

    private static JsonNode Normalize(JsonObject request)
    {
        static JsonNode? Sort(JsonNode? node) => node switch
        {
            JsonObject obj => new JsonObject(obj.OrderBy(p => p.Key, StringComparer.Ordinal)
                .Select(p => KeyValuePair.Create(p.Key, Sort(p.Value)))),
            JsonArray array => new JsonArray([.. array.Select(Sort)]),
            null => null,
            _ => node.DeepClone(),
        };
        var copy = request.DeepClone().AsObject();
        copy.Remove("id");
        return Sort(copy)!;
    }
}
