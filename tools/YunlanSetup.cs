using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Program
{
    internal const string AppVersion = "1.1.0";
    internal const string ExpectedSha = "4B85E4AC87744ED424947ACE15716D52C0D14CE8699CE18632EBCA43237E76B0";
    internal const string GithubUrl = "https://github.com/shjsxx/dsh-dafeiyu-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip";
    internal static readonly string[] GiteeParts = new[]
    {
        "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.001",
        "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.002",
        "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.003",
        "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.004",
    };

    [STAThread]
    private static int Main(string[] args)
    {
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        var silent = args.Any(x => x.Equals("--silent", StringComparison.OrdinalIgnoreCase));
        var source = ArgValue(args, "--source") ?? "gitee";
        var target = ArgValue(args, "--target") ?? DefaultTarget();
        var localZip = ArgValue(args, "--zip");
        var allowRunning = args.Any(x => x.Equals("--allow-running", StringComparison.OrdinalIgnoreCase));
        var noOpen = args.Any(x => x.Equals("--no-open", StringComparison.OrdinalIgnoreCase));
        if (silent)
        {
            var logPath = Path.Combine(Path.GetTempPath(), "yunlan-setup.log");
            try
            {
                using (var writer = new StreamWriter(logPath, false, Encoding.UTF8))
                {
                    var code = Installer.RunAsync(source, target, localZip, allowRunning,
                        message => writer.WriteLine(message), percent => { }, noOpen).GetAwaiter().GetResult();
                    writer.WriteLine("RESULT=" + code);
                    return code;
                }
            }
            catch (Exception error)
            {
                File.WriteAllText(logPath, error.ToString(), Encoding.UTF8);
                return 2;
            }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new InstallerForm(target, source));
        return 0;
    }

    private static string ArgValue(string[] args, string name)
    {
        for (var i = 0; i < args.Length - 1; i++)
            if (args[i].Equals(name, StringComparison.OrdinalIgnoreCase)) return args[i + 1];
        return null;
    }

    internal static string DefaultTarget()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".dsh", "plugins", "dsh-dafeiyu-growth");
    }
}

internal sealed class InstallerForm : Form
{
    private readonly ComboBox source = new ComboBox();
    private readonly TextBox target = new TextBox();
    private readonly Button install = new Button();
    private readonly Button browse = new Button();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Label status = new Label();
    private readonly TextBox log = new TextBox();

    public InstallerForm(string initialTarget, string initialSource)
    {
        Text = "云岚 · 大肥鱼智能养成 安装器";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        ClientSize = new Size(660, 480);
        Font = new Font("Microsoft YaHei UI", 9F);

        var title = new Label
        {
            Text = "云岚 · 大肥鱼智能养成",
            Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold),
            AutoSize = true, Location = new Point(22, 18)
        };
        Controls.Add(title);
        var subtitle = new Label
        {
            Text = "版本 " + Program.AppVersion + " · 自包含安装 · 不需要管理员权限",
            ForeColor = Color.DimGray, AutoSize = true, Location = new Point(24, 52)
        };
        Controls.Add(subtitle);

        Controls.Add(new Label { Text = "下载源", AutoSize = true, Location = new Point(24, 92) });
        source.DropDownStyle = ComboBoxStyle.DropDownList;
        source.Items.AddRange(new object[] { "GitHub（单包）", "Gitee（四卷，国内推荐）" });
        source.SelectedIndex = initialSource.Equals("github", StringComparison.OrdinalIgnoreCase) ? 0 : 1;
        source.Location = new Point(92, 88);
        source.Width = 230;
        Controls.Add(source);

        Controls.Add(new Label { Text = "安装位置", AutoSize = true, Location = new Point(24, 132) });
        target.Text = initialTarget;
        target.Location = new Point(92, 128);
        target.Width = 440;
        Controls.Add(target);
        browse.Text = "浏览...";
        browse.Location = new Point(542, 126);
        browse.Width = 88;
        browse.Click += (s, e) =>
        {
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "选择 DSH 插件目录";
                dialog.SelectedPath = target.Text;
                if (dialog.ShowDialog(this) == DialogResult.OK) target.Text = dialog.SelectedPath;
            }
        };
        Controls.Add(browse);

        var note = new Label
        {
            Text = "请先退出 DeepSeek Harness。安装会保留已有的记忆、训练、情感和云端配置。",
            ForeColor = Color.FromArgb(160, 80, 20), AutoSize = true, Location = new Point(24, 174)
        };
        Controls.Add(note);

        install.Text = "开始安装";
        install.Location = new Point(24, 208);
        install.Width = 160;
        install.Height = 36;
        install.Click += async (s, e) => await StartInstallAsync();
        Controls.Add(install);

        progress.Location = new Point(200, 216);
        progress.Width = 330;
        progress.Height = 18;
        Controls.Add(progress);
        status.Text = "等待开始";
        status.AutoSize = true;
        status.Location = new Point(24, 254);
        Controls.Add(status);

        log.Multiline = true;
        log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Vertical;
        log.Location = new Point(24, 282);
        log.Size = new Size(606, 150);
        log.BackColor = Color.White;
        Controls.Add(log);

        var close = new Button { Text = "退出", Location = new Point(548, 442), Width = 82 };
        close.Click += (s, e) => Close();
        Controls.Add(close);
    }

    private async Task StartInstallAsync()
    {
        install.Enabled = false;
        progress.Value = 0;
        log.Clear();
        AddLog("准备安装云岚 " + Program.AppVersion);
        var selected = source.SelectedIndex == 1 ? "gitee" : "github";
        try
        {
            var code = await Installer.RunAsync(selected, target.Text.Trim(), null, false,
                AddLog, percent => progress.Value = Math.Max(0, Math.Min(100, percent)), false);
            if (code == 0)
            {
                progress.Value = 100;
                status.Text = "安装完成，请重启 DSH";
                AddLog("安装完成。请重启 DeepSeek Harness，然后在 设置 → 插件 启用云岚养成。");
                MessageBox.Show(this, "安装完成。\n\n请重启 DeepSeek Harness，然后在 设置 → 插件 启用「云岚养成」。",
                    "云岚安装器", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            else
            {
                status.Text = "安装失败";
                MessageBox.Show(this, "安装没有完成，请查看窗口中的日志。", "云岚安装器",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
        catch (Exception error)
        {
            AddLog("错误：" + error.Message);
            status.Text = "安装失败";
            MessageBox.Show(this, error.Message, "云岚安装器", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            install.Enabled = true;
        }
    }

    private void AddLog(string text)
    {
        if (log.TextLength > 20000) log.Clear();
        log.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + text + Environment.NewLine);
        status.Text = text.Length > 70 ? text.Substring(0, 70) + "..." : text;
        Application.DoEvents();
    }
}

internal static class Installer
{
    public static async Task<int> RunAsync(string source, string target, string localZip,
        bool allowRunning, Action<string> log, Action<int> progress, bool noOpen)
    {
        if (!allowRunning && IsHelperRunning())
        {
            log("检测到云岚 helper 仍在运行，请先完全退出 DeepSeek Harness。");
            return 3;
        }
        var work = Path.Combine(Path.GetTempPath(), "yunlan-setup-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(work);
        var zip = localZip;
        var stage = Path.Combine(work, "stage");
        try
        {
            if (string.IsNullOrEmpty(zip))
            {
                zip = Path.Combine(work, "dsh-dafeiyu-growth-v1.1.0-windows-x64.zip");
                if (source.Equals("gitee", StringComparison.OrdinalIgnoreCase))
                    await DownloadGiteeAsync(zip, log, progress);
                else
                {
                    var githubOk = false;
                    try
                    {
                        await DownloadAsync(Program.GithubUrl, zip, log, progress);
                        githubOk = true;
                    }
                    catch (Exception error)
                    {
                        log("GitHub 下载失败：" + error.Message);
                        log("自动改用 Gitee 镜像...");
                    }
                    if (!githubOk) await DownloadGiteeAsync(zip, log, progress);
                }
            }
            else
            {
                log("使用本地安装包：" + zip);
                progress(60);
            }
            if (!File.Exists(zip)) throw new InvalidOperationException("安装包不存在：" + zip);
            log("校验 SHA-256...");
            progress(82);
            var hash = Sha256(zip);
            if (!hash.Equals(Program.ExpectedSha, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("SHA-256 校验失败。期望 " + Program.ExpectedSha + "，实际 " + hash);
            log("SHA-256 校验通过");

            log("解压安装包...");
            progress(88);
            Directory.CreateDirectory(stage);
            ZipFile.ExtractToDirectory(zip, stage);
            if (!File.Exists(Path.Combine(stage, "package.json")))
                throw new InvalidOperationException("安装包缺少 package.json");

            log("安装到：" + target);
            progress(94);
            Directory.CreateDirectory(target);
            var copied = 0;
            foreach (var file in Directory.GetFiles(stage, "*", SearchOption.AllDirectories))
            {
                var relative = file.Substring(stage.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                var destination = Path.Combine(target, relative);
                var folder = Path.GetDirectoryName(destination);
                if (!string.IsNullOrEmpty(folder)) Directory.CreateDirectory(folder);
                File.Copy(file, destination, true);
                copied++;
            }
            if (!File.Exists(Path.Combine(target, "package.json")))
                throw new InvalidOperationException("安装后目标目录缺少 package.json");
            log("已安装 " + copied + " 个文件；原有本地数据保留。");
            progress(100);
            if (!noOpen)
            {
                try { Process.Start("explorer.exe", "\"" + target + "\""); } catch { }
            }
            return 0;
        }
        catch (Exception error)
        {
            log("失败：" + error.Message);
            return 2;
        }
        finally
        {
            try { Directory.Delete(work, true); } catch { }
        }
    }

    private static bool IsHelperRunning()
    {
        try
        {
            return Process.GetProcesses().Any(p =>
                p.ProcessName.StartsWith("dsh-dafeiyu-helper", StringComparison.OrdinalIgnoreCase));
        }
        catch { return false; }
    }

    private static async Task DownloadAsync(string url, string destination, Action<string> log, Action<int> progress)
    {
        log("下载：" + url);
        using (var client = new HttpClient { Timeout = TimeSpan.FromMinutes(30) })
        {
            client.DefaultRequestHeaders.UserAgent.ParseAdd("Yunlan-Setup/1.1.0");
            using (var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead))
            {
                response.EnsureSuccessStatusCode();
                var total = response.Content.Headers.ContentLength ?? -1;
                using (var input = await response.Content.ReadAsStreamAsync())
                using (var output = File.Create(destination))
                {
                    var buffer = new byte[81920];
                    long received = 0;
                    int read;
                    while ((read = await input.ReadAsync(buffer, 0, buffer.Length)) > 0)
                    {
                        await output.WriteAsync(buffer, 0, read);
                        received += read;
                        if (total > 0) progress((int)Math.Min(80, received * 80 / total));
                    }
                }
            }
        }
        log("下载完成");
    }

    private static async Task DownloadGiteeAsync(string destination, Action<string> log, Action<int> progress)
    {
        using (var output = File.Create(destination))
        {
            for (var i = 0; i < Program.GiteeParts.Length; i++)
            {
                log("下载 Gitee 分卷 " + (i + 1) + "/" + Program.GiteeParts.Length + "...");
                using (var client = new HttpClient { Timeout = TimeSpan.FromMinutes(30) })
                {
                    client.DefaultRequestHeaders.UserAgent.ParseAdd("Yunlan-Setup/1.1.0");
                    using (var response = await client.GetAsync(Program.GiteeParts[i], HttpCompletionOption.ResponseHeadersRead))
                    {
                        response.EnsureSuccessStatusCode();
                        using (var input = await response.Content.ReadAsStreamAsync())
                        {
                            await input.CopyToAsync(output);
                        }
                    }
                }
                progress((i + 1) * 20);
            }
        }
        log("Gitee 分卷合并完成");
    }

    private static string Sha256(string path)
    {
        using (var sha = SHA256.Create())
        using (var stream = File.OpenRead(path))
        {
            var hash = sha.ComputeHash(stream);
            var builder = new StringBuilder(hash.Length * 2);
            foreach (var value in hash) builder.Append(value.ToString("X2"));
            return builder.ToString();
        }
    }
}
