using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace Youpdated.App.Views;

/// <summary>Children in centered rows, wrapping to the next row when one is full</summary>
public sealed class WrapPanel : Panel
{
    private const double Spacing = 8;

    protected override Size MeasureOverride(Size availableSize)
    {
        foreach (var child in Children)
        {
            child.Measure(new Size(availableSize.Width, double.PositiveInfinity));
        }
        var rows = Wrap(availableSize.Width);
        return new Size(
            rows.Count == 0 ? 0 : rows.Max(row => row.Width),
            rows.Sum(row => row.Height) + Spacing * Math.Max(rows.Count - 1, 0));
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var y = 0.0;
        foreach (var row in Wrap(finalSize.Width))
        {
            var x = Math.Max((finalSize.Width - row.Width) / 2, 0);
            foreach (var child in row.Children)
            {
                child.Arrange(new Rect(x, y, child.DesiredSize.Width, row.Height));
                x += child.DesiredSize.Width + Spacing;
            }
            y += row.Height + Spacing;
        }
        return finalSize;
    }

    private List<Row> Wrap(double width)
    {
        var rows = new List<Row>();
        Row? row = null;
        foreach (var child in Children)
        {
            if (child.Visibility != Visibility.Visible)
            {
                continue;
            }
            var size = child.DesiredSize;
            if (row is null || row.Width + Spacing + size.Width > width)
            {
                rows.Add(row = new Row());
            }
            row.Width += (row.Children.Count > 0 ? Spacing : 0) + size.Width;
            row.Height = Math.Max(row.Height, size.Height);
            row.Children.Add(child);
        }
        return rows;
    }

    private sealed class Row
    {
        public List<UIElement> Children { get; } = [];

        public double Width { get; set; }

        public double Height { get; set; }
    }
}
