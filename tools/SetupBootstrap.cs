using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class SetupBootstrap {
    [STAThread] private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "tools", "installer-bootstrap.ps1");
        if (!File.Exists(script)) { MessageBox.Show("安装组件不完整，请重新运行安装程序。", "诗矩安装程序"); return 1; }
        string arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"";
        foreach (var arg in args) {
            string parameter = arg == "--self-test" ? "-SelfTest" :
                arg == "--request" ? "-Request" :
                arg == "--events" ? "-Events" :
                arg == "--result" ? "-Result" :
                arg == "--data-dir" ? "-DataDir" : null;
            arguments += parameter != null ? " " + parameter : " \"" + arg.Replace("\"", "") + "\"";
        }
        var info = new ProcessStartInfo {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"),
            Arguments = arguments, WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true
        };
        try { using (var process = Process.Start(info)) { process.WaitForExit(); return process.ExitCode; } }
        catch (Exception ex) { MessageBox.Show("安装程序启动部署引擎失败：" + ex.Message, "诗矩安装程序"); return 1; }
    }
}
