import os
import json
import requests


class Notifier:
    """
    Currently supports Slack via env var SLACK_WEBHOOK_URL.
    Falls back to console if not configured.
    """

    def __init__(self):
        self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()

    def _post_slack(self, text: str, **context):
        if not self.slack_webhook:
            return False

        # Create blocks for better Slack formatting
        blocks = []

        # Main message block
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

        # Context block with event details
        context_elements = [
            {"type": "mrkdwn", "text": f"*Event:* <{context.get('event_url','')}>"}
        ]

        if context.get("lowest_price") is not None:
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Lowest:* ${context.get('lowest_price'):.2f}",
                }
            )
        else:
            context_elements.append({"type": "mrkdwn", "text": "*Lowest:* N/A"})

        if context.get("median_sale") is not None:
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Median:* ${context.get('median_sale'):.2f}",
                }
            )
        else:
            context_elements.append({"type": "mrkdwn", "text": "*Median:* N/A"})

        if context.get("num_listings") is not None:
            context_elements.append(
                {"type": "mrkdwn", "text": f"*Listings:* {context.get('num_listings')}"}
            )
        else:
            context_elements.append({"type": "mrkdwn", "text": "*Listings:* N/A"})

        blocks.append({"type": "context", "elements": context_elements})

        payload = {"text": text, "blocks": blocks}

        try:
            r = requests.post(
                self.slack_webhook,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception:
            return False

    def notify(self, message: str, **context):
        if self.slack_webhook and self._post_slack(message, **context):
            return
        # fallback to console
        print("\n=== ALERT ===")
        print(message)
        print(context)
        print("=============\n")
