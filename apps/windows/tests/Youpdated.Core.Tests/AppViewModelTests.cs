using System.Text.Json.Nodes;
using Youpdated.Core.Contract;
using Youpdated.Core.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.Core.Tests;

public class AppViewModelTests
{
    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static JsonObject FixtureReply(string name, Action<JsonObject> change)
    {
        var reply = (JsonObject)Fixtures.Get(name).Reply.DeepClone();
        change(reply);
        return reply;
    }

    [Fact]
    public async Task LaunchLoadsEverythingAndStartsTheSchedule()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        var app = test.App;
        Assert.True(app.IsReady);
        Assert.Equal(1, test.UrlScheme.Registered);
        Assert.Equal(4, app.Unread);
        Assert.Equal([FeedSectionKind.Today, FeedSectionKind.Earlier], app.Feed.Sections.Select(s => s.Key.Kind));
        Assert.Equal(WindowState.Feed, app.WindowState);
        Assert.Equal(6, app.Sources.Targets.Count);
        Assert.True(app.Sources.Targets[0].Starred);
        Assert.Equal(2, app.Problems.Problems.Count);
        Assert.Equal(TrayState.Problems, app.TrayState);
        Assert.Equal(AppViewModel.TrayItems, app.RecentUnread.Count);
        Assert.Equal("1.0.0", app.Settings.EngineVersion);
        // status/ok: last checked 08:00:06
        Assert.Equal(new DateTimeOffset(2026, 10, 29, 14, 0, 6, TimeSpan.Zero), app.Scheduler.NextCheck);
        Assert.True(test.Startup.IsEnabled);
        Assert.True(test.LocalState.State.SetUp);
    }

    [Fact]
    public async Task TheSelfWatchReleaseIsAnUpdate()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        var update = test.App.AvailableUpdate!;
        Assert.Equal(new Version(1, 1, 0), update.Version);
        Assert.Equal("1.1", update.DisplayVersion);
        Assert.Equal(
            "https://github.com/Void1-1/youpdated/releases/download/app-windows-v1.1.0/Youpdated-1.1.0-Windows-x64.zip",
            update.DownloadUrl.ToString());
    }

    [Fact]
    public async Task FirstLaunchCreatesTheAppsConfig()
    {
        var engine = new FakeEngine().Next("status", "first_launch");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        var init = Assert.Single(engine.RequestsFor("config.init"));
        Assert.Null(init["self_watch"]);
        Assert.True(test.App.IsReady);
        Assert.Equal(0, test.SelfWatchOffers);
    }

    [Fact]
    public async Task AnExistingConfigIsOfferedTheSelfWatchOnce()
    {
        var engine = new FakeEngine().Then("status", _ => FakeEngine.Reply(FixtureReply("status/ok", r => r["self_watch"] = "absent")));
        await using var test = new TestApp(engine);
        await test.StartAsync();
        Assert.Equal(1, test.SelfWatchOffers);

        await test.App.AnswerSelfWatchOfferAsync(watch: false);

        var set = engine.RequestsFor("settings.set").Single();
        Assert.True(set["settings"]!["self_watch_declined"]!.GetValue<bool>());
    }

    [Fact]
    public async Task PrivacyIsReadAtStartAndAProxyThatFailsIsExplained()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var settings = test.App.Settings;
        Assert.True(settings.CanEditPrivacy);
        Assert.Equal(ProxyChoice.None, settings.ProxyChoice);
        Assert.Equal("0.5-3 s", settings.GapText);
        var problems = test.App.Problems.Problems.Count;

        engine.Next("privacy.set", "unreachable");
        await settings.ChooseProxyAsync(ProxyChoice.Tor);
        Assert.Contains("cannot reach the proxy", settings.PrivacyError);
        Assert.Equal(ProxyChoice.None, settings.ProxyChoice);
        Assert.Equal(problems, test.App.Problems.Problems.Count);

        engine.Next("privacy.set", "tor");
        await settings.ChooseProxyAsync(ProxyChoice.Tor);
        Assert.Null(settings.PrivacyError);
        Assert.Equal(ProxyChoice.Tor, settings.ProxyChoice);
        Assert.Equal("socks5://127.0.0.1:9050", engine.RequestsFor("privacy.set").Last()["proxy"]!.GetValue<string>());
    }

    [Fact]
    public async Task ACustomProxyThatFailsKeepsWhatWasTyped()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var settings = test.App.Settings;

        await settings.ChooseProxyAsync(ProxyChoice.Custom);
        Assert.Empty(engine.RequestsFor("privacy.set"));
        Assert.False(settings.CanUseAddress);
        settings.ProxyAddress = " socks5://10.0.0.2:1080 ";
        engine.Next("privacy.set", "unreachable");
        await settings.UseProxyAddressAsync();

        Assert.Equal("socks5://10.0.0.2:1080", engine.RequestsFor("privacy.set").Single()["proxy"]!.GetValue<string>());
        Assert.Equal(ProxyChoice.Custom, settings.ProxyChoice);
        Assert.Equal(" socks5://10.0.0.2:1080 ", settings.ProxyAddress);
        Assert.NotNull(settings.PrivacyError);
    }

    [Fact]
    public async Task HiddenSelfWatchSuggestionsAreNotOfferedAndAreRemembered()
    {
        var engine = new FakeEngine().Then("status", _ => FakeEngine.Reply(FixtureReply("status/ok", r => r["self_watch"] = "absent")));
        await using var test = new TestApp(engine);
        test.App.Settings.HideSelfWatchSuggestions = true;
        await test.StartAsync();

        Assert.Equal(0, test.SelfWatchOffers);
        Assert.True(test.LocalState.Load().HideSelfWatchSuggestions);
        Assert.False(test.App.Settings.OffersWatchSelf);
        // nothing is declined for the CLI
        Assert.Empty(engine.RequestsFor("settings.set"));
    }

    [Fact]
    public async Task ALockedSetupAsksForThePassphrase()
    {
        var engine = new FakeEngine().Next("status", "locked");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        Assert.Equal(1, test.UnlockNeeded);
        Assert.True(test.App.IsLocked);
        Assert.False(test.App.IsReady);
        Assert.Equal(TrayState.Locked, test.App.TrayState);
        Assert.Empty(engine.RequestsFor("check"));

        engine.Next("unlock", "wrong_passphrase");
        test.App.Unlock.Passphrase = "wrong";
        Assert.False(await test.App.Unlock.UnlockAsync());
        Assert.Contains("wrong passphrase", test.App.Unlock.Error);

        test.App.Unlock.Passphrase = "correct horse";
        Assert.False(test.App.Unlock.Remember);
        Assert.True(await test.App.Unlock.UnlockAsync());
        Assert.True(test.App.IsReady);
        Assert.Null(test.Credentials.Stored);
    }

    [Fact]
    public async Task RememberStoresThePassphrase()
    {
        var engine = new FakeEngine().Next("status", "locked");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        test.App.Unlock.Passphrase = "correct horse";
        test.App.Unlock.Remember = true;
        await test.App.Unlock.UnlockAsync();

        Assert.Equal("correct horse", test.Credentials.Stored);
        Assert.True(test.App.Settings.RememberPassphrase);
    }

    [Fact]
    public async Task AStoredPassphraseUnlocksWithoutAsking()
    {
        var engine = new FakeEngine().Next("status", "locked");
        await using var test = new TestApp(engine);
        test.Credentials.Stored = "correct horse";
        await test.StartAsync();

        Assert.Equal(0, test.UnlockNeeded);
        Assert.True(test.App.IsReady);
        Assert.Equal("correct horse", engine.RequestsFor("unlock").Single()["passphrase"]!.GetValue<string>());
    }

    [Fact]
    public async Task AStoredPassphraseThatNoLongerWorksIsForgotten()
    {
        var engine = new FakeEngine().Next("status", "locked").Next("unlock", "wrong_passphrase");
        await using var test = new TestApp(engine);
        test.Credentials.Stored = "old";
        await test.StartAsync();

        Assert.Equal(1, test.UnlockNeeded);
        Assert.Null(test.Credentials.Stored);
    }

    [Fact]
    public async Task ACheckPostsTheEnginesAlerts()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        Assert.True(await test.App.CheckAsync());

        Assert.Equal(2, test.Notifications.Shown.Count);
        var high = test.Notifications.Shown[0];
        Assert.Equal(AlertPriority.High, high.Priority);
        Assert.Equal("python/cpython", high.Target!.Display);
        Assert.Equal("git", high.Group);
        var combined = test.Notifications.Shown[1];
        Assert.Null(combined.Target);
        Assert.Equal(2, combined.Items.Count);
        Assert.Equal(NotificationRequest.MixedGroup, combined.Group);
        Assert.False(combined.IsAppUpdate);
        Assert.Equal(new DateTimeOffset(2026, 10, 29, 8, 0, 6, TimeSpan.Zero), test.App.Scheduler.LastCheck);
    }

    [Fact]
    public async Task ACheckTheCliHoldsIsRetried()
    {
        var engine = new FakeEngine().Next("check", "history_busy");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        Assert.False(await test.App.CheckAsync());
        Assert.False(test.App.IsChecking);
    }

    [Fact]
    public async Task AddingPreviewsPicksASourceAddsAndChecksTheNewTarget()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var add = test.App.Sources.Add;

        engine.Next("targets.preview", "ambiguous");
        add.Value = "express";
        await add.PreviewAsync();
        Assert.Equal(AddStage.Choosing, add.Stage);
        Assert.Equal(["homebrew", "npm", "pypi"], add.Choices);

        engine.Next("targets.preview", "picked");
        await add.ChooseAsync("npm");
        Assert.Equal(AddStage.Ready, add.Stage);
        Assert.Equal("npm", engine.RequestsFor("targets.preview").Last()["source"]!.GetValue<string>());
        Assert.False(add.CanAdd);

        add.Value = "https://github.com/python/cpython";
        Assert.Equal(AddStage.Idle, add.Stage);
        await add.PreviewAsync();
        Assert.True(add.CanAdd);
        Assert.Equal("v3.14.7", add.Preview!.Newest!.Version);

        var checkedAfter = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        engine.Then("check", request =>
        {
            checkedAfter.TrySetResult();
            return FakeEngine.Answer(Fixtures.Get("check/targets"));
        });
        await add.AddAsync();
        await checkedAfter.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);

        Assert.Equal(AddStage.Added, add.Stage);
        var added = engine.RequestsFor("targets.add").Single();
        Assert.Equal("git", added["source"]!.GetValue<string>());
        Assert.Equal("python/cpython", added["entries"]![0]!.GetValue<string>());
        var check = engine.RequestsFor("check").Single();
        Assert.Equal("github.com/python/cpython", check["targets"]![0]!["key"]!.GetValue<string>());
    }

    [Fact]
    public async Task AnUnrecognizedValueSaysWhy()
    {
        var engine = new FakeEngine().Next("targets.preview", "unrecognized");
        await using var test = new TestApp(engine);
        await test.StartAsync();

        await test.App.Sources.Add.StartAsync("hello there");

        Assert.Equal(AddStage.Failed, test.App.Sources.Add.Stage);
        Assert.Contains("could not be determined", test.App.Sources.Add.Error);
    }

    [Fact]
    public async Task RemovingCanForgetTheHistory()
    {
        var engine = new FakeEngine().Next("targets.remove", "wipe");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var tf2 = test.App.Sources.Targets.Single(t => t.Key == "440");

        Assert.True(await test.App.Sources.RemoveAsync(tf2, wipe: true));

        var remove = engine.RequestsFor("targets.remove").Single();
        Assert.True(remove["wipe"]!.GetValue<bool>());
        Assert.DoesNotContain(test.App.Sources.Targets, t => t.Key == "440");
    }

    [Fact]
    public async Task ImportPreviewsThenAppliesAndChecksWhatItAdded()
    {
        var engine = new FakeEngine().Next("import.preview", "path");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var import = test.App.Sources.Import;

        await import.PreviewFileAsync("/Users/me/project/package.json");
        Assert.Equal(2, import.ToAdd);
        Assert.Single(import.Preview!.Skipped);

        var checkedAfter = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        engine.Then("check", _ =>
        {
            checkedAfter.TrySetResult();
            return FakeEngine.Answer(Fixtures.Get("check/targets"));
        });
        await import.ApplyAsync();
        await checkedAfter.Task.WaitAsync(TimeSpan.FromSeconds(5), Token);

        var check = engine.RequestsFor("check").Single();
        Assert.Equal(["react-dom", "vite"], check["targets"]!.AsArray().Select(t => t!["key"]!.GetValue<string>()));
    }

    [Fact]
    public async Task SettingsGoToTheEngineAndTheScheduleFollows()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var settings = test.App.Settings;
        Assert.Empty(engine.RequestsFor("settings.set"));

        settings.DigestEnabled = true;
        await settings.PendingSave;

        var sent = engine.RequestsFor("settings.set").Single()["settings"]!.AsObject();
        Assert.Equal("18:00", sent["digest_time"]!.GetValue<string>());
        Assert.Equal(new TimeOnly(18, 0), test.App.Scheduler.DigestTime);
        Assert.Equal(new DateTimeOffset(2026, 10, 29, 18, 0, 0, TimeSpan.Zero), test.App.Scheduler.NextDigest);
    }

    [Fact]
    public async Task StartAtLoginIsOsLevel()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        test.App.Settings.StartAtLogin = false;

        Assert.False(test.Startup.IsEnabled);
        Assert.Empty(engine.RequestsFor("settings.set"));
    }

    [Fact]
    public async Task TheCliNoticeShowsOnce()
    {
        var engine = new FakeEngine().Next("status", "cli_ran");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        Assert.Equal(1, test.CliNotices);

        await test.App.CliNoticeShownAsync();
        Assert.True(engine.RequestsFor("settings.set").Single()["settings"]!["cli_notice_shown"]!.GetValue<bool>());
    }

    [Fact]
    public async Task NotificationButtonsActOnTheirItems()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        await test.App.HandleNotificationAsync(NotificationAction.MarkRead, ["i_8f3a1c2e9b7d4a10"]);
        var mark = engine.RequestsFor("inbox.mark").Single();
        Assert.True(mark["read"]!.GetValue<bool>());
        Assert.Contains("i_8f3a1c2e9b7d4a10", test.Notifications.Removed);

        await test.App.HandleNotificationAsync(NotificationAction.Open, ["i_2b9e5d7f0c1a3e44"]);
        Assert.Equal(["https://store.steampowered.com/news/app/440/view/5001"], test.Shell.Opened);

        await test.App.HandleNotificationAsync(NotificationAction.Open, ["i_2b9e5d7f0c1a3e44", "i_c4d1e8a2f6b39017"]);
        Assert.Equal([AppPage.Updates], test.Shown);
    }

    [Fact]
    public async Task AConfigChangeFromTheCliReloadsThePages()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();
        var before = engine.RequestsFor("targets.list").Count();

        await test.App.ConfigChangedAsync();

        Assert.Equal(before + 1, engine.RequestsFor("targets.list").Count());
    }

    [Fact]
    public async Task ReadyWaitsForTheFirstUnlock()
    {
        var engine = new FakeEngine().Next("status", "locked");
        await using var test = new TestApp(engine);
        await test.StartAsync();
        Assert.False(test.App.Ready.IsCompleted);

        test.App.Unlock.Passphrase = "correct horse";
        await test.App.Unlock.UnlockAsync();

        await test.App.Ready.WaitAsync(TimeSpan.FromSeconds(5), Token);
    }

    [Fact]
    public async Task ASnoozedItemComesBackWhenTheSnoozeEnds()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        engine.Then("inbox.list", _ => FakeEngine.Reply(FixtureReply("inbox.list/ok", reply =>
        {
            foreach (var item in reply["items"]!.AsArray())
            {
                if (item!["id"]!.GetValue<string>() == "i_2b9e5d7f0c1a3e44")
                {
                    item["snoozed_until"] = "2026-10-29T10:00:00Z";
                }
            }
        })));
        await test.App.HandleNotificationAsync(NotificationAction.Snooze, ["i_2b9e5d7f0c1a3e44"]);
        Assert.Equal(60, engine.RequestsFor("inbox.snooze").Single()["minutes"]!.GetValue<int>());
        Assert.Empty(test.Notifications.Shown);

        test.Time.Advance(TimeSpan.FromHours(1));

        var again = Assert.Single(test.Notifications.Shown);
        Assert.Equal("i_2b9e5d7f0c1a3e44", again.Items.Single().Id);
        Assert.Equal("steam", again.Group);
    }

    [Fact]
    public async Task LinksAddOpenAndCheck()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        var asked = new List<string?>();
        test.App.AddRequested += asked.Add;
        await test.App.HandleLinkAsync(YoupdatedLink.Parse("youpdated://add?url=https%3A%2F%2Fgithub.com%2Fpython%2Fcpython")!);
        Assert.Equal([AppPage.Updates], test.Shown);
        Assert.Equal(["https://github.com/python/cpython"], asked);
        Assert.Equal(AddStage.Ready, test.App.Sources.Add.Stage);
        Assert.Equal("https://github.com/python/cpython",
            engine.RequestsFor("targets.preview").Single()["value"]!.GetValue<string>());

        await test.App.HandleLinkAsync(new YoupdatedLink.Check());
        Assert.Single(engine.RequestsFor("check"));
    }

    [Fact]
    public async Task AnEngineCrashShowsUnderProblems()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        await test.StartAsync();

        engine.Crash(1, "ValueError: boom");
        for (var i = 0; i < 100 && !test.App.Problems.Problems.Any(p => p.Kind == Problem.EngineKind); i++)
        {
            await Task.Delay(10, Token);
        }

        var crash = test.App.Problems.Problems[0];
        Assert.Equal(Problem.EngineKind, crash.Kind);
        Assert.Contains("ValueError: boom", crash.Message);
        Assert.Equal(TrayState.Problems, test.App.TrayState);
        Assert.Null(test.App.Scheduler.NextCheck);
    }

    [Fact]
    public async Task PausingShowsInTheTray()
    {
        await using var test = new TestApp();
        await test.StartAsync();

        test.App.PauseForHour();
        Assert.Equal(TrayState.Paused, test.App.TrayState);
        Assert.Equal(test.Time.GetUtcNow() + TimeSpan.FromHours(1), test.App.Scheduler.PausedUntil);

        test.App.Resume();
        Assert.NotEqual(TrayState.Paused, test.App.TrayState);
    }

    [Fact]
    public async Task RemovingFromThisPcClearsTheAppsTraces()
    {
        var engine = new FakeEngine();
        await using var test = new TestApp(engine);
        test.Credentials.Stored = "x";
        await test.StartAsync();

        Assert.True(await test.App.RemoveFromThisPcAsync(alsoData: true));

        Assert.False(test.Startup.IsEnabled);
        Assert.True(test.UrlScheme.Unregistered);
        Assert.True(test.Notifications.Unregistered);
        Assert.Null(test.Credentials.Stored);
        Assert.True(test.DataRemover.Removed);
        Assert.Single(engine.RequestsFor("shutdown"));
    }
}
