using System.Diagnostics;
using System.Numerics;
using System.Runtime.CompilerServices;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Windows.UI.ViewManagement;

namespace Youpdated.App;

internal static class Motion
{
    public static readonly TimeSpan Short = TimeSpan.FromMilliseconds(150);
    public static readonly TimeSpan Medium = TimeSpan.FromMilliseconds(250);
    public static readonly TimeSpan Longest = TimeSpan.FromMilliseconds(350);

    public static readonly TimeSpan NotesDelay = TimeSpan.FromMilliseconds(60);

    private static readonly UISettings Settings = new();
    private static readonly TimeSpan Turn = TimeSpan.FromSeconds(1);

    // Spinning elements, and since when
    private static readonly ConditionalWeakTable<UIElement, Stopwatch> Turning = [];

    public static bool Enabled => Settings.AnimationsEnabled;

    public static Visual VisualOf(UIElement element) => ElementCompositionPreview.GetElementVisual(element);

    public static void AnimateLayoutMoves(UIElement element)
    {
        var visual = VisualOf(element);
        if (!Enabled)
        {
            visual.ImplicitAnimations = null;
            return;
        }
        var compositor = visual.Compositor;
        var move = compositor.CreateVector3KeyFrameAnimation();
        move.Target = "Offset";
        move.InsertExpressionKeyFrame(1f, "this.FinalValue", Settle(compositor));
        move.Duration = Medium;
        var animations = compositor.CreateImplicitAnimationCollection();
        animations["Offset"] = move;
        visual.ImplicitAnimations = animations;
    }

    /// <summary>Fluent's decelerate curve</summary>
    private static CompositionEasingFunction Settle(Compositor compositor) =>
        compositor.CreateCubicBezierEasingFunction(new Vector2(0.1f, 0.9f), new Vector2(0.2f, 1f));

    /// <summary>Shown: fade in after <paramref name="delay"/>; hidden: fade out quickly</summary>
    public static void FadeOnShowAndHide(UIElement element, TimeSpan delay)
    {
        var compositor = VisualOf(element).Compositor;
        ElementCompositionPreview.SetImplicitShowAnimation(element, Fade(compositor, 0f, 1f, Enabled ? Medium : Short, Enabled ? delay : TimeSpan.Zero));
        ElementCompositionPreview.SetImplicitHideAnimation(element, Fade(compositor, 1f, 0f, Short, TimeSpan.Zero));
    }

    /// <summary>Fade an element's opacity to <paramref name="to"/>, after delay</summary>
    public static void FadeTo(UIElement element, float to, TimeSpan delay)
    {
        var visual = VisualOf(element);
        visual.StartAnimation("Opacity", Fade(visual.Compositor, visual.Opacity, to, Medium, delay));
    }

    public static void SlideIn(UIElement element)
    {
        var visual = VisualOf(element);
        var compositor = visual.Compositor;
        visual.StartAnimation("Opacity", Fade(compositor, 0f, 1f, Longest, TimeSpan.Zero));
        if (!Enabled)
        {
            return;
        }
        ElementCompositionPreview.SetIsTranslationEnabled(element, true);
        var slide = compositor.CreateVector3KeyFrameAnimation();
        slide.InsertKeyFrame(0f, new Vector3(0, -16, 0));
        slide.InsertKeyFrame(1f, Vector3.Zero, compositor.CreateCubicBezierEasingFunction(new Vector2(0.1f, 0.9f), new Vector2(0.2f, 1f)));
        slide.Duration = Longest;
        visual.StartAnimation("Translation", slide);
    }

    public static void NumberChanged(UIElement element)
    {
        var visual = VisualOf(element);
        var compositor = visual.Compositor;
        visual.StartAnimation("Opacity", Fade(compositor, 0f, 1f, Medium, TimeSpan.Zero));
        if (!Enabled)
        {
            return;
        }
        ElementCompositionPreview.SetIsTranslationEnabled(element, true);
        var rise = compositor.CreateVector3KeyFrameAnimation();
        rise.InsertKeyFrame(0f, new Vector3(0, 8, 0));
        rise.InsertKeyFrame(1f, Vector3.Zero, compositor.CreateCubicBezierEasingFunction(new Vector2(0.1f, 0.9f), new Vector2(0.2f, 1f)));
        rise.Duration = Medium;
        visual.StartAnimation("Translation", rise);
    }

    public static void Lift(UIElement element, bool lifted)
    {
        element.TranslationTransition = Enabled ? new Vector3Transition { Duration = Short } : null;
        element.Translation = lifted ? new Vector3(0, 0, 12) : Vector3.Zero;
    }

    public static void CastShadow(UIElement element, UIElement receiver)
    {
        var shadow = new ThemeShadow();
        shadow.Receivers.Add(receiver);
        element.Shadow = shadow;
    }

    public static void Spin(FrameworkElement element, bool spinning)
    {
        Turning.TryGetValue(element, out var started);
        Turning.Remove(element);
        if (!spinning && started is null)
        {
            return;
        }
        CenterPivot(element);
        var compositor = CompositionTarget.GetCompositorForCurrentThread();
        var turn = compositor.CreateScalarKeyFrameAnimation();
        turn.Target = "Rotation";
        if (spinning && Enabled)
        {
            turn.InsertKeyFrame(0f, 0f);
            turn.InsertKeyFrame(1f, 360f, compositor.CreateLinearEasingFunction());
            turn.Duration = Turn;
            turn.IterationBehavior = AnimationIterationBehavior.Forever;
            Turning.Add(element, Stopwatch.StartNew());
        }
        else if (started is not null)
        {
            // Upright again at the same speed. Stopping where it is would leave it tilted
            var left = 1 - started.Elapsed.TotalSeconds / Turn.TotalSeconds % 1;
            turn.InsertExpressionKeyFrame(0f, "this.StartingValue");
            turn.InsertKeyFrame(1f, 360f, compositor.CreateLinearEasingFunction());
            turn.Duration = Enabled ? Turn * Math.Max(left, 0.02) : TimeSpan.FromMilliseconds(1);
        }
        else
        {
            return;
        }
        element.StartAnimation(turn);
    }

    /// <summary>Rotate and scale around the middle; call again when the size changes</summary>
    public static void CenterPivot(FrameworkElement element) =>
        element.CenterPoint = new Vector3((float)element.ActualWidth / 2, (float)element.ActualHeight / 2, 0);

    private static ScalarKeyFrameAnimation Fade(Compositor compositor, float from, float to, TimeSpan duration, TimeSpan delay)
    {
        var fade = compositor.CreateScalarKeyFrameAnimation();
        fade.Target = "Opacity";
        fade.InsertKeyFrame(0f, from);
        fade.InsertKeyFrame(1f, to);
        fade.Duration = duration;
        fade.DelayTime = delay;
        fade.DelayBehavior = AnimationDelayBehavior.SetInitialValueBeforeDelay;
        return fade;
    }
}
