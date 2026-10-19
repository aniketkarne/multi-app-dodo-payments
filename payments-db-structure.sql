-- ============================================================================
-- Dodo Payments Microservice - Database Schema
--
-- Mirrors the SQLAlchemy models in services/database.py.
-- Used by docker-compose as the MySQL init script:
--   ./payments-db-structure.sql:/docker-entrypoint-initdb.d/init.sql
--
-- Apply manually if running MySQL outside Docker:
--   mysql -uroot -p dodo_payments_db < payments-db-structure.sql
--
-- Idempotent: every CREATE uses IF NOT EXISTS so re-running the script against
-- an already-initialized database is a no-op.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS dodo_payments_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE dodo_payments_db;

-- ----------------------------------------------------------------------------
-- app_registry: registered applications using the microservice
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_registry (
    id                    BIGINT          NOT NULL AUTO_INCREMENT,
    app_name              VARCHAR(100)    NOT NULL,
    app_display_name      VARCHAR(255)    NOT NULL,
    app_secret_key        VARCHAR(255)    NOT NULL,
    callback_url          VARCHAR(500),
    success_redirect_base VARCHAR(500),
    cancel_redirect_base  VARCHAR(500),
    user_table_reference  VARCHAR(255),
    credits_enabled       BOOLEAN         DEFAULT TRUE,
    subscription_enabled  BOOLEAN         DEFAULT FALSE,
    currency              VARCHAR(3)      DEFAULT 'USD',
    is_active             BOOLEAN         DEFAULT TRUE,
    extra_data            JSON,
    created_at            DATETIME,
    updated_at            DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_app_registry_app_name (app_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- product_catalog: products per app
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_catalog (
    id                    BIGINT          NOT NULL AUTO_INCREMENT,
    app_name              VARCHAR(100)    NOT NULL,
    product_id            VARCHAR(100)    NOT NULL,
    product_name          VARCHAR(255)    NOT NULL,
    description           TEXT,
    product_type          ENUM('ONE_TIME','SUBSCRIPTION') NOT NULL DEFAULT 'ONE_TIME',
    price                 DECIMAL(10,2)   NOT NULL,
    currency              VARCHAR(3)      NOT NULL DEFAULT 'USD',
    credits_granted       INT             DEFAULT 0,
    subscription_interval VARCHAR(20),
    subscription_benefits JSON,
    is_popular            BOOLEAN         DEFAULT FALSE,
    display_order         INT             DEFAULT 0,
    is_active             BOOLEAN         DEFAULT TRUE,
    extra_data            JSON,
    created_at            DATETIME,
    updated_at            DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_catalog_app_product (app_name, product_id),
    KEY ix_product_catalog_app_name (app_name),
    CONSTRAINT fk_product_catalog_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- checkout_sessions: Dodo checkout sessions (one per checkout)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkout_sessions (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    session_id      VARCHAR(255)    NOT NULL,
    app_name        VARCHAR(100)    NOT NULL,
    user_id         VARCHAR(255)    NOT NULL,
    product_id      VARCHAR(100)    NOT NULL,
    amount          DECIMAL(10,2)   NOT NULL,
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
    created_at      DATETIME,
    updated_at      DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_checkout_sessions_session_id (session_id),
    KEY ix_checkout_sessions_app_name (app_name),
    KEY ix_checkout_sessions_user_id (user_id),
    CONSTRAINT fk_checkout_sessions_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- payments: completed payments (one-time + subscription invoices)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id                  BIGINT          NOT NULL AUTO_INCREMENT,
    payment_id          VARCHAR(255)    NOT NULL,
    session_id          VARCHAR(255),
    app_name            VARCHAR(100)    NOT NULL,
    user_id             VARCHAR(255)    NOT NULL,
    product_id          VARCHAR(100)    NOT NULL,
    amount              DECIMAL(10,2)   NOT NULL,
    currency            VARCHAR(3)      NOT NULL DEFAULT 'USD',
    status              ENUM('SUCCESS','FAILED','REFUNDED','PARTIALLY_REFUNDED') DEFAULT 'SUCCESS',
    payment_method      VARCHAR(50),
    customer_email      VARCHAR(255),
    credits_granted     INT             DEFAULT 0,
    subscription_id     VARCHAR(255),
    subscription_start  DATETIME,
    subscription_end    DATETIME,
    dodo_response       JSON,
    refund_amount       DECIMAL(10,2)   DEFAULT 0.00,
    refund_reason       TEXT,
    refunded_at         DATETIME,
    created_at          DATETIME,
    updated_at          DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_payments_payment_id (payment_id),
    KEY ix_payments_session_id (session_id),
    KEY ix_payments_subscription_id (subscription_id),
    KEY ix_payments_app_user (app_name, user_id),
    CONSTRAINT fk_payments_session
        FOREIGN KEY (session_id) REFERENCES checkout_sessions(session_id) ON DELETE CASCADE,
    CONSTRAINT fk_payments_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- webhook_events: audit log of every webhook received from Dodo
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhook_events (
    id                   BIGINT          NOT NULL AUTO_INCREMENT,
    event_id             VARCHAR(255)    NOT NULL,
    event_type           VARCHAR(100)    NOT NULL,
    session_id           VARCHAR(255),
    payment_id           VARCHAR(255),
    subscription_id      VARCHAR(255),
    app_name             VARCHAR(100),
    status               ENUM('RECEIVED','PROCESSING','PROCESSED','FAILED') DEFAULT 'RECEIVED',
    payload              JSON            NOT NULL,
    signature            VARCHAR(500),
    signature_verified   BOOLEAN         DEFAULT FALSE,
    processing_error     TEXT,
    retry_count          INT             DEFAULT 0,
    processed_at         DATETIME,
    created_at           DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_webhook_events_event_id (event_id),
    KEY ix_webhook_events_app_name (app_name),
    KEY ix_webhook_events_event_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- subscriptions: long-lived subscription state (independent of payment rows)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id                    BIGINT          NOT NULL AUTO_INCREMENT,
    subscription_id       VARCHAR(255)    NOT NULL,
    app_name              VARCHAR(100)    NOT NULL,
    user_id               VARCHAR(255)    NOT NULL,
    product_id            VARCHAR(100)    NOT NULL,
    status                ENUM('ACTIVE','CANCELLED','EXPIRED','PAUSED') DEFAULT 'ACTIVE',
    interval_type         VARCHAR(20)     NOT NULL,
    amount                DECIMAL(10,2)   NOT NULL,
    currency              VARCHAR(3)      NOT NULL DEFAULT 'USD',
    current_period_start  DATETIME,
    current_period_end    DATETIME,
    cancel_at_period_end  BOOLEAN         DEFAULT FALSE,
    cancelled_at          DATETIME,
    cancellation_reason   TEXT,
    created_at            DATETIME,
    updated_at            DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_subscriptions_subscription_id (subscription_id),
    KEY ix_subscriptions_app_user (app_name, user_id),
    CONSTRAINT fk_subscriptions_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- user_credits: per-(app,user) credit balance
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_credits (
    id                       BIGINT        NOT NULL AUTO_INCREMENT,
    app_name                 VARCHAR(100)  NOT NULL,
    user_id                  VARCHAR(255)  NOT NULL,
    credit_balance           INT           NOT NULL DEFAULT 0,
    total_credits_purchased  INT           DEFAULT 0,
    total_credits_used       INT           DEFAULT 0,
    total_spent              DECIMAL(10,2) DEFAULT 0.00,
    last_purchase_at         DATETIME,
    created_at               DATETIME,
    updated_at               DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_credits_app_user (app_name, user_id),
    CONSTRAINT fk_user_credits_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- credit_transactions: append-only ledger of every credit balance change
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credit_transactions (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    transaction_id  VARCHAR(100)    NOT NULL,
    app_name        VARCHAR(100)    NOT NULL,
    user_id         VARCHAR(255)    NOT NULL,
    type            ENUM('PURCHASE','DEDUCTION','REFUND','ADMIN_GRANT','BONUS') NOT NULL,
    amount          INT             NOT NULL,
    balance_before  INT             NOT NULL,
    balance_after   INT             NOT NULL,
    payment_id      VARCHAR(255),
    description     VARCHAR(500),
    extra_data      JSON,
    created_at      DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_credit_transactions_transaction_id (transaction_id),
    KEY ix_credit_transactions_app_user (app_name, user_id),
    KEY ix_credit_transactions_payment_id (payment_id),
    CONSTRAINT fk_credit_transactions_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- refunds: refund records (one per refund transaction)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refunds (
    id                BIGINT          NOT NULL AUTO_INCREMENT,
    refund_id         VARCHAR(255)    NOT NULL,
    payment_id        VARCHAR(255)    NOT NULL,
    app_name          VARCHAR(100)    NOT NULL,
    user_id           VARCHAR(255)    NOT NULL,
    amount            DECIMAL(10,2)   NOT NULL,
    currency          VARCHAR(3)      NOT NULL DEFAULT 'USD',
    credits_revoked   INT             DEFAULT 0,
    reason            TEXT,
    status            VARCHAR(20)     DEFAULT 'PENDING',
    dodo_response     JSON,
    processed_at      DATETIME,
    created_at        DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_refunds_refund_id (refund_id),
    KEY ix_refunds_payment_id (payment_id),
    KEY ix_refunds_app_user (app_name, user_id),
    CONSTRAINT fk_refunds_payment
        FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE,
    CONSTRAINT fk_refunds_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- api_keys: per-app API keys for backend-to-backend auth
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id              BIGINT          NOT NULL AUTO_INCREMENT,
    app_name        VARCHAR(100)    NOT NULL,
    api_key         VARCHAR(255)    NOT NULL,
    api_key_prefix  VARCHAR(20)     NOT NULL,
    key_type        VARCHAR(10)     DEFAULT 'LIVE',
    scopes          JSON,
    is_active       BOOLEAN         DEFAULT TRUE,
    last_used_at    DATETIME,
    expires_at      DATETIME,
    created_by      VARCHAR(255),
    created_at      DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_api_keys_api_key (api_key),
    KEY ix_api_keys_app_name (app_name),
    CONSTRAINT fk_api_keys_app
        FOREIGN KEY (app_name) REFERENCES app_registry(app_name) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;