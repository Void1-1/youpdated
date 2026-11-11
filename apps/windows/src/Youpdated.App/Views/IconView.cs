using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage.Streams;
using Youpdated.Core.Contract;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Views;

/// <summary>A target's icon. monogram with a small source badge, or a real icon when Show icons is on and the engine has some</summary>
public sealed class IconView : Grid
{
    public static readonly DependencyProperty TargetProperty = DependencyProperty.Register(
        nameof(Target), typeof(TargetKey), typeof(IconView), new PropertyMetadata(null, (d, _) => ((IconView)d).Refresh()));

    public static readonly DependencyProperty DisplayProperty = DependencyProperty.Register(
        nameof(Display), typeof(string), typeof(IconView), new PropertyMetadata("", (d, _) => ((IconView)d).Refresh()));

    public static readonly DependencyProperty SizeProperty = DependencyProperty.Register(
        nameof(Size), typeof(double), typeof(IconView), new PropertyMetadata(40.0, (d, _) => ((IconView)d).Layout()));

    private readonly Border _tile = new();
    private readonly TextBlock _initials = new()
    {
        HorizontalAlignment = HorizontalAlignment.Center,
        VerticalAlignment = VerticalAlignment.Center,
        Foreground = new SolidColorBrush(Colors.White),
        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
    };
    private readonly Image _image = new() { Stretch = Stretch.UniformToFill, Visibility = Visibility.Collapsed };
    private readonly Border _badge = new()
    {
        HorizontalAlignment = HorizontalAlignment.Right,
        VerticalAlignment = VerticalAlignment.Bottom,
        BorderThickness = new Thickness(1),
    };
    private readonly FontIcon _badgeGlyph = new();
    private int _loads;

    public IconView()
    {
        _tile.Child = new Grid { Children = { _initials, _image } };
        _badge.Child = _badgeGlyph;
        Children.Add(_tile);
        Children.Add(_badge);
        Themed.Brush(_badge, "SolidBackgroundFillColorBaseBrush", (badge, brush) => badge.Background = brush);
        Themed.Brush(_badge, "CardStrokeColorDefaultBrush", (badge, brush) => badge.BorderBrush = brush);
        Themed.Brush(_badgeGlyph, "TextFillColorSecondaryBrush", (glyph, brush) => glyph.Foreground = brush);
        Layout();
        Loaded += (_, _) =>
        {
            if (AppController.Current?.App.Icons is { } icons)
            {
                icons.Changed += Refresh;
                icons.TargetChanged += OnTargetChanged;
            }
            Refresh();
        };
        Unloaded += (_, _) =>
        {
            if (AppController.Current?.App.Icons is { } icons)
            {
                icons.Changed -= Refresh;
                icons.TargetChanged -= OnTargetChanged;
            }
        };
    }

    public TargetKey? Target
    {
        get => (TargetKey?)GetValue(TargetProperty);
        set => SetValue(TargetProperty, value);
    }

    public string Display
    {
        get => (string)GetValue(DisplayProperty);
        set => SetValue(DisplayProperty, value);
    }

    public double Size
    {
        get => (double)GetValue(SizeProperty);
        set => SetValue(SizeProperty, value);
    }

    private void Layout()
    {
        Width = Height = Size;
        _tile.CornerRadius = new CornerRadius(Size / 5);
        _initials.FontSize = Size * 0.4;
        var badge = Math.Round(Size * 0.42);
        _badge.Width = _badge.Height = badge;
        _badge.CornerRadius = new CornerRadius(badge / 2);
        _badge.Margin = new Thickness(0, 0, -badge / 4, -badge / 4);
        _badgeGlyph.FontSize = badge * 0.55;
        _badge.Visibility = Size >= 32 ? Visibility.Visible : Visibility.Collapsed;
    }

    /// <summary>Engine's icon event: this target's icon arrived</summary>
    private void OnTargetChanged(TargetKey target)
    {
        if (target == Target)
        {
            DispatcherQueue.TryEnqueue(Refresh);
        }
    }

    private async void Refresh()
    {
        // The color comes from the target, the initials from its name
        var monogram = Monogram.For(Target ?? new TargetKey("", Display ?? ""), Display ?? "");
        var (r, g, b) = monogram.Color;
        var (tr, tg, tb) = monogram.Lighter;
        _tile.Background = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(0, 1),
            GradientStops =
            {
                new GradientStop { Color = Windows.UI.Color.FromArgb(255, tr, tg, tb), Offset = 0 },
                new GradientStop { Color = Windows.UI.Color.FromArgb(255, r, g, b), Offset = 1 },
            },
        };
        _initials.Text = monogram.Initials;
        _initials.Visibility = Visibility.Visible;
        _image.Visibility = Visibility.Collapsed;
        _image.Source = null;
        if (Target is not { } target)
        {
            return;
        }
        _badgeGlyph.Glyph = Bind.SourceGlyph(target.Source);
        AutomationProperties.SetName(this, Display);

        if (AppController.Current?.App.Icons is not { Enabled: true } icons)
        {
            return;
        }
        var load = ++_loads;
        var bytes = await icons.GetAsync(target);
        if (load != _loads || bytes is null)
        {
            return;
        }
        try
        {
            using var stream = new InMemoryRandomAccessStream();
            await stream.WriteAsync(bytes.AsBuffer());
            stream.Seek(0);
            var bitmap = new BitmapImage();
            await bitmap.SetSourceAsync(stream);
            if (load == _loads)
            {
                _image.Source = bitmap;
                _image.Visibility = Visibility.Visible;
                _initials.Visibility = Visibility.Collapsed;
            }
        }
        catch (Exception)
        {
            // Not an image this platform decodes
        }
    }
}
