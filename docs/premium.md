# Premium Monetization Strategy

AllInKeys offers optional premium features that accelerate key search and provide enterprise-level support.

## Subscription Tiers
- **Basic** – Free tier with community support and standard features.
- **Pro** – Includes access to the faster address database for improved lookup performance.
- **Enterprise** – Unlocks distributed GPU cluster capabilities, access to precomputed ranges service, and priority support.

## Integration Steps
1. Purchase a subscription and receive a license token.
2. Set the token as an environment variable:
   ```bash
   export ALLINKEYS_LICENSE="YOUR-TOKEN-HERE"
   ```
3. Use `PremiumManager` to verify the token and enable premium functionality.
4. (Optional) Run the FastAPI service to distribute precomputed ranges and submit telemetry:
   ```bash
   python -m premium.service
   ```
