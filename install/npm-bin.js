#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const packageJsonPath = path.join(repoRoot, "package.json");
const installScript = path.join(repoRoot, "install", "install.sh");

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
  openrelix install [install-options]
  openrelix --version
  openrelix --help

Examples:
  npx openrelix install
  npx openrelix install --language en
  npx openrelix install --profile integrated
  npx openrelix install --profile integrated --enable-learning-refresh
  npx openrelix install --profile integrated --record-memory-only
  npx openrelix install --profile integrated --enable-learning-refresh --read-codex-app
  npx openrelix install --profile integrated --disable-personal-memory
  npx openrelix install --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
  npx openrelix install --profile integrated --enable-nightly --nightly-organize-time 22:30 --nightly-finalize-time 01:00

This npm command is a thin wrapper around install/install.sh.
Interactive installs prompt for 中文 (zh) or English (en) when --language is omitted.
Memory mode defaults to integrated: local personal memory stays on and a bounded summary is injected into Codex native context. Use --record-memory-only for strict local-only recording, or --disable-personal-memory to only visualize AI CLI memory.
Add --enable-learning-refresh when you want the 30-minute overview-refresh LaunchAgent to call the Codex adapter and learn from a 7-day window automatically.
Activity source defaults to history. Add --read-codex-app or --activity-source auto only when you explicitly want to read Codex app/server threads.
Nightly defaults to 23:00 preview and 00:10 previous-day finalize. Use --nightly-organize-time and --nightly-finalize-time with HH:MM to override.
Run "npx openrelix install --help" to show installer options.`);
}

function runInstaller(args) {
  if (!fs.existsSync(installScript)) {
    console.error(`Missing installer: ${installScript}`);
    process.exit(1);
  }

  const result = spawnSync("zsh", [installScript, ...args], {
    cwd: repoRoot,
    stdio: "inherit",
    env: process.env,
  });

  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

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
}

if (command.startsWith("-")) {
  runInstaller(args);
}

console.error(`Unknown command: ${command}`);
printHelp();
process.exit(1);
