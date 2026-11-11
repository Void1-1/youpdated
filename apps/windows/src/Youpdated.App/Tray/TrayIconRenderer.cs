using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using Microsoft.Win32;
using Youpdated.App.Platform;
using Youpdated.Core.ViewModels;

namespace Youpdated.App.Tray;

internal static class TrayIconRenderer
{
    public static Icon Render(TrayState state, int unread, Color accent, int size = 32)
    {
        var light = TaskbarIsLight();
        var ink = light ? Color.FromArgb(28, 28, 28) : Color.White;
        using var bitmap = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
            g.Clear(Color.Transparent);
            var s = size / 32f;

            // Checking dims the bell
            DrawBell(g, Color.FromArgb(state == TrayState.Checking ? 150 : 255, ink), s);

            switch (state)
            {
                case TrayState.Checking:
                    DrawSync(g, ink, s);
                    break;
                case TrayState.Locked:
                    DrawLock(g, ink, s);
                    break;
                case TrayState.Paused:
                    DrawPause(g, ink, s);
                    break;
                case TrayState.Problems:
                    DrawWarning(g, light, s);
                    break;
            }
            if (unread > 0 && state != TrayState.Locked)
            {
                DrawCount(g, unread, accent, s);
            }
        }
        var handle = bitmap.GetHicon();
        try
        {
            return (Icon)Icon.FromHandle(handle).Clone();
        }
        finally
        {
            Native.DestroyIcon(handle);
        }
    }

    /// <summary>The taskbar follows "Choose your default Windows mode", not the app mode</summary>
    public static bool TaskbarIsLight()
    {
        using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
        return key?.GetValue("SystemUsesLightTheme") is int value && value == 1;
    }

    /// <summary>The bell (apps/shared/app-icon.svg)</summary>
    private static void DrawBell(Graphics g, Color color, float s)
    {
        using var body = new GraphicsPath();
        body.AddBezier(512, 268, 400, 268, 350, 350, 350, 452);
        body.AddLine(350, 452, 350, 556);
        body.AddBezier(350, 556, 350, 598, 330, 620, 304, 638);
        body.AddBezier(304, 638, 284, 652, 292, 676, 318, 676);
        body.AddLine(318, 676, 706, 676);
        body.AddBezier(706, 676, 732, 676, 740, 652, 720, 638);
        body.AddBezier(720, 638, 694, 620, 674, 598, 674, 556);
        body.AddLine(674, 556, 674, 452);
        body.AddBezier(674, 452, 674, 350, 624, 268, 512, 268);
        body.CloseFigure();
        body.StartFigure();
        body.AddLine(454, 700, 570, 700);
        body.AddBezier(570, 700, 570, 734, 544, 760, 512, 760);
        body.AddBezier(512, 760, 480, 760, 454, 734, 454, 700);
        body.CloseFigure();
        using var knob = new GraphicsPath();
        knob.AddEllipse(482, 232, 60, 60);

        const float Fit = 28f / 528;
        using var place = new Matrix(Fit * s, 0, 0, Fit * s, (16 - 512 * Fit) * s, (2 - 232 * Fit) * s);
        body.Transform(place);
        knob.Transform(place);
        using var brush = new SolidBrush(color);
        g.FillPath(brush, body);
        g.FillPath(brush, knob);
    }

    private static RectangleF Corner(float s) => new(18 * s, 18 * s, 14 * s, 14 * s);

    private static void Knockout(Graphics g, RectangleF area)
    {
        var previous = g.CompositingMode;
        g.CompositingMode = CompositingMode.SourceCopy;
        using var clear = new SolidBrush(Color.Transparent);
        g.FillEllipse(clear, area);
        g.CompositingMode = previous;
    }

    private static void DrawSync(Graphics g, Color ink, float s)
    {
        var area = Corner(s);
        Knockout(g, area);
        using var pen = new Pen(ink, 2.2f * s) { StartCap = LineCap.Round, EndCap = LineCap.ArrowAnchor };
        var inner = RectangleF.Inflate(area, -3 * s, -3 * s);
        g.DrawArc(pen, inner, 200, 250);
    }

    private static void DrawLock(Graphics g, Color ink, float s)
    {
        var area = Corner(s);
        Knockout(g, area);
        using var brush = new SolidBrush(ink);
        using var pen = new Pen(ink, 2f * s);
        g.FillRectangle(brush, 20 * s, 25 * s, 10 * s, 6.5f * s);
        g.DrawArc(pen, 21.8f * s, 20 * s, 6.4f * s, 8 * s, 180, 180);
    }

    private static void DrawPause(Graphics g, Color ink, float s)
    {
        var area = Corner(s);
        Knockout(g, area);
        using var brush = new SolidBrush(ink);
        g.FillRectangle(brush, 20.5f * s, 20.5f * s, 3.5f * s, 9 * s);
        g.FillRectangle(brush, 26 * s, 20.5f * s, 3.5f * s, 9 * s);
    }

    private static void DrawWarning(Graphics g, bool lightTaskbar, float s)
    {
        var area = Corner(s);
        Knockout(g, area);
        using var fill = new SolidBrush(lightTaskbar ? Color.FromArgb(157, 93, 0) : Color.FromArgb(252, 225, 0));
        using var mark = new SolidBrush(lightTaskbar ? Color.White : Color.Black);
        PointF P(float x, float y) => new(x * s, y * s);
        g.FillPolygon(fill, new[] { P(25, 18.5f), P(32, 31.5f), P(18, 31.5f) });
        g.FillRectangle(mark, 24.1f * s, 22.5f * s, 1.8f * s, 5 * s);
        g.FillEllipse(mark, 24.1f * s, 28.3f * s, 1.8f * s, 1.8f * s);
    }

    private static void DrawCount(Graphics g, int unread, Color accent, float s)
    {
        var text = unread > 9 ? "9+" : unread.ToString(System.Globalization.CultureInfo.CurrentCulture);
        var width = (text.Length > 1 ? 17 : 13) * s;
        var area = new RectangleF(32 * s - width, 0, width, 13 * s);
        Knockout(g, RectangleF.Inflate(area, 1.5f * s, 1.5f * s));
        using var badge = new SolidBrush(accent);
        using var path = Pill(area);
        g.FillPath(badge, path);
        using var font = new Font("Segoe UI", 8.5f * s, FontStyle.Bold, GraphicsUnit.Pixel);
        using var ink = new SolidBrush(Readable(accent));
        using var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
        g.DrawString(text, font, ink, area, format);
    }

    private static GraphicsPath Pill(RectangleF area)
    {
        var path = new GraphicsPath();
        var d = area.Height;
        path.AddArc(area.Left, area.Top, d, d, 90, 180);
        path.AddArc(area.Right - d, area.Top, d, d, 270, 180);
        path.CloseFigure();
        return path;
    }
    
    private static Color Readable(Color background) =>
        (background.R * 299 + background.G * 587 + background.B * 114) / 1000 > 150 ? Color.Black : Color.White;
}
