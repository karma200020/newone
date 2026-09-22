const { spawn, spawnSync } = require("node:child_process");
const { mkdirSync, existsSync, rmSync } = require("node:fs");
const { randomBytes } = require("node:crypto");
const path = require("node:path");

mkdirSync("artifacts", { recursive: true });
const database = path.join("artifacts", `browser-${process.pid}.db`);
const localPython = path.join(".venv", process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python");
const python = process.env.OPS_TEST_PYTHON || (existsSync(localPython) ? localPython : "python");
const env = {
  ...process.env,
  OPS_ENVIRONMENT: "test",
  OPS_SIGNING_KEY: randomBytes(48).toString("base64url"),
  OPS_DATABASE_URL: `sqlite:///${database}`,
  OPS_REDIS_URL: "",
  OPS_DEMO_MODE: "true",
  OPS_TOOL_TRANSPORT: "local",
  OPS_LLM_ENABLED: "false",
};
const initialized = spawnSync(python, ["-m", "ops.cli", "migrate"], { env, stdio: "inherit" });
if (initialized.status !== 0) process.exit(initialized.status || 1);
const children = [
  spawn(python, ["-m", "ops.cli", "serve", "--port", "8010"], { env, stdio: "inherit" }),
  spawn(python, ["-m", "ops.cli", "worker"], { env, stdio: "inherit" }),
];
let stopping = false;
function shutdown(code) {
  if (stopping) return;
  stopping = true;
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
  const timer = setTimeout(() => process.exit(code), 3000);
  timer.unref();
  Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() :
    new Promise(resolve => child.once("exit", resolve)))).then(() => {
    for (const file of [database, `${database}-wal`, `${database}-shm`]) {
      if (existsSync(file)) rmSync(file);
    }
    process.exit(code);
  });
}
process.on("SIGTERM", () => shutdown(0));
process.on("SIGINT", () => shutdown(0));
for (const child of children) {
  child.on("error", error => { console.error(error); shutdown(1); });
  child.on("exit", code => { if (!stopping) shutdown(code || 1); });
}
