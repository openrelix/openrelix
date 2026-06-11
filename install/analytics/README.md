# OpenRelix Analytics Install Resources

Official release packaging may include `OpenRelixAnalyticsToken.txt` in this
directory so first-time macOS app installs can report anonymous, whitelisted
product analytics without extra user setup.

Do not commit the token file to git. The GitHub npm publish workflow injects it
from the `OPENRELIX_ANALYTICS_CLIENT_TOKEN` repository secret before `npm pack`
and `npm publish`. It is a client ingest token, not the PostHog project token,
but it should still be injected only by the release packaging environment. The
privileged PostHog project token remains in the Cloudflare Worker secret store.
