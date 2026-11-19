using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class SteamProxyLauncher {
    static readonly string GameExe = Path.Combine(Environment.GetEnvironmentVariable("TOPHEROES_GAME_ROOT") ?? "", "TopHeroes_Steam.exe");
    static readonly string PwshExe = Environment.GetEnvironmentVariable("TOPHEROES_PWSH") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "PowerShell", "7", "pwsh.exe");
    static int Main(string[] args) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        try {
            if(args.Length==0 || !string.Equals(Path.GetFullPath(args[0]),GameExe,StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("This launcher only accepts Top Heroes. Use the Steam launch option ending in %command%.");
            string script=Path.Combine(root,"启动专用代理试验.ps1");
            if(!File.Exists(PwshExe)||!File.Exists(script)||!File.Exists(GameExe))
                throw new FileNotFoundException("The proxy launcher files or game have moved.");
            var start=new ProcessStartInfo(PwshExe);
            start.Arguments="-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \""+script+"\"";
            start.WorkingDirectory=root;
            start.UseShellExecute=false;
            start.CreateNoWindow=true;
            start.EnvironmentVariables["TOPHEROES_STEAM_COMMAND"]=new JavaScriptSerializer().Serialize(args);
            using(var process=Process.Start(start)) {
                process.WaitForExit();
                if(process.ExitCode!=0) throw new InvalidOperationException("Proxy launch failed. Check trial.log in this folder. Close the game before using the restore script.");
            }
            return 0;
        } catch(Exception error) {
            File.AppendAllText(Path.Combine(root,"steam-launch.log"),DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")+" ERROR "+error.Message+Environment.NewLine,Encoding.UTF8);
            MessageBox.Show(error.Message,"Top Heroes proxy launcher",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
    }
}
