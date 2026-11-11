using System.Text;

namespace Youpdated.Core.Engine;

/// <summary>What to run for <c>youpdated api serve</c></summary>
public sealed record EngineCommand(string FileName, IReadOnlyList<string> Arguments, string WorkingDirectory)
{
    /// <summary>A command line to run instead of the bundled engine, with <c>api serve</c> appended</summary>
    public const string EngineVariable = "YOUPDATED_ENGINE";

    /// <summary>Passed as <c>--config</c></summary>
    public const string ConfigVariable = "YOUPDATED_ENGINE_CONFIG";

    /// <summary>Passed as <c>--state</c></summary>
    public const string StateVariable = "YOUPDATED_ENGINE_STATE";

    public const string BundledRelativePath = @"youpdated-engine\youpdated-engine.exe";

    /// <summary>The bundled engine for the app (<c>youpdated-engine\youpdated-engine.exe</c>), or override in <see cref="EngineVariable"/></summary>
    public static EngineCommand Resolve(string appDirectory, Func<string, string?> environment) =>
        Resolve(appDirectory, environment, ["api", "serve"]);

    /// <summary>The engine's CLI running <paramref name="command"/></summary>
    public static EngineCommand Resolve(string appDirectory, Func<string, string?> environment, IReadOnlyList<string> command)
    {
        var extra = new List<string>();
        if (environment(ConfigVariable) is { Length: > 0 } config)
        {
            extra.AddRange(["--config", config]);
        }
        if (environment(StateVariable) is { Length: > 0 } state)
        {
            extra.AddRange(["--state", state]);
        }

        if (environment(EngineVariable) is { Length: > 0 } custom)
        {
            var words = SplitCommandLine(custom);
            if (words.Count == 0)
            {
                throw new ArgumentException($"{EngineVariable} is empty");
            }
            return new EngineCommand(
                words[0], [.. words.Skip(1), .. command, .. extra], Directory.GetCurrentDirectory());
        }

        var bundled = Path.Combine(appDirectory, BundledRelativePath);
        return new EngineCommand(bundled, [.. command, .. extra], Path.GetDirectoryName(bundled)!);
    }

    /// <summary>
    /// Split like CommandLineToArgvW: whitespace separates, double quotes group, backslashes are literal unless before a quote
    /// </summary>
    public static List<string> SplitCommandLine(string commandLine)
    {
        var words = new List<string>();
        var word = new StringBuilder();
        var inWord = false;
        var quoted = false;
        for (var i = 0; i < commandLine.Length; i++)
        {
            var c = commandLine[i];
            if (c == '\\')
            {
                var run = 0;
                while (i < commandLine.Length && commandLine[i] == '\\')
                {
                    run++;
                    i++;
                }
                if (i < commandLine.Length && commandLine[i] == '"')
                {
                    word.Append('\\', run / 2);
                    if (run % 2 == 1)
                    {
                        word.Append('"');
                    }
                    else
                    {
                        quoted = !quoted;
                    }
                }
                else
                {
                    word.Append('\\', run);
                    i--;
                }
                inWord = true;
            }
            else if (c == '"')
            {
                quoted = !quoted;
                inWord = true;
            }
            else if (char.IsWhiteSpace(c) && !quoted)
            {
                if (inWord)
                {
                    words.Add(word.ToString());
                    word.Clear();
                    inWord = false;
                }
            }
            else
            {
                word.Append(c);
                inWord = true;
            }
        }
        if (inWord)
        {
            words.Add(word.ToString());
        }
        return words;
    }
}
