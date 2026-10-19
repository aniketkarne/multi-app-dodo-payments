# Dodo Payments Microservice

Autonomous payment processing service supporting multiple applications using Dodo Payments.

## 🚀 Features

- **Multi-App Support**: Single service handles payments for multiple applications
- **Dodo Payments Integration**: Full integration with Dodo Payments API
- **Credit Management**: Built-in credit system per app/user
- **Subscription Support**: Handle recurring subscriptions
- **Webhook Processing**: Secure webhook handling with signature verification
- **Idempotency**: Prevents duplicate payment processing
- **Audit Trails**: Complete transaction and event logging
- **Backend-to-Backend**: Secure API key authentication

## 📋 Prerequisites

- Python 3.11+
- MySQL 8.0+
- Dodo Payments Account & API Keys
- Docker & Docker Compose (optional)

## 🛠️ Installation

### Option 1: Docker Compose (Recommended)

1. **Clone and navigate to directory**
```bash
clone https://github.com/aniketkarne/multi-app-dodo-payments.git
```

2. **Set environment variables**
```bash
cp .env.example .env
# Edit .env with your Dodo API keys
```

3. **Start services**
```bash
docker-compose up -d
```

4. **Service will be available at**: `http://localhost:8001`

   The MySQL container auto-loads `payments-db-structure.sql` on first startup
   via the `docker-entrypoint-initdb.d/init.sql` volume mount, so the schema is
   ready by the time the payment service first connects.

### Option 2: Local Development

1. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Setup MySQL database**
```bash
just run the app, it will be done by our app
```

4. **Configure environment**
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. **Run service**
```bash
python main.py
```

## 🔧 Configuration

### Environment Variables

```env
# Service
ENVIRONMENT=development
SERVICE_PORT=8001

# Dodo Payments
DODO_API_KEY=your_dodo_api_key
DODO_WEBHOOK_SECRET=your_webhook_secret

# Database
MYSQL_HOST=localhost
MYSQL_USER=payment_user
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=dodo_payments_db

# Security
API_KEY_ENCRYPTION_KEY=min_32_chars_encryption_key
WEBHOOK_SIGNING_SECRET=webhook_signing_secret
```

## 📡 API Usage

### 1. Register Your App

```bash
curl -X POST http://localhost:8001/apps/register \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "anikets-suppper-app",
    "app_display_name": "Super App By  Aniket",
    "callback_url": "https://your-app.com/webhooks/payment",
    "success_redirect_base": "https://your-app.com/payment/success",
    "cancel_redirect_base": "https://your-app.com/payment/cancel",
    "credits_enabled": true,
    "currency": "USD"
  }'
```

**Response:**
```json
{
  "app_name": "anikets-suppper-app",
  "api_key": "pk_live_anikets_app_xxxxx",
  "app_secret": "sk_anikets_app_xxxxx",
  "message": "App registered successfully"
}
```

⚠️ **Save the API key securely - it won't be shown again!**

### 2. Create Checkout Session

```bash
curl -X POST http://localhost:8001/payments/checkout \
  -H "Content-Type: application/json" \
  -H "X-API-Key: pk_live_anikets_app_xxxxx" \
  -d '{
    "app_name": "anikets-suppper-app",
    "user_id": "firebase_uid_123",
    "product_id": "credits_50",
    "customer_email": "user@example.com",
    "extra_data": {
      "return_url": "https://your-app.com/payment/success"
    }
  }'
```

**Response:**
```json
{
  "session_id": "cks_xxxxx",
  "checkout_url": "https://checkout.dodopayments.com/xxxxx",
  "expires_at": "2025-10-02T10:00:00Z"
}
```

**User completes payment, then Dodo redirects to:**
- One-time: `return_url?payment_id=pay_xxx&status=succeeded`
- Subscription: `return_url?subscription_id=sub_xxx&status=active`

### 3. Verify Payment

```bash
# Verify by payment_id (from Dodo redirect)
curl http://localhost:8001/payments/verify/pay_xxxxx \
  -H "X-API-Key: pk_live_anikets_app_xxxxx"

# Or verify by subscription_id
curl http://localhost:8001/payments/verify/sub_xxxxx \
  -H "X-API-Key: pk_live_anikets_app_xxxxx"
```

### 4. Get User Credits

```bash
curl http://localhost:8001/credits/anikets-suppper-app/firebase_uid_123 \
  -H "X-API-Key: pk_live_anikets_app_xxxxx"
```

### 5. Deduct Credits

```bash
curl -X POST http://localhost:8001/credits/deduct \
  -H "Content-Type: application/json" \
  -H "X-API-Key: pk_live_anikets_app_xxxxx" \
  -d '{
    "app_name": "anikets-suppper-app",
    "user_id": "firebase_uid_123",
    "amount": 1,
    "description": "Super App generation"
  }'
```

## 🔄 Webhook Integration

### Webhook Endpoint
Dodo Payments (via Svix) will send webhooks to: `http://your-service.com/payments/webhook`

**Configure in Dodo Dashboard:**
- Webhook URL: Your service URL + `/payments/webhook`
- Webhook Secret: Copy from Dodo dashboard → Set as `DODO_WEBHOOK_SECRET`

**Svix Headers Received:**
- `webhook-id`: Message ID
- `webhook-timestamp`: Unix timestamp
- `webhook-signature`: Base64 HMAC-SHA256 signature (format: `v1,<signature>`)

**Event Types:**
- `payment.succeeded` - One-time payment completed
- `payment.failed` - Payment failed
- `subscription.renewed` - Subscription created/renewed
- `subscription.cancelled` - Subscription cancelled

### App Callback
After successful payment, the service calls your app's callback URL:

```json
POST {your_app.callback_url}
Headers: X-Webhook-Signature: signature

{
  "event": "payment.success",
  "app_name": "anikets-suppper-app",
  "user_id": "firebase_uid_123",
  "payment_id": "pay_xxxxx",
  "session_id": "cks_xxxxx",
  "product_id": "credits_50",
  "credits_granted": 50,
  "amount": 29.99,
  "currency": "USD",
  "timestamp": "2025-10-01T10:30:00Z"
}
```

### Verify Webhook Signature

```python
import hmac
import hashlib
import json

def verify_webhook(payload: dict, signature: str, secret: str) -> bool:
    payload_str = json.dumps(payload, sort_keys=True)
    expected = hmac.new(
        secret.encode(),
        payload_str.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

## 📊 API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

## 🗄️ Database Schema

See `payments-db-structure.sql` for complete schema.

**Core Tables:**
- `app_registry` - Registered applications
- `product_catalog` - Products per app
- `checkout_sessions` - Dodo checkout sessions
- `payments` - Completed payments
- `webhook_events` - Webhook audit log
- `user_credits` - User credit balances
- `credit_transactions` - Transaction history

## 🧪 Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=. --cov-report=html
```

## 🚢 Deployment

### Docker Deployment

```bash
# Build image
docker build -t dodo-payments:latest .

# Run container
docker run -d \
  -p 8001:8001 \
  --env-file .env \
  --name payment-service \
  dodo-payments:latest
```

### AWS Lambda Deployment

1. Use AWS Lambda container image support
2. Or use Mangum adapter for API Gateway integration

### Environment Variables in Production

Use AWS Secrets Manager, Parameter Store, or similar:

```bash
aws secretsmanager create-secret \
  --name /payment-service/dodo-api-key \
  --secret-string "your_dodo_api_key"
```

## 🔐 Security Best Practices

1. **Never commit `.env` files** - Use `.env.example` template
2. **Rotate API keys regularly** - Use `/apps/{app_name}/rotate-key` endpoint
3. **Enable IP whitelisting** - Set `ENABLE_IP_WHITELIST=true` in production
4. **Use HTTPS only** - Enforce SSL/TLS
5. **Verify webhook signatures** - Always validate Dodo webhooks
6. **Monitor for fraud** - Check analytics for suspicious patterns

## 📈 Monitoring

### Health Check
```bash
curl http://localhost:8001/health
```

### Metrics to Monitor
- Payment success rate
- Webhook processing time
- Failed payments
- API key usage
- Database connection pool

## 🐛 Troubleshooting

### Database Connection Failed
```bash
# Check MySQL is running
docker ps | grep payment-db

# Check connection
mysql -h localhost -P 3307 -u payment_user -p
```

### Webhook Signature Invalid
- Verify `DODO_WEBHOOK_SECRET` matches Dodo dashboard
- Check payload is raw JSON (not parsed)

### API Key Not Working
- Verify key is active: Check `api_keys` table
- Check expiration date
- Ensure correct `X-API-Key` header format

## 📚 Additional Resources

- [Dodo Payments Documentation](https://docs.dodopayments.com)
- [FastAPI Documentation](https://fastapi.tiangolo.com)


## 🤝 Support

For issues or questions:
1. Check logs: `docker logs dodo-payments-service`
2. Review webhook events in database
3. Contact platform team


---
## Buy me a coffee if you like my work: 

<a href="https://www.buymeacoffee.com/aniketkarne" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>
--
