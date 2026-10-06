using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Windows.Security.Credentials.UI;

namespace Javis.WindowsHelloHelper;

internal static class Program
{
    private const int MaxRequestBytes = 8192;

    [STAThread]
    private static int Main()
    {
        Request? request = ReadRequest();
        if (request is null)
        {
            return 2;
        }

        ApplicationConfiguration.Initialize();
        using var window = new VerificationWindow(request);
        Application.Run(window);
        if (window.Response is null)
        {
            return 3;
        }

        Console.Out.Write(JsonSerializer.Serialize(window.Response, JsonContext.Default.Response));
        return 0;
    }

    private static Request? ReadRequest()
    {
        try
        {
            using Stream input = Console.OpenStandardInput();
            var buffer = new byte[MaxRequestBytes + 1];
            int used = 0;
            while (used < buffer.Length)
            {
                int read = input.Read(buffer, used, buffer.Length - used);
                if (read == 0)
                {
                    break;
                }

                used += read;
            }

            if (used == 0 || used > MaxRequestBytes)
            {
                return null;
            }

            int payloadLength = used;
            if (payloadLength > 0 && buffer[payloadLength - 1] == (byte)'\n')
            {
                payloadLength--;
                if (payloadLength > 0 && buffer[payloadLength - 1] == (byte)'\r')
                {
                    payloadLength--;
                }
            }

            if (payloadLength == 0 || buffer.AsSpan(0, payloadLength).ContainsAny((byte)'\r', (byte)'\n'))
            {
                return null;
            }

            using JsonDocument document = JsonDocument.Parse(
                buffer.AsMemory(0, payloadLength),
                new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 4,
            });
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.EnumerateObject().Count() != 3)
            {
                return null;
            }

            if (!TryString(root, "schema_version", out string? schemaVersion) || schemaVersion != "1.0" ||
                !TryString(root, "challenge", out string? challengeText) ||
                !Guid.TryParseExact(challengeText, "D", out Guid challenge) ||
                challenge == Guid.Empty ||
                !TryString(root, "message", out string? message) ||
                message.Length is < 1 or > 1024)
            {
                return null;
            }

            foreach (Rune rune in message.EnumerateRunes())
            {
                if (Rune.GetUnicodeCategory(rune) == System.Globalization.UnicodeCategory.Control)
                {
                    return null;
                }
            }

            return new Request(challenge, message);
        }
        catch (Exception exception) when (exception is IOException or JsonException or DecoderFallbackException)
        {
            return null;
        }
    }

    private static bool TryString(JsonElement root, string name, out string value)
    {
        value = string.Empty;
        if (!root.TryGetProperty(name, out JsonElement element) ||
            element.ValueKind != JsonValueKind.String)
        {
            return false;
        }

        string? candidate = element.GetString();
        if (candidate is null)
        {
            return false;
        }

        value = candidate;
        return true;
    }
}

internal sealed class VerificationWindow : Form
{
    private readonly Request request;

    internal VerificationWindow(Request request)
    {
        this.request = request;
        Text = "JAVIS Windows Hello verification";
        ClientSize = new Size(360, 90);
        ControlBox = false;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        ShowInTaskbar = true;
        StartPosition = FormStartPosition.CenterScreen;
        TopMost = true;

        Controls.Add(new Label
        {
            AutoSize = false,
            Dock = DockStyle.Fill,
            Text = "Windows Hello에서 JAVIS 작업을 확인하세요.",
            TextAlign = ContentAlignment.MiddleCenter,
        });

        Shown += VerifyAsync;
    }

    internal Response? Response { get; private set; }

    private async void VerifyAsync(object? sender, EventArgs eventArgs)
    {
        string result = "FAILED";
        try
        {
            UserConsentVerifierAvailability availability =
                await UserConsentVerifier.CheckAvailabilityAsync();
            if (availability != UserConsentVerifierAvailability.Available)
            {
                result = "UNAVAILABLE";
                return;
            }

            Activate();
            UserConsentVerificationResult verification =
                await UserConsentVerifierInterop.RequestVerificationForWindowAsync(Handle, request.Message);
            result = verification switch
            {
                UserConsentVerificationResult.Verified => "VERIFIED",
                UserConsentVerificationResult.Canceled => "CANCELED",
                _ => "FAILED",
            };
        }
        catch (Exception)
        {
            result = "FAILED";
        }
        finally
        {
            Response = new Response("1.0", request.Challenge, result);
            Close();
        }
    }
}

internal sealed record Request(Guid Challenge, string Message);

internal sealed record Response(string SchemaVersion, Guid Challenge, string Result);

[JsonSerializable(typeof(Response))]
[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower)]
internal sealed partial class JsonContext : JsonSerializerContext;
