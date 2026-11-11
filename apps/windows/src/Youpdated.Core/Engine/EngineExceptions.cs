using Youpdated.Core.Contract;

namespace Youpdated.Core.Engine;

/// <summary>An op answered with <c>ok: false</c></summary>
public sealed class EngineException(EngineError error) : Exception(error.Message)
{
    public string Code => error.Code;

    /// <summary>With <c>ambiguous</c>: the sources the value could belong to</summary>
    public IReadOnlyList<string> Choices => error.Choices ?? [];

    public bool Is(string code) => Code == code;
}

/// <summary>The engine couldn't be started</summary>
public sealed class EngineStartException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>The engine went away with requests unanswered</summary>
public sealed class EngineExitedException(EngineExit? exit, Exception? inner = null)
    : Exception(Describe(exit), inner)
{
    public EngineExit? Exit => exit;

    private static string Describe(EngineExit? exit) => exit switch
    {
        { ExitCode: { } code } => $"The Youpdated engine stopped (exit code {code}).",
        _ => "The Youpdated engine stopped.",
    };
}

/// <summary>The engine sent something not in the contract</summary>
public sealed class EngineProtocolException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Different engine contract version</summary>
public sealed class EngineIncompatibleException(int contract)
    : Exception($"This Youpdated engine uses contract {contract}, and this app needs {EngineConnection.ContractVersion}. Reinstall or update Youpdated.")
{
    public int Contract => contract;
}

/// <summary>There's no engine running</summary>
public sealed class EngineUnavailableException(string message) : Exception(message);
