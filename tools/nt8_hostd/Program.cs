using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Server.Kestrel.Core;

const string BacktestOnlyRequired = "BACKTEST_ONLY_REQUIRED";
const string StateFileName = "state.json";
const string DoneFileName = "done.json";
const string JobsDirName = "jobs";
const string Tier2Dir = "tier2";
const string LogsDir = "logs";
const string RunnerLogFile = "runner.log";
const string ArtifactDirFileName = "artifact_dir.txt";
const string ConfirmSpecFileName = "confirm_spec.json";

var repoRoot = Environment.GetEnvironmentVariable("OPENCLAW_REPO_ROOT") ?? Directory.GetCurrentDirectory();
var stateDir = Path.Combine(repoRoot, "artifacts", "nt8_hostd");
var statePath = Path.Combine(stateDir, StateFileName);

var defaultPort = 8878;
var portEnv = Environment.GetEnvironmentVariable("NT8_HOSTD_PORT");
if (!string.IsNullOrEmpty(portEnv) && int.TryParse(portEnv, out var p) && p > 0)
    defaultPort = p;

var builder = WebApplication.CreateBuilder(args);
builder.Host.UseWindowsService(); // When run under SCM, report correct lifetime and handle shutdown
builder.WebHost.ConfigureKestrel(opt =>
{
    opt.Listen(System.Net.IPAddress.Loopback, defaultPort); // 127.0.0.1 only
});
builder.Services.Configure<HostOptions>(o => o.ShutdownTimeout = TimeSpan.FromSeconds(30));

var app = builder.Build();

// --- Auth: bearer token from OPENCLAW_NT8_HOSTD_TOKEN ---
var expectedToken = Environment.GetEnvironmentVariable("OPENCLAW_NT8_HOSTD_TOKEN");
var tokenFingerprint = string.IsNullOrEmpty(expectedToken)
    ? ""
    : TokenFingerprint(expectedToken);

static string TokenFingerprint(string token)
{
    var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(token));
    return Convert.ToHexString(bytes).AsSpan(0, 6).ToString();
}

bool Authorize(HttpRequest request)
{
    if (string.IsNullOrEmpty(expectedToken))
        return false;
    var auth = request.Headers.Authorization.FirstOrDefault();
    if (string.IsNullOrEmpty(auth) || !auth.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
        return false;
    var token = auth["Bearer ".Length..].Trim();
    return CryptographicOperations.FixedTimeEquals(
        Encoding.UTF8.GetBytes(token),
        Encoding.UTF8.GetBytes(expectedToken));
}

// Resolve Python executable: try python, python3, py (align with ops/windows/run_tier2_confirm.ps1)
static string GetPythonExecutable(string repoRoot)
{
    foreach (var name in new[] { "python", "python3", "py" })
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = name,
                ArgumentList = { "-c", "import sys" },
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            };
            foreach (var key in new[] { "OPENCLAW_REPO_ROOT", "PATH" })
            {
                var v = Environment.GetEnvironmentVariable(key);
                if (!string.IsNullOrEmpty(v)) psi.Environment[key] = v;
            }
            using var p = Process.Start(psi);
            if (p != null && p.WaitForExit(TimeSpan.FromSeconds(5)) && p.ExitCode == 0)
                return name;
        }
        catch { /* try next */ }
    }
    return "python"; // fallback for error message
}

// --- Single-flight: one active run at a time (lock around start) ---
var runStartLock = new SemaphoreSlim(1, 1);

// --- Single-flight state ---
void EnsureStateDir()
{
    Directory.CreateDirectory(stateDir);
}

string? GetActiveRunId()
{
    try
    {
        if (!File.Exists(statePath))
            return null;
        var json = File.ReadAllText(statePath);
        var doc = JsonDocument.Parse(json);
        var runId = doc.RootElement.TryGetProperty("active_run_id", out var id) ? id.GetString() : null;
        if (string.IsNullOrEmpty(runId))
            return null;
        var artifactDir = doc.RootElement.TryGetProperty("artifact_dir", out var ad) ? ad.GetString() : null;
        if (string.IsNullOrEmpty(artifactDir) || !Directory.Exists(artifactDir))
            return null;
        var donePath = Path.Combine(artifactDir, Tier2Dir, DoneFileName);
        if (File.Exists(donePath))
            return null; // run finished, no longer active
        return runId;
    }
    catch { return null; }
}

void WriteState(string? runId, string? artifactDir, int? processId, string? addRunMapping = null)
{
    EnsureStateDir();
    Dictionary<string, object?>? runs = null;
    try
    {
        if (File.Exists(statePath))
        {
            var existing = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(File.ReadAllText(statePath));
            if (existing != null && existing.TryGetValue("runs", out var r) && r.ValueKind == JsonValueKind.Object)
            {
                runs = new Dictionary<string, object?>();
                foreach (var p in r.EnumerateObject())
                    runs[p.Name] = p.Value.GetString();
            }
        }
    }
    catch { /* ignore */ }
    runs ??= new Dictionary<string, object?>();
    if (!string.IsNullOrEmpty(addRunMapping) && !string.IsNullOrEmpty(artifactDir))
        runs[addRunMapping] = artifactDir;
    var obj = new Dictionary<string, object?>
    {
        ["active_run_id"] = runId,
        ["artifact_dir"] = artifactDir,
        ["process_id"] = processId,
        ["updated_at"] = DateTime.UtcNow.ToString("O"),
        ["runs"] = runs,
    };
    File.WriteAllText(statePath, JsonSerializer.Serialize(obj, new JsonSerializerOptions { WriteIndented = false }));
}

// --- BACKTEST_ONLY gate (request + env) ---
bool IsBacktestOnlyAllowed()
{
    var env = Environment.GetEnvironmentVariable("BACKTEST_ONLY")?.Trim().Equals("true", StringComparison.OrdinalIgnoreCase) ?? false;
    return env;
}

// --- Helpers ---
static string NewRunId()
{
    var t = DateTime.UtcNow.ToString("yyyyMMdd-HHmmss");
    var h = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Guid.NewGuid().ToString()))).AsSpan(0, 8).ToString();
    return $"{t}-{h}";
}

// --- Routes: only /v1/orb/backtest/confirm_nt8/run, status, collect ---

app.Use(async (ctx, next) =>
{
    var path = ctx.Request.Path.Value ?? "";
    if (!path.StartsWith("/v1/orb/backtest/confirm_nt8/", StringComparison.OrdinalIgnoreCase))
    {
        ctx.Response.StatusCode = 404;
        return;
    }
    if (!Authorize(ctx.Request))
    {
        ctx.Response.StatusCode = 403;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "Unauthorized", code = "TOKEN_REQUIRED" }));
        return;
    }
    await next(ctx);
});

// POST /v1/orb/backtest/confirm_nt8/run
app.MapPost("/v1/orb/backtest/confirm_nt8/run", async (HttpContext ctx) =>
{
    await runStartLock.WaitAsync(ctx.RequestAborted);
    try
    {
    if (!IsBacktestOnlyAllowed())
    {
        ctx.Response.StatusCode = 403;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "BACKTEST_ONLY required", code = BacktestOnlyRequired }));
        return;
    }

    JsonElement body;
    try
    {
        body = await JsonSerializer.DeserializeAsync<JsonElement>(ctx.Request.Body);
    }
    catch
    {
        ctx.Response.StatusCode = 400;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "Invalid JSON" }));
        return;
    }

    bool backtestOnlyInBody = false;
    if (body.TryGetProperty("BACKTEST_ONLY", out var bo))
        backtestOnlyInBody = bo.ValueKind == JsonValueKind.True;
    if (!backtestOnlyInBody && body.TryGetProperty("topk_inline", out var topkInline))
    {
        try
        {
            var topkRaw = topkInline.ValueKind == JsonValueKind.String ? topkInline.GetString() : topkInline.GetRawText();
            if (!string.IsNullOrEmpty(topkRaw))
            {
                using var doc = JsonDocument.Parse(topkRaw);
                backtestOnlyInBody = doc.RootElement.TryGetProperty("BACKTEST_ONLY", out var bk) && bk.ValueKind == JsonValueKind.True;
            }
        }
        catch { /* ignore */ }
    }
    if (!backtestOnlyInBody)
    {
        ctx.Response.StatusCode = 403;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "BACKTEST_ONLY must be true in request/topk", code = BacktestOnlyRequired }));
        return;
    }

    var activeRunId = GetActiveRunId();
    if (!string.IsNullOrEmpty(activeRunId))
    {
        ctx.Response.StatusCode = 409;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "Single-flight: another run active", active_run_id = activeRunId }));
        return;
    }

    var outputRoot = body.TryGetProperty("output_root", out var or) ? or.GetString() : null;
    var mode = body.TryGetProperty("mode", out var m) ? m.GetString() : "strategy_analyzer";
    if (string.IsNullOrWhiteSpace(outputRoot))
    {
        ctx.Response.StatusCode = 400;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "output_root required" }));
        return;
    }

    var runId = NewRunId();
    // Canonical layout: <output_root>/<run_id>/tier2_nt8/tier2/{results.csv, summary.json, raw_exports/, done.json, logs/}
    var runRoot = Path.Combine(Path.GetFullPath(outputRoot.Trim()), runId);
    var artifactDirFinal = Path.Combine(runRoot, "tier2_nt8");
    Directory.CreateDirectory(artifactDirFinal);

    string topkPath;
    if (body.TryGetProperty("topk_path", out var tp) && !string.IsNullOrWhiteSpace(tp.GetString()))
    {
        topkPath = tp.GetString()!;
        if (!Path.IsPathRooted(topkPath))
            topkPath = Path.Combine(repoRoot, topkPath);
    }
    else if (body.TryGetProperty("topk_inline", out var ti))
    {
        topkPath = Path.Combine(runRoot, "topk.json");
        var topkContent = ti.ValueKind == JsonValueKind.String ? (ti.GetString() ?? "{}") : ti.GetRawText();
        File.WriteAllText(topkPath, topkContent);
    }
    else
    {
        ctx.Response.StatusCode = 400;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "topk_path or topk_inline required" }));
        return;
    }

    // Validate topk.json before starting (fail fast)
    try
    {
        var pythonExe = GetPythonExecutable(repoRoot);
        var validatePsi = new ProcessStartInfo
        {
            FileName = pythonExe,
            ArgumentList = { "-m", "tools.validate_topk", topkPath },
            WorkingDirectory = repoRoot,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        foreach (var key in new[] { "OPENCLAW_REPO_ROOT", "PATH" })
        {
            var v = Environment.GetEnvironmentVariable(key);
            if (!string.IsNullOrEmpty(v)) validatePsi.Environment[key] = v;
        }
        using var validateProc = Process.Start(validatePsi);
        if (validateProc != null)
        {
            validateProc.WaitForExit(TimeSpan.FromSeconds(15));
            if (validateProc.ExitCode != 0)
            {
                var err = validateProc.StandardError.ReadToEnd();
                ctx.Response.StatusCode = 400;
                ctx.Response.ContentType = "application/json";
                await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "topk validation failed", detail = err.Trim(), code = "TOPK_VALIDATION_FAILED" }));
                return;
            }
        }
    }
    catch (Exception ex)
    {
        ctx.Response.StatusCode = 500;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "validate_topk failed", detail = ex.Message }));
        return;
    }

    var tier2Logs = Path.Combine(artifactDirFinal, Tier2Dir, LogsDir);
    Directory.CreateDirectory(tier2Logs);

    // Jobs folder queue: hostd writes artifact_dir so harness/AddOn knows where to write tier2/
    var jobsDir = Path.Combine(stateDir, JobsDirName, runId);
    Directory.CreateDirectory(jobsDir);
    File.WriteAllText(Path.Combine(jobsDir, ArtifactDirFileName), artifactDirFinal);

    var ps1Path = Path.Combine(repoRoot, "ops", "windows", "run_tier2_confirm.ps1");
    if (!File.Exists(ps1Path))
    {
        ctx.Response.StatusCode = 500;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "run_tier2_confirm.ps1 not found", path = ps1Path }));
        return;
    }

    var psi = new ProcessStartInfo
    {
        FileName = "pwsh",
        ArgumentList = { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1Path, "-TopkPath", topkPath, "-OutputDir", artifactDirFinal, "-Mode", mode ?? "strategy_analyzer" },
        WorkingDirectory = repoRoot,
        UseShellExecute = false,
        RedirectStandardOutput = true,
        RedirectStandardError = true,
        CreateNoWindow = true,
    };
    psi.Environment["BACKTEST_ONLY"] = "true";
    psi.Environment["OPENCLAW_TIER2_RUN_ID"] = runId;
    psi.Environment["OPENCLAW_TIER2_JOB_DIR"] = jobsDir;
    foreach (var key in new[] { "OPENCLAW_REPO_ROOT", "PATH" })
    {
        var v = Environment.GetEnvironmentVariable(key);
        if (!string.IsNullOrEmpty(v))
            psi.Environment[key] = v;
    }

    Process? proc = null;
    try
    {
        proc = Process.Start(psi);
    }
    catch (Exception ex)
    {
        ctx.Response.StatusCode = 500;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "Failed to start runner", detail = ex.Message }));
        return;
    }

    if (proc == null)
    {
        ctx.Response.StatusCode = 500;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "Process start returned null" }));
        return;
    }

    WriteState(runId, artifactDirFinal, proc.Id, addRunMapping: runId);

    _ = Task.Run(async () =>
    {
        try
        {
            var stdoutTask = proc.StandardOutput.ReadToEndAsync();
            var stderrTask = proc.StandardError.ReadToEndAsync();
            await Task.WhenAll(stdoutTask, stderrTask);
            var stdout = await stdoutTask;
            var stderr = await stderrTask;
            var logDir = Path.Combine(artifactDirFinal, Tier2Dir, LogsDir);
            Directory.CreateDirectory(logDir);
            await File.WriteAllTextAsync(Path.Combine(logDir, RunnerLogFile),
                $"[stdout]\n{stdout}\n[stderr]\n{stderr}");
        }
        catch { /* best effort */ }
        finally
        {
            proc.WaitForExit();
            var donePath = Path.Combine(artifactDirFinal, Tier2Dir, DoneFileName);
            if (!File.Exists(donePath))
            {
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(donePath)!);
                    var stub = new Dictionary<string, object>
                    {
                        ["done"] = true,
                        ["run_id"] = runId,
                        ["status"] = "RUNNER_EXITED",
                        ["exit_code"] = proc.ExitCode,
                        ["finished_at"] = DateTime.UtcNow.ToString("O"),
                    };
                    File.WriteAllText(donePath, JsonSerializer.Serialize(stub));
                }
                catch { /* ignore */ }
            }
            WriteState(null, null, null, null);
        }
    });

    ctx.Response.StatusCode = 200;
    ctx.Response.ContentType = "application/json";
    await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { ok = true, run_id = runId, artifact_dir = artifactDirFinal }));
    }
    finally { runStartLock.Release(); }
});

// GET /v1/orb/backtest/confirm_nt8/status?run_id=...
app.MapGet("/v1/orb/backtest/confirm_nt8/status", (HttpContext ctx) =>
{
    var runId = ctx.Request.Query["run_id"].FirstOrDefault();
    if (string.IsNullOrEmpty(runId))
    {
        ctx.Response.StatusCode = 400;
        ctx.Response.ContentType = "application/json";
        return ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "run_id required" }));
    }

    try
    {
        string? artifactDir = null;
        if (File.Exists(statePath))
        {
            var json = File.ReadAllText(statePath);
            var doc = JsonDocument.Parse(json);
            var activeRunId = doc.RootElement.TryGetProperty("active_run_id", out var ar) ? ar.GetString() : null;
            artifactDir = doc.RootElement.TryGetProperty("artifact_dir", out var ad) ? ad.GetString() : null;
            if (activeRunId == runId && !string.IsNullOrEmpty(artifactDir))
                ; // use artifact_dir for active run
            else if (doc.RootElement.TryGetProperty("runs", out var runs) && runs.TryGetProperty(runId, out var runAd))
                artifactDir = runAd.GetString(); // completed run lookup
            else
                artifactDir = null;
        }
        if (string.IsNullOrEmpty(artifactDir) || !Directory.Exists(artifactDir))
            return RespondStatus(ctx, "running", null, null, null);

        var donePath = Path.Combine(artifactDir, Tier2Dir, DoneFileName);
        if (!File.Exists(donePath))
            return RespondStatus(ctx, "running", null, null, artifactDir);

        var doneJson = File.ReadAllText(donePath);
        var done = JsonDocument.Parse(doneJson).RootElement;
        var exitCode = done.TryGetProperty("exit_code", out var ec) ? ec.GetInt32() : (int?)null;
        var status = done.TryGetProperty("status", out var st) ? st.GetString() : null;
        var summaryPath = Path.Combine(artifactDir, Tier2Dir, "summary.json");
        object? summary = null;
        if (File.Exists(summaryPath))
        {
            try
            {
                summary = JsonSerializer.Deserialize<JsonElement>(File.ReadAllText(summaryPath));
            }
            catch { /* ignore */ }
        }

        return RespondStatus(ctx, "done", exitCode, summary, artifactDir, status);
    }
    catch
    {
        return RespondStatus(ctx, "running", null, null, null);
    }
});

Task RespondStatus(HttpContext ctx, string state, int? exit_code, object? summary, string? artifact_dir, string? status = null)
{
    ctx.Response.StatusCode = 200;
    ctx.Response.ContentType = "application/json";
    var o = new Dictionary<string, object?> { ["state"] = state };
    if (exit_code.HasValue) o["exit_code"] = exit_code.Value;
    if (summary != null) o["summary"] = summary;
    if (!string.IsNullOrEmpty(artifact_dir)) o["artifact_dir"] = artifact_dir;
    if (!string.IsNullOrEmpty(status)) o["status"] = status;
    return ctx.Response.WriteAsync(JsonSerializer.Serialize(o));
}

// GET /v1/orb/backtest/confirm_nt8/collect?run_id=...
app.MapGet("/v1/orb/backtest/confirm_nt8/collect", async (HttpContext ctx) =>
{
    var runId = ctx.Request.Query["run_id"].FirstOrDefault();
    if (string.IsNullOrEmpty(runId))
    {
        ctx.Response.StatusCode = 400;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "run_id required" }));
        return;
    }

    string? artifactDir = null;
    try
    {
        if (File.Exists(statePath))
        {
            var json = File.ReadAllText(statePath);
            var doc = JsonDocument.Parse(json);
            var activeRunId = doc.RootElement.TryGetProperty("active_run_id", out var ar) ? ar.GetString() : null;
            if (activeRunId == runId)
                artifactDir = doc.RootElement.TryGetProperty("artifact_dir", out var ad) ? ad.GetString() : null;
            if (string.IsNullOrEmpty(artifactDir) && doc.RootElement.TryGetProperty("runs", out var runs) && runs.TryGetProperty(runId, out var runAd))
                artifactDir = runAd.GetString();
        }
        if (string.IsNullOrEmpty(artifactDir) || !Directory.Exists(artifactDir))
        {
            ctx.Response.StatusCode = 404;
            ctx.Response.ContentType = "application/json";
            await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "run_id not found or artifacts missing" }));
            return;
        }
    }
    catch
    {
        ctx.Response.StatusCode = 404;
        ctx.Response.ContentType = "application/json";
        await ctx.Response.WriteAsync(JsonSerializer.Serialize(new { error = "run_id not found" }));
        return;
    }

    var zipName = $"tier2-{runId}.zip";
    ctx.Response.Headers.ContentDisposition = $"attachment; filename=\"{zipName}\"";
    ctx.Response.ContentType = "application/zip";

    await using var zipStream = new MemoryStream();
    using (var zip = new ZipArchive(zipStream, ZipArchiveMode.Create))
    {
        foreach (var file in Directory.GetFiles(artifactDir, "*", SearchOption.AllDirectories))
        {
            var entryName = Path.GetRelativePath(artifactDir, file).Replace('\\', '/');
            var entry = zip.CreateEntry(entryName, CompressionLevel.Fastest);
            await using var entryStream = entry.Open();
            await using var fileStream = File.OpenRead(file);
            await fileStream.CopyToAsync(entryStream);
        }
    }
    zipStream.Position = 0;
    await zipStream.CopyToAsync(ctx.Response.Body);
});

app.MapGet("/v1/orb/backtest/confirm_nt8/health", (HttpContext ctx) =>
{
    ctx.Response.ContentType = "application/json";
    return ctx.Response.WriteAsync(JsonSerializer.Serialize(new
    {
        ok = true,
        token_fingerprint = tokenFingerprint,
        backtest_only_env = IsBacktestOnlyAllowed(),
    }));
});

app.Run();
