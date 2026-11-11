using Youpdated.Core.Platform;

namespace Youpdated.Core.Scheduling;

/// <summary>When to check and send the digest</summary>
public sealed class CheckScheduler : IDisposable
{
    /// <summary>After wake, pause for a moment</summary>
    public static readonly TimeSpan SettleAfterWake = TimeSpan.FromSeconds(20);

    /// <summary>After a check that couldn't run</summary>
    public static readonly TimeSpan RetryAfterFailure = TimeSpan.FromMinutes(5);

    public static readonly TimeSpan DefaultInterval = TimeSpan.FromHours(6);

    private readonly TimeProvider _time;
    private readonly IUiDispatcher _ui;
    private readonly ILocalStateStore _store;
    private readonly Func<Task<bool>> _check;
    private readonly Func<Task<bool>> _digest;
    private readonly object _gate = new();
    private ITimer? _checkTimer;
    private ITimer? _digestTimer;
    private DateTimeOffset? _retryAt;
    private DateTimeOffset? _digestRetryAt;
    private DateTimeOffset? _settleUntil;
    // Each reschedule's number
    private long _generation;
    private bool _enabled;
    private bool _checking;
    private bool _disposed;

    /// <param name="check">Runs a check</param>
    /// <param name="digest">Sends the digest</param>
    public CheckScheduler(
        TimeProvider time, IUiDispatcher ui, ILocalStateStore store, Func<Task<bool>> check, Func<Task<bool>> digest)
    {
        _time = time;
        _ui = ui;
        _store = store;
        _check = check;
        _digest = digest;
    }

    /// <summary>Something about the schedule or pause changed</summary>
    public event Action? Changed;

    public TimeSpan Interval { get; private set; } = DefaultInterval;

    public TimeOnly? DigestTime { get; private set; }

    /// <summary>The last check by anyone (CLI included)</summary>
    public DateTimeOffset? LastCheck { get; private set; }

    public DateTimeOffset? NextCheck { get; private set; }

    public DateTimeOffset? NextDigest { get; private set; }

    public DateTimeOffset? PausedUntil => _store.Load().PausedUntil is { } until && until > _time.GetUtcNow() ? until : null;

    public bool IsPaused => PausedUntil is not null;

    public bool IsPausedUntilResumed => PausedUntil == DateTimeOffset.MaxValue;

    public bool IsDue => LastCheck is not { } last || _time.GetUtcNow() >= last + Interval;

    /// <summary>Start scheduling</summary>
    public void Start()
    {
        lock (_gate)
        {
            _enabled = true;
        }
        Reschedule();
    }

    /// <summary>Stop scheduling</summary>
    public void Stop()
    {
        lock (_gate)
        {
            _enabled = false;
        }
        Reschedule();
    }

    public void Configure(TimeSpan interval, TimeOnly? digest)
    {
        var store = _store.Load();
        if (digest is not null && (digest != DigestTime || store.LastDigestAt is null))
        {
            // A digest time set now starts from now, not from one already past today
            _store.Save(store with { LastDigestAt = _time.GetUtcNow() });
        }
        Interval = interval;
        DigestTime = digest;
        Reschedule();
    }

    /// <summary>A check finished</summary>
    public void Checked(DateTimeOffset at)
    {
        LastCheck = at;
        _retryAt = null;
        Reschedule();
    }

    /// <summary>What the engine last reported</summary>
    public void LastRunWas(DateTimeOffset? at)
    {
        if (at is { } when && (LastCheck is null || when > LastCheck))
        {
            LastCheck = when;
            Reschedule();
        }
    }

    public void PauseFor(TimeSpan duration) => Pause(_time.GetUtcNow() + duration);

    /// <summary>Until tomorrow, local time</summary>
    public void PauseUntilTomorrow()
    {
        var zone = _time.LocalTimeZone;
        var local = TimeZoneInfo.ConvertTime(_time.GetUtcNow(), zone);
        var midnight = local.Date.AddDays(1);
        Pause(new DateTimeOffset(midnight, zone.GetUtcOffset(midnight)));
    }

    public void PauseUntilResumed() => Pause(DateTimeOffset.MaxValue);

    public void Resume() => Pause(null);

    /// <summary>check if a digest was missed during sleep, send a missed digest</summary>
    public void SystemResumed()
    {
        _settleUntil = _time.GetUtcNow() + SettleAfterWake;
        Reschedule();
    }

    public void Dispose()
    {
        lock (_gate)
        {
            _disposed = true;
            _checkTimer?.Dispose();
            _digestTimer?.Dispose();
        }
    }

    /// <summary>The first <paramref name="time"/> in <paramref name="zone"/> after <paramref name="after"/></summary>
    public static DateTimeOffset NextOccurrence(TimeOnly time, DateTimeOffset after, TimeZoneInfo zone)
    {
        var local = TimeZoneInfo.ConvertTime(after, zone);
        var candidate = local.Date + time.ToTimeSpan();
        if (candidate <= local.DateTime)
        {
            candidate = candidate.AddDays(1);
        }
        // A time skipped by daylight saving comes an hour later that day
        if (zone.IsInvalidTime(candidate))
        {
            candidate = candidate.AddHours(1);
        }
        return new DateTimeOffset(candidate, zone.GetUtcOffset(candidate));
    }

    private void Pause(DateTimeOffset? until)
    {
        _store.Save(_store.Load() with { PausedUntil = until });
        Reschedule();
    }

    private void Reschedule()
    {
        var now = _time.GetUtcNow();
        long generation;
        TimeSpan? checkWait = null;
        TimeSpan? digestWait = null;
        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }
            generation = ++_generation;
            _checkTimer?.Dispose();
            _checkTimer = null;
            _digestTimer?.Dispose();
            _digestTimer = null;
            NextCheck = null;
            NextDigest = null;

            if (_enabled)
            {
                var due = LastCheck is { } last ? last + Interval : now;
                if (_retryAt is { } retry && retry > due)
                {
                    due = retry;
                }
                if (_settleUntil is { } settle && settle > due)
                {
                    due = settle;
                }
                if (PausedUntil is { } paused && paused > due)
                {
                    due = paused;
                }
                if (due != DateTimeOffset.MaxValue)
                {
                    NextCheck = due;
                    checkWait = Wait(due, now);
                }

                if (DigestTime is { } digest)
                {
                    var lastDigest = _store.Load().LastDigestAt ?? now;
                    var next = _digestRetryAt ?? NextOccurrence(digest, lastDigest, _time.LocalTimeZone);
                    if (_settleUntil is { } settleDigest && next < settleDigest)
                    {
                        next = settleDigest;
                    }
                    NextDigest = next;
                    digestWait = Wait(next, now);
                }
            }
        }
        Changed?.Invoke();
        if (checkWait is { } untilCheck)
        {
            Arm(generation, untilCheck, digest: false);
        }
        if (digestWait is { } untilDigest)
        {
            Arm(generation, untilDigest, digest: true);
        }
    }

    private void Arm(long generation, TimeSpan wait, bool digest)
    {
        var timer = _time.CreateTimer(
            _ => _ui.Post(() => _ = digest ? RunDigestAsync() : RunCheckAsync()),
            null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        lock (_gate)
        {
            if (generation != _generation || _disposed)
            {
                timer.Dispose();
                return;
            }
            if (digest)
            {
                _digestTimer = timer;
            }
            else
            {
                _checkTimer = timer;
            }
        }
        timer.Change(wait, Timeout.InfiniteTimeSpan);
    }

    private static TimeSpan Wait(DateTimeOffset due, DateTimeOffset now) => due > now ? due - now : TimeSpan.Zero;

    private async Task RunCheckAsync()
    {
        lock (_gate)
        {
            if (!_enabled || _checking || _disposed)
            {
                return;
            }
            _checking = true;
        }
        _settleUntil = null;
        bool ran;
        try
        {
            ran = await _check().ConfigureAwait(true);
        }
        catch (Exception)
        {
            ran = false;
        }
        finally
        {
            lock (_gate)
            {
                _checking = false;
            }
        }
        if (!ran)
        {
            _retryAt = _time.GetUtcNow() + RetryAfterFailure;
        }
        Reschedule();
    }

    private async Task RunDigestAsync()
    {
        lock (_gate)
        {
            if (!_enabled || _disposed)
            {
                return;
            }
        }
        _settleUntil = null;
        bool sent;
        try
        {
            sent = await _digest().ConfigureAwait(true);
        }
        catch (Exception)
        {
            sent = false;
        }
        if (sent)
        {
            _digestRetryAt = null;
            _store.Save(_store.Load() with { LastDigestAt = _time.GetUtcNow() });
        }
        else
        {
            // Again shortly, rather than at tomorrow's time
            _digestRetryAt = _time.GetUtcNow() + RetryAfterFailure;
        }
        Reschedule();
    }
}
