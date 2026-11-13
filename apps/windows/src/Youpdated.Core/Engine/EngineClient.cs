using System.Text.Json;
using System.Text.Json.Nodes;
using Youpdated.Core.Contract;

namespace Youpdated.Core.Engine;

public sealed record ClientInfo(string Name, string Version, string Platform);

/// <summary>Sort orders <c>inbox.list</c> accepts</summary>
public enum InboxSort
{
    Newest,
    Priority,
    Source,
    Name,
}

/// <summary>Filters for <c>inbox.list</c></summary>
public sealed record InboxQuery(
    bool UnreadOnly = false,
    string? Source = null,
    TargetKey? Target = null,
    InboxSort Sort = InboxSort.Newest,
    int Limit = 100,
    string? Cursor = null);

public abstract record ImportSource
{
    public static IReadOnlyList<string> Formats { get; } = ["package.json", "requirements.txt", "Brewfile"];

    public sealed record File(string Path) : ImportSource;

    public sealed record Content(string Text, string Format) : ImportSource;

    internal JsonObject ToJson() => this switch
    {
        File file => new JsonObject { ["path"] = file.Path },
        Content content => new JsonObject { ["content"] = content.Text, ["format"] = content.Format },
        _ => throw new InvalidOperationException(),
    };
}

/// <summary>All ops of contract v1</summary>
public sealed class EngineClient(IEngineChannel channel)
{
    public IEngineChannel Channel => channel;

    public event Action<ProgressEvent>? Progress
    {
        add => channel.Progress += value;
        remove => channel.Progress -= value;
    }

    public event Action<TargetKey>? IconArrived
    {
        add => channel.IconArrived += value;
        remove => channel.IconArrived -= value;
    }

    public Task<HelloReply> HelloAsync(ClientInfo client, CancellationToken cancellationToken = default) =>
        channel.SendAsync("hello", new JsonObject
        {
            ["client"] = new JsonObject
            {
                ["name"] = client.Name,
                ["version"] = client.Version,
                ["platform"] = client.Platform,
            },
        }, ContractJson.Default.HelloReply, cancellationToken);

    public Task<UnlockReply> UnlockAsync(string passphrase, CancellationToken cancellationToken = default) =>
        channel.SendAsync("unlock", new JsonObject { ["passphrase"] = passphrase },
            ContractJson.Default.UnlockReply, cancellationToken);

    public Task<StatusReply> StatusAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("status", null, ContractJson.Default.StatusReply, cancellationToken);

    /// <summary>Check everything, only <paramref name="targets"/> or <paramref name="sources"/></summary>
    public Task<CheckReply> CheckAsync(
        IReadOnlyList<TargetKey>? targets = null, bool notify = true, CancellationToken cancellationToken = default,
        IReadOnlyList<string>? sources = null)
    {
        var parameters = new JsonObject();
        if (targets is { Count: > 0 })
        {
            parameters["targets"] = JsonSerializer.SerializeToNode(targets.ToList(), ContractJson.Default.ListTargetKey);
        }
        if (sources is { Count: > 0 })
        {
            parameters["sources"] = new JsonArray([.. sources.Select(source => (JsonNode?)source)]);
        }
        if (!notify)
        {
            parameters["notify"] = false;
        }
        return channel.SendAsync("check", parameters, ContractJson.Default.CheckReply, cancellationToken);
    }

    public Task<InboxListReply> InboxListAsync(InboxQuery query, CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject { ["limit"] = query.Limit };
        if (query.UnreadOnly)
        {
            parameters["unread"] = true;
        }
        if (query.Source is not null)
        {
            parameters["source"] = query.Source;
        }
        if (query.Target is not null)
        {
            parameters["target"] = JsonSerializer.SerializeToNode(query.Target, ContractJson.Default.TargetKey);
        }
        if (query.Sort != InboxSort.Newest)
        {
            parameters["sort"] = query.Sort.ToString().ToLowerInvariant();
        }
        if (query.Cursor is not null)
        {
            parameters["cursor"] = query.Cursor;
        }
        return channel.SendAsync("inbox.list", parameters, ContractJson.Default.InboxListReply, cancellationToken);
    }

    /// <summary>Every page of <paramref name="query"/>, up to <paramref name="max"/> items</summary>
    public async Task<(List<InboxItem> Items, int Unread)> InboxListAllAsync(
        InboxQuery query, int max = 2000, CancellationToken cancellationToken = default)
    {
        var items = new List<InboxItem>();
        var page = query with { Limit = 500, Cursor = null };
        while (true)
        {
            var reply = await InboxListAsync(page, cancellationToken).ConfigureAwait(false);
            items.AddRange(reply.Items);
            if (reply.Cursor is null || items.Count >= max)
            {
                return (items, reply.Unread);
            }
            page = page with { Cursor = reply.Cursor };
        }
    }

    public Task<InboxMarkReply> InboxMarkAsync(
        IReadOnlyList<string> ids, bool read, CancellationToken cancellationToken = default) =>
        channel.SendAsync("inbox.mark", new JsonObject
        {
            ["read"] = read,
            ["ids"] = new JsonArray([.. ids.Select(id => (JsonNode?)id)]),
        }, ContractJson.Default.InboxMarkReply, cancellationToken);

    public Task<InboxMarkReply> InboxMarkAllAsync(
        bool read, string? source = null, CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject { ["read"] = read, ["all"] = true };
        if (source is not null)
        {
            parameters["source"] = source;
        }
        return channel.SendAsync("inbox.mark", parameters, ContractJson.Default.InboxMarkReply, cancellationToken);
    }

    public Task<InboxDeleteReply> InboxDeleteAsync(IReadOnlyList<string> ids, CancellationToken cancellationToken = default) =>
        channel.SendAsync("inbox.delete", new JsonObject
        {
            ["ids"] = new JsonArray([.. ids.Select(id => (JsonNode?)id)]),
        }, ContractJson.Default.InboxDeleteReply, cancellationToken);

    public Task<InboxSnoozeReply> InboxSnoozeAsync(
        IReadOnlyList<string> ids, int minutes = 60, CancellationToken cancellationToken = default) =>
        channel.SendAsync("inbox.snooze", new JsonObject
        {
            ["ids"] = new JsonArray([.. ids.Select(id => (JsonNode?)id)]),
            ["minutes"] = minutes,
        }, ContractJson.Default.InboxSnoozeReply, cancellationToken);

    public Task<TargetsListReply> TargetsListAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("targets.list", null, ContractJson.Default.TargetsListReply, cancellationToken);

    public Task<PreviewReply> TargetsPreviewAsync(
        string value, string? source = null, CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject { ["value"] = value };
        if (source is not null)
        {
            parameters["source"] = source;
        }
        return channel.SendAsync("targets.preview", parameters, ContractJson.Default.PreviewReply, cancellationToken);
    }

    public Task<TargetsAddReply> TargetsAddAsync(
        string source, IReadOnlyList<string> entries, CancellationToken cancellationToken = default) =>
        channel.SendAsync("targets.add", new JsonObject
        {
            ["source"] = source,
            ["entries"] = new JsonArray([.. entries.Select(entry => (JsonNode?)entry)]),
        }, ContractJson.Default.TargetsAddReply, cancellationToken);

    public Task<TargetsRemoveReply> TargetsRemoveAsync(
        TargetKey target, bool wipe, CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject { ["source"] = target.Source, ["key"] = target.Key };
        if (wipe)
        {
            parameters["wipe"] = true;
        }
        return channel.SendAsync("targets.remove", parameters, ContractJson.Default.TargetsRemoveReply, cancellationToken);
    }

    public Task<ImportPreviewReply> ImportPreviewAsync(ImportSource source, CancellationToken cancellationToken = default) =>
        channel.SendAsync("import.preview", source.ToJson(), ContractJson.Default.ImportPreviewReply, cancellationToken);

    public Task<ImportApplyReply> ImportApplyAsync(ImportSource source, CancellationToken cancellationToken = default) =>
        channel.SendAsync("import.apply", source.ToJson(), ContractJson.Default.ImportApplyReply, cancellationToken);

    public Task<SettingsReply> SettingsGetAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("settings.get", null, ContractJson.Default.SettingsReply, cancellationToken);

    public Task<SettingsReply> SettingsSetAsync(SettingsPatch patch, CancellationToken cancellationToken = default) =>
        channel.SendAsync("settings.set", new JsonObject { ["settings"] = patch.ToJson() },
            ContractJson.Default.SettingsReply, cancellationToken);

    public Task<PriorityReply> PrioritySetAsync(
        TargetKey target, Priority priority, CancellationToken cancellationToken = default) =>
        channel.SendAsync("priority.set", new JsonObject
        {
            ["source"] = target.Source,
            ["key"] = target.Key,
            ["priority"] = JsonSerializer.SerializeToNode(priority, ContractJson.Default.Priority),
        }, ContractJson.Default.PriorityReply, cancellationToken);

    /// <summary>Only when <c>hello.ops</c> has <c>filters.get</c></summary>
    public Task<Filters> FiltersGetAsync(TargetKey target, CancellationToken cancellationToken = default) =>
        channel.SendAsync("filters.get", new JsonObject { ["source"] = target.Source, ["key"] = target.Key },
            ContractJson.Default.Filters, cancellationToken);

    public Task<Filters> FiltersSetAsync(
        TargetKey target, IReadOnlyList<string> match, bool skipPrereleases, Bump bump, string? min,
        CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject
        {
            ["source"] = target.Source,
            ["key"] = target.Key,
            ["match"] = new JsonArray([.. match.Select(glob => (JsonNode?)glob)]),
            ["skip_prereleases"] = skipPrereleases,
            ["bump"] = JsonSerializer.SerializeToNode(bump, ContractJson.Default.Bump),
        };
        if (min is not null)
        {
            parameters["min"] = min;
        }
        return channel.SendAsync("filters.set", parameters, ContractJson.Default.Filters, cancellationToken);
    }

    /// <summary>What these rules would keep; <paramref name="refresh"/> has the engine re-ask</summary>
    public Task<FiltersPreview> FiltersPreviewAsync(
        TargetKey target, IReadOnlyList<string>? match = null, bool skipPrereleases = false, Bump bump = Bump.Any,
        string? min = null, bool refresh = false, CancellationToken cancellationToken = default)
    {
        var parameters = new JsonObject { ["source"] = target.Source, ["key"] = target.Key };
        if (match is { Count: > 0 })
        {
            parameters["match"] = new JsonArray([.. match.Select(glob => (JsonNode?)glob)]);
        }
        if (skipPrereleases)
        {
            parameters["skip_prereleases"] = true;
        }
        if (bump != Bump.Any)
        {
            parameters["bump"] = JsonSerializer.SerializeToNode(bump, ContractJson.Default.Bump);
        }
        if (min is not null)
        {
            parameters["min"] = min;
        }
        if (refresh)
        {
            parameters["refresh"] = true;
        }
        return channel.SendAsync("filters.preview", parameters, ContractJson.Default.FiltersPreview, cancellationToken);
    }

    public Task<ProblemsListReply> ProblemsListAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("problems.list", null, ContractJson.Default.ProblemsListReply, cancellationToken);

    public Task<DigestReply> DigestSendAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("digest.send", null, ContractJson.Default.DigestReply, cancellationToken);

    public Task<SnapshotReply> SnapshotWriteAsync(string path, CancellationToken cancellationToken = default) =>
        channel.SendAsync("snapshot.write", new JsonObject { ["path"] = path },
            ContractJson.Default.SnapshotReply, cancellationToken);

    public Task<ConfigInitReply> ConfigInitAsync(bool selfWatch = true, CancellationToken cancellationToken = default) =>
        channel.SendAsync("config.init", selfWatch ? null : new JsonObject { ["self_watch"] = false },
            ContractJson.Default.ConfigInitReply, cancellationToken);

    public Task<IconReply> IconGetAsync(TargetKey target, CancellationToken cancellationToken = default) =>
        channel.SendAsync("icon.get", new JsonObject { ["source"] = target.Source, ["key"] = target.Key },
            ContractJson.Default.IconReply, cancellationToken);

    public Task<EmptyReply> ShutdownAsync(CancellationToken cancellationToken = default) =>
        channel.SendAsync("shutdown", null, ContractJson.Default.EmptyReply, cancellationToken);
}
