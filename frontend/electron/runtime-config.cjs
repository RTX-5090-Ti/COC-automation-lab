const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function initializeRuntimeConfig(runtimeDataDir, defaultConfigPath) {
  const configPath = path.join(runtimeDataDir, "bot_config.json");
  const markerPath = path.join(runtimeDataDir, ".config-initialized");
  fs.mkdirSync(runtimeDataDir, { recursive: true });
  // An initialized profile with a missing config must remain missing.
  if (fs.existsSync(markerPath)) return configPath;
  if (!fs.existsSync(configPath)) {
    const temporaryPath = path.join(runtimeDataDir, `.config-${randomUUID()}.tmp`);
    try {
      // Stage the whole copy before publishing it. A failed copy cannot leave
      // a partial config that the next launch mistakes for a legacy profile.
      fs.copyFileSync(defaultConfigPath, temporaryPath, fs.constants.COPYFILE_EXCL);
      fs.renameSync(temporaryPath, configPath);
    } finally {
      fs.rmSync(temporaryPath, { force: true });
    }
  }
  // Also adopt existing profiles from versions without markers, unchanged.
  fs.writeFileSync(markerPath, "1\n", { flag: "wx" });
  return configPath;
}

module.exports = { initializeRuntimeConfig };
