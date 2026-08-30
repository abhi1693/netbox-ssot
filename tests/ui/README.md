# Browser UI checks

These opt-in Playwright checks exercise the operator-facing synchronization pages at desktop and mobile sizes. They
capture a full-page screenshot for each route and fail on serious or critical axe accessibility violations.

Run them against a development NetBox with this plugin installed:

```shell
cd tests/ui
npm ci
npx playwright install chromium
NETBOX_BASE_URL=http://127.0.0.1:8000 \
NETBOX_USERNAME=admin \
NETBOX_PASSWORD=your-password \
npm test
```

Artifacts are written below `test-results/` and are intentionally ignored by Git.
