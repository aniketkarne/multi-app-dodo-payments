-- ============================================================================
-- Dodo Payments Microservice - MySQL Schema
-- ============================================================================
-- This file is the canonical MySQL DDL for the payment service.
-- It mirrors the SQLAlchemy models in services/database.py and is loaded
-- automatically by docker-compose on first container startup
-- (mounted to /docker-entrypoint-initdb.d/init.sql).
--
-- For local development without Docker, run:
--     mysql -u payment_user -p dodo_payments_db < payments-db-structure.sql
-- ============================================================================

SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------------------------------------------------------
-- 1. app_registry - Registered applications using the service
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_registry (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    app_name                VARCHAR(100)    NOT NULL,
    app_display_name        VARCHAR(255)    NOT NULL,
    app_secret_key          VARCHAR(255)    NOT NULL,
    callback_url            VARCHAR(500),
    success_redirect_base   VARCHAR(500),
    cancel_redirect_base    VARCHAR(500),
    user_table_reference    VARCHAR(255),
    credits_enabled         TINYINT(1)      DEFAULT 1,
    subscription_enabled    TINYINT(1)      DEFAULT 0,
    currency                VARCHAR(3)      DEFAULT 'USD',
    is_active               TINYINT(1)      DEFAULT 1,
    extra_data              JSON,
    created_at              DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_app_app_name (app_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 2. product_catalog - Products offered by each app
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_catalog (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    app_name                VARCHAR(100)    NOT NULL,
    product_id              VARCHAR(100)    NOT NULL,
    product_name            VARCHAR(255)    NOT NULL,
    description             TEXT,
    product_type            ENUM('ONE_TIME','SUBSCRIPTION') NOT NULL DEFAULT 'ONE_TIME',
    price                   DECIMAL(10, 2)  NOT NULL,
    currency                VARCHAR(3)      NOT NULL DEFAULT 'USD',
    credits_granted         INT             DEFAULT 0,
    subscription_interval   VARCHAR(20),
    subscription_benefits   JSON,
    is_popular              TINYINT(1)      DEFAULT 0,
    display_order           INT             DEFAULT 0,
    is_active               TINYINT(1)      DEFAULT 1,
    extra_data              JSON,
    created_at              DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_product_app_product (app_name, product_id),
    KEY idx_product_app (app_name),
    CONSTRAINT fk_product_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 3. checkout_sessions - Dodo Payments checkout sessions
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkout_sessions (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    session_id      VARCHAR(255)    NOT NULL,
    app_name        VARCHAR(100)    NOT NULL,
    user_id         VARCHAR(255)    NOT NULL,
    product_id      VARCHAR(100)    NOT NULL,
    amount          DECIMAL(10, 2)  NOT NULL,
    currency        VARCHAR(3)      NOT NULL DEFAULT 'USD',
    status          ENUM('PENDING','COMPLETED','FAILED','EXPIRED','CANCELLED') DEFAULT 'PENDING',
    payment_method  VARCHAR(50),
    customer_email  VARCHAR(255),
    success_url     VARCHAR(500),
    cancel_url      VARCHAR(500),
    checkout_url    VARCHAR(500),
    expires_at      DATETIME,
    completed_at    DATETIME,
    extra_data      JSON,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_checkout_session (session_id),
    KEY idx_checkout_app (app_name),
    KEY idx_checkout_user (user_id),
    CONSTRAINT fk_checkout_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 4. payments - Completed payments (one-time and subscription)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    payment_id              VARCHAR(255)    NOT NULL,
    session_id              VARCHAR(255),
    app_name                VARCHAR(100)    NOT NULL,
    user_id                 VARCHAR(255)    NOT NULL,
    product_id              VARCHAR(100)    NOT NULL,
    amount                  DECIMAL(10, 2)  NOT NULL,
    currency                VARCHAR(3)      NOT NULL DEFAULT 'USD',
    status                  ENUM('SUCCESS','FAILED','REFUNDED','PARTIALLY_REFUNDED') DEFAULT 'SUCCESS',
    payment_method          VARCHAR(50),
    customer_email          VARCHAR(255),
    credits_granted         INT             DEFAULT 0,
    subscription_id         VARCHAR(255),
    subscription_start      DATETIME,
    subscription_end        DATETIME,
    dodo_response           JSON,
    refund_amount           DECIMAL(10, 2)  DEFAULT 0.00,
    refund_reason           TEXT,
    refunded_at             DATETIME,
    created_at              DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_payment_payment_id (payment_id),
    KEY idx_payment_app (app_name),
    KEY idx_payment_user (user_id),
    KEY idx_payment_subscription (subscription_id),
    CONSTRAINT fk_payment_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE,
    CONSTRAINT fk_payment_session FOREIGN KEY (session_id)
        REFERENCES checkout_sessions (session_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 5. webhook_events - Webhook audit log (idempotency + forensics)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhook_events (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    event_id                VARCHAR(255)    NOT NULL,
    event_type              VARCHAR(100)    NOT NULL,
    session_id              VARCHAR(255),
    payment_id              VARCHAR(255),
    subscription_id         VARCHAR(255),
    app_name                VARCHAR(100),
    status                  ENUM('RECEIVED','PROCESSING','PROCESSED','FAILED') DEFAULT 'RECEIVED',
    payload                 JSON            NOT NULL,
    signature               VARCHAR(500),
    signature_verified      TINYINT(1)      DEFAULT 0,
    processing_error        TEXT,
    retry_count             INT             DEFAULT 0,
    processed_at            DATETIME,
    created_at              DATETIME        DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_webhook_event_id (event_id),
    KEY idx_webhook_app (app_name),
    KEY idx_webhook_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 6. subscriptions - Active and historical subscriptions
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    subscription_id         VARCHAR(255)    NOT NULL,
    app_name                VARCHAR(100)    NOT NULL,
    user_id                 VARCHAR(255)    NOT NULL,
    product_id              VARCHAR(100)    NOT NULL,
    status                  ENUM('ACTIVE','CANCELLED','EXPIRED','PAUSED') DEFAULT 'ACTIVE',
    interval_type           VARCHAR(20)     NOT NULL,
    amount                  DECIMAL(10, 2)  NOT NULL,
    currency                VARCHAR(3)      NOT NULL DEFAULT 'USD',
    current_period_start    DATETIME,
    current_period_end      DATETIME,
    cancel_at_period_end    TINYINT(1)      DEFAULT 0,
    cancelled_at            DATETIME,
    cancellation_reason     TEXT,
    created_at              DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_subscription_id (subscription_id),
    KEY idx_subscription_app_user (app_name, user_id),
    CONSTRAINT fk_subscription_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 7. user_credits - Per-app, per-user credit balance
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_credits (
    id                          BIGINT      NOT NULL AUTO_INCREMENT,
    app_name                    VARCHAR(100) NOT NULL,
    user_id                     VARCHAR(255) NOT NULL,
    credit_balance              INT         NOT NULL DEFAULT 0,
    total_credits_purchased     INT         DEFAULT 0,
    total_credits_used          INT         DEFAULT 0,
    total_spent                 DECIMAL(10, 2) DEFAULT 0.00,
    last_purchase_at            DATETIME,
    created_at                  DATETIME    DEFAULT CURRENT_TIMESTAMP,
    updated_at                  DATETIME    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_credits_app_user (app_name, user_id),
    CONSTRAINT fk_user_credits_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 8. credit_transactions - Append-only credit ledger
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credit_transactions (
    id                  BIGINT          NOT NULL AUTO_INCREMENT,
    transaction_id      VARCHAR(100)    NOT NULL,
    app_name            VARCHAR(100)    NOT NULL,
    user_id             VARCHAR(255)    NOT NULL,
    type                ENUM('PURCHASE','DEDUCTION','REFUND','ADMIN_GRANT','BONUS') NOT NULL,
    amount              INT             NOT NULL,
    balance_before      INT             NOT NULL,
    balance_after       INT             NOT NULL,
    payment_id          VARCHAR(255),
    description         VARCHAR(500),
    extra_data          JSON,
    created_at          DATETIME        DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_credit_tx_id (transaction_id),
    KEY idx_credit_tx_app_user (app_name, user_id),
    CONSTRAINT fk_credit_tx_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 9. refunds - Refund records
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refunds (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    refund_id       VARCHAR(255)    NOT NULL,
    payment_id      VARCHAR(255)    NOT NULL,
    app_name        VARCHAR(100)    NOT NULL,
    user_id         VARCHAR(255)    NOT NULL,
    amount          DECIMAL(10, 2)  NOT NULL,
    currency        VARCHAR(3)      NOT NULL DEFAULT 'USD',
    credits_revoked INT             DEFAULT 0,
    reason          TEXT,
    status          VARCHAR(20)     DEFAULT 'PENDING',
    dodo_response   JSON,
    processed_at    DATETIME,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_refund_id (refund_id),
    KEY idx_refund_payment (payment_id),
    CONSTRAINT fk_refund_payment FOREIGN KEY (payment_id)
        REFERENCES payments (payment_id) ON DELETE CASCADE,
    CONSTRAINT fk_refund_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 10. api_keys - Authentication keys per app
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    app_name        VARCHAR(100)    NOT NULL,
    api_key         VARCHAR(255)    NOT NULL,
    api_key_prefix  VARCHAR(20)     NOT NULL,
    key_type        VARCHAR(10)     DEFAULT 'LIVE',
    scopes          JSON,
    is_active       TINYINT(1)      DEFAULT 1,
    last_used_at    DATETIME,
    expires_at      DATETIME,
    created_by      VARCHAR(255),
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_api_key (api_key),
    KEY idx_api_key_app (app_name),
    CONSTRAINT fk_api_key_app FOREIGN KEY (app_name)
        REFERENCES app_registry (app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;