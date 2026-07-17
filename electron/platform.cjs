const path = require("path");

function executableName(baseName, platform = process.platform) {
  return platform === "win32" ? `${baseName}.exe` : baseName;
}

function venvPythonPath(root, platform = process.platform) {
  return path.join(
    root,
    ".demucs-venv",
    platform === "win32" ? "Scripts" : "bin",
    executableName("python", platform)
  );
}

function developmentPythonPath(root, platform = process.platform) {
  return path.join(
    root,
    ".venv",
    platform === "win32" ? "Scripts" : "bin",
    executableName("python", platform)
  );
}

function pythonCommands(platform = process.platform) {
  return platform === "win32" ? ["python.exe", "py.exe"] : ["python3", "python"];
}

function isPythonExecutableName(value) {
  return /^python(?:\d+(?:\.\d+)*)?(?:\.exe)?$/i.test(path.basename(String(value || "")));
}

module.exports = {
  developmentPythonPath,
  executableName,
  isPythonExecutableName,
  pythonCommands,
  venvPythonPath,
};
