const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const candidates = [];
if (process.env.PYTHON) candidates.push({ command: process.env.PYTHON, prefix: [] });
candidates.push({
  command: path.join(
    projectRoot,
    ".venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python"
  ),
  prefix: [],
});
if (process.platform === "win32") {
  candidates.push({ command: "python.exe", prefix: [] });
  candidates.push({ command: "py.exe", prefix: ["-3"] });
} else {
  candidates.push({ command: "python3", prefix: [] });
  candidates.push({ command: "python", prefix: [] });
}

for (const candidate of candidates) {
  if (path.isAbsolute(candidate.command) && !fs.existsSync(candidate.command)) continue;
  const result = spawnSync(candidate.command, [...candidate.prefix, ...args], {
    cwd: projectRoot,
    stdio: "inherit",
  });
  if (result.error?.code === "ENOENT") continue;
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error("Python was not found. Install Python 3.11+ or create the project .venv.");
process.exit(1);
