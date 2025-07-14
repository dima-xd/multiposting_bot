# Multiposting Bot

Allow to post images (videos in future) in Telegram, VK, Bsky, X, Tumblr (more social networks will be added in future).

# How to

1. Create Bot in Telegram using @BotFather
2. Create config.yaml file in project's root
3. Using template below for your config file:

```yaml
admins: 123456, 654321

TG_BOT_TOKEN: "TOKEN"

profiles:
  dimaxd:
    TG_CHANNEL_ID: -1000000000

    VK_TOKEN: "TOKEN"
    VK_GROUP_ID: 23492934923

    TWITTER_CONSUMER_KEY: "TOKEN"
    TWITTER_CONSUMER_SECRET: "TOKEN"
    TWITTER_ACCESS_TOKEN: "TOKEN"
    TWITTER_ACCESS_SECRET: "TOKEN"

    TUMBLR_CONSUMER_KEY: "TOKEN"
    TUMBLR_CONSUMER_SECRET: "TOKEN"
    TUMBLR_ACCESS_TOKEN: "TOKEN"
    TUMBLR_ACCESS_SECRET: "TOKEN"

    BLUESKY_LOGIN: "LOGIN"
    BLUESKY_PASSWORD: "PASSWORD"
```
