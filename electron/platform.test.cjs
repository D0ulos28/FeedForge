const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const {
  developmentPythonPath,
  executableName,
  isPythonExecutableName,
  pythonCommands,
  venvPythonPath,
} = require("./platform.cjs");

test("uses platform-native executable names", () => {
  assert.equal(executableName("psarc2feedpak", "win32"), "psarc2feedpak.exe");
  assert.equal(executableName("psarc2feedpak", "linux"), "psarc2feedpak");
  assert.equal(executableName("psarc2feedpak", "darwin"), "psarc2feedpak");
});

test("resolves virtual environments on each platform", () => {
  assert.equal(venvPythonPath("root", "win32"), path.join("root", ".demucs-venv", "Scripts", "python.exe"));
  assert.equal(venvPythonPath("root", "linux"), path.join("root", ".demucs-venv", "bin", "python"));
  assert.equal(developmentPythonPath("root", "darwin"), path.join("root", ".venv", "bin", "python"));
});

test("recognizes supported Python executable names", () => {
  for (const name of ["python", "python3", "python3.12", "python.exe", "Python.EXE"]) {
    assert.equal(isPythonExecutableName(name), true, name);
  }
  assert.equal(isPythonExecutableName("not-python"), false);
  assert.deepEqual(pythonCommands("win32"), ["python.exe", "py.exe"]);
  assert.deepEqual(pythonCommands("linux"), ["python3", "python"]);
});
