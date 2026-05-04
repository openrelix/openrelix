#!/usr/bin/env node

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const packageJsonPath = path.join(repoRoot, "package.json");
const installScript = path.join(repoRoot, "install", "install.sh");
const openrelixCli = path.join(repoRoot, "scripts", "openrelix.py");

function readPackageVersion() {
  try {
    return JSON.parse(fs.readFileSync(packageJsonPath, "utf8")).version || "0.0.0";
  } catch (_) {
    return "0.0.0";
  }
}

function printHelp() {
  console.log(`Usage:
  npx openrelix install [install-options]
  npx openrelix update [--check | --recommended | --print-command]
  npx openrelix uninstall [--delete-local-memory | --keep-local-memory]
  npx openrelix app [--build | --no-open]
  npx openrelix models [--json | --bundled | --all]
  npx openrelix index status|rebuild|search-memory|search-window
  openrelix install [install-options]
  openrelix uninstall [--delete-local-memory | --keep-local-memory]
  openrelix --version
  openrelix --help

Examples:
  npx openrelix install
  npx openrelix install --language en
  npx openrelix install --enable-learning-refresh
  npx openrelix install --record-memory-only
  npx openrelix install --enable-learning-refresh --activity-source history
  npx openrelix install --disable-personal-memory
  npx openrelix install --minimal
  npx openrelix install --enable-learning-refresh --keep-awake=during-job
  npx openrelix install --enable-learning-refresh --enable-update-check
  npx openrelix uninstall
  npx openrelix uninstall --delete-local-memory
  npx openrelix update --print-command
  npx openrelix app
  npx openrelix models
  npx openrelix index status
  npx openrelix index search-memory sqlite
  npx openrelix install --nightly-organize-time 22:30 --nightly-finalize-time 01:00

This npm command is a thin wrapper around install/install.sh.
Interactive installs prompt for 中文 (zh) or English (en) when --language is omitted.
Install profile defaults to integrated: the installer sets up the openrelix shell command, user-level Codex skill symlink, bounded history config, lightweight macOS client, overview refresh service, nightly organization LaunchAgents, and local reports. Use --minimal for a local-only bootstrap without shell command, macOS client, or LaunchAgents.
Memory mode defaults to integrated: local personal memory stays on and a bounded summary is injected into enabled host context. Use --record-memory-only for strict local-only recording, or --disable-personal-memory to only visualize AI CLI memory.
Add --enable-learning-refresh when you want the 30-minute overview-refresh LaunchAgent to call the configured activity host adapter and learn from a 7-day window automatically.
Activity source defaults to auto: try Codex app-server first, then fall back to CLI history/session. Add --activity-source history to force CLI files only.
Nightly defaults to 23:00 preview and 00:10 previous-day finalize. Use --nightly-organize-time and --nightly-finalize-time with HH:MM to override.
Daily update check defaults to 09:30 when --enable-update-check is passed; it checks npm only and does not auto-install updates.
Uninstall removes OpenRelix LaunchAgents, the macOS app, shell command, user-level skill, and prompt. It asks before deleting the local state root; use --delete-local-memory for a full purge or --keep-local-memory for unattended removal that keeps memory.
Run "npx openrelix install --help" to show installer options.`);
}

function exitCodeForSignal(signal) {
  if (signal === "SIGHUP") return 129;
  if (signal === "SIGINT") return 130;
  if (signal === "SIGTERM") return 143;
  return 1;
}

function runManagedChild(command, args) {
  const child = spawn(command, args, {
    cwd: repoRoot,
    stdio: "inherit",
    env: process.env,
  });
  let forwardedSignal = null;
  let killTimer = null;

  function cleanup() {
    process.off("SIGHUP", forwardSignal);
    process.off("SIGINT", forwardSignal);
    process.off("SIGTERM", forwardSignal);
    if (killTimer) {
      clearTimeout(killTimer);
      killTimer = null;
    }
  }

  function forwardSignal(signal) {
    forwardedSignal = signal;
    child.kill(signal);
    if (!killTimer) {
      killTimer = setTimeout(() => {
        child.kill("SIGKILL");
      }, 5000);
      killTimer.unref();
    }
  }

  process.on("SIGHUP", forwardSignal);
  process.on("SIGINT", forwardSignal);
  process.on("SIGTERM", forwardSignal);

  child.on("error", (error) => {
    cleanup();
    console.error(error.message);
    process.exit(1);
  });

  child.on("exit", (code, signal) => {
    cleanup();
    if (signal) {
      process.exit(exitCodeForSignal(signal));
    }
    if (typeof code === "number") {
      process.exit(code);
    }
    if (forwardedSignal) {
      process.exit(exitCodeForSignal(forwardedSignal));
    }
    process.exit(1);
  });
}

function runPythonCli(args) {
  if (!fs.existsSync(openrelixCli)) {
    console.error(`Missing CLI: ${openrelixCli}`);
    process.exit(1);
  }

  const pythonBin = process.env.PYTHON_BIN || "python3";
  runManagedChild(pythonBin, [openrelixCli, ...args]);
}

function runInstaller(args) {
  if (!fs.existsSync(installScript)) {
    console.error(`Missing installer: ${installScript}`);
    process.exit(1);
  }

  runManagedChild("zsh", [installScript, ...args]);
}

function updateArgsToInstallArgs(args) {
  const installArgs = [];
  let recommended = false;

  for (const arg of args) {
    if (arg === "--check") {
      const latest = spawnSync("npm", ["view", "openrelix", "version"], {
        cwd: repoRoot,
        encoding: "utf8",
      });
      const currentVersion = readPackageVersion();
      const latestVersion = latest.status === 0 ? String(latest.stdout || "").trim() : "";
      if (latestVersion) {
        console.log(`current=${currentVersion} latest=${latestVersion}`);
      } else {
        console.log(`current=${currentVersion} latest=unknown`);
        if (latest.stderr) {
          console.error(String(latest.stderr).trim());
        }
      }
      return null;
    }
    if (arg === "--print-command") {
      continue;
    }
    if (arg === "--recommended") {
      recommended = true;
      continue;
    }
    if (arg === "--yes" || arg === "-y" || arg === "--force") {
      continue;
    }
    installArgs.push(arg);
  }

  if (recommended) {
    installArgs.push(
      "--enable-learning-refresh",
      "--keep-awake=during-job",
      "--enable-update-check",
      "--update-check-time=09:30"
    );
  }
  return installArgs;
}

function shellQuote(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_/:=.,@+-]+$/.test(text)) {
    return text;
  }
  return `'${text.replace(/'/g, `'\\''`)}'`;
}

function handleUpdate(args) {
  const installArgs = updateArgsToInstallArgs(args);
  if (installArgs === null) {
    return false;
  }
  const command = ["npx", "-y", "openrelix@latest", "install", ...installArgs];
  if (args.includes("--print-command")) {
    console.log(command.map(shellQuote).join(" "));
    return false;
  }
  runInstaller(installArgs);
  return true;
}

function main() {
  const args = process.argv.slice(2);
  const command = args[0];

  if (!command || command === "help" || command === "--help" || command === "-h") {
    printHelp();
    process.exit(0);
  }

  if (command === "--version" || command === "-v" || command === "version") {
    console.log(readPackageVersion());
    process.exit(0);
  }

  if (command === "install") {
    runInstaller(args.slice(1));
    return;
  }

  if (command === "update" || command === "upgrade") {
    if (handleUpdate(args.slice(1))) {
      return;
    }
    process.exit(0);
  }

  if (command === "uninstall" || command === "remove") {
    runPythonCli(["uninstall", ...args.slice(1)]);
    return;
  }

  if (command === "app" || command === "client" || command === "mac") {
    runPythonCli(["app", ...args.slice(1)]);
    return;
  }

  if (command === "models") {
    runPythonCli(["models", ...args.slice(1)]);
    return;
  }

  if (command === "index") {
    runPythonCli(["index", ...args.slice(1)]);
    return;
  }

  if (command.startsWith("-")) {
    runInstaller(args);
    return;
  }

  console.error(`Unknown command: ${command}`);
  printHelp();
  process.exit(1);
}

main();
