using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class SinglePlayerLauncher {
    [STAThread] private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "launcher.ps1");
        if (!File.Exists(script)) {
            MessageBox.Show("诗矩文件不完整，请重新运行安装程序。", "诗矩");
            return 1;
        }
        var info = new ProcessStartInfo {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"),
            Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -File \"" + script + "\"",
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        try {
            using (var process = Process.Start(info)) {
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch (Exception ex) {
            MessageBox.Show("诗矩启动失败：" + ex.Message, "诗矩");
            return 1;
        }
    }
}
