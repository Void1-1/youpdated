using Microsoft.Extensions.Time.Testing;
using Youpdated.Core.Platform;
using Youpdated.Core.Scheduling;

namespace Youpdated.Core.Tests;

public sealed class ImmediateDispatcher : IUiDispatcher
{
    public void Post(Action action) => action();
}

public class SchedulerTests
{
    // 09:00 UTC, Thursday
    private static readonly DateTimeOffset Start = new(2026, 10, 29, 9, 0, 0, TimeSpan.Zero);

    private readonly FakeTimeProvider _time = new(Start);
    private readonly MemoryLocalStateStore _store = new();
    private readonly List<DateTimeOffset> _checks = [];
    private readonly List<DateTimeOffset> _digests = [];
    private bool _checkSucceeds = true;

    public SchedulerTests()
    {
        _time.SetLocalTimeZone(TimeZoneInfo.Utc);
    }

    private CheckScheduler Scheduler()
    {
        CheckScheduler? scheduler = null;
        scheduler = new CheckScheduler(_time, new ImmediateDispatcher(), _store,
            () =>
            {
                _checks.Add(_time.GetUtcNow());
                if (_checkSucceeds)
                {
                    scheduler!.Checked(_time.GetUtcNow());
                }
                return Task.FromResult(_checkSucceeds);
            },
            () =>
            {
                _digests.Add(_time.GetUtcNow());
                return Task.FromResult(true);
            });
        return scheduler;
    }

    [Fact]
    public void ChecksAtLaunchWhenNeverChecked()
    {
        using var scheduler = Scheduler();
        scheduler.Start();
        _time.Advance(TimeSpan.Zero);

        Assert.Equal([Start], _checks);
        Assert.Equal(Start + TimeSpan.FromHours(6), scheduler.NextCheck);
    }

    [Fact]
    public void WaitsOutTheIntervalFromTheLastRun()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start - TimeSpan.FromHours(2));
        scheduler.Start();

        _time.Advance(TimeSpan.FromHours(3.9));
        Assert.Empty(_checks);
        _time.Advance(TimeSpan.FromHours(0.1));
        Assert.Equal([Start + TimeSpan.FromHours(4)], _checks);
    }

    [Fact]
    public void FollowsTheIntervalSetting()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start);
        scheduler.Configure(TimeSpan.FromHours(1), null);
        scheduler.Start();

        for (var hour = 0; hour < 3; hour++)
        {
            _time.Advance(TimeSpan.FromHours(1));
        }
        Assert.Equal(3, _checks.Count);
    }

    [Fact]
    public void PauseHoldsChecksUntilItEnds()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start - TimeSpan.FromHours(6));
        scheduler.PauseFor(TimeSpan.FromHours(1));
        scheduler.Start();

        _time.Advance(TimeSpan.FromMinutes(59));
        Assert.Empty(_checks);
        Assert.True(scheduler.IsPaused);
        _time.Advance(TimeSpan.FromMinutes(1));
        Assert.Single(_checks);
        Assert.False(scheduler.IsPaused);
    }

    [Fact]
    public void PauseUntilTomorrowEndsAtLocalMidnight()
    {
        using var scheduler = Scheduler();
        scheduler.PauseUntilTomorrow();
        Assert.Equal(new DateTimeOffset(2026, 10, 30, 0, 0, 0, TimeSpan.Zero), scheduler.PausedUntil);
    }

    [Fact]
    public void PauseUntilResumedSurvivesARestartOfTheApp()
    {
        using (var scheduler = Scheduler())
        {
            scheduler.PauseUntilResumed();
        }
        using var again = Scheduler();
        again.Start();
        _time.Advance(TimeSpan.FromDays(3));

        Assert.True(again.IsPausedUntilResumed);
        Assert.Empty(_checks);
        Assert.Null(again.NextCheck);

        again.Resume();
        _time.Advance(TimeSpan.Zero);
        Assert.Single(_checks);
    }

    [Fact]
    public void AFailedCheckIsRetriedSoon()
    {
        _checkSucceeds = false;
        using var scheduler = Scheduler();
        scheduler.Start();
        _time.Advance(TimeSpan.Zero);
        Assert.Single(_checks);

        _time.Advance(CheckScheduler.RetryAfterFailure);
        Assert.Equal(2, _checks.Count);
    }

    [Fact]
    public void WakeChecksIfDueAfterSettling()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start);
        scheduler.Start();
        scheduler.Stop();
        _time.SetUtcNow(Start + TimeSpan.FromHours(10));

        scheduler.SystemResumed();
        scheduler.Start();
        _time.Advance(CheckScheduler.SettleAfterWake - TimeSpan.FromSeconds(1));
        Assert.Empty(_checks);
        _time.Advance(TimeSpan.FromSeconds(1));

        Assert.Single(_checks);
    }

    [Fact]
    public void DigestGoesOutAtItsTime()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start);
        scheduler.Configure(TimeSpan.FromHours(24), new TimeOnly(18, 0));
        scheduler.Start();

        _time.Advance(TimeSpan.FromHours(8.9));
        Assert.Empty(_digests);
        _time.Advance(TimeSpan.FromHours(0.1));
        Assert.Equal([new DateTimeOffset(2026, 10, 29, 18, 0, 0, TimeSpan.Zero)], _digests);
        Assert.Equal(new DateTimeOffset(2026, 10, 30, 18, 0, 0, TimeSpan.Zero), scheduler.NextDigest);
    }

    [Fact]
    public void TurningTheDigestOnDoesNotSendTodaysPastOne()
    {
        _time.SetUtcNow(Start + TimeSpan.FromHours(10)); // 19:00
        using var scheduler = Scheduler();
        scheduler.LastRunWas(_time.GetUtcNow());
        scheduler.Configure(TimeSpan.FromHours(6), new TimeOnly(18, 0));
        scheduler.Start();
        _time.Advance(TimeSpan.FromMinutes(1));

        Assert.Empty(_digests);
        Assert.Equal(new DateTimeOffset(2026, 10, 30, 18, 0, 0, TimeSpan.Zero), scheduler.NextDigest);
    }

    [Fact]
    public void AMissedDigestGoesOutOnWake()
    {
        using var scheduler = Scheduler();
        scheduler.LastRunWas(Start);
        scheduler.Configure(TimeSpan.FromHours(24), new TimeOnly(18, 0));
        scheduler.Start();
        scheduler.Stop();
        _time.SetUtcNow(new DateTimeOffset(2026, 10, 29, 22, 0, 0, TimeSpan.Zero));

        scheduler.SystemResumed();
        scheduler.Start();
        Assert.Empty(_digests);
        _time.Advance(CheckScheduler.SettleAfterWake);

        Assert.Single(_digests);
        Assert.Equal(new DateTimeOffset(2026, 10, 30, 18, 0, 0, TimeSpan.Zero), scheduler.NextDigest);
    }

    [Fact]
    public void NoChecksWhileStopped()
    {
        using var scheduler = Scheduler();
        scheduler.Start();
        scheduler.Stop();
        _time.Advance(TimeSpan.FromDays(1));
        Assert.True(_checks.Count <= 1);
        Assert.Null(scheduler.NextCheck);
    }

    [Fact]
    public void ASkippedDaylightSavingTimeComesAnHourLater()
    {
        var zone = TimeZoneInfo.FindSystemTimeZoneById("America/New_York");
        var after = new DateTimeOffset(2026, 3, 8, 0, 0, 0, TimeSpan.FromHours(-5));
        var next = CheckScheduler.NextOccurrence(new TimeOnly(2, 30), after, zone);
        Assert.Equal(new DateTimeOffset(2026, 3, 8, 3, 30, 0, TimeSpan.FromHours(-4)), next);
    }
}
