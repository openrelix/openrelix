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
  npx openkeepsake install [install-options]
  openkeepsake install [install-options]
  okeep install [install-options]
  okeep --version
  okeep --help

Examples:
  npx openkeepsake install
  npx openkeepsake install --language en
  npx openkeepsake install --profile integrated
  npx openkeepsake install --profile integrated --record-memory-only
  npx openkeepsake install --profile integrated --read-codex-app
  npx openkeepsake install --profile integrated --disable-personal-memory
  npx openkeepsake install --profile integrated --enable-nightly --keep-awake=during-job

This npm command is a thin wrapper around install/install.sh.
Interactive installs prompt for 中文 (zh) or English (en) when --language is omitted.
Memory mode defaults to codex-context: local personal memory stays on and a bounded summary is injected into Codex native context. Use --record-memory-only for strict local-only recording, or --disable-personal-memory to only visualize AI CLI memory.
Activity source defaults to history. Add --read-codex-app or --activity-source auto only when you explicitly want to read Codex app/server threads.
Run "npx openkeepsake install --help" to show installer options.`);
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
