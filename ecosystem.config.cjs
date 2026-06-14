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
    env: { HOME: "/home/webapp", PLAYWRIGHT_BROWSERS_PATH: "/opt/ms-playwright", PORT: "5500", PB_ADMIN_EMAIL: "zulqurnainapps@gmail.com", PB_ADMIN_PASSWORD: "***REMOVED-SEE-INCIDENT-2026-06-14***" }
  }]
};
