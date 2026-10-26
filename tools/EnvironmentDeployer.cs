using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class EnvironmentDeployer {
    [STAThread] private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "tools", "environment-tool.ps1");
        if (!File.Exists(script)) { MessageBox.Show("工具文件不完整，请完整解压后启动。", "诗矩环境部署"); return 1; }
        if (args.Length == 1 && args[0] == "--self-test") return 0;
        var info = new ProcessStartInfo();
        info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
        info.Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -File \"" + script + "\"";
        if (args.Length == 2 && args[0] == "--auto-inspect-report") {
            if (!Path.IsPathRooted(args[1]) || args[1].Contains("\"") || args[1].EndsWith("\\")) return 2;
            info.Arguments += " -AutoInspectReport \"" + args[1] + "\"";
        }
        info.WorkingDirectory = root;
        // Keep the PowerShell console hidden while allowing the WinForms child
        // window to be shown. WindowStyle=Hidden with ShellExecute can hide
        // the WinForms window as well on some Windows builds.
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        try {
            using (var process = Process.Start(info)) {
                process.WaitForExit();
                if (process.ExitCode != 0) {
                    MessageBox.Show("部署面板启动失败，请查看工具目录中的启动错误日志。", "诗矩环境部署");
                }
                return process.ExitCode;
            }
        }
        catch (Exception ex) { MessageBox.Show(ex.Message, "诗矩环境部署启动失败"); return 1; }
    }
}
