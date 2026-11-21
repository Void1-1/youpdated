namespace Youpdated.Core.ViewModels;

public static class CodeSpans
{
    /// <summary>Text in order, each part marked as code or not. An unpaired backtick stays text</summary>
    public static IReadOnlyList<(string Text, bool Code)> Split(string message)
    {
        var parts = new List<(string, bool)>();
        var at = 0;
        while (at < message.Length)
        {
            var open = message.IndexOf('`', at);
            var close = open < 0 ? -1 : message.IndexOf('`', open + 1);
            if (close < 0)
            {
                parts.Add((message[at..], false));
                break;
            }
            if (open > at)
            {
                parts.Add((message[at..open], false));
            }
            if (close > open + 1)
            {
                parts.Add((message[(open + 1)..close], true));
            }
            at = close + 1;
        }
        return parts;
    }
}
