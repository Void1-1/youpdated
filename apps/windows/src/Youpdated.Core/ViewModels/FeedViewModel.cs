using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using Youpdated.Core.Contract;
using Youpdated.Core.Engine;
using Youpdated.Core.Platform;

namespace Youpdated.Core.ViewModels;

public sealed partial class FeedCard : ObservableObject
{
    internal FeedCard(FeedCardData data)
    {
        Data = data;
        ShowsUnread = data.ShowsUnread;
    }

    public FeedCardData Data { get; private set; }

    public TargetKey Target => Data.Target;

    public string Display => Data.Display;

    public string Source => Data.Source;

    public InboxItem Lead => Data.Lead;

    public string LeadText => Data.LeadText;

    public bool LeadIsVersion => Data.LeadIsVersion;

    public string? Line => Data.Line;

    public int More => Data.More;

    public Priority Priority => Data.Priority;

    public bool Starred => Data.Starred;

    public bool IsCompact => Data.IsCompact;

    public bool IsDimmed => Data.IsDimmed;

    public bool ShowsHighPriority => Data.ShowsHighPriority;

    public bool ShowsAccentEdge => Data.ShowsHighPriority && ShowsUnread;

    public DateTimeOffset When => Data.Lead.When;

    public IReadOnlyList<InboxItem> Items => Data.Items;

    public IReadOnlyList<InboxItem> AlsoNew => Data.AlsoNew;

    public Monogram Monogram => Monogram.For(Data.Target, Data.Display);

    public NotesPreview Notes => ViewModels.Notes.Preview(Data.Lead.Body, ShowFullNotes ? int.MaxValue : ViewModels.Notes.PreviewBlocks);

    public bool HasNotes => !string.IsNullOrWhiteSpace(Data.Lead.Body);

    public string Key => Data.Key;

    [ObservableProperty]
    public partial bool IsExpanded { get; set; }

    [ObservableProperty]
    public partial bool ShowsUnread { get; set; }

    [ObservableProperty]
    public partial TimeSpan FadeDelay { get; set; }

    [ObservableProperty]
    public partial bool ShowFullNotes { get; set; }

    public bool LeadChanged { get; private set; }

    internal bool Fading { get; set; }

    internal void Update(FeedCardData data)
    {
        LeadChanged = data.LeadText != Data.LeadText;
        Data = data;
        if (!Fading)
        {
            ShowsUnread = data.ShowsUnread;
        }
        // Everything derived from data
        OnPropertyChanged(string.Empty);
    }

    partial void OnShowsUnreadChanged(bool value) => OnPropertyChanged(nameof(ShowsAccentEdge));

    partial void OnShowFullNotesChanged(bool value) => OnPropertyChanged(nameof(Notes));
}

/// <summary>A section of the feed, as a group for a list to show with a header</summary>
public sealed class FeedSection(FeedSectionKey key, IList<FeedCard> cards) : ReadOnlyCollection<FeedCard>(cards)
{
    public FeedSectionKey Key => key;
}

/// <summary>Updates feed</summary>
public sealed partial class FeedViewModel : ObservableObject
{
    public static readonly TimeSpan DotFadeDelay = TimeSpan.FromSeconds(1.2);

    public static readonly TimeSpan CascadeStep = TimeSpan.FromMilliseconds(40);

    public const int CascadeSteps = 8;

    public static readonly TimeSpan SettleDelay = TimeSpan.FromMilliseconds(350);

    public const int SnoozeMinutes = 60;

    private readonly InboxViewModel _inbox;
    private readonly IShell _shell;
    private readonly TimeProvider _time;
    private readonly IUiDispatcher _ui;
    private readonly Version _appVersion;
    private readonly Dictionary<string, FeedCard> _cards = [];
    private readonly HashSet<string> _held = [];
    private ITimer? _settle;
    private int _own;
    private bool _loaded;

    public FeedViewModel(InboxViewModel inbox, IShell shell, TimeProvider time, IUiDispatcher ui, Version appVersion)
    {
        _inbox = inbox;
        _shell = shell;
        _time = time;
        _ui = ui;
        _appVersion = appVersion;
        _inbox.ItemsChanged += OnItemsChanged;
    }

    [ObservableProperty]
    public partial IReadOnlyList<FeedSection> Sections { get; private set; } = [];

    [ObservableProperty]
    public partial AppUpdate? SelfUpdate { get; private set; }

    [ObservableProperty]
    public partial FeedHeading? Heading { get; private set; }

    /// <summary>Settings → View, and options in ...</summary>
    [ObservableProperty]
    public partial ViewOptions View { get; set; } = ViewOptions.Defaults;

    /// <summary>Filter by source: a label from <see cref="Labels"/></summary>
    [ObservableProperty]
    public partial string? SourceFilter { get; set; }

    public IReadOnlyList<string> Labels =>
        [.. _inbox.Items.Select(item => SourceLabels.Label(item.Source, item.Target)).Distinct().Order(StringComparer.CurrentCultureIgnoreCase)];

    /// <summary>A selected row in Following</summary>
    [ObservableProperty]
    public partial TargetRef? TargetFilter { get; set; }

    [ObservableProperty]
    public partial FeedCard? Expanded { get; private set; }

    [ObservableProperty]
    public partial DateTimeOffset? LastCheck { get; set; }

    [ObservableProperty]
    public partial CheckProgress? Checking { get; set; }

    /// <summary>Checks paused until then (MaxValue: until resumed)</summary>
    [ObservableProperty]
    public partial DateTimeOffset? PausedUntil { get; set; }

    [ObservableProperty]
    public partial FeedText Text { get; set; } = new();

    /// <summary>Cards a check just added</summary>
    public IReadOnlySet<string> NewCards { get; private set; } = new HashSet<string>();

    public bool IsEmpty => Sections.Count == 0 && SelfUpdate is null;

    public string HeadingTitle => Heading?.Title(Text) ?? "";

    public IReadOnlyList<HeadingPart> HeadingLine => Heading?.Line(Text) ?? [];

    public string HeadingLineText => Heading?.LineText(Text) ?? "";

    public IEnumerable<FeedCard> Cards => Sections.SelectMany(section => section);

    public void Rebuild() => Rebuild(markNew: false);

    /// <summary>Click, Space, or Enter: expand or collapse</summary>
    public Task ToggleAsync(FeedCard card)
    {
        if (card.IsExpanded)
        {
            Collapse();
            return Task.CompletedTask;
        }
        return ExpandAsync(card);
    }

    public async Task ExpandAsync(FeedCard card)
    {
        if (Expanded is { } other && other != card)
        {
            Collapse();
        }
        card.IsExpanded = true;
        Expanded = card;
        var unread = LiveUnread(card);
        if (unread.Count == 0)
        {
            return;
        }
        _held.UnionWith(unread);
        if (await Own(() => _inbox.MarkAsync(unread, read: true)))
        {
            FadeLater(card, DotFadeDelay);
        }
    }

    /// <summary>Click again or Escape</summary>
    public void Collapse()
    {
        if (Expanded is not { } card)
        {
            return;
        }
        card.IsExpanded = false;
        card.ShowFullNotes = false;
        Expanded = null;
        _held.Clear();
        Rebuild();
    }

    /// <summary>Opening a card's link marks read</summary>
    public async Task OpenAsync(FeedCard card)
    {
        _shell.OpenUrl(card.Lead.Url);
        await MarkReadAsync(card);
    }

    public async Task OpenItemAsync(InboxItem item)
    {
        _shell.OpenUrl(item.Url);
        await Own(() => _inbox.MarkAsync([item.Id], read: true));
        RebuildSoon(DotFadeDelay + SettleDelay);
    }

    public async Task MarkReadAsync(FeedCard card)
    {
        var unread = LiveUnread(card);
        if (unread.Count == 0)
        {
            return;
        }
        if (card.IsExpanded)
        {
            _held.UnionWith(unread);
        }
        if (await Own(() => _inbox.MarkAsync(unread, read: true)))
        {
            FadeLater(card, DotFadeDelay);
            if (!card.IsExpanded)
            {
                RebuildSoon(DotFadeDelay + SettleDelay);
            }
        }
    }

    public async Task MarkUnreadAsync(FeedCard card)
    {
        var ids = card.Items.Select(item => item.Id).ToList();
        _held.ExceptWith(ids);
        if (await Own(() => _inbox.MarkAsync(ids, read: false)))
        {
            card.Fading = false;
            if (card.IsExpanded)
            {
                card.ShowsUnread = true;
            }
            else
            {
                Rebuild();
            }
        }
    }

    public Task ToggleReadAsync(FeedCard card) =>
        LiveUnread(card).Count > 0 ? MarkReadAsync(card) : MarkUnreadAsync(card);

    public async Task MarkAllReadAsync()
    {
        var unread = Cards.Where(card => card.ShowsUnread).ToList();
        // A label can be part of a source, so a filtered feed marks its own items
        var marked = SourceFilter is { } label
            ? Own(() => _inbox.MarkAsync(
                [.. _inbox.Items.Where(item => !item.Read && SourceLabels.Label(item.Source, item.Target) == label).Select(item => item.Id)],
                read: true))
            : Own(() => _inbox.MarkAllReadAsync());
        if (!await marked)
        {
            return;
        }
        for (var i = 0; i < unread.Count; i++)
        {
            var card = unread[i];
            card.FadeDelay = CascadeStep * Math.Min(i, CascadeSteps);
            card.Fading = true;
            card.ShowsUnread = false;
        }
        if (Expanded is { } expanded)
        {
            _held.UnionWith(expanded.Items.Select(item => item.Id));
        }
        RebuildSoon(CascadeStep * Math.Min(unread.Count, CascadeSteps) + SettleDelay);
    }

    public async Task SetPriorityAsync(FeedCard card, Priority priority)
    {
        if (await Own(() => _inbox.SetPriorityAsync(card.Target, priority)))
        {
            Rebuild();
        }
    }

    public Task SnoozeAsync(FeedCard card)
    {
        var unread = LiveUnread(card);
        return unread.Count == 0 ? Task.CompletedTask : Own(() => _inbox.SnoozeAsync(unread, SnoozeMinutes));
    }

    partial void OnViewChanged(ViewOptions value) => Rebuild();

    partial void OnCheckingChanged(CheckProgress? value) => UpdateHeading(Layout());

    partial void OnPausedUntilChanged(DateTimeOffset? value) => UpdateHeading(Layout());

    partial void OnSourceFilterChanged(string? value) => Rebuild();

    partial void OnTargetFilterChanged(TargetRef? value) => Rebuild();

    partial void OnLastCheckChanged(DateTimeOffset? value) => UpdateHeading(Layout());

    partial void OnTextChanged(FeedText value) => UpdateHeading(Layout());

    partial void OnSectionsChanged(IReadOnlyList<FeedSection> value) => OnPropertyChanged(nameof(IsEmpty));

    partial void OnSelfUpdateChanged(AppUpdate? value) => OnPropertyChanged(nameof(IsEmpty));

    private void OnItemsChanged(bool refreshed)
    {
        if (refreshed)
        {
            Rebuild(markNew: _loaded);
            _loaded = true;
        }
        else if (_own == 0)
        {
            // Read from a notification, the tray, or elsewhere
            Rebuild();
        }
    }

    private async Task<bool> Own(Func<Task<bool>> change)
    {
        _own++;
        try
        {
            return await change();
        }
        finally
        {
            _own--;
        }
    }

    private void FadeLater(FeedCard card, TimeSpan delay)
    {
        card.Fading = true;
        card.FadeDelay = TimeSpan.Zero;
        ITimer? timer = null;
        timer = _time.CreateTimer(_ => _ui.Post(() =>
        {
            timer?.Dispose();
            if (card.Fading)
            {
                card.ShowsUnread = LiveUnread(card).Count > 0;
                card.Fading = false;
            }
        }), null, delay, Timeout.InfiniteTimeSpan);
    }

    private List<string> LiveUnread(FeedCard card)
    {
        var ids = card.Items.Select(item => item.Id).ToHashSet();
        return [.. _inbox.Items.Where(item => ids.Contains(item.Id) && !item.Read).Select(item => item.Id)];
    }

    private void RebuildSoon(TimeSpan delay)
    {
        _settle?.Dispose();
        _settle = _time.CreateTimer(_ => _ui.Post(Rebuild), null, delay, Timeout.InfiniteTimeSpan);
    }

    private FeedLayout Layout() =>
        FeedBuilder.Build(
            _inbox.Items,
            new FeedOptions(View, SourceFilter, TargetFilter?.AsKey()),
            _time.GetUtcNow(),
            _time.LocalTimeZone,
            _appVersion,
            _held);

    private void Rebuild(bool markNew)
    {
        _settle?.Dispose();
        _settle = null;
        var layout = Layout();
        var before = _cards.Keys.ToHashSet();
        var seen = new HashSet<string>();
        var sections = new List<FeedSection>();
        foreach (var section in layout.Sections)
        {
            var cards = new List<FeedCard>();
            foreach (var data in section.Cards)
            {
                if (_cards.TryGetValue(data.Key, out var card))
                {
                    card.Fading = card.Fading && card.IsExpanded;
                    card.Update(data);
                }
                else
                {
                    card = _cards[data.Key] = new FeedCard(data);
                }
                card.FadeDelay = TimeSpan.Zero;
                seen.Add(data.Key);
                cards.Add(card);
            }
            sections.Add(new FeedSection(section.Key, cards));
        }
        foreach (var gone in _cards.Keys.Where(key => !seen.Contains(key)).ToList())
        {
            _cards.Remove(gone);
        }
        if (Expanded is { } expanded && !seen.Contains(expanded.Key))
        {
            Expanded = null;
            _held.Clear();
        }
        NewCards = markNew ? seen.Where(key => !before.Contains(key)).ToHashSet() : new HashSet<string>();
        SelfUpdate = layout.SelfUpdate;
        Sections = sections;
        UpdateHeading(layout);
    }

    private void UpdateHeading(FeedLayout layout)
    {
        Heading = FeedHeading.For(layout, LastCheck, _time.GetUtcNow(), _time.LocalTimeZone, Checking, PausedUntil);
        OnPropertyChanged(nameof(HeadingTitle));
        OnPropertyChanged(nameof(HeadingLine));
        OnPropertyChanged(nameof(HeadingLineText));
    }
}
