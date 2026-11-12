using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Youpdated.Core.Contract;

/// <summary>Source-generated serializers for every contract type</summary>
[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    RespectNullableAnnotations = true,
    RespectRequiredConstructorParameters = true,
    Converters = [typeof(TimestampConverter)])]
[JsonSerializable(typeof(HelloReply))]
[JsonSerializable(typeof(UnlockReply))]
[JsonSerializable(typeof(StatusReply))]
[JsonSerializable(typeof(CheckReply))]
[JsonSerializable(typeof(InboxListReply))]
[JsonSerializable(typeof(InboxMarkReply))]
[JsonSerializable(typeof(InboxSnoozeReply))]
[JsonSerializable(typeof(InboxDeleteReply))]
[JsonSerializable(typeof(TargetsListReply))]
[JsonSerializable(typeof(PreviewReply))]
[JsonSerializable(typeof(TargetsAddReply))]
[JsonSerializable(typeof(TargetsRemoveReply))]
[JsonSerializable(typeof(ImportPreviewReply))]
[JsonSerializable(typeof(ImportApplyReply))]
[JsonSerializable(typeof(SettingsReply))]
[JsonSerializable(typeof(PriorityReply))]
[JsonSerializable(typeof(ProblemsListReply))]
[JsonSerializable(typeof(DigestReply))]
[JsonSerializable(typeof(SnapshotReply))]
[JsonSerializable(typeof(ConfigInitReply))]
[JsonSerializable(typeof(EmptyReply))]
[JsonSerializable(typeof(IconReply))]
[JsonSerializable(typeof(ProgressEvent))]
[JsonSerializable(typeof(IconEvent))]
[JsonSerializable(typeof(EngineError))]
[JsonSerializable(typeof(TargetKey))]
[JsonSerializable(typeof(List<TargetKey>))]
[JsonSerializable(typeof(Priority))]
public sealed partial class ContractJson : JsonSerializerContext;

/// <summary>Contract timestamps: UTC, whole seconds, <c>2026-10-29T08:00:06Z</c></summary>
public sealed class TimestampConverter : JsonConverter<DateTimeOffset>
{
    public const string Format = "yyyy-MM-dd'T'HH:mm:ss'Z'";

    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        var text = reader.GetString();
        if (text is not null && DateTimeOffset.TryParseExact(
                text, Format, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var exact))
        {
            return exact;
        }
        // Lenient for anything else ISO 8601, so a stray offset or fraction isn't fatal
        if (text is not null && DateTimeOffset.TryParse(
                text, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var loose))
        {
            return loose.ToUniversalTime();
        }
        throw new JsonException($"`{text}` is not a contract timestamp");
    }

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) =>
        writer.WriteStringValue(value.ToUniversalTime().ToString(Format, CultureInfo.InvariantCulture));

    public static string ToWire(DateTimeOffset value) =>
        value.ToUniversalTime().ToString(Format, CultureInfo.InvariantCulture);
}

/// <summary>Fields <c>settings.set</c> changes</summary>
public sealed class SettingsPatch
{
    private readonly JsonObject _fields = new();

    public bool IsEmpty => _fields.Count == 0;

    public SettingsPatch CheckIntervalHours(int hours)
    {
        _fields["check_interval_hours"] = hours;
        return this;
    }

    public SettingsPatch Digest(TimeOnly? time)
    {
        _fields["digest_time"] = time?.ToString("HH:mm", CultureInfo.InvariantCulture);
        return this;
    }

    public SettingsPatch OsNotifications(bool on)
    {
        _fields["os_notifications"] = on;
        return this;
    }

    public SettingsPatch SelfWatchDeclined(bool declined)
    {
        _fields["self_watch_declined"] = declined;
        return this;
    }

    public SettingsPatch CliNoticeShown(bool shown)
    {
        _fields["cli_notice_shown"] = shown;
        return this;
    }

    public SettingsPatch ShowIcons(bool on)
    {
        _fields["show_icons"] = on;
        return this;
    }

    public JsonObject ToJson() => (JsonObject)_fields.DeepClone();
}
