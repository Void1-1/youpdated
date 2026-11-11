using System.Threading.Channels;

namespace Youpdated.Core.Engine;

/// <summary>Engine stdio</summary>
public interface IEngineTransport : IAsyncDisposable
{
    ChannelReader<string> Lines { get; }

    Task<EngineExit> Exited { get; }

    Task WriteLineAsync(string line, CancellationToken cancellationToken = default);

    /// <summary>Close stdin, which the engine takes as <c>shutdown</c></summary>
    void CloseInput();

    void Kill();
}

/// <summary>How the engine ended</summary>
public sealed record EngineExit(int? ExitCode, string StderrTail);
