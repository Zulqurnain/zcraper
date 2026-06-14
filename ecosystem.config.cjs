module.exports = {
  apps: [{
    name: "zcraper-api",
    script: "/root/zcraper/extract_api.py",
    interpreter: "/root/zcraper/venv/bin/python",
    cwd: "/root/zcraper",
    exec_mode: "fork",
    instances: 1,
    uid: "webapp",
    gid: "webapp",
    // PB credentials are loaded from .env by pb_client.py — do NOT add them here
    env: { HOME: "/home/webapp", PLAYWRIGHT_BROWSERS_PATH: "/opt/ms-playwright", PORT: "5500" }
  }]
};
