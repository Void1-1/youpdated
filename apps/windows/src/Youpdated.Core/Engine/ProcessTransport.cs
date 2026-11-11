using System.Diagnostics;
using System.Text;
using System.Threading.Channels;

namespace Youpdated.Core.Engine;

/// <summary>The engine as a child process</summary>
public sealed class ProcessTransport : IEngineTransport
{
    // Kept for a crash report
    private const int StderrLines = 40;

    private static readonly UTF8Encoding Utf8 = new(encoderShouldEmitUTF8Identifier: false);

    private readonly Process _process;
    private readonly Channel<string> _lines = Channel.CreateUnbounded<string>(
        new UnboundedChannelOptions { SingleReader = true, SingleWriter = true });
    private readonly Queue<string> _stderr = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly Action<string>? _log;
    private bool _inputClosed;

    private ProcessTransport(Process process, Action<string>? log)
    {
        _process = process;
        _log = log;
        var stdout = PumpStdoutAsync();
        var stderr = PumpStderrAsync();
        Exited = WaitForExitAsync(stdout, stderr);
    }

    public ChannelReader<string> Lines => _lines.Reader;

    public Task<EngineExit> Exited { get; }

    public int ProcessId => _process.Id;

    /// <summary>Start the engine</summary>
    public static ProcessTransport Start(EngineCommand command, Action<string>? log = null)
    {
        var info = new ProcessStartInfo(command.FileName)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = Utf8,
            StandardOutputEncoding = Utf8,
            StandardErrorEncoding = Utf8,
            WorkingDirectory = command.WorkingDirectory,
        };
        foreach (var argument in command.Arguments)
        {
            info.ArgumentList.Add(argument);
        }
        // The app unlocks over the pipe
        info.Environment.Remove("YOUPDATED_PASSPHRASE");
        // dev engine through python: line-buffered, UTF-8 pipes
        info.Environment["PYTHONUNBUFFERED"] = "1";
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["PYTHONUTF8"] = "1";

        var process = new Process { StartInfo = info, EnableRaisingEvents = true };
        try
        {
            if (!process.Start())
            {
                throw new EngineStartException($"The Youpdated engine couldn't start ({command.FileName}).");
            }
        }
        catch (Exception exc) when (exc is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            process.Dispose();
            throw new EngineStartException($"The Youpdated engine couldn't start ({command.FileName}): {exc.Message}", exc);
        }
        return new ProcessTransport(process, log);
    }

    public async Task WriteLineAsync(string line, CancellationToken cancellationToken = default)
    {
        await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (_inputClosed)
            {
                throw new EngineExitedException(null);
            }
            var stdin = _process.StandardInput;
            await stdin.WriteAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
            await stdin.WriteAsync("\n".AsMemory(), cancellationToken).ConfigureAwait(false);
            await stdin.FlushAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (IOException exc)
        {
            // The pipe broke
            throw new EngineExitedException(null, exc);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    public void CloseInput()
    {
        _writeLock.Wait();
        try
        {
            if (!_inputClosed)
            {
                _inputClosed = true;
                _process.StandardInput.Close();
            }
        }
        catch (IOException)
        {
            // already gone
        }
        finally
        {
            _writeLock.Release();
        }
    }

    public void Kill()
    {
        try
        {
            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
            // exited during
        }
    }

    public async ValueTask DisposeAsync()
    {
        CloseInput();
        var exited = await Task.WhenAny(Exited, Task.Delay(TimeSpan.FromSeconds(3))).ConfigureAwait(false);
        if (exited != Exited)
        {
            Kill();
        }
        _process.Dispose();
        _writeLock.Dispose();
    }

    private async Task PumpStdoutAsync()
    {
        try
        {
            var stdout = _process.StandardOutput;
            while (await stdout.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                _lines.Writer.TryWrite(line);
            }
        }
        finally
        {
            _lines.Writer.TryComplete();
        }
    }

    private async Task PumpStderrAsync()
    {
        var stderr = _process.StandardError;
        while (await stderr.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            lock (_stderr)
            {
                _stderr.Enqueue(line);
                while (_stderr.Count > StderrLines)
                {
                    _stderr.Dequeue();
                }
            }
            _log?.Invoke(line);
        }
    }

    private async Task<EngineExit> WaitForExitAsync(Task stdout, Task stderr)
    {
        await _process.WaitForExitAsync().ConfigureAwait(false);
        // What it wrote before exiting is still worth reading
        await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
        string tail;
        lock (_stderr)
        {
            tail = string.Join('\n', _stderr);
        }
        return new EngineExit(_process.ExitCode, tail);
    }
}
