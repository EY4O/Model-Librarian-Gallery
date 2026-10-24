// Model Librarian Gallery.exe: starts the portable Gallery (python\pythonw.exe app\start.pyw, side by side with it).
//
// Before it starts, it swaps in an update the Gallery downloaded (update\app, with update\ready), but only
// while no Gallery has the data folder open: the running one would otherwise serve the new interface with
// the old server. The old app\ is kept as update\app.old until the swap is done, and put back if it fails.
//
//   Model Librarian Gallery.exe [--wait] [--apply-update-only] [the Gallery's own arguments, e.g. --data DIR]
//     --wait               wait (up to 30 s) for an open Gallery to close first (Restart now in Settings)
//     --apply-update-only  swap in a waiting update and stop (the tests)
//
// Built by tools/build_portable.py with the C# compiler that comes with Windows (.NET Framework 4).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class Launcher
{
    const string AppName = "Model Librarian Gallery";

    [STAThread]
    static int Main(string[] argv)
    {
        string pkg = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        var rest = new List<string>();
        bool wait = false, applyOnly = false;
        string data = null;
        for (int i = 0; i < argv.Length; i++)
        {
            if (argv[i] == "--wait") { wait = true; continue; }
            if (argv[i] == "--apply-update-only") { applyOnly = true; continue; }
            if (argv[i] == "--data" && i + 1 < argv.Length) data = argv[i + 1];
            rest.Add(argv[i]);
        }
        data = data ?? Environment.GetEnvironmentVariable("MLG_DATA");
        if (string.IsNullOrEmpty(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), AppName);

        string applied = ApplyUpdate(pkg, Path.Combine(data, "gallery.lock"), wait);
        if (applyOnly)
        {
            Console.WriteLine(applied ?? "nothing applied");
            return 0;
        }

        string pyw = Path.Combine(pkg, "python", "pythonw.exe"), start = Path.Combine(pkg, "app", "start.pyw");
        if (!File.Exists(pyw) || !File.Exists(start))
        {
            MessageBox.Show("The Gallery's files aren't next to this program (python\\ and app\\). Copy the whole folder, not just the program.", AppName,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var args = new StringBuilder(Quote(start));
        foreach (string a in rest) args.Append(' ').Append(Quote(a));
        var psi = new ProcessStartInfo(pyw, args.ToString()) { WorkingDirectory = pkg, UseShellExecute = false };
        try
        {
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show("The Gallery didn't start: " + ex.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }

    // The version swapped in, or null: no update waiting, a Gallery still open, or the swap failed (the old one stays).
    static string ApplyUpdate(string pkg, string lockPath, bool wait)
    {
        string upd = Path.Combine(pkg, "update"), staged = Path.Combine(upd, "app"), ready = Path.Combine(upd, "ready");
        if (!File.Exists(ready) || !File.Exists(Path.Combine(staged, "start.pyw")))
            return null;
        for (int tries = wait ? 120 : 1; !LockFree(lockPath); )
        {
            if (--tries <= 0) return null;
            Thread.Sleep(250);
        }
        string version = File.ReadAllText(ready).Trim(), app = Path.Combine(pkg, "app"), old = Path.Combine(upd, "app.old");
        try
        {
            if (Directory.Exists(old)) Directory.Delete(old, true);
            Directory.Move(app, old);
        }
        catch (Exception)
        {
            return null;  // something in app\ is in use: try again at the next start
        }
        try
        {
            Directory.Move(staged, app);
        }
        catch (Exception)
        {
            try { Directory.Move(old, app); } catch (Exception) { }
            return null;
        }
        try { File.Delete(ready); } catch (Exception) { }
        try { Directory.Delete(old, true); } catch (Exception) { }  // gone next time if something held it
        return version;
    }

    // The Gallery keeps gallery.lock open while it runs: a file nobody has open can be opened unshared.
    static bool LockFree(string path)
    {
        if (!File.Exists(path)) return true;
        try
        {
            using (new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            return true;
        }
        catch (IOException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    // One argument as CommandLineToArgvW reads it back.
    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
        var sb = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in a)
        {
            if (c == '\\') { slashes++; continue; }
            sb.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            slashes = 0;
            sb.Append(c);
        }
        sb.Append('\\', slashes * 2).Append('"');
        return sb.ToString();
    }
}
