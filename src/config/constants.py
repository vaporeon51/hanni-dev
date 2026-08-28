"""Small, deployment-independent defaults for the Hanni web application."""

import os


DISCORD_CONTENT_CHANNEL_ID = os.getenv("DISCORD_CONTENT_CHANNEL_ID", "124767749099618304")
DISCORD_CONTENT_GUILD_ID = os.getenv("DISCORD_CONTENT_GUILD_ID", "124767749099618304")

# Content eligibility. This is the site-wide equivalent of the old per-guild
# setting; the web app does not have Discord guild context.
MIN_CONTENT_AGE = os.getenv("MIN_CONTENT_AGE", "18 year 1 month")

# Feed selection and moderation.
REPORT_THRESHOLD = 5
DEAD_LINK_REPORT_THRESHOLD = 3
SAMPLING_EXPONENT = 0.23137821316
INITIAL_REACT_CAP = 100
MAX_FEED_ITEMS = 30

# Background jobs.
INGESTION_INTERVAL_SECONDS = int(os.getenv("INGESTION_INTERVAL_SECONDS", str(12 * 60 * 60)))
DEAD_LINK_INTERVAL_SECONDS = int(os.getenv("DEAD_LINK_INTERVAL_SECONDS", str(5 * 60)))
DEAD_LINK_BATCH_SIZE = int(os.getenv("DEAD_LINK_BATCH_SIZE", "50"))
DEAD_LINK_MAX_FAILURES = int(os.getenv("DEAD_LINK_MAX_FAILURES", "2"))
DEAD_LINK_REQUEST_TIMEOUT_SECONDS = int(os.getenv("DEAD_LINK_REQUEST_TIMEOUT_SECONDS", "30"))
RECOVERY_INTERVAL_SECONDS = int(os.getenv("RECOVERY_INTERVAL_SECONDS", str(75 * 60)))
CONTENT_RECOVERY_BATCH_SIZE = int(os.getenv("CONTENT_RECOVERY_BATCH_SIZE", "70"))
CONTENT_RECOVERY_UPLOAD_INTERVAL = float(os.getenv("CONTENT_RECOVERY_UPLOAD_INTERVAL", "2.0"))
CONTENT_RECOVERY_MAX_UPLOADS_PER_HOUR = int(os.getenv("CONTENT_RECOVERY_MAX_UPLOADS_PER_HOUR", "100"))
CONTENT_RECOVERY_MAX_GENERATION = int(os.getenv("CONTENT_RECOVERY_MAX_GENERATION", "3"))
CONTENT_RECOVERY_MAX_ATTEMPTS = int(os.getenv("CONTENT_RECOVERY_MAX_ATTEMPTS", "3"))
RUN_BACKGROUND_TASKS = os.getenv("RUN_BACKGROUND_TASKS", "false").lower() in {"1", "true", "yes", "on"}

# Direct media checks intentionally use an allowlist. Content URLs originate
# outside the application, so the worker must not become an arbitrary network
# proxy or SSRF primitive.
DEFAULT_MEDIA_HOSTS = (
    "imgur.com,www.imgur.com,i.imgur.com,i.imgur.gg,"
    "cdn.discordapp.com,media.discordapp.net,"
    "files.catbox.moe,"
    "giphy.com,www.giphy.com,media0.giphy.com,media1.giphy.com,"
    "media2.giphy.com,media3.giphy.com,media4.giphy.com,"
    "cdn.goyangi.pics,pixeldrain.com,cdn.kpopping.com,"
    "www.youtube.com,www.vxinstagram.com,www.kkinstagram.com"
)
MEDIA_ALLOWED_HOSTS = frozenset(
    host.strip().lower()
    for host in os.getenv("MEDIA_ALLOWED_HOSTS", DEFAULT_MEDIA_HOSTS).split(",")
    if host.strip()
)
