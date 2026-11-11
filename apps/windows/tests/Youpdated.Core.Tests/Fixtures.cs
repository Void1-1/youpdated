using System.Text.Json;
using System.Text.Json.Nodes;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

/// <summary>An exchange from apps/contract/v1/fixtures</summary>
public sealed record Fixture(
    string Name, string Op, string Case, JsonObject Request, IReadOnlyList<JsonObject> Events, JsonObject Reply,
    IReadOnlyList<JsonObject> After)
{
    public bool Ok => Reply["ok"]?.GetValue<bool>() == true;

    public override string ToString() => Name;
}

public static class Fixtures
{
    private static readonly Lazy<IReadOnlyList<Fixture>> Loaded = new(Load);

    public static string Root => Path.Combine(AppContext.BaseDirectory, "contract", "fixtures");

    public static IReadOnlyList<Fixture> All => Loaded.Value;

    public static Fixture Get(string name) =>
        All.FirstOrDefault(fixture => fixture.Name == name)
        ?? throw new KeyNotFoundException($"no fixture {name}");

    public static TheoryData<string> Names()
    {
        var data = new TheoryData<string>();
        foreach (var fixture in All)
        {
            data.Add(fixture.Name);
        }
        return data;
    }

    public static IEnumerable<string> SnapshotFiles() =>
        Directory.EnumerateFiles(Path.Combine(Root, "_snapshot"), "*.json");

    private static string AsThisAppsRelease(string json) =>
        json.Replace("app-v1.1.0", AppUpdate.TagPrefix + "1.1.0", StringComparison.Ordinal);

    private static IReadOnlyList<Fixture> Load()
    {
        var fixtures = new List<Fixture>();
        foreach (var path in Directory.EnumerateFiles(Root, "*.json", SearchOption.AllDirectories).Order(StringComparer.Ordinal))
        {
            var folder = Path.GetFileName(Path.GetDirectoryName(path)!);
            if (folder == "_snapshot")
            {
                continue;
            }
            var node = JsonNode.Parse(AsThisAppsRelease(File.ReadAllText(path)))!.AsObject();
            var request = node["request"]!.AsObject();
            var op = request["op"]!.GetValue<string>();
            var name = $"{folder}/{Path.GetFileNameWithoutExtension(path)}";
            fixtures.Add(new Fixture(
                name, op, Path.GetFileNameWithoutExtension(path), request,
                [.. node["events"]!.AsArray().Select(e => e!.AsObject())],
                node["reply"]!.AsObject(),
                [.. (node["after"]?.AsArray() ?? []).Select(e => e!.AsObject())]));
        }
        if (fixtures.Count == 0)
        {
            throw new InvalidOperationException($"no fixtures under {Root}");
        }
        return fixtures;
    }

    public static JsonElement ToElement(this JsonNode node) => JsonDocument.Parse(node.ToJsonString()).RootElement.Clone();
}
